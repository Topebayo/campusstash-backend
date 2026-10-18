from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_current_active_user
from app.models.user import User, PublicProfile
from app.core import security
from app.schemas.user import UserRead, UserUpdate, ChangePasswordRequest

router = APIRouter()

@router.get("/me", response_model=UserRead)
async def read_user_me(
    current_user: User = Depends(get_current_active_user)
) -> Any:
    return current_user

@router.put("/me", response_model=UserRead)
async def update_user_me(
    user_in: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    if user_in.phone is not None:
        phone_cleaned = user_in.phone.strip()
        if not phone_cleaned:
            raise HTTPException(status_code=400, detail="Phone number is mandatory")
        result = await db.execute(
            select(User).where(User.phone == phone_cleaned, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Phone number already in use")
        current_user.phone = phone_cleaned
        
    if user_in.burner_handle is not None:
        handle_cleaned = user_in.burner_handle.strip()
        if not handle_cleaned:
            raise HTTPException(status_code=400, detail="Burner handle is mandatory")
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
async def change_password(
    payload: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    if not security.verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")
    current_user.hashed_password = security.get_password_hash(payload.new_password)
    await db.commit()
    return {"message": "Password updated successfully"}
