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

async def send_phone_otp(phone: str, otp_code: str) -> dict:
    """
    Dispatches a 6-digit OTP verification SMS via Termii API.
    If settings.TERMII_API_KEY is not configured, logs code for development testing.
    """
    normalized = normalize_phone(phone)
    if not normalized or len(normalized) < 10:
        return {"success": False, "error": "Invalid phone number format."}

    # Development / Simulated mode
    if not settings.TERMII_API_KEY or settings.TERMII_API_KEY.strip() == "":
        print(f"\n[DEV OTP] Termii API Key not configured. Simulated OTP for {normalized} is: {otp_code}\n")
        logger.info(f"[DEV OTP] Simulated OTP for {normalized} is: {otp_code}")
        return {
            "success": True, 
            "provider": "dev_simulation", 
            "message": "Simulated OTP logged (Dev mode)",
            "dev_code": otp_code
        }

    # Production Termii API Dispatch
    url = f"{settings.TERMII_BASE_URL.rstrip('/')}/api/sms/send"
    payload = {
        "to": normalized,
        "from": settings.TERMII_SENDER_ID,
        "sms": f"Your CampusStash verification code is {otp_code}. Valid for {settings.OTP_EXPIRE_MINUTES} minutes. Do not share this code with anyone.",
        "type": "plain",
        "channel": settings.TERMII_CHANNEL or "generic",
        "api_key": settings.TERMII_API_KEY
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(url, json=payload)
            data = resp.json() if resp.status_code == 200 else {}
            
            # If generic channel fails or DND needed, Termii returns message response
            if resp.status_code == 200 and data.get("message") == "Successfully Sent":
                return {"success": True, "provider": "termii", "data": data}
            else:
                logger.warning(f"Termii send returned non-200: {resp.status_code} - {resp.text}")
                # Fallback to dnd channel if generic failed
                if payload.get("channel") != "dnd":
                    payload["channel"] = "dnd"
                    resp2 = await client.post(url, json=payload)
                    if resp2.status_code == 200:
                        return {"success": True, "provider": "termii_dnd", "data": resp2.json()}

                return {"success": False, "error": resp.text, "status_code": resp.status_code}
    except Exception as e:
        logger.error(f"Failed to contact Termii API: {e}")
        # Ensure user can still test in fallback
        return {"success": False, "error": str(e), "dev_code": otp_code}
