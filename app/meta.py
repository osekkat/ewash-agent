"""Meta Cloud API client — signature verification + outbound send."""
import hashlib
import hmac
import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)

GRAPH_API_URL = (
    f"https://graph.facebook.com/v21.0/{settings.meta_phone_number_id}/messages"
)


def verify_signature(payload: bytes, signature_header: str | None) -> bool:
    """Validate Meta's X-Hub-Signature-256 header using the App Secret.

    Header format: 'sha256=<hex digest>'
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = signature_header.removeprefix("sha256=")
    digest = hmac.new(
        settings.meta_app_secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, digest)


async def send_text(to: str, body: str) -> dict:
    """Send a freeform text message.

    Only works inside the 24h session window (the customer must have
    messaged the business first, or within the last 24h). Outside that
    window you need an approved template.
    """
    headers = {
        "Authorization": f"Bearer {settings.meta_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(GRAPH_API_URL, headers=headers, json=payload)
    if r.status_code >= 400:
        log.error("Meta send failed status=%s body=%s", r.status_code, r.text)
    r.raise_for_status()
    return r.json()
