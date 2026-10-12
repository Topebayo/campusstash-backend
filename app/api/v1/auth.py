import uuid
from datetime import timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from jose import ExpiredSignatureError, JWTError

from app.api.deps import get_db, get_current_user
from app.core import security
from app.core.config import settings
from app.models.user import User, PublicProfile
from app.schemas.token import TokenResponse, RefreshTokenRequest
from app.schemas.user import UserRegister, UserRead, LoginRequest

router = APIRouter()

def build_token_response(user: User) -> TokenResponse:
    """Helper to generate standardized JSON auth response with user metadata."""
    access_token = security.create_access_token(user.id)
    refresh_token = security.create_refresh_token(user.id)
    expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserRead.model_validate(user)
    )

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_in: UserRegister,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Register a new user with email, password, and optional name/profile.
    Returns standard JSON token response with access and refresh tokens.
    """
    # 1. Check if email already registered
    res_email = await db.execute(select(User).where(User.email == user_in.email))
    if res_email.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Email is already registered"
        )

    # 2. Check phone if provided
    phone_cleaned = user_in.phone.strip() if user_in.phone else None
    if phone_cleaned:
        res_phone = await db.execute(select(User).where(User.phone == phone_cleaned))
        if res_phone.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="Phone number is already registered"
            )

    # 3. Determine burner handle
    handle = user_in.burner_handle
    if not handle and user_in.profile:
        handle = user_in.profile.burner_handle
    if not handle:
        handle = f"student_{uuid.uuid4().hex[:6]}"
    handle = handle.strip()

    res_handle = await db.execute(select(PublicProfile).where(PublicProfile.burner_handle == handle))
    if res_handle.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Burner handle already taken. Please choose another."
        )

    # 4. Hash password with Passlib Bcrypt
    hashed_password = security.get_password_hash(user_in.password)

    # 5. Determine display name
    name = user_in.name
    if not name:
        name = handle or user_in.email.split("@")[0].capitalize()

    # 6. Create User & PublicProfile
    user = User(
        email=user_in.email,
        hashed_password=hashed_password,
        name=name,
        phone=phone_cleaned,
    )
    profile = PublicProfile(
        burner_handle=handle,
        avatar_slug=user_in.profile.avatar_slug if user_in.profile else None,
        user=user
    )

    db.add(user)
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)
    user.profile = profile

    return build_token_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Authenticate user using email and password.
    Supports both standard JSON payload (`{"email": "...", "password": "..."}`)
    and form data (`username`/`email`, `password`).
    """
    email: Optional[str] = None
    password: Optional[str] = None

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            email = body.get("email") or body.get("username")
            password = body.get("password")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload"
            )
    else:
        form = await request.form()
        email = form.get("email") or form.get("username")
        password = form.get("password")

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password are required"
        )

    # Query user with profile loaded
    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.email == email.strip())
    )
    user = result.scalar_one_or_none()

    if not user or not security.verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive"
        )

    return build_token_response(user)


@router.post("/login/access-token", response_model=TokenResponse)
async def login_access_token_form(
    db: AsyncSession = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    """
    OAuth2 standard password request form endpoint (email sent in 'username' field).
    Provides compatibility for Swagger UI and form-based clients.
    """
    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.email == form_data.username.strip())
    )
    user = result.scalar_one_or_none()

    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive"
        )

    return build_token_response(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Renew expired access token using a valid refresh token.
    Accepts JSON body: `{"refresh_token": "..."}` or Authorization Bearer header.
    """
    token_str: Optional[str] = None

    # Check JSON body first
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            token_str = body.get("refresh_token")
    except Exception:
        pass

    # Fallback to Authorization header if not in body
    if not token_str:
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if auth_header:
            parts = auth_header.strip().split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token_str = parts[1]

    if not token_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="refresh_token is required in request body or Authorization header"
        )

    try:
        payload = security.decode_token(token_str)
        user_id_str = payload.get("sub")
        token_type = payload.get("type")

        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload: missing subject",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if token_type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type: refresh token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id = uuid.UUID(user_id_str)

    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch user from DB
    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account no longer exists"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive"
        )

    return build_token_response(user)


@router.get("/me", response_model=UserRead)
async def read_current_user_profile(
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get profile information of the currently authenticated user.
    Requires Bearer token in Authorization header.
    """
    return current_user
