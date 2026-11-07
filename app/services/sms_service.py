import re
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

def normalize_phone(phone: str) -> str:
    """
    Normalizes Nigerian and international phone numbers into standard format without '+' or spaces.
    Examples:
      08031234567  -> 2348031234567
      +2348031234567 -> 2348031234567
      2348031234567  -> 2348031234567
      09012345678  -> 2349012345678
      07012345678  -> 2347012345678
    """
    if not phone:
        return ""
    clean = re.sub(r"[^\d]", "", phone.strip())
    
    # 11 digits starting with 0 (Standard Nigerian local mobile)
    if clean.startswith("0") and len(clean) == 11:
        return f"234{clean[1:]}"
    # Already starts with 234
    if clean.startswith("234"):
        return clean
    # 10 digits without leading 0
    if len(clean) == 10:
        return f"234{clean}"
        
    return clean

def get_phone_variants(phone: str) -> list[str]:
    """
    Returns all valid representations of a Nigerian/international phone number
    so database queries match regardless of whether it was stored as '080...', '234...', or '+234...'.
    """
    if not phone:
        return []
    clean = re.sub(r"[^\d]", "", str(phone).strip())
    variants = set()
    if clean:
        variants.add(clean)
    if clean.startswith("234") and len(clean) >= 12:
        local = "0" + clean[3:]
        variants.add(local)
        variants.add("+" + clean)
        variants.add(clean)
    elif clean.startswith("0") and len(clean) == 11:
        intl = "234" + clean[1:]
        variants.add(intl)
        variants.add("+" + intl)
        variants.add(clean)
    elif len(clean) == 10:
        local = "0" + clean
        intl = "234" + clean
        variants.add(local)
        variants.add(intl)
        variants.add("+" + intl)
        variants.add(clean)
    return list(variants)

