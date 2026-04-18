"""Ewash WhatsApp agent — Meta Cloud API webhook receiver.

Endpoints:
  GET  /health   → liveness probe for Railway
  GET  /webhook  → Meta webhook verification challenge (one-time at setup)
  POST /webhook  → Inbound customer messages (signature-verified)
"""
import logging

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from . import handlers, meta
from .config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ewash")

app = FastAPI(title="Ewash WhatsApp Agent", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(request: Request):
    """Meta calls this once when you register the webhook URL.

    It sends ?hub.mode=subscribe&hub.verify_token=<x>&hub.challenge=<y>.
    If the token matches ours, we must echo the challenge back as plain text.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.meta_verify_token:
        log.info("webhook verified ✓")
        return PlainTextResponse(content=challenge or "", status_code=200)

    log.warning("webhook verification failed mode=%s", mode)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")

    if not meta.verify_signature(raw, sig):
        log.warning("invalid signature, rejecting")
        raise HTTPException(status_code=403, detail="Bad signature")

    payload = await request.json()
    log.debug("inbound payload=%s", payload)

    # Meta nests things: entry[].changes[].value.{messages,contacts,statuses}[]
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", []) or []
            contacts = value.get("contacts", []) or []
            for i, msg in enumerate(messages):
                contact = contacts[i] if i < len(contacts) else None
                try:
                    await handlers.handle_message(msg, contact)
                except Exception:
                    log.exception("handler error msg_id=%s", msg.get("id"))

    # Always 200 fast — Meta retries aggressively on non-200s.
    return Response(status_code=200)
