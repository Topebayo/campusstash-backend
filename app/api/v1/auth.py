import uuid
import secrets
import logging

logger = logging.getLogger(__name__)
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
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
from app.services.sms_service import normalize_phone, send_phone_otp, get_phone_variants
from app.services.email_service import send_email_otp

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
    Generate and dispatch a 6-digit OTP verification code via Termii SMS, WhatsApp, or Resend Email.
    Rate limited to 3 requests per minute per IP.
    """
    now_utc = datetime.now(timezone.utc)
    req_channel = getattr(req, "channel", "sms") or "sms"
    raw_phone = (req.phone or "").strip()
    raw_email = (req.email or "").strip().lower()

    # Detect email channel
    is_email_channel = req_channel == "email" or bool(raw_email) or ("@" in raw_phone)
    if is_email_channel:
        target_email = raw_email or raw_phone.lower()
        if not target_email or "@" not in target_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please provide a valid email address."
            )

        # Check if email is already registered
        existing = await db.execute(select(User).where(User.email == target_email))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This email address is already registered to an existing account. Please log in instead."
            )

        verification_key = f"email:{target_email}"
        recent_query = await db.execute(
            select(PhoneVerification)
            .where(PhoneVerification.phone == verification_key)
            .order_by(PhoneVerification.created_at.desc())
        )
        latest = recent_query.scalars().first()
        if latest and (now_utc - latest.created_at).total_seconds() < 60:
            remaining = int(60 - (now_utc - latest.created_at).total_seconds())
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {remaining} seconds before requesting another code."
            )

        otp_code = f"{secrets.randbelow(900000) + 100000}"
        expires_at = now_utc + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

        pv = PhoneVerification(
            phone=verification_key,
            otp_code=otp_code,
            expires_at=expires_at,
            is_verified=False,
            attempts=0
        )
        db.add(pv)
        await db.commit()

        email_res = await send_email_otp(target_email, otp_code, purpose="verification")
        dev_code = email_res.get("dev_code")
        is_local_dev = settings.ENVIRONMENT.lower() == "development" and bool(getattr(settings, "DEV_BYPASS_OTP", False))
        dev_code_out = dev_code if is_local_dev else None

        return OtpResponse(
            status="success",
            message=f"Verification code delivered to {target_email}. Check your inbox or spam folder!",
            email=target_email,
            dev_code=dev_code_out
        )

    # Phone channel (SMS or WhatsApp)
    normalized = normalize_phone(raw_phone)
    if not normalized or len(normalized) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid Nigerian or international phone number (e.g. 08012345678 or +234...)"
        )

    # 1. Check if phone is already registered and verified by another user (matching all local & international formats)
    phone_variants = get_phone_variants(raw_phone)
    existing = await db.execute(select(User).where(User.phone.in_(phone_variants)))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This phone number is already registered to an existing account. Please log in instead."
        )

    # 2. Anti-spam Rate limit: 60 seconds check
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
    sms_res = await send_phone_otp(normalized, otp_code, channel=req_channel)

    dev_code = sms_res.get("dev_code")
    is_local_dev = settings.ENVIRONMENT.lower() == "development" and bool(getattr(settings, "DEV_BYPASS_OTP", False))
    dev_code_out = dev_code if is_local_dev else None

    actual_channel = sms_res.get("channel", req_channel)
    if str(actual_channel).lower() == "whatsapp":
        msg = f"A 6-digit verification code has been sent to +{normalized} via WhatsApp."
    else:
        s_id = sms_res.get("sender_id")
        tag = f" (via {s_id})" if s_id else ""
        msg = f"A 6-digit verification code has been sent to +{normalized} via SMS{tag}."

    return OtpResponse(
        status="success",
        message=msg,
        phone=normalized,
        dev_code=dev_code_out
    )


@router.post("/phone/verify-otp", response_model=OtpResponse)
@limiter.limit("5/minute")
async def verify_phone_otp(
    request: Request,
    req: VerifyOtpRequest,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Verify the 6-digit OTP code sent to user's phone number or email.
    Rate limited to 5 verification attempts per minute.
    """
    raw_phone = (req.phone or "").strip()
    raw_email = (req.email or "").strip().lower()

    if raw_email or "@" in raw_phone or raw_phone.startswith("email:"):
        target_email = raw_email or raw_phone.replace("email:", "").strip().lower()
        verification_key = f"email:{target_email}"
    else:
        normalized = normalize_phone(raw_phone)
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid phone number."
            )
        verification_key = normalized

    now_utc = datetime.now(timezone.utc)
    query = await db.execute(
        select(PhoneVerification)
        .where(PhoneVerification.phone == verification_key)
        .order_by(PhoneVerification.created_at.desc())
    )
    pv = query.scalars().first()

    if not pv:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No verification code was requested for this identifier."
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
    is_prod = str(getattr(settings, "ENVIRONMENT", "")).lower() == "production"
    allow_dev_bypass = (not is_prod) and bool(settings.DEV_BYPASS_OTP)
    is_valid = (submitted_code == pv.otp_code) or (
        allow_dev_bypass and submitted_code == settings.DEV_BYPASS_OTP
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
        message="Identity verified successfully! You can now complete your registration.",
        phone=verification_key
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

    # 2. Check phone if provided (matching all phone number representations)
    phone_cleaned = user_in.phone.strip() if user_in.phone else None
    if phone_cleaned:
        variants = get_phone_variants(phone_cleaned)
        res_phone = await db.execute(select(User).where(User.phone.in_(variants)))
        if res_phone.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="This phone number is already registered to an existing account. Please log in instead."
            )

    # Account type & Store requirements validation
    acc_type = (user_in.account_type or "individual").strip().lower()
    if acc_type not in ["individual", "store"]:
        acc_type = "individual"

    b_name = user_in.business_name.strip() if user_in.business_name else None
    owner_name = user_in.name.strip() if user_in.name else None

    if acc_type == "store":
        if not b_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Store accounts must provide a real business / store name."
            )
        if not owner_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Store accounts must provide the owner's real name."
            )

    # 3. Determine burner handle / store handle
    handle = user_in.burner_handle
    if not handle and user_in.profile:
        handle = user_in.profile.burner_handle
    if not handle:
        if acc_type == "store" and b_name:
            import re
            clean_b = re.sub(r'[^a-zA-Z0-9_]', '_', b_name.lower().replace("'", ""))
            clean_b = re.sub(r'_+', '_', clean_b).strip('_')[:20]
            handle = clean_b or f"store_{uuid.uuid4().hex[:6]}"
        else:
            handle = f"student_{uuid.uuid4().hex[:6]}"
    handle = handle.strip().lstrip('@')

    res_handle = await db.execute(
        select(PublicProfile).where(func.lower(PublicProfile.burner_handle) == handle.lower())
    )
    if res_handle.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Username / store handle already taken. Please choose another."
        )

    # 4. Hash password with Passlib Bcrypt (Validates strength: min 8, max 72 chars)
    hashed_password = security.get_password_hash(user_in.password)

    # 5. Determine display name
    name = owner_name
    if not name:
        name = handle or user_in.email.split("@")[0].capitalize()

    # 6. Create User & PublicProfile
    user = User(
        email=user_in.email,
        hashed_password=hashed_password,
        name=name,
        phone=phone_cleaned,
        is_phone_verified=True,
        campus_id=user_in.campus_id,
        account_type=acc_type,
        business_name=b_name,
        business_category=user_in.business_category.strip() if user_in.business_category else None,
        business_description=user_in.business_description.strip() if user_in.business_description else None,
        business_address=user_in.business_address.strip() if user_in.business_address else None,
        business_whatsapp=user_in.business_whatsapp.strip() if user_in.business_whatsapp else (phone_cleaned if acc_type == "store" else None),
        business_instagram=user_in.business_instagram.strip().lstrip('@') if user_in.business_instagram else None,
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
    Authenticate user using email or phone number and password.
    Rate limited to 5 attempts per minute per IP.
    """
    identifier: Optional[str] = None
    password: Optional[str] = None

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            identifier = body.get("email") or body.get("username") or body.get("phone")
            password = body.get("password")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload"
            )
    else:
        form = await request.form()
        identifier = form.get("email") or form.get("username") or form.get("phone")
        password = form.get("password")

    if not identifier or not str(identifier).strip() or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email or phone number and password are required"
        )

    clean_id = str(identifier).strip()
    phone_variants = get_phone_variants(clean_id)

    conditions = [func.lower(User.email) == clean_id.lower()]
    if phone_variants:
        conditions.append(User.phone.in_(phone_variants))

    # Match user by email (case-insensitive) OR by phone number (all representations)
    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(or_(*conditions))
    )
    user = result.scalar_one_or_none()

    if not user or not security.verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email/phone number or password"
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
    OAuth2 standard password request form endpoint (email or phone sent in 'username' field).
    Rate limited to 5 attempts per minute per IP.
    """
    clean_id = form_data.username.strip()
    phone_variants = get_phone_variants(clean_id)

    conditions = [func.lower(User.email) == clean_id.lower()]
    if phone_variants:
        conditions.append(User.phone.in_(phone_variants))

    # Match user by email (case-insensitive) OR by phone number (all representations)
    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(or_(*conditions))
    )
    user = result.scalar_one_or_none()

    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email/phone number or password"
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
    Dispatch password reset OTP code to registered user via SMS, WhatsApp, or Resend Email.
    Rate limited to 3 requests per minute per IP.
    """
    now_utc = datetime.now(timezone.utc)
    raw_phone = (req.phone or "").strip()
    raw_email = (req.email or "").strip().lower()
    req_channel = getattr(req, "channel", "sms") or "sms"

    is_email = (req_channel == "email") or bool(raw_email) or ("@" in raw_phone)
    if is_email:
        target_email = raw_email or raw_phone.lower()
        if not target_email or "@" not in target_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please provide a valid registered email address."
            )

        user_query = await db.execute(select(User).where(User.email == target_email))
        user = user_query.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No CampusStash account found with this email address. Please check your spelling or sign up."
            )

        reset_key = f"reset:{target_email}"
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

        email_res = await send_email_otp(target_email, otp_code, purpose="reset")
        dev_code = email_res.get("dev_code")
        is_local_dev = settings.ENVIRONMENT.lower() == "development" and bool(getattr(settings, "DEV_BYPASS_OTP", False))
        dev_code_out = dev_code if is_local_dev else None

        return OtpResponse(
            status="success",
            message=f"Password reset code has been sent to your email ({target_email}). Check your inbox or spam folder!",
            email=target_email,
            dev_code=dev_code_out
        )

    # Phone recovery route (SMS or WhatsApp)
    normalized = normalize_phone(raw_phone)
    if not normalized or len(normalized) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid Nigerian phone number."
        )

    # 1. Verify that an account exists with this phone number (matching all storage formats)
    variants = get_phone_variants(raw_phone)
    user_query = await db.execute(select(User).where(User.phone.in_(variants)))
    user = user_query.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No CampusStash account found with this phone number. Please check the number or sign up."
        )

    # 2. Anti-spam Rate limit: 60 seconds check
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
    sms_res = await send_phone_otp(normalized, otp_code, channel=req_channel)

    dev_code = sms_res.get("dev_code")
    is_local_dev = settings.ENVIRONMENT.lower() == "development" and bool(getattr(settings, "DEV_BYPASS_OTP", False))
    dev_code_out = dev_code if is_local_dev else None

    actual_channel = sms_res.get("channel", req_channel)
    if str(actual_channel).lower() == "whatsapp":
        msg = f"Password reset code has been sent to +{normalized} via WhatsApp."
    else:
        s_id = sms_res.get("sender_id")
        tag = f" (via {s_id})" if s_id else ""
        msg = f"Password reset code has been sent to +{normalized} via SMS{tag}."

    return OtpResponse(
        status="success",
        message=msg,
        phone=normalized,
        dev_code=dev_code_out
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
    raw_phone = (req.phone or "").strip()
    raw_email = (req.email or "").strip().lower()

    is_email = bool(raw_email) or ("@" in raw_phone)
    if is_email:
        target_email = raw_email or raw_phone.lower()
        reset_key = f"reset:{target_email}"
        identifier_display = target_email
    else:
        normalized = normalize_phone(raw_phone)
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid phone number."
            )
        reset_key = f"reset:{normalized}"
        identifier_display = normalized

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

    submitted_code = req.otp_code.strip()
    is_prod = str(getattr(settings, "ENVIRONMENT", "")).lower() == "production"
    allow_dev_bypass = (not is_prod) and bool(settings.DEV_BYPASS_OTP)
    is_valid = (submitted_code == pv.otp_code) or (
        allow_dev_bypass and submitted_code == settings.DEV_BYPASS_OTP
    )

    if not is_valid:
        pv.attempts += 1
        await db.commit()
        remaining = max(0, 5 - pv.attempts)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid verification code. {remaining} attempts remaining."
        )

    # 1. Fetch user to update
    if is_email:
        user_query = await db.execute(
            select(User).options(selectinload(User.profile)).where(User.email == target_email)
        )
    else:
        variants = get_phone_variants(raw_phone)
        user_query = await db.execute(
            select(User).options(selectinload(User.profile)).where(User.phone.in_(variants))
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

    logger.info(f"Password reset successfully for user {user.id} ({identifier_display})")
    return build_token_response(user)
