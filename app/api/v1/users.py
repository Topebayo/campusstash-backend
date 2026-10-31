from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_current_active_user
from app.models.user import User, PublicProfile
from app.core import security
from app.core.limiter import limiter
from app.schemas.user import UserRead, UserUpdate, ChangePasswordRequest

router = APIRouter()

@router.get("/me", response_model=UserRead)
async def read_user_me(
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Retrieve profile of the currently authenticated user.
    Strictly isolated: users can ONLY access their own record.
    """
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
