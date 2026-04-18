"""Inbound message handlers — v1 is a plain echo bot."""
import logging

from . import meta

log = logging.getLogger(__name__)


async def handle_message(message: dict, contact: dict | None = None) -> None:
    """Process a single inbound message object from the Meta webhook."""
    msg_type = message.get("type")
    from_number = message.get("from")
    profile_name = (contact or {}).get("profile", {}).get("name", "?")

    if msg_type == "text":
        body = message["text"]["body"]
        log.info("inbound text from=%s (%s) body=%r", from_number, profile_name, body)
        await meta.send_text(from_number, f"You said: {body}")
        return

    # v1 only handles text; politely acknowledge everything else.
    log.info("inbound non-text type=%s from=%s", msg_type, from_number)
    await meta.send_text(
        from_number,
        f"(Echo bot only handles text right now — got a {msg_type})",
    )
