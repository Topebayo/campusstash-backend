import uuid
import secrets
from datetime import datetime, timezone, timedelta
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
from app.core.limiter import limiter
from app.models.user import User, PublicProfile
from app.models.phone_verification import PhoneVerification
from app.schemas.token import TokenResponse, RefreshTokenRequest
from app.schemas.user import UserRegister, UserRead, LoginRequest, SendOtpRequest, VerifyOtpRequest, OtpResponse, ForgotPasswordSendOtpRequest, ForgotPasswordResetRequest
from app.services.sms_service import normalize_phone, send_phone_otp

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


@router.post("/phone/send-otp", response_model=OtpResponse)
@limiter.limit("3/minute")
async def send_phone_verification_otp(
    request: Request,
    req: SendOtpRequest,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Generate and dispatch a 6-digit OTP verification code via Termii SMS.
    Normalizes Nigerian phone numbers (080... -> 23480...).
    Rate limited to 3 requests per minute per IP.
    """
    normalized = normalize_phone(req.phone)
    if not normalized or len(normalized) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid Nigerian or international phone number (e.g. 08012345678 or +234...)"
        )

    # 1. Check if phone is already registered and verified by another user
    existing = await db.execute(select(User).where(User.phone == normalized))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This phone number is already registered to an existing account. Please log in instead."
        )

    # 2. Anti-spam Rate limit: 60 seconds check
    now_utc = datetime.now(timezone.utc)
    recent_query = await db.execute(
        select(PhoneVerification)
        .where(PhoneVerification.phone == normalized)
        .order_by(PhoneVerification.created_at.desc())
    )
    latest = recent_query.scalars().first()
    if latest and (now_utc - latest.created_at).total_seconds() < 60:
        remaining = int(60 - (now_utc - latest.created_at).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining} seconds before requesting another verification code."
        )

    # 3. Generate cryptographic 6-digit OTP
    otp_code = f"{secrets.randbelow(900000) + 100000}"
    expires_at = now_utc + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

    pv = PhoneVerification(
        phone=normalized,
        otp_code=otp_code,
        expires_at=expires_at,
        is_verified=False,
        attempts=0
    )
    db.add(pv)
    await db.commit()

    # 4. Dispatch OTP via Termii (SMS or WhatsApp)
    req_channel = getattr(req, "channel", "sms") or "sms"
    sms_res = await send_phone_otp(normalized, otp_code, channel=req_channel)

    dev_code = sms_res.get("dev_code")
    if not dev_code and (not settings.TERMII_API_KEY or not settings.TERMII_API_KEY.strip()):
        dev_code = otp_code

    ch_label = "WhatsApp" if str(req_channel).lower() == "whatsapp" else "SMS"
    return OtpResponse(
        status="success",
        message=f"A 6-digit verification code has been sent to +{normalized} via {ch_label}.",
        phone=normalized,
        dev_code=dev_code
    )


@router.post("/phone/verify-otp", response_model=OtpResponse)
@limiter.limit("5/minute")
async def verify_phone_otp(
    request: Request,
    req: VerifyOtpRequest,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Verify the 6-digit OTP code sent to user's phone number.
    Rate limited to 5 verification attempts per minute.
    """
    normalized = normalize_phone(req.phone)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number."
        )

    now_utc = datetime.now(timezone.utc)
    query = await db.execute(
        select(PhoneVerification)
        .where(PhoneVerification.phone == normalized)
        .order_by(PhoneVerification.created_at.desc())
    )
    pv = query.scalars().first()

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
            detail="Too many incorrect attempts. Please request a new verification code."
        )

    submitted_code = req.otp_code.strip()
    is_valid = (submitted_code == pv.otp_code) or (
        bool(settings.DEV_BYPASS_OTP) and submitted_code == settings.DEV_BYPASS_OTP
    )

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
    await db.commit()

    return OtpResponse(
        status="success",
        message="Phone number verified successfully!",
        phone=normalized
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    user_in: UserRegister,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Register a new user with email, password, and optional name/profile.
    Rate limited to 5 registrations per minute per IP.
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

    # 4. Hash password with Passlib Bcrypt (Validates strength: min 8, max 72 chars)
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
        is_phone_verified=True,
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
@limiter.limit("5/minute")
async def login(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Authenticate user using email and password.
    Rate limited to 5 attempts per minute per IP.
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
@limiter.limit("5/minute")
async def login_access_token_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    """
    OAuth2 standard password request form endpoint (email sent in 'username' field).
    Rate limited to 5 attempts per minute per IP.
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
@limiter.limit("15/minute")
async def refresh_token(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Renew expired access token using a valid refresh token.
    Rate limited to 15 refreshes per minute per IP.
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


# ─── Password Recovery Endpoints ─────────────────────────────────────────────

@router.post("/forgot-password/send-otp", response_model=OtpResponse)
@limiter.limit("3/minute")
async def forgot_password_send_otp(
    request: Request,
    req: ForgotPasswordSendOtpRequest,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Dispatch password reset OTP code to registered user's phone via SMS or WhatsApp.
    Rate limited to 3 requests per minute per IP.
    """
    normalized = normalize_phone(req.phone)
    if not normalized or len(normalized) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid Nigerian phone number."
        )

    # 1. Verify that an account exists with this phone number
    user_query = await db.execute(select(User).where(User.phone == normalized))
    user = user_query.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No CampusStash account found with this phone number. Please check the number or sign up."
        )

    # 2. Anti-spam Rate limit: 60 seconds check
    now_utc = datetime.now(timezone.utc)
    reset_key = f"reset:{normalized}"
    recent_query = await db.execute(
        select(PhoneVerification)
        .where(PhoneVerification.phone == reset_key)
        .order_by(PhoneVerification.created_at.desc())
    )
    latest = recent_query.scalars().first()
    if latest and (now_utc - latest.created_at).total_seconds() < 60:
        remaining = int(60 - (now_utc - latest.created_at).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining} seconds before requesting another code."
        )

    # 3. Generate cryptographic 6-digit OTP
    otp_code = f"{secrets.randbelow(900000) + 100000}"
    expires_at = now_utc + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

    pv = PhoneVerification(
        phone=reset_key,
        otp_code=otp_code,
        expires_at=expires_at,
        is_verified=False,
        attempts=0
    )
    db.add(pv)
    await db.commit()

    # 4. Dispatch OTP via Termii (SMS or WhatsApp)
    req_channel = getattr(req, "channel", "sms") or "sms"
    sms_res = await send_phone_otp(normalized, otp_code, channel=req_channel)

    dev_code = sms_res.get("dev_code")
    if not dev_code and (not settings.TERMII_API_KEY or not settings.TERMII_API_KEY.strip()):
        dev_code = otp_code

    ch_label = "WhatsApp" if str(req_channel).lower() == "whatsapp" else "SMS"
    return OtpResponse(
        status="success",
        message=f"Password reset code has been sent to +{normalized} via {ch_label}.",
        phone=normalized,
        dev_code=dev_code
    )


