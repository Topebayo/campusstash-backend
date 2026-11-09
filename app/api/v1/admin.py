import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_, update, delete
from sqlalchemy.orm import selectinload, joinedload

from app.api.deps import get_current_admin, get_db
from app.models.user import User
from app.models.phone_verification import PhoneVerification
from app.models.marketplace import Listing
from app.models.campus import Campus

router = APIRouter()


class DirectVerifyRequest(BaseModel):
    identifier: str  # Email or phone number


class KycActionRequest(BaseModel):
    reason: Optional[str] = None


@router.get("/me")
async def get_admin_profile(
    admin: User = Depends(get_current_admin),
) -> Dict[str, Any]:
    """
    Validates admin credentials and returns the administrator profile.
    Only authorized emails can access this endpoint.
    """
    return {
        "id": str(admin.id),
        "email": admin.email,
        "name": admin.name or "Admin",
        "phone": admin.phone,
        "is_admin": True,
    }


@router.get("/stats")
async def get_admin_stats(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Returns platform-wide metrics in real time.
    """
    # Total users
    res_users = await db.execute(select(func.count(User.id)))
    total_users = res_users.scalar() or 0

    # Verified users
    res_verified = await db.execute(
        select(func.count(User.id)).where(User.is_phone_verified == True)
    )
    verified_users = res_verified.scalar() or 0

    # Total listings
    res_listings = await db.execute(select(func.count(Listing.id)))
    total_listings = res_listings.scalar() or 0

    # Active listings
    res_active_listings = await db.execute(
        select(func.count(Listing.id)).where(Listing.status == "active")
    )
    active_listings = res_active_listings.scalar() or 0

    # Pending KYC
    res_kyc = await db.execute(
        select(func.count(User.id)).where(User.kyc_status == "pending")
    )
    pending_kyc = res_kyc.scalar() or 0

    # New users last 24h
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    res_24h = await db.execute(
        select(func.count(User.id)).where(User.created_at >= cutoff_24h)
    )
    users_last_24h = res_24h.scalar() or 0

    # Total campuses
    res_campuses = await db.execute(select(func.count(Campus.id)))
    total_campuses = res_campuses.scalar() or 0

    return {
        "total_users": total_users,
        "verified_users": verified_users,
        "unverified_users": max(0, total_users - verified_users),
        "total_listings": total_listings,
        "active_listings": active_listings,
        "pending_kyc": pending_kyc,
        "users_last_24h": users_last_24h,
        "total_campuses": total_campuses,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/users")
async def list_users(
    query: Optional[str] = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Search and paginate users. Includes campus and verification details.
    """
    stmt = (
        select(User)
        .options(joinedload(User.campus))
        .order_by(desc(User.created_at))
    )

    if query and query.strip():
        term = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                User.email.ilike(term),
                User.phone.ilike(term),
                User.name.ilike(term),
                User.business_name.ilike(term),
            )
        )

    # Get count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = (await db.execute(count_stmt)).scalar() or 0

    # Paginate
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    users = result.unique().scalars().all()

    # Enrich with latest phone verification code if applicable
    items = []
    for u in users:
        latest_otp = None
        otp_expires = None
        if u.phone:
            # Query latest OTP for this phone
            clean_phone = u.phone.strip()
            pv_res = await db.execute(
                select(PhoneVerification)
                .where(
                    or_(
                        PhoneVerification.phone == clean_phone,
                        PhoneVerification.phone == f"234{clean_phone.lstrip('0')}",
                        PhoneVerification.phone == f"0{clean_phone[3:]}" if clean_phone.startswith("234") else False
                    )
                )
                .order_by(desc(PhoneVerification.created_at))
                .limit(1)
            )
            pv = pv_res.scalar_one_or_none()
            if pv:
                latest_otp = pv.otp_code
                otp_expires = pv.expires_at.isoformat() if pv.expires_at else None

        items.append({
            "id": str(u.id),
            "email": u.email,
            "name": u.name or "Unnamed",
            "phone": u.phone or "No Phone",
            "is_phone_verified": u.is_phone_verified,
            "account_type": u.account_type,
            "business_name": u.business_name,
            "campus": u.campus.name if u.campus else "No Campus",
            "campus_acronym": u.campus.acronym if u.campus else None,
            "kyc_status": u.kyc_status,
            "latest_otp": latest_otp,
            "otp_expires": otp_expires,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })

    return {
        "items": items,
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/recent-otps")
async def get_recent_otps(
    limit: int = Query(default=25, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """
    Returns the most recent OTP verification codes issued across the platform.
    Allows the admin to instantly see any student's OTP during telco SMS delays.
    """
    stmt = (
        select(PhoneVerification)
        .order_by(desc(PhoneVerification.created_at))
        .limit(limit)
    )
    result = await db.execute(stmt)
    records = result.scalars().all()

    now_utc = datetime.now(timezone.utc)
    return [
        {
            "id": str(r.id),
            "phone": r.phone,
            "otp_code": r.otp_code,
            "is_verified": r.is_verified,
            "attempts": r.attempts,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "is_expired": r.expires_at < now_utc if r.expires_at else False,
            "verified_at": r.verified_at.isoformat() if r.verified_at else None,
        }
        for r in records
    ]


@router.post("/users/{user_id}/force-verify")
async def force_verify_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Force-verifies a user's phone number and identity.
    Bypasses SMS telco delivery delays instantly.
    """
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    user.is_phone_verified = True
    user.updated_at = datetime.now(timezone.utc)

    # Also mark any pending PhoneVerification as verified
    if user.phone:
        clean_phone = user.phone.strip()
        now_utc = datetime.now(timezone.utc)
        await db.execute(
            update(PhoneVerification)
            .where(
                or_(
                    PhoneVerification.phone == clean_phone,
                    PhoneVerification.phone == f"234{clean_phone.lstrip('0')}",
                )
            )
            .values(is_verified=True, verified_at=now_utc)
        )

    await db.commit()
    return {
        "status": "success",
        "message": f"Successfully force-verified {user.name or user.email} ({user.phone or 'no phone'})",
        "user_id": str(user.id),
        "is_phone_verified": True,
    }


@router.post("/verify-direct")
async def verify_direct_identifier(
    payload: DirectVerifyRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Direct 1-tap verification by phone number or email address.
    """
    ident = payload.identifier.strip()
    if not ident:
        raise HTTPException(status_code=400, detail="Identifier cannot be empty")

    now_utc = datetime.now(timezone.utc)
    user = None

    # Try lookup by email
    if "@" in ident:
        res = await db.execute(select(User).where(User.email.ilike(ident)))
        user = res.scalar_one_or_none()
    else:
        # Try lookup by phone
        phone_clean = ident.replace(" ", "").replace("-", "").replace("+", "")
        variants = [
            phone_clean,
            f"0{phone_clean[3:]}" if phone_clean.startswith("234") else phone_clean,
            f"234{phone_clean.lstrip('0')}",
        ]
        res = await db.execute(select(User).where(User.phone.in_(variants)))
        user = res.scalar_one_or_none()

        # Also update PhoneVerification table
        await db.execute(
            update(PhoneVerification)
            .where(PhoneVerification.phone.in_(variants))
            .values(is_verified=True, verified_at=now_utc)
        )

    if user:
        user.is_phone_verified = True
        user.updated_at = now_utc
        await db.commit()
        return {
            "status": "success",
            "message": f"Verified user: {user.name or user.email} ({user.phone})",
            "user_id": str(user.id),
            "is_phone_verified": True,
        }
    else:
        # Only PhoneVerification existed (student midway through signup)
        await db.commit()
        return {
            "status": "success",
            "message": f"Phone record verified for {ident}. Student can now complete registration.",
            "is_phone_verified": True,
        }


@router.get("/kyc/pending")
async def list_pending_kyc(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """
    Lists all store owners and users with pending KYC reviews.
    """
    stmt = (
        select(User)
        .options(joinedload(User.campus))
        .where(User.kyc_status == "pending")
        .order_by(desc(User.kyc_submitted_at))
    )
    result = await db.execute(stmt)
    users = result.unique().scalars().all()

    return [
        {
            "id": str(u.id),
            "name": u.name or "Unnamed",
            "email": u.email,
            "phone": u.phone or "No Phone",
            "account_type": u.account_type,
            "business_name": u.business_name,
            "business_category": u.business_category,
            "business_address": u.business_address,
            "campus": u.campus.name if u.campus else "No Campus",
            "kyc_user_type": u.kyc_user_type,
            "kyc_id_type": u.kyc_id_type,
            "kyc_id_number": u.kyc_id_number,
            "kyc_id_card_url": u.kyc_id_card_url,
            "kyc_selfie_url": u.kyc_selfie_url,
            "kyc_submitted_at": u.kyc_submitted_at.isoformat() if u.kyc_submitted_at else None,
        }
        for u in users
    ]


@router.post("/kyc/{user_id}/approve")
async def approve_kyc(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Approves a user's Store KYC, granting them verified badge and store privileges.
    """
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.kyc_status = "verified"
    user.kyc_verified_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "status": "success",
        "message": f"Approved Store KYC for {user.name or user.email}",
        "user_id": str(user.id),
        "kyc_status": "verified",
    }


@router.post("/kyc/{user_id}/reject")
async def reject_kyc(
    user_id: uuid.UUID,
    payload: KycActionRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Rejects a user's Store KYC.
    """
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.kyc_status = "rejected"
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "status": "success",
        "message": f"Rejected Store KYC for {user.name or user.email}. Reason: {payload.reason or 'Unspecified'}",
        "user_id": str(user.id),
        "kyc_status": "rejected",
    }


@router.get("/listings")
async def list_admin_listings(
    query: Optional[str] = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Listings moderation feed.
    """
    stmt = (
        select(Listing)
        .options(joinedload(Listing.seller), joinedload(Listing.campus))
        .order_by(desc(Listing.created_at))
    )

    if query and query.strip():
        term = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Listing.title.ilike(term),
                Listing.description.ilike(term),
                Listing.category.ilike(term),
            )
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    listings = result.unique().scalars().all()

    return {
        "items": [
            {
                "id": str(l.id),
                "title": l.title,
                "price": l.price,
                "category": l.category,
                "status": l.status,
                "image_url": l.image_url,
                "seller_name": l.seller.name if l.seller else "Unknown",
                "seller_email": l.seller.email if l.seller else None,
                "campus": l.campus.name if l.campus else "No Campus",
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in listings
        ],
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.delete("/listings/{listing_id}")
async def admin_delete_listing(
    listing_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Moderator deletion of spam or prohibited listings.
    """
    res = await db.execute(select(Listing).where(Listing.id == listing_id))
    listing = res.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    title = listing.title
    await db.delete(listing)
    await db.commit()

    return {
        "status": "success",
        "message": f"Successfully deleted listing: '{title}'",
        "listing_id": str(listing_id),
    }
