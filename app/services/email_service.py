import logging
import httpx
from typing import Dict, Any
from app.core.config import settings

logger = logging.getLogger("campusstash.email")

RESEND_API_URL = "https://api.resend.com/emails"

def build_otp_html(otp_code: str, purpose: str = "verification") -> str:
    """Generate high-contrast, professional HTML email for OTP delivery."""
    is_reset = purpose == "reset"
    title = "Reset Your Password" if is_reset else "Verify Your Student Account"
    desc = (
        "We received a request to reset your CampusStash account password. Enter this 6-digit code to complete the process:"
        if is_reset else
        "Thank you for joining CampusStash! Enter this 6-digit verification code to activate your anonymous student profile:"
    )

    # Format code with spaces for readability (e.g. 1 2 3 4 5 6)
    spaced_code = " ".join(list(otp_code))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0d1117; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #e6edf3;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #0d1117; padding: 40px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width: 480px; background-color: #161b22; border-radius: 20px; border: 1px solid #30363d; overflow: hidden; box-shadow: 0 12px 36px rgba(0,0,0,0.4);">
          <!-- Header -->
          <tr>
            <td style="padding: 32px 32px 20px 32px; text-align: center; border-bottom: 1px solid #21262d;">
              <div style="display: inline-block; padding: 10px 18px; border-radius: 12px; background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%);">
                <span style="font-size: 20px; font-weight: 800; color: #ffffff; letter-spacing: -0.03em;">CampusStash</span>
              </div>
              <p style="margin: 10px 0 0 0; font-size: 13px; color: #8b949e; font-weight: 500;">
                Verified Nigerian Student Commerce
              </p>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding: 28px 32px;">
              <h1 style="margin: 0 0 12px 0; font-size: 20px; font-weight: 700; color: #ffffff; text-align: center;">
                {title}
              </h1>
              <p style="margin: 0 0 24px 0; font-size: 14px; line-height: 1.5; color: #8b949e; text-align: center;">
                {desc}
              </p>

              <!-- OTP Code Display Card -->
              <div style="background-color: #0d1117; border: 1px solid #3b82f6; border-radius: 16px; padding: 22px; text-align: center; margin-bottom: 24px;">
                <div style="font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: #60a5fa; margin-bottom: 8px;">
                  Your 6-Digit Code
                </div>
                <div style="font-family: 'Courier New', Courier, monospace; font-size: 34px; font-weight: 800; letter-spacing: 0.25em; color: #ffffff;">
                  {spaced_code}
                </div>
              </div>

              <div style="background-color: rgba(59, 130, 246, 0.08); border-left: 3px solid #3b82f6; border-radius: 6px; padding: 12px 14px; margin-bottom: 20px;">
                <p style="margin: 0; font-size: 12px; line-height: 1.5; color: #93c5fd;">
                  â±ï¸ <strong>Expires in 10 minutes.</strong> Never share this code with anyone â€” CampusStash admins will never ask for it.
                </p>
              </div>

              <p style="margin: 0; font-size: 12px; line-height: 1.5; color: #6e7681; text-align: center;">
                If you did not request this verification code, you can safely ignore this email. Your account credentials remain secure.
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding: 20px 32px; background-color: #0d1117; border-top: 1px solid #21262d; text-align: center;">
              <p style="margin: 0; font-size: 11px; color: #484f58;">
                Â© CampusStash â€¢ Student-to-Student Campus Marketplace<br>
                Lead City â€¢ UI â€¢ UNILAG â€¢ OAU â€¢ FUTA
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


async def send_email_otp(to_email: str, otp_code: str, purpose: str = "verification") -> Dict[str, Any]:
    """
    Dispatch an OTP code to a student's email address via Resend REST API.
    Gracefully falls back to simulation mode if no API key is provided.
    """
    clean_email = to_email.strip().lower()
    resend_key = getattr(settings, "RESEND_API_KEY", "") or ""

    # Simulated / dev mode if no key configured
    if not resend_key or resend_key.strip() == "":
        logger.info(f"[SIMULATED EMAIL OTP] to {clean_email} ({purpose}): {otp_code}")
        return {
            "success": True,
            "provider": "simulation",
            "message": f"Simulated email verification code generated for {clean_email}.",
            "dev_code": otp_code
        }

    from_email = getattr(settings, "RESEND_FROM_EMAIL", "") or "CampusStash <onboarding@resend.dev>"
    is_reset = purpose == "reset"
    subject = f"{otp_code} is your CampusStash {'Password Reset' if is_reset else 'Verification'} Code"
    html_content = build_otp_html(otp_code, purpose=purpose)

    payload = {
        "from": from_email,
        "to": [clean_email],
        "subject": subject,
        "html": html_content
    }

    headers = {
        "Authorization": f"Bearer {resend_key.strip()}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(RESEND_API_URL, json=payload, headers=headers)
            
            if resp.status_code in (200, 201):
                data = resp.json()
                logger.info(f"Resend email sent successfully to {clean_email} (ID: {data.get('id')})")
                return {
                    "success": True,
                    "provider": "resend",
                    "message": f"Verification code delivered to {clean_email}.",
                    "id": data.get("id")
                }
            else:
                err_body = resp.text
                logger.error(f"Resend API error ({resp.status_code}): {err_body}")
                return {
                    "success": False,
                    "provider": "resend",
                    "message": f"Resend error: {err_body}",
                    "status_code": resp.status_code
                }
    except Exception as exc:
        logger.exception(f"Unexpected error sending email via Resend to {clean_email}: {exc}")
        return {
            "success": False,
            "provider": "resend",
            "message": f"Network error sending email: {str(exc)}"
        }

