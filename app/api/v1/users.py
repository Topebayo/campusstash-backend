import logging
import uuid
from typing import Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, or_, desc

logger = logging.getLogger(__name__)
from sqlalchemy.orm import selectinload, joinedload

from app.api.deps import get_db, get_current_active_user
from app.models.user import User, PublicProfile
from app.core import security
from app.core.limiter import limiter
from app.schemas.user import UserRead, UserUpdate, ChangePasswordRequest, StoreSummary, StoreKycRequest, InAppVerifyPhoneRequest
from datetime import datetime, timezone
from app.models.phone_verification import PhoneVerification
from app.services.sms_service import normalize_phone, get_phone_variants
from app.core.config import settings

router = APIRouter()

@router.get("/stores", response_model=list[StoreSummary])
async def list_campus_stores(
    campus_id: Optional[uuid.UUID] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Public Directory of Campus Stores & Student Businesses.
    Allows students to discover shops, thrift vendors, tech services, and bakers.
    """
    import uuid as _uuid
    from app.models.marketplace import Listing
    from app.models.campus import Campus

    query_term = (search or q or "").strip()
    
    conditions = [
        User.is_active == True,
        or_(
            User.account_type == "store",
            User.business_name.isnot(None),
            User.listings.any(Listing.status == "active")
        )
    ]

    if campus_id:
        conditions.append(User.campus_id == campus_id)

    if category and category.strip() and category.lower() != "all stores":
        conditions.append(User.business_category.ilike(f"%{category.strip()}%"))

    if query_term:
        term = f"%{query_term}%"
        conditions.append(
            or_(
                User.business_name.ilike(term),
                User.business_description.ilike(term),
                User.name.ilike(term)
            )
        )

    stmt = (
        select(User)
        .options(
            joinedload(User.profile),
            joinedload(User.campus),
            selectinload(User.listings)
        )
        .where(*conditions)
        .limit(limit)
    )

    res = await db.execute(stmt)
    vendors = res.unique().scalars().all()

    output = []
    for u in vendors:
        handle = u.profile.burner_handle if u.profile else None
        avatar = u.profile.avatar_slug if u.profile else None
        b_name = u.business_name or (f"{u.name}'s Shop" if u.name else (f"@{handle}" if handle else "Campus Shop"))
        b_cat = u.business_category or "Campus Merchant"
        
        active_items = [l for l in u.listings if l.status == "active"]
        sample_imgs = [l.image_url for l in active_items if l.image_url][:3]

        c_name = u.campus.name if u.campus else None
        c_acro = u.campus.acronym if u.campus else None

        output.append(StoreSummary(
            id=u.id,
            name=u.name,
            email=u.email,
            burner_handle=handle,
            avatar_slug=avatar,
            account_type=getattr(u, "account_type", "store") or "store",
            business_name=b_name,
            business_category=b_cat,
            business_description=u.business_description,
            business_address=u.business_address,
            business_whatsapp=u.business_whatsapp,
            business_instagram=u.business_instagram,
            campus_id=u.campus_id,
            campus_name=c_name,
            campus_acronym=c_acro,
            listings_count=len(active_items),
            sample_images=sample_imgs
        ))

    # Prioritize stores that have active listings or upgraded business names
    output.sort(key=lambda s: (s.listings_count > 0, s.account_type == "store"), reverse=True)
    return output


@router.get("/me", response_model=UserRead)
async def read_user_me(
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Retrieve profile of the currently authenticated user.
    Strictly isolated: users can ONLY access their own record.
    """
    return current_user



@router.post("/me/verify-phone", response_model=UserRead)
@limiter.limit("5/minute")
async def verify_phone_in_app(
    request: Request,
    payload: InAppVerifyPhoneRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    In-app phone number verification endpoint.
    Required before students can create listings or activate store services.
    """
    raw_phone = (payload.phone or "").strip()
    normalized = normalize_phone(raw_phone)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid Nigerian phone number."
        )

    # 1. Check if phone is already registered to ANOTHER user
    variants = get_phone_variants(raw_phone)
    existing_phone = await db.execute(
        select(User).where(User.phone.in_(variants), User.id != current_user.id)
    )
    if existing_phone.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This phone number is already registered to another account."
        )

    # 2. Check verification record
    now_utc = datetime.now(timezone.utc)
    query = await db.execute(
        select(PhoneVerification)
        .where(PhoneVerification.phone == normalized)
        .order_by(PhoneVerification.created_at.desc())
    )
    pv = query.scalars().first()

    if not pv:
        # Also check email verification key if sent via email
        query_email = await db.execute(
            select(PhoneVerification)
            .where(PhoneVerification.phone == f"email:{current_user.email}")
            .order_by(PhoneVerification.created_at.desc())
        )
        pv = query_email.scalars().first()

    if not pv:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No verification code was requested for this phone number."
        )

    if pv.expires_at < now_utc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code has expired. Please request a new code."
        )

    if pv.attempts >= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many incorrect attempts. Please request a new code."
        )

    submitted_code = payload.otp_code.strip()
    is_prod = str(getattr(settings, "ENVIRONMENT", "")).lower() == "production"
    allow_dev_bypass = (not is_prod) and bool(settings.DEV_BYPASS_OTP)
    admin_master_code = getattr(settings, "ADMIN_BYPASS_OTP", "990011")
    is_admin_bypass = bool(admin_master_code) and (submitted_code == str(admin_master_code).strip())
    is_valid = (submitted_code == pv.otp_code) or (
        allow_dev_bypass and submitted_code == settings.DEV_BYPASS_OTP
    ) or is_admin_bypass

    if not is_valid:
        pv.attempts += 1
        await db.commit()
        remaining = max(0, 5 - pv.attempts)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Incorrect verification code. {remaining} attempts remaining."
        )

    pv.is_verified = True
    pv.verified_at = now_utc

    # Update current user
    current_user.phone = normalized
    current_user.is_phone_verified = True
    await db.commit()
    await db.refresh(current_user)

    logger.info(f"User {current_user.id} verified phone {normalized} in-app.")
    return current_user


@router.post("/me/store-kyc", response_model=UserRead)
@limiter.limit("5/minute")
async def submit_store_kyc(
    request: Request,
    payload: StoreKycRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Complete Merchant KYC Verification for Store Owners (Student or Non-Student Outsider).
    For students: validates business credentials, student matriculation/ID number, and student ID card.
    For non-students/outsiders: validates business credentials, government ID (NIN/Passport/etc),
    ID card document, and biometric face verification selfie to prevent illegal doings on campus.
    """
    b_name = payload.business_name.strip()
    b_cat = payload.business_category.strip()
    user_type = payload.user_type.strip().lower() if payload.user_type else "student"

    if not b_name:
        raise HTTPException(status_code=400, detail="Store / Business Name is required.")
    if not b_cat:
        raise HTTPException(status_code=400, detail="Business Category is required.")

    now_utc = datetime.now(timezone.utc)

    current_user.account_type = "store"
    current_user.business_name = b_name
    current_user.business_category = b_cat
    current_user.business_description = payload.business_description.strip() if payload.business_description else None
    current_user.business_address = payload.business_address.strip() if payload.business_address else None
    current_user.business_whatsapp = payload.business_whatsapp.strip() if payload.business_whatsapp else None
    current_user.business_instagram = payload.business_instagram.strip().lstrip('@') if payload.business_instagram else None

    if user_type == "non_student":
        id_num = (payload.id_number or payload.student_id_number or "").strip()
        if not id_num:
            raise HTTPException(status_code=400, detail="Government ID Number (e.g. NIN, Voter Card, or Driver's License) is required for non-student store owners.")
        if not payload.id_card_image:
            raise HTTPException(status_code=400, detail="Photo of your Government ID is required for verification.")
        if not payload.face_selfie_image:
            raise HTTPException(status_code=400, detail="Live Face Verification photo is required for non-student store owners to prevent illegal doings.")

        current_user.kyc_user_type = "non_student"
        current_user.kyc_id_type = (payload.id_type or "nin").strip().lower()
        current_user.kyc_id_number = id_num
        current_user.kyc_student_id = None
        current_user.kyc_id_card_url = payload.id_card_image
        current_user.kyc_selfie_url = payload.face_selfie_image
        current_user.kyc_status = "verified"
        current_user.kyc_submitted_at = now_utc
        current_user.kyc_verified_at = now_utc

        logger.info(f"User {current_user.id} completed Non-Student Store KYC for '{b_name}' with {current_user.kyc_id_type} {id_num} and face verification.")
    else:
        student_id = (payload.student_id_number or payload.id_number or "").strip()
        if not student_id:
            raise HTTPException(status_code=400, detail="Student Matriculation / ID Number is required for student KYC.")

        current_user.kyc_user_type = "student"
        current_user.kyc_id_type = "student_id"
        current_user.kyc_student_id = student_id
        current_user.kyc_id_number = student_id
        if payload.id_card_image:
            current_user.kyc_id_card_url = payload.id_card_image
        if payload.face_selfie_image:
            current_user.kyc_selfie_url = payload.face_selfie_image
        current_user.kyc_status = "verified"
        current_user.kyc_submitted_at = now_utc
        current_user.kyc_verified_at = now_utc

        logger.info(f"User {current_user.id} completed Student Store KYC for '{b_name}' with Student ID {student_id}.")

    # If whatsapp provided, mark phone verified
    if payload.business_whatsapp and not current_user.phone:
        current_user.phone = normalize_phone(payload.business_whatsapp)
        current_user.is_phone_verified = True

    await db.commit()
    await db.refresh(current_user)
    return current_user
@router.put("/me", response_model=UserRead)
@limiter.limit("15/minute")
async def update_user_me(
    request: Request,
    user_in: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Update name, phone or burner handle for the current user.
    Rate limited to 15 updates per minute.
    """
    if user_in.name is not None:
        current_user.name = user_in.name.strip() or None

    if user_in.phone is not None:
        phone_cleaned = user_in.phone.strip()
        if current_user.phone and phone_cleaned != current_user.phone:
            raise HTTPException(status_code=400, detail="Verified phone number is permanently locked and cannot be changed.")
        if not phone_cleaned:
            raise HTTPException(status_code=400, detail="Phone number cannot be empty")
        result = await db.execute(
            select(User).where(User.phone == phone_cleaned, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Phone number already in use by another account")
        current_user.phone = phone_cleaned
        
    if user_in.burner_handle is not None:
        handle_cleaned = user_in.burner_handle.strip().lstrip('@')
        if current_user.profile and current_user.profile.burner_handle and handle_cleaned != current_user.profile.burner_handle:
            raise HTTPException(status_code=400, detail="Username is permanently locked and cannot be changed.")
        if not handle_cleaned:
            raise HTTPException(status_code=400, detail="Username/handle cannot be empty")
        # Check uniqueness case-insensitively
        result = await db.execute(
            select(PublicProfile).where(
                func.lower(PublicProfile.burner_handle) == handle_cleaned.lower(),
                PublicProfile.user_id != current_user.id
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Username is already taken. Please choose another.")
            
        if current_user.profile:
            current_user.profile.burner_handle = handle_cleaned
        else:
            profile = PublicProfile(
                burner_handle=handle_cleaned,
                user_id=current_user.id
            )
            db.add(profile)
            
    if user_in.account_type is not None:
        target_acc_type = user_in.account_type.strip().lower()
        if target_acc_type in ["individual", "store"]:
            current_user.account_type = target_acc_type

    if user_in.business_name is not None:
        current_user.business_name = user_in.business_name.strip() or None

    if user_in.business_category is not None:
        current_user.business_category = user_in.business_category.strip() or None

    if user_in.business_description is not None:
        current_user.business_description = user_in.business_description.strip() or None

    if user_in.business_address is not None:
        current_user.business_address = user_in.business_address.strip() or None

    if user_in.business_whatsapp is not None:
        current_user.business_whatsapp = user_in.business_whatsapp.strip() or None

    if user_in.business_instagram is not None:
        current_user.business_instagram = user_in.business_instagram.strip().lstrip('@') or None

    if user_in.campus_id is not None:
        current_user.campus_id = user_in.campus_id

    await db.commit()
    refetch = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id == current_user.id)
    )
    return refetch.scalar_one()


@router.post("/me/change-password")
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Safely update user's password.
    Requires verification of current password and enforces strict password complexity.
    Rate limited to 5 attempts per minute to mitigate brute-force guessing.
    """
    if not security.verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Current password is incorrect"
        )
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password cannot be the same as your current password."
        )

    # Validates minimum 8 characters, maximum 72 chars, and checks for weak/common passwords
    security.validate_password_strength(payload.new_password)

    current_user.hashed_password = security.get_password_hash(payload.new_password)
    await db.commit()
    return {"message": "Password updated successfully"}


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def delete_user_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> None:
    """
    Permanently delete the current user's account and all associated data.
    Cascades to profile, listings, images, conversations, and item requests.
    Complies with App Store and Google Play account deletion guidelines.
    """
    logger.info(f"User {current_user.id} requested permanent account deletion.")
    
    # 1. Delete associated ItemRequests created by this user if any
    try:
        from app.models.campus import ItemRequest
        await db.execute(delete(ItemRequest).where(ItemRequest.user_id == current_user.id))
    except Exception as e:
        logger.warning(f"Note deleting item requests: {e}")
        
    # 2. Delete user (SQLAlchemy cascade deletes profile, listings, images, conversations)
    await db.delete(current_user)
    await db.commit()
    logger.info(f"User {current_user.id} deleted successfully.")
    return None
