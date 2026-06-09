"""Ewash WhatsApp agent — Meta Cloud API webhook receiver.

Endpoints:
  GET  /health    → liveness probe for Railway
  GET  /webhook   → Meta webhook verification challenge
  POST /webhook   → Inbound customer messages (signature-verified)
"""
import hashlib
import logging
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from . import admin, api as api_module, handlers, meta, reminders
from .config import settings
from .db import session_scope
from .operational_tracking import create_operational_service_record, operational_record_input_from_payload
from .persistence import _configured_engine, mark_abandoned_conversations
from .rate_limit import (
    PerPhoneRateLimitExceeded,
    limiter,
    per_phone_rate_limit_handler as _per_phone_rate_limit_handler,
    rate_limit_exceeded_handler as _rate_limit_exceeded_handler,
)

APP_VERSION = "v0.3.0-alpha17"
STATIC_DIR = Path(__file__).resolve().parent / "static"

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ewash")
api_log = logging.getLogger("ewash.api")


def _hash_log_value(value: str, *, length: int = 12) -> str:
    """Return a stable SHA-256 prefix for non-PII log correlation."""
    if not value:
        return "-"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


class ApiAccessLogMiddleware(BaseHTTPMiddleware):
    """Emit one structured log line for each PWA API request."""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        source_ip = request.client.host if request.client else ""
        phone = getattr(request.state, "phone_normalized", "")
        booking_ref = getattr(request.state, "booking_ref", "")
        error_code = response.headers.get("X-Ewash-Error-Code", "")

        api_log.info(
            "ewash.api endpoint=%s method=%s status=%d duration_ms=%.1f "
            "phone_hash=%s source_ip_hash=%s ref=%s error_code=%s",
            request.url.path,
            request.method,
            response.status_code,
            duration_ms,
            _hash_log_value(phone),
            _hash_log_value(source_ip),
            booking_ref or "-",
            error_code or "-",
        )
        return response


def _configure_access_logging(target_app: FastAPI) -> None:
    """Install structured access logging for /api/* requests."""
    target_app.add_middleware(ApiAccessLogMiddleware)


def _configure_cors(target_app: FastAPI) -> None:
    """Wire browser CORS for the planned /api/v1 PWA surface."""
    if not settings.api_enabled:
        return

    origins = settings.allowed_origins_list()
    if not origins and not settings.allowed_origin_regex:
        log.warning(
            "API is enabled but CORS is not configured. "
            "Browsers will reject PWA requests. "
            "Set ALLOWED_ORIGINS and/or ALLOWED_ORIGIN_REGEX."
        )
    target_app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=settings.allowed_origin_regex or None,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Ewash-Token", "If-None-Match"],
        expose_headers=["ETag"],
        max_age=600,
    )


def _configure_api(target_app: FastAPI) -> None:
    """Mount the PWA API only when the rollout flag is enabled."""
    if settings.api_enabled:
        api_module.install_exception_handlers(target_app)
        target_app.include_router(api_module.router)
        log.info("ewash.api enabled - /api/v1/* mounted")
    else:
        log.warning("ewash.api disabled - /api/v1/* NOT mounted (feature flag off)")


app = FastAPI(title="Ewash WhatsApp Agent", version=APP_VERSION.removeprefix("v"))
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(PerPhoneRateLimitExceeded, _per_phone_rate_limit_handler)
_configure_cors(app)
_configure_access_logging(app)
_configure_api(app)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(admin.router)
app.include_router(reminders.router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": APP_VERSION}


@app.post("/internal/conversations/abandon")
async def abandon_stale_conversations(
    x_internal_cron_secret: str | None = Header(default=None, alias="X-Internal-Cron-Secret"),
):
    """Protected maintenance hook for marking inactive conversation sessions abandoned."""
    if not settings.internal_cron_secret:
        raise HTTPException(status_code=503, detail="Internal cron is not configured")
    if not secrets.compare_digest(x_internal_cron_secret or "", settings.internal_cron_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    count = mark_abandoned_conversations()
    return {"abandoned": count}


@app.post("/internal/operational-tracking/records")
async def ingest_operational_tracking_records(
    request: Request,
    x_internal_cron_secret: str | None = Header(default=None, alias="X-Internal-Cron-Secret"),
):
    """Protected ingestion hook for WhatsApp/Hermes operational service records."""
    if not settings.internal_cron_secret:
        raise HTTPException(status_code=503, detail="Internal cron is not configured")
    if not secrets.compare_digest(x_internal_cron_secret or "", settings.internal_cron_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    engine = _configured_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")

    try:
        payload = await request.json()
        raw_records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(raw_records, list):
            raise ValueError("records must be a list")
        inputs = [operational_record_input_from_payload(item) for item in raw_records]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record_ids: list[int] = []
    with session_scope(engine) as session:
        for data in inputs:
            row = create_operational_service_record(session, data)
            record_ids.append(row.id)
    return {"created": len(record_ids), "record_ids": record_ids}


@app.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(request: Request):
    params = request.query_params
    verify_token = params.get("hub.verify_token") or ""
    if (params.get("hub.mode") == "subscribe"
            and secrets.compare_digest(verify_token, settings.meta_verify_token)):
        log.info("webhook verified OK")
        return PlainTextResponse(content=params.get("hub.challenge") or "", status_code=200)
    log.warning("webhook verification failed mode=%s", params.get("hub.mode"))
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive_webhook(request: Request):
    raw = await request.body()
    if not meta.verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        log.warning("invalid signature, rejecting")
        raise HTTPException(status_code=403, detail="Bad signature")

    payload = await request.json()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", []) or []
            contacts = value.get("contacts", []) or []
            for i, msg in enumerate(messages):
                contact = contacts[i] if i < len(contacts) else None
                try:
                    await handlers.handle_message(msg, contact)
                except meta.MetaSendError as exc:
                    log.exception(
                        "handler Meta send error msg_id=%s status=%s path=%s body=%s",
                        msg.get("id"),
                        exc.status_code,
                        exc.request_path,
                        exc.body,
                    )
                except Exception:
                    log.exception("handler error msg_id=%s", msg.get("id"))

    # Always 200 fast — Meta retries on non-2xx.
    return Response(status_code=200)
