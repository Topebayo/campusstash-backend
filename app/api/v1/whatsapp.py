import logging
import re
import secrets
from typing import Any, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.api.deps import get_db
from app.core import security
from app.core.config import settings
from app.models.user import User, PublicProfile
from app.models.phone_verification import PhoneVerification
from app.models.marketplace import Listing
from app.services.sms_service import normalize_phone, get_phone_variants
from app.services.whatsapp_service import send_whatsapp_message, extract_incoming_messages

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/webhook")
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """
    Meta WhatsApp Cloud API Webhook Verification Endpoint.
    Echoes back hub.challenge when hub.verify_token matches WHATSAPP_VERIFY_TOKEN.
    """
    expected_token = settings.WHATSAPP_VERIFY_TOKEN
    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        logger.info("Meta WhatsApp Webhook successfully verified!")
        return Response(content=hub_challenge or "", media_type="text/plain", status_code=200)

    logger.warning(f"Meta WhatsApp Webhook verification failed. Received: {hub_verify_token}")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch"
    )


@router.post("/webhook")
async def handle_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Receives incoming WhatsApp messages from Meta Cloud API.
    Executes on-the-go admin commands (student provisioning, telco bypass, password reset, stats).
    """
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ok"}

    incoming = extract_incoming_messages(payload)
    if not incoming:
        return {"status": "ok"}

    for item in incoming:
        sender_raw = item.get("sender", "")
        sender_phone = normalize_phone(sender_raw)
        text = item.get("text", "").strip()
        sender_name = item.get("sender_name", "Admin")

        if not text:
            continue

        logger.info(f"Incoming WhatsApp message from {sender_phone} ({sender_name}): {text}")

        # Check authorization: sender phone in ADMIN_WHATSAPP_NUMBERS or command contains admin PIN
        admin_numbers = [
            normalize_phone(p) for p in settings.ADMIN_WHATSAPP_NUMBERS.split(",") if p.strip()
        ]
        is_admin_phone = sender_phone in admin_numbers

        # Check if message contains explicit PIN override e.g. "pin:7799"
        pin_match = re.search(r"\bpin:(\d+)\b", text, re.IGNORECASE)
        provided_pin = pin_match.group(1) if pin_match else None
        clean_text = re.sub(r"\bpin:\d+\b", "", text, flags=re.IGNORECASE).strip()

        is_authorized = is_admin_phone or (
            provided_pin and provided_pin == getattr(settings, "ADMIN_BYPASS_PIN", "7799")
        )

        reply = await process_whatsapp_command(clean_text, is_authorized, db)
        if reply:
            await send_whatsapp_message(sender_phone, reply)

    return {"status": "ok"}


async def process_whatsapp_command(text: str, is_authorized: bool, db: AsyncSession) -> Optional[str]:
    """
    Parses and executes WhatsApp assistant commands.
    """
    lower = text.lower().strip()

    if lower in ["hi", "hello", "help", "menu", "start", "commands"]:
        if not is_authorized:
            return (
                "👋 Hello! This is the CampusStash Assistant.\n\n"
                "To run admin commands, your WhatsApp number must be authorized, "
                "or you can append your admin PIN (e.g. `pin:7799 help`).\n\n"
                "Visit campusstash.ng for campus marketplace deals!"
            )
        return (
            "🚀 *CampusStash Mobile Admin Desk*\n\n"
            "Here are the emergency commands you can use on WhatsApp:\n\n"
            "⚡ *create <email> <phone> <password> [name]*\n"
            "   -> Creates student account with instant phone verification.\n\n"
            "🔓 *verify <phone_or_email>*\n"
            "   -> Telco bypass: force-verifies a student's phone or email.\n\n"
            "🔑 *code* or *otp*\n"
            "   -> Returns the current emergency master bypass OTP.\n\n"
            "📊 *stats*\n"
            "   -> Live numbers of students, stores, and active listings.\n\n"
            "🔄 *reset <email> <new_password>*\n"
            "   -> Resets student password on the spot."
        )

    if not is_authorized:
        return (
            "🔒 *Unauthorized*\n"
            "Your WhatsApp number is not authorized to execute admin commands. "
            "Please include your admin PIN (e.g. `pin:7799 <command>`)."
        )

    # 1. Master OTP Code Query
    if lower in ["code", "otp", "bypass", "master code", "master otp"]:
        master_otp = getattr(settings, "ADMIN_BYPASS_OTP", "990011")
        return (
            f"🔑 *Emergency Master Bypass OTP: {master_otp}*\n\n"
            f"If Airtel or MTN is delaying SMS, tell the student:\n"
            f"\"Just enter code *{master_otp}* on your screen — it will verify you immediately!\""
        )

    # 2. Live Platform Stats
    if lower in ["stats", "status", "count", "metrics"]:
        u_count = await db.scalar(select(func.count(User.id)))
        verified_count = await db.scalar(select(func.count(User.id)).where(User.is_phone_verified == True))
        store_count = await db.scalar(select(func.count(User.id)).where(User.account_type == "store"))
        l_count = await db.scalar(select(func.count(Listing.id)))
        return (
            "📊 *CampusStash Live Stats*\n\n"
            f"👥 Total Users: {u_count}\n"
            f"✅ Phone Verified: {verified_count}\n"
            f"🏪 Campus Stores: {store_count}\n"
            f"📦 Active Listings: {l_count}\n\n"
            f"⚡ Master OTP: {getattr(settings, 'ADMIN_BYPASS_OTP', '990011')}"
        )

    # 3. Force Verify Student (Telco SMS Bypass)
    if lower.startswith("verify "):
        identifier = text[7:].strip()
        if not identifier:
            return "⚠️ Usage: `verify <phone_or_email>` (e.g. `verify 08012345678`)"

        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        updated_users = 0

        if "@" in identifier:
            clean_email = identifier.lower().strip()
            res = await db.execute(select(User).where(func.lower(User.email) == clean_email))
            user = res.scalar_one_or_none()
            if user:
                user.is_phone_verified = True
                updated_users += 1
            pv_res = await db.execute(
                select(PhoneVerification).where(PhoneVerification.phone == f"email:{clean_email}")
            )
            for pv in pv_res.scalars().all():
                pv.is_verified = True
                pv.verified_at = now_utc
            await db.commit()
        else:
            variants = get_phone_variants(identifier)
            res = await db.execute(select(User).where(User.phone.in_(variants)))
            for user in res.scalars().all():
                user.is_phone_verified = True
                updated_users += 1
            for v in variants:
                pv_res = await db.execute(select(PhoneVerification).where(PhoneVerification.phone == v))
                for pv in pv_res.scalars().all():
                    pv.is_verified = True
                    pv.verified_at = now_utc
            await db.commit()

        return (
            f"✅ *Verification Successful!*\n\n"
            f"Identifier: {identifier}\n"
            f"User account(s) updated: {updated_users}\n"
            f"The student's phone is now verified and they can proceed immediately."
        )

    # 4. Quick Account Registration
    if lower.startswith("create ") or lower.startswith("register "):
        # Syntax: create email phone password [name]
        parts = text.split()
        if len(parts) < 4:
            return (
                "⚠️ *Usage:*\n`create <email> <phone> <password> [name]`\n\n"
                "Example:\n`create student@gmail.com 08012345678 pass123 Adeola Johnson`"
            )

        email = parts[1].strip().lower()
        phone = parts[2].strip()
        password = parts[3].strip()
        name = " ".join(parts[4:]).strip() if len(parts) > 4 else None

        if "@" not in email:
            return f"⚠️ Invalid email address: {email}"

        phone_cleaned = normalize_phone(phone) or phone

        # Check existing user
        res = await db.execute(select(User).options(selectinload(User.profile)).where(func.lower(User.email) == email))
        user = res.scalar_one_or_none()

        if user:
            # Update password and verify
            user.hashed_password = security.get_password_hash(password)
            user.phone = phone_cleaned
            user.is_phone_verified = True
            user.is_active = True
            if name:
                user.name = name
            await db.commit()
            return (
                f"✅ *Existing Account Updated & Verified!*\n\n"
                f"📧 Email: {email}\n"
                f"📱 Phone: {phone_cleaned}\n"
                f"🔑 Password: {password}\n"
                f"Status: Phone Verified ✅\n\n"
                f"Student can log in immediately at campusstash.ng!"
            )

        # Check phone clash
        variants = get_phone_variants(phone_cleaned)
        phone_clash = await db.execute(select(User).where(User.phone.in_(variants)))
        if phone_clash.scalar_one_or_none():
            return f"⚠️ Phone number {phone_cleaned} is already registered to another account."

        base_handle = re.sub(r'[^a-zA-Z0-9]', '', (name or email.split('@')[0]).lower())[:12] or "student"
        handle = f"{base_handle}_{secrets.token_hex(2)}"

        hashed_password = security.get_password_hash(password)
        new_user = User(
            email=email,
            hashed_password=hashed_password,
            name=name or handle.capitalize(),
            phone=phone_cleaned,
            is_phone_verified=True,
            is_active=True,
            account_type="individual"
        )
        profile = PublicProfile(
            burner_handle=handle,
            user=new_user
        )
        db.add(new_user)
        db.add(profile)
        await db.commit()

        return (
            f"🎉 *New Student Account Created!*\n\n"
            f"👤 Name: {name or 'Student'}\n"
            f"📧 Email: {email}\n"
            f"📱 Phone: {phone_cleaned}\n"
            f"🔑 Password: {password}\n"
            f"🏷️ Handle: @{handle}\n"
            f"Status: Phone Verified ✅\n\n"
            f"Student can log in right now at campusstash.ng!"
        )

    # 5. Password Reset
    if lower.startswith("reset "):
        parts = text.split()
        if len(parts) < 3:
            return "⚠️ Usage: `reset <email> <new_password>`"
        email = parts[1].strip().lower()
        new_password = parts[2].strip()

        res = await db.execute(select(User).where(func.lower(User.email) == email))
        user = res.scalar_one_or_none()
        if not user:
            return f"⚠️ No account found with email: {email}"

        user.hashed_password = security.get_password_hash(new_password)
        await db.commit()
        return f"✅ Password for *{email}* has been reset to: `{new_password}`"

    return (
        "❓ Unknown command.\n"
        "Send *help* to see available commands (create, verify, code, stats, reset)."
    )