@router.post("/forgot-password/reset", response_model=TokenResponse)
@limiter.limit("5/minute")
async def forgot_password_reset(
    request: Request,
    req: ForgotPasswordResetRequest,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Validate recovery OTP code and update user password.
    Returns new access token so user is logged in immediately.
    """
    normalized = normalize_phone(req.phone)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number."
        )

    reset_key = f"reset:{normalized}"
    now_utc = datetime.now(timezone.utc)
    query = await db.execute(
        select(PhoneVerification)
        .where(PhoneVerification.phone == reset_key)
        .order_by(PhoneVerification.created_at.desc())
    )
    pv = query.scalars().first()

    if not pv:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active password reset request found. Please request a new code."
        )

    if now_utc > pv.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset code has expired. Please request a new code."
        )

    if pv.attempts >= 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Maximum verification attempts exceeded. Please request a new code."
        )

    if pv.otp_code != req.otp_code.strip():
        pv.attempts += 1
        await db.commit()
        remaining = 5 - pv.attempts
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid verification code. {remaining} attempts remaining."
        )

    # 1. Fetch user to update
    user_query = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.phone == normalized)
    )
    user = user_query.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account could not be found."
        )

    # 2. Update password hash
    user.hashed_password = security.get_password_hash(req.new_password)
    pv.is_verified = True
    await db.commit()
    await db.refresh(user)

    logger.info(f"Password reset successfully for user {user.id} ({normalized})")
    return build_token_response(user)