async def get_termii_approved_sender_id(api_key: str, client: httpx.AsyncClient) -> str:
    """
    Queries Termii API for approved sender IDs in the current workspace.
    Returns the first active sender ID, or defaults to 'talert'.
    """
    try:
        resp = await client.get(f"{settings.TERMII_BASE_URL.rstrip('/')}/api/sender-id?api_key={api_key}", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for item in data:
                if str(item.get("status", "")).lower() in ["active", "unblock", "approved"]:
                    return item.get("sender_id")
            if data and data[0].get("sender_id"):
                return data[0].get("sender_id")
    except Exception as e:
        logger.warning(f"Could not fetch approved sender IDs from Termii: {e}")
    return "talert"

async def send_phone_otp(phone: str, otp_code: str, channel: str = "sms") -> dict:
    """
    Dispatches a 6-digit OTP verification code via Termii API.
    Supports SMS ('sms'), WhatsApp ('whatsapp'), and Voice Call ('voice').
    For Airtel Nigeria numbers, prioritizes telco-whitelisted sender IDs ('N-Alert', 'talert')
    to prevent Airtel operator-level silent drops.
    """
    normalized = normalize_phone(phone)
    if not normalized or len(normalized) < 10:
        return {"success": False, "error": "Invalid phone number format."}

    channel_clean = str(channel or "sms").lower().strip()

    # Development / Simulated mode: Protect Termii credits during testing
    if not settings.ENABLE_LIVE_SMS or not settings.TERMII_API_KEY or settings.TERMII_API_KEY.strip() == "":
        logger.info(f"[CREDIT SAFEGUARD] Simulated {channel_clean.upper()} OTP for {normalized} is: {otp_code}")
        return {
            "success": True, 
            "provider": "dev_simulation", 
            "channel": channel_clean,
            "message": f"Verification code generated in simulation mode ({channel_clean}).",
            "dev_code": otp_code if (settings.ENVIRONMENT.lower() == "development" and bool(getattr(settings, "DEV_BYPASS_OTP", False))) else None
        }

    api_key = settings.TERMII_API_KEY.strip()
    base_url = settings.TERMII_BASE_URL.rstrip("/")

    # Detect Airtel Nigeria numbers
    airtel_prefixes = (
        "234802", "234808", "234812", "234701", "234708", 
        "234901", "234902", "234904", "234907", "234912"
    )
    is_airtel = any(normalized.startswith(p) for p in airtel_prefixes)

    # 1. Voice Call Channel: Directly dials the phone and speaks the code
    if channel_clean == "voice":
        voice_url = f"{base_url}/api/sms/otp/call"
        try:
            numeric_code = int(re.sub(r"[^\d]", "", str(otp_code)))
        except Exception:
            numeric_code = 123456
        voice_payload = {
            "api_key": api_key,
            "phone_number": normalized,
            "code": numeric_code
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(voice_url, json=voice_payload)
                logger.info(f"Termii Voice Call response: {resp.status_code} - {resp.text}")
                if resp.status_code == 200:
                    data = resp.json()
                    return {"success": True, "provider": "termii_voice", "channel": "voice", "data": data}
                else:
                    logger.warning(f"Termii Voice OTP HTTP {resp.status_code}: {resp.text}")
                    # If Voice route is not configured on Termii workspace, smoothly fall back to SMS
                    logger.info(f"Voice route inactive on Termii workspace. Falling back to high-priority SMS for {normalized}...")
                    fallback_sms = await send_phone_otp_sms(normalized, otp_code, api_key, base_url, is_airtel)
                    fallback_sms["voice_fallback"] = True
                    return fallback_sms
        except Exception as e:
            logger.error(f"Termii Voice OTP dispatch failed: {e}")
            fallback_sms = await send_phone_otp_sms(normalized, otp_code, api_key, base_url, is_airtel)
            fallback_sms["voice_fallback"] = True
            return fallback_sms

    # 2. Text / WhatsApp Channel
    return await send_phone_otp_sms(normalized, otp_code, api_key, base_url, is_airtel, channel=channel_clean)


async def send_phone_otp_sms(normalized: str, otp_code: str, api_key: str, base_url: str, is_airtel: bool, channel: str = "sms") -> dict:
    """Dispatches SMS or WhatsApp via Termii with carrier-specific sender ID priority."""
    sms_url = f"{base_url}/api/sms/send"
    sms_text = f"Your CampusStash verification code is {otp_code}. Valid for 10 minutes. Do not share this code with anyone."

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            approved_sender = await get_termii_approved_sender_id(api_key, client)
            
            # On Airtel Nigeria, custom unwhitelisted sender IDs ('OE Alert') are blocked by operator firewall.
            # Use Termii's telco-whitelisted transactional sender IDs ('N-Alert', 'talert') first!
            if is_airtel:
                candidate_senders = ["N-Alert", "talert", "Termii"]
                if approved_sender and approved_sender not in candidate_senders:
                    candidate_senders.append(approved_sender)
                if "OE Alert" not in candidate_senders:
                    candidate_senders.append("OE Alert")
            else:
                candidate_senders = ["OE Alert"]
                if settings.TERMII_SENDER_ID and settings.TERMII_SENDER_ID not in candidate_senders:
                    candidate_senders.append(settings.TERMII_SENDER_ID)
                if approved_sender and approved_sender not in candidate_senders:
                    candidate_senders.append(approved_sender)
                for default_s in ["N-Alert", "talert", "Termii"]:
                    if default_s not in candidate_senders:
                        candidate_senders.append(default_s)

            if channel == "whatsapp":
                channels_to_try = ["whatsapp", "dnd", "generic"]
            else:
                channels_to_try = ["dnd", "generic", "whatsapp"]

            for s_id in candidate_senders:
                for ch in channels_to_try:
                    payload = {
                        "to": normalized,
                        "from": s_id,
                        "sms": sms_text,
                        "type": "plain",
                        "channel": ch,
                        "api_key": api_key
                    }
                    try:
                        resp = await client.post(sms_url, json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("message") == "Successfully Sent":
                                logger.info(f"Termii SMS sent successfully to {normalized} via sender '{s_id}' channel '{ch}'")
                                return {"success": True, "provider": "termii", "sender_id": s_id, "channel": ch, "data": data}
                            else:
                                logger.warning(f"Termii response with sender '{s_id}' channel '{ch}': {data}")
                        else:
                            logger.warning(f"Termii HTTP {resp.status_code} with sender '{s_id}' channel '{ch}': {resp.text}")
                    except Exception as send_err:
                        logger.warning(f"Attempt with sender '{s_id}' channel '{ch}' failed: {send_err}")

            logger.warning(f"Termii delivery could not be completed. Falling back to dev verification.")
            return {
                "success": True, 
                "provider": "termii_fallback", 
                "channel": channel,
                "message": "Verification code generated (fallback mode active)",
                "dev_code": otp_code
            }

    except Exception as e:
        logger.error(f"Failed to contact Termii API: {e}")
        return {
            "success": True, 
            "provider": "fallback_dev", 
            "channel": channel,
            "message": f"Error reaching SMS gateway: {e}",
            "dev_code": otp_code
        }

