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
    Dispatches a 6-digit OTP verification SMS via Termii API.
    Uses approved sender ID or Termii's default trial sender ID 'talert'.
    If Termii rejects (e.g. unapproved sender or insufficient balance), 
    gracefully provides fallback dev_code so signup is never blocked.
    """
    normalized = normalize_phone(phone)
    if not normalized or len(normalized) < 10:
        return {"success": False, "error": "Invalid phone number format."}

    # Development / Simulated mode: Protect Termii credits during testing
    if not settings.ENABLE_LIVE_SMS or not settings.TERMII_API_KEY or settings.TERMII_API_KEY.strip() == "":
        logger.info(f"[CREDIT SAFEGUARD] Simulated OTP for {normalized} is: {otp_code}")
        return {
            "success": True, 
            "provider": "dev_simulation", 
            "message": "Verification code generated in simulation mode (0 Termii credits consumed).",
            "dev_code": otp_code
        }

    url = f"{settings.TERMII_BASE_URL.rstrip('/')}/api/sms/send"
    sms_text = f"Your NEXISFLOW verification code is {otp_code}. Use this code to complete your NEXISFLOW account verification. Valid for 10 minutes. Do not share this code with anyone."
    api_key = settings.TERMII_API_KEY.strip()

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            # 1. Discover active sender ID from account
            approved_sender = await get_termii_approved_sender_id(api_key, client)
            
            # Priority list of sender IDs to try (approved 'OE Alert' via 'dnd' first)
            candidate_senders = ["OE Alert"]
            if settings.TERMII_SENDER_ID and settings.TERMII_SENDER_ID not in candidate_senders:
                candidate_senders.append(settings.TERMII_SENDER_ID)
            if approved_sender and approved_sender not in candidate_senders:
                candidate_senders.append(approved_sender)
            for default_s in ["talert", "Termii", "N-Alert"]:
                if default_s not in candidate_senders:
                    candidate_senders.append(default_s)

            # Determine channels based on preference (WhatsApp vs SMS)
            if str(channel).lower() == "whatsapp":
                channels_to_try = ["whatsapp", "dnd", "generic"]
            else:
                channels_to_try = ["dnd", "generic", "whatsapp"]
            for s_id in candidate_senders:
                for channel in channels_to_try:
                    payload = {
                        "to": normalized,
                        "from": s_id,
                        "sms": sms_text,
                        "type": "plain",
                        "channel": channel,
                        "api_key": api_key
                    }
                    try:
                        resp = await client.post(url, json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("message") == "Successfully Sent":
                                logger.info(f"Termii SMS sent successfully via sender '{s_id}' channel '{channel}'")
                                return {"success": True, "provider": "termii", "data": data}
                            else:
                                logger.warning(f"Termii response with sender '{s_id}' channel '{channel}': {data}")
                        else:
                            logger.warning(f"Termii HTTP {resp.status_code} with sender '{s_id}' channel '{channel}': {resp.text}")
                    except Exception as send_err:
                        logger.warning(f"Attempt with sender '{s_id}' failed: {send_err}")

            # If all live Termii attempts fail (e.g. sender ID not approved or account awaiting review)
            logger.warning(f"Termii delivery could not be completed. Falling back to dev verification.")
            return {
                "success": True, 
                "provider": "termii_fallback", 
                "message": "Verification code generated (fallback mode active)",
                "dev_code": otp_code
            }

    except Exception as e:
        logger.error(f"Failed to contact Termii API: {e}")
        return {
            "success": True, 
            "provider": "fallback_dev", 
            "message": f"Error reaching SMS gateway: {e}",
            "dev_code": otp_code
        }
