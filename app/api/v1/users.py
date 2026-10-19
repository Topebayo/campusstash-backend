from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
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
    Update phone or burner handle for the current user.
    Rate limited to 15 updates per minute.
    """
    if user_in.phone is not None:
        phone_cleaned = user_in.phone.strip()
        if not phone_cleaned:
            raise HTTPException(status_code=400, detail="Phone number cannot be empty")
        result = await db.execute(
            select(User).where(User.phone == phone_cleaned, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Phone number already in use by another account")
        current_user.phone = phone_cleaned
        
    if user_in.burner_handle is not None:
        handle_cleaned = user_in.burner_handle.strip()
        if not handle_cleaned:
            raise HTTPException(status_code=400, detail="Burner handle cannot be empty")
        # Check uniqueness
        result = await db.execute(
            select(PublicProfile).where(PublicProfile.burner_handle == handle_cleaned, PublicProfile.user_id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Burner handle already taken")
            
        if current_user.profile:
            current_user.profile.burner_handle = handle_cleaned
        else:
            profile = PublicProfile(
                burner_handle=handle_cleaned,
                user_id=current_user.id
            )
            db.add(profile)
            
    await db.commit()
    await db.refresh(current_user)
    return current_user


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
