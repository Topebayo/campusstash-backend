import logging
import httpx
from typing import Optional, List, Dict, Any
from app.core.config import settings
from app.services.sms_service import normalize_phone

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"

async def send_whatsapp_message(to_phone: str, text: str) -> Dict[str, Any]:
    """
    Sends a direct WhatsApp text message using Meta WhatsApp Cloud API.
    """
    token = settings.WHATSAPP_ACCESS_TOKEN
    phone_id = settings.WHATSAPP_PHONE_NUMBER_ID

    if not token or not phone_id:
        logger.warning("WhatsApp Cloud API credentials not configured. Message simulated in logs.")
        logger.info(f"[SIMULATED WHATSAPP TO {to_phone}]: {text}")
        return {
            "success": False,
            "simulated": True,
            "message": "WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID is not configured."
        }

    clean_phone = normalize_phone(to_phone)
    if not clean_phone:
        logger.error(f"Cannot send WhatsApp message: invalid recipient phone {to_phone}")
        return {"success": False, "message": "Invalid recipient phone number."}

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_phone,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()
            if resp.status_code in [200, 201]:
                logger.info(f"WhatsApp message successfully sent to {clean_phone} via Cloud API.")
                return {"success": True, "data": data}
            else:
                err_msg = data.get("error", {}).get("message", "Unknown Meta API error")
                logger.error(f"Meta WhatsApp Cloud API error ({resp.status_code}): {err_msg}")
                return {"success": False, "error": data, "message": err_msg}
    except Exception as e:
        logger.error(f"Failed to deliver WhatsApp message to {clean_phone}: {e}")
        return {"success": False, "error": str(e), "message": "Failed to connect to Meta WhatsApp API."}


def extract_incoming_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extracts incoming text messages from a Meta WhatsApp Cloud API webhook event.
    """
    results = []
    try:
        entries = payload.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
                messages = value.get("messages", [])
                for msg in messages:
                    sender = msg.get("from")
                    sender_name = contacts.get(sender, "User")
                    msg_type = msg.get("type")
                    text_body = ""
                    if msg_type == "text":
                        text_body = msg.get("text", {}).get("body", "").strip()
                    elif msg_type == "button":
                        text_body = msg.get("button", {}).get("text", "").strip()
                    elif msg_type == "interactive":
                        interactive = msg.get("interactive", {})
                        text_body = interactive.get("button_reply", {}).get("title") or interactive.get("list_reply", {}).get("title", "")

                    if sender and text_body:
                        results.append({
                            "sender": sender,
                            "sender_name": sender_name,
                            "message_id": msg.get("id"),
                            "text": text_body,
                            "timestamp": msg.get("timestamp")
                        })
    except Exception as e:
        logger.warning(f"Error parsing Meta WhatsApp webhook payload: {e}")
    return results
