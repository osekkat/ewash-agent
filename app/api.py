"""PWA-facing /api/v1 router skeleton and shared error handling."""

import hashlib
import logging
import re
import time
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Body, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import api_validation, booking as booking_module, catalog, notifications, persistence
from app.models import CustomerTokenRow
from app.api_schemas import (
    BookingCreateRequest,
    BookingCreateResponse,
    BookingLineItemOut,
    BookingListItemOut,
    BookingsListResponse,
    BootstrapResponse,
    CategoryOut,
    CenterOut,
    ErrorResponse,
    MeDeleteRequest,
    MeDeleteResponse,
    PromoValidateRequest,
    PromoValidateResponse,
    ServiceOut,
    StaffContactOut,
    TimeSlotOut,
    TokenRevokeRequest,
    TokenRevokeResponse,
    VehicleHistoryItem,
    VehicleHistoryResponse,
)
from app.config import settings
from app.notifications import InvalidPhone
from app.rate_limit import (
    PerPhoneRateLimitExceeded,
    _token_key_func,
    hit_phone_limit,
    limiter,
    per_phone_rate_limit_handler,
)
from app.security import hash_token

logger = logging.getLogger("ewash.api")

router = APIRouter(
    prefix="/api/v1",
    tags=["pwa-api"],
)

_BOOTSTRAP_CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=300"

_DOMAIN_EXC_MAP: dict[type[Exception], tuple[int, str]] = {
    api_validation.ClosedDate: (400, api_validation.ClosedDate.error_code),
    api_validation.UnknownSlot: (400, api_validation.UnknownSlot.error_code),
    api_validation.SlotTooSoon: (400, api_validation.SlotTooSoon.error_code),
    api_validation.InvalidDate: (400, api_validation.InvalidDate.error_code),
    api_validation.UnknownService: (400, api_validation.UnknownService.error_code),
    api_validation.InvalidServiceForCategory: (
        400,
        api_validation.InvalidServiceForCategory.error_code,
    ),
    api_validation.UnknownAddon: (400, api_validation.UnknownAddon.error_code),
    api_validation.DuplicateAddon: (400, api_validation.DuplicateAddon.error_code),
    api_validation.NotADetailingService: (
        400,
        api_validation.NotADetailingService.error_code,
    ),
    api_validation.UnknownCenter: (400, api_validation.UnknownCenter.error_code),
    api_validation.MissingCenterId: (400, api_validation.MissingCenterId.error_code),
    api_validation.CenterIdNotAllowed: (
        400,
        api_validation.CenterIdNotAllowed.error_code,
    ),
    InvalidPhone: (400, InvalidPhone.error_code),
}

_DOMAIN_EXC_FIELD_MAP: dict[type[Exception], str] = {
    api_validation.ClosedDate: "date",
    api_validation.UnknownSlot: "slot",
    api_validation.SlotTooSoon: "slot",
    api_validation.InvalidDate: "date",
    api_validation.UnknownService: "service_id",
    api_validation.InvalidServiceForCategory: "service_id",
    api_validation.UnknownAddon: "addon_ids",
    api_validation.DuplicateAddon: "addon_ids",
    api_validation.NotADetailingService: "addon_ids",
    api_validation.UnknownCenter: "location.center_id",
    api_validation.MissingCenterId: "location.center_id",
    api_validation.CenterIdNotAllowed: "location.center_id",
    InvalidPhone: "phone",
}

_JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _service_out(
    service: tuple,
    *,
    bucket: Literal["wash", "detailing", "moto"],
    category: Literal["A", "B", "C", "MOTO"],
    promo_code: str | None,
) -> ServiceOut:
    service_id, name, desc, _prices = service
    price = catalog.service_price(service_id, category, promo_code=promo_code)
    regular_price = catalog.service_price(service_id, category)
    return ServiceOut(
        id=service_id,
        name=name,
        desc=desc,
        price_dh=price or 0,
        regular_price_dh=regular_price if promo_code and price != regular_price else None,
        bucket=bucket,
    )


def _collect_services(
    *,
    category: Literal["A", "B", "C", "MOTO"],
    promo: str | None,
) -> dict[str, list[ServiceOut]]:
    promo_code = catalog.normalize_promo_code(promo) if promo else None

    if category == "MOTO":
        return {
            "moto": [
                _service_out(service, bucket="moto", category=category, promo_code=promo_code)
                for service in catalog.SERVICES_MOTO
            ]
        }
    return {
        "wash": [
            _service_out(service, bucket="wash", category=category, promo_code=promo_code)
            for service in catalog.SERVICES_WASH
        ],
        "detailing": [
            _service_out(
                service,
                bucket="detailing",
                category=category,
                promo_code=promo_code,
            )
            for service in catalog.SERVICES_DETAILING
        ],
    }


@router.get("/catalog/services", response_model=dict[str, list[ServiceOut]])
@limiter.limit(settings.rate_limit_catalog_per_ip, key_func=get_remote_address)
async def get_services(
    request: Request,
    response: Response,
    category: Literal["A", "B", "C", "MOTO"] = Query(...),
    promo: str | None = Query(None, max_length=40),
) -> dict[str, list[ServiceOut]]:
    """List bookable services with server-computed catalog pricing."""
    del request, response
    promo_code = catalog.normalize_promo_code(promo) if promo else None
    result = _collect_services(category=category, promo=promo)

    if category == "MOTO":
        logger.info(
            "catalog.services listed category=%s promo=%s count_moto=%d",
            category,
            promo_code or "-",
            len(result["moto"]),
        )
        return result

    logger.info(
        "catalog.services listed category=%s promo=%s count_wash=%d count_detailing=%d",
        category,
        promo_code or "-",
        len(result["wash"]),
        len(result["detailing"]),
    )
    return result


def _json_error(
    status_code: int,
    code: str,
    message: str,
    *,
    field: str | None = None,
    details: dict | None = None,
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error_code=code,
            message=message,
            field=field,
            details=details or {},
        ).model_dump(),
    )
    response.headers["X-Ewash-Error-Code"] = code
    return response


def domain_error_response(exc: Exception) -> JSONResponse:
    """Convert a known domain exception into the stable PWA error envelope."""
    status_code, code = _DOMAIN_EXC_MAP.get(type(exc), (500, "internal_error"))
    field = _DOMAIN_EXC_FIELD_MAP.get(type(exc))
    logger.info(
        "ewash.api domain_error type=%s status=%d error_code=%s field=%s",
        type(exc).__name__,
        status_code,
        code,
        field or "-",
    )
    return _json_error(status_code, code, str(exc), field=field)


_API_PATH_PREFIX = "/api/v1"


async def api_exception_handler(request: Request, exc: Exception) -> Response:
    """FastAPI exception handler — scoped to /api/v1/* paths only.

    Registered against ``Exception`` on the global FastAPI app, so Starlette
    routes every unhandled exception through here regardless of route. Without
    the prefix guard the PWA error envelope (``{error_code, message, field,
    details}``) leaked into ``/admin/*`` and webhook 500 responses, where it
    confuses the admin operator and preempts each route's own redirect-with-
    flash handling.

    Non-API paths fall through to a plain 500. Once Starlette's
    ``ExceptionMiddleware`` has called this handler and we return a response,
    the exception is considered handled — it does NOT re-propagate to
    ``ServerErrorMiddleware``, so we must log the traceback ourselves before
    returning, otherwise admin/webhook 500s become invisible in production.
    """
    if not request.url.path.startswith(_API_PATH_PREFIX):
        logger.exception("ewash.non_api unhandled exception path=%s", request.url.path)
        return PlainTextResponse("Internal Server Error", status_code=500)

    if type(exc) in _DOMAIN_EXC_MAP:
        return domain_error_response(exc)

    logger.exception("ewash.api unhandled exception path=%s", request.url.path)
    return _json_error(500, "internal_error", "")


def install_exception_handlers(app: FastAPI) -> None:
    """Register API exception handling on the FastAPI application.

    FastAPI's APIRouter has no exception-handler decorator, so app/main.py
    calls this after including the router. The ``Exception`` handler is
    path-guarded to ``/api/v1/*`` — see :func:`api_exception_handler`.

    Also installs a dedicated handler for ``PerPhoneRateLimitExceeded`` so the
    per-phone 429 envelope matches the slowapi-keyed per-IP shape — without
    this override FastAPI's default HTTPException handler returns
    ``{"detail": {...}}`` and the PWA's top-level ``error_code`` read misses
    the canonical ``rate_limit_exceeded`` value. The exception is only raised
    from ``/api/v1`` code paths, so no prefix check is needed here.
    """
    app.add_exception_handler(Exception, api_exception_handler)
    app.add_exception_handler(
        PerPhoneRateLimitExceeded,
        per_phone_rate_limit_handler,
    )


# ── Catalog endpoints ────────────────────────────────────────────────────

def _hash_for_log(value: str, *, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length] if value else "-"


def _date_label(date_iso: str) -> str:
    try:
        parsed = date_cls.fromisoformat(date_iso)
    except ValueError:
        return date_iso
    return f"{_JOURS_FR[parsed.weekday()].capitalize()} {parsed.strftime('%d/%m/%Y')}"


def _slot_label(slot_id: str) -> str:
    return catalog.label_for(catalog.active_time_slots(), slot_id)


def _clean_booking_text_fields(booking: booking_module.Booking) -> None:
    booking.note = api_validation.clean_text(booking.note, max_len=500) or ""
    booking.address = api_validation.clean_text(booking.address, max_len=200) or ""
    booking.location_address = api_validation.clean_text(
        booking.location_address,
        max_len=200,
    ) or ""
    booking.car_model = api_validation.clean_text(booking.car_model, max_len=64) or ""
    booking.color = api_validation.clean_text(booking.color, max_len=64) or ""
    booking.name = api_validation.clean_text(booking.name, max_len=120) or ""


def _resolve_booking_addons(
    addon_ids: list[str],
    *,
    category: str,
    promo_code: str | None,
) -> list[tuple[str, str, int, int]]:
    resolved: list[tuple[str, str, int, int]] = []
    for addon_id in addon_ids:
        base_price = catalog.service_price(addon_id, category, promo_code=promo_code)
        if base_price is None:
            raise api_validation.UnknownAddon(
                f"addon_id={addon_id} has no price for category={category}"
            )
        addon_price = round(base_price * 0.9)
        label = f"{catalog.service_name(addon_id)} — {addon_price} DH (-10%)"
        resolved.append((addon_id, label, addon_price, base_price))
    return resolved


def _booking_location_label_from_row(row) -> str:
    if row.location_mode == "home":
        return "À domicile"
    return row.center or row.location_name or "Au stand"


def _regular_price_for_response(price: int, regular_price: int | None) -> int | None:
    if regular_price is None or regular_price == price:
        return None
    return regular_price


def _booking_create_response_from_row(
    row,
    *,
    bookings_token: str,
    is_idempotent_replay: bool,
) -> BookingCreateResponse:
    line_items = [
        BookingLineItemOut(
            kind=item.kind,
            service_id=item.service_id,
            label=item.label_snapshot or item.service_id,
            price_dh=item.total_price_dh or item.unit_price_dh or 0,
            regular_price_dh=_regular_price_for_response(
                item.total_price_dh or item.unit_price_dh or 0,
                item.regular_price_dh,
            ),
            sort_order=item.sort_order,
        )
        for item in row.line_items
        if item.kind in {"main", "addon"}
    ]
    if not line_items:
        line_items = [
            BookingLineItemOut(
                kind="main",
                service_id=row.service_id or "",
                label=row.service_label or row.service_id or "",
                price_dh=row.price_dh or 0,
                regular_price_dh=_regular_price_for_response(
                    row.price_dh or 0,
                    row.price_regular_dh,
                ),
                sort_order=0,
            )
        ]
        if row.addon_service:
            addon_price = row.addon_price_dh or 0
            line_items.append(
                BookingLineItemOut(
                    kind="addon",
                    service_id=row.addon_service,
                    label=row.addon_service_label or row.addon_service,
                    price_dh=addon_price,
                    regular_price_dh=None,
                    sort_order=10,
                )
            )

    return BookingCreateResponse(
        ref=row.ref,
        status=row.status,
        price_dh=row.price_dh or 0,
        total_dh=row.total_price_dh or row.price_dh or 0,
        vehicle_label=row.vehicle_type or "",
        service_label=row.service_label or row.service_id or "",
        date_label=row.date_label
        or (row.appointment_date.isoformat() if row.appointment_date else ""),
        slot_label=row.slot or row.slot_id or "",
        location_label=_booking_location_label_from_row(row),
        line_items=line_items,
        bookings_token=bookings_token,
        is_idempotent_replay=is_idempotent_replay,
    )


def _idempotent_booking_response(
    client_request_id: str | None,
    *,
    engine,
    request: Request,
    caller_phone_raw: str,
    caller_bookings_token: str | None = None,
) -> BookingCreateResponse | None:
    """Replay a cached booking response only when the caller owns the booking.

    The lookup key is the customer-supplied ``client_request_id`` (UUIDv4 in
    the PWA). Pre-bug ewash-416 fix this function returned the cached row to
    anyone who knew the id, *minting a fresh bookings_token bound to the
    original customer's phone* — which turned `client_request_id` into a
    session takeover key whenever it leaked (server logs at line 388, DB
    column on bookings, etc).

    The fix: before any replay, normalize the caller's ``body.phone`` and
    require it to match the booking's stored ``customer_phone``. On mismatch
    (or malformed phone) return ``None`` so the fresh-create path runs — that
    path then either succeeds for an unrelated client_request_id (impossible
    by definition because the id was just observed in the DB) or fails
    through the normal validation envelope, never confirming whether the id
    existed.
    """
    if not client_request_id:
        return None

    try:
        caller_phone = notifications.normalize_phone(caller_phone_raw)
    except notifications.InvalidPhone:
        # Malformed phone — let the fresh-create path emit the canonical
        # 400 invalid_phone error envelope. Returning None here also closes
        # the side-channel where "is this client_request_id known?" could be
        # answered by comparing 4xx error codes.
        return None

    with persistence.session_scope(engine) as session:
        row = persistence.find_booking_by_client_request_id(
            client_request_id,
            session=session,
        )
        if row is None:
            return None
        if row.customer_phone != caller_phone:
            # Replay attempt from a caller who doesn't own this booking.
            # Don't return the cached row, don't mint a token, don't emit
            # the idempotent_hit log (which would tell an attacker their
            # id-guess landed). Fall through to the fresh-create path.
            logger.warning(
                "ewash.api.idempotent_mismatch client_request_id_hash=%s "
                "caller_phone_hash=%s row_phone_hash=%s",
                _hash_for_log(client_request_id),
                _hash_for_log(caller_phone),
                _hash_for_log(row.customer_phone),
            )
            return None
        # Load relationship rows while the session is open so the replay body
        # can be built without issuing more queries after the transaction ends.
        _ = list(row.line_items)
        response = _booking_create_response_from_row(
            row,
            bookings_token="",
            is_idempotent_replay=True,
        )
        ref = row.ref
        phone = row.customer_phone

    returned_token = ""
    if caller_bookings_token:
        matched_phone = persistence.verify_customer_token(
            caller_bookings_token,
            expected_phone=phone,
            engine=engine,
        )
        if matched_phone == phone:
            returned_token = caller_bookings_token
            logger.info(
                "bookings.token reused ref=%s phone_hash=%s replay=true",
                ref,
                _hash_for_log(phone),
            )
    if not returned_token:
        # Don't mint on every replay — the winning request already minted one
        # on the create path, and the plaintext of an existing row is not
        # retrievable (only its SHA-256 hash is stored). Returning "" here
        # lets the caller fall back to whatever they already cached from
        # their first successful response; minting again would just accrue
        # orphan customer_tokens rows that admin revocation has to chase.
        with persistence.session_scope(engine) as session:
            has_existing_token = session.scalar(
                select(CustomerTokenRow.id)
                .where(CustomerTokenRow.customer_phone == phone)
                .limit(1)
            ) is not None
        if has_existing_token:
            logger.info(
                "bookings.token mint_skipped_existing ref=%s phone_hash=%s replay=true",
                ref,
                _hash_for_log(phone),
            )
        else:
            returned_token = persistence.mint_customer_token(phone, engine=engine)
            logger.info(
                "bookings.token minted ref=%s phone_hash=%s replay=true",
                ref,
                _hash_for_log(phone),
            )

    response.bookings_token = returned_token
    request.state.booking_ref = ref
    request.state.phone_normalized = phone
    logger.info(
        "ewash.api.idempotent_hit client_request_id=%s ref=%s phone_hash=%s",
        client_request_id,
        ref,
        _hash_for_log(phone),
    )
    return response


@router.post("/bookings", response_model=BookingCreateResponse)
@limiter.limit(settings.rate_limit_bookings_per_ip, key_func=get_remote_address)
async def create_booking(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    body: BookingCreateRequest = Body(...),
) -> BookingCreateResponse | JSONResponse:
    """Create a PWA booking in the same pending staff-confirmation state as WhatsApp."""
    del response  # SlowAPI requires the parameter so it can inject rate-limit headers.
    started = time.perf_counter()

    engine = persistence._configured_engine()
    if engine is None:
        return _json_error(
            503,
            "db_unavailable",
            "Database not configured",
            details={"resource": "database"},
        )

    replay = _idempotent_booking_response(
        body.client_request_id,
        engine=engine,
        request=request,
        caller_phone_raw=body.phone,
        caller_bookings_token=body.bookings_token,
    )
    if replay is not None:
        return replay

    try:
        phone = notifications.normalize_phone(body.phone)
        request.state.phone_normalized = phone
        hit_phone_limit(phone, settings.rate_limit_bookings_per_phone)

        api_validation.validate_service_for_category(body.service_id, body.category)
        api_validation.validate_addon_ids(body.addon_ids, main_service_id=body.service_id)
        api_validation.validate_center_id(
            body.location.center_id,
            location_kind=body.location.kind,
        )
        api_validation.validate_slot_and_date(body.date, body.slot)

        promo_code = catalog.normalize_promo_code(body.promo_code) if body.promo_code else None
        promo_label = catalog.promo_label(promo_code) if promo_code else ""
        server_price = catalog.service_price(body.service_id, body.category, promo_code=promo_code)
        server_regular = catalog.public_service_price(body.service_id, body.category)
        if server_price is None or server_regular is None:
            raise api_validation.UnknownService(
                f"service_id={body.service_id} has no price for category={body.category}"
            )
        addons_resolved = _resolve_booking_addons(
            body.addon_ids,
            category=body.category,
            promo_code=promo_code,
        )
    except tuple(_DOMAIN_EXC_MAP) as exc:
        return domain_error_response(exc)

    service_label = catalog.service_label(
        body.service_id,
        body.category,
        promo_code=promo_code,
    )
    vehicle_label = catalog.vehicle_label(
        body.category,
        make=body.vehicle.make if body.vehicle else None,
    )
    location_label = catalog.location_label(
        body.location.kind,
        center_id=body.location.center_id,
    )
    slot_label = _slot_label(body.slot)

    booking = booking_module.from_api_payload(
        body,
        server_price_dh=server_price,
        server_regular_price_dh=server_regular,
        service_label=service_label,
        vehicle_label=vehicle_label,
        location_label=location_label,
        date_label=_date_label(body.date),
        slot_label=slot_label,
        promo_label=promo_label,
    )
    _clean_booking_text_fields(booking)
    if addons_resolved:
        first_addon_id, first_addon_label, first_addon_price, _first_regular_price = (
            addons_resolved[0]
        )
        booking.addon_service = first_addon_id
        booking.addon_service_label = first_addon_label
        booking.addon_price_dh = first_addon_price
    total_dh = booking.price_dh + sum(
        addon_price for _addon_id, _label, addon_price, _regular in addons_resolved
    )

    try:
        with persistence.session_scope(engine) as session:
            persistence.assign_booking_ref(booking, session=session, record_shadow=False)
            request.state.booking_ref = booking.ref
            booking.client_request_id = body.client_request_id
            persistence.persist_confirmed_booking(booking, source="api", session=session)
            for index, (addon_id, addon_label, addon_price, addon_regular_price) in enumerate(
                addons_resolved
            ):
                persistence.persist_booking_addon(
                    booking.ref,
                    addon_service=addon_id,
                    addon_service_label=addon_label,
                    addon_price_dh=addon_price,
                    regular_price_dh=addon_regular_price,
                    discount_label="-10% Esthétique",
                    denormalize_to_legacy=index == 0,
                    session=session,
                )
            persistence.persist_customer_name(phone, booking.name, session=session)
    except IntegrityError:
        # Concurrent INSERT with the same client_request_id (Postgres partial
        # unique index). Fall back to the replay path — same caller-phone
        # guard applies so a racing attacker can't piggyback on a victim's id.
        replay = _idempotent_booking_response(
            body.client_request_id,
            engine=engine,
            request=request,
            caller_phone_raw=body.phone,
            caller_bookings_token=body.bookings_token,
        )
        if replay is not None:
            return replay
        raise

    if addons_resolved:
        logger.info(
            "bookings.addons added ref=%s count=%d addon_ids=%s total_dh=%d",
            booking.ref,
            len(addons_resolved),
            ",".join(
                addon_id
                for addon_id, _label, _addon_price, _regular in addons_resolved
            ),
            total_dh,
        )

    returned_token = ""
    if body.bookings_token:
        matched_phone = persistence.verify_customer_token(
            body.bookings_token,
            expected_phone=phone,
            engine=engine,
        )
        if matched_phone == phone:
            returned_token = body.bookings_token
            logger.info(
                "bookings.token reused ref=%s phone_hash=%s",
                booking.ref,
                _hash_for_log(phone),
            )
    if not returned_token:
        returned_token = persistence.mint_customer_token(phone, engine=engine)
        logger.info(
            "bookings.token minted ref=%s phone_hash=%s",
            booking.ref,
            _hash_for_log(phone),
        )

    background_tasks.add_task(
        notifications.notify_booking_confirmation_safe,
        booking,
        event_label="Nouvelle réservation PWA",
    )

    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "ewash.api.bookings.create ref=%s phone_hash=%s source=api category=%s "
        "service=%s price=%d total=%d promo=%s duration_ms=%.1f",
        booking.ref,
        _hash_for_log(phone),
        booking.category,
        booking.service,
        booking.price_dh,
        total_dh,
        promo_code or "-",
        duration_ms,
    )

    return BookingCreateResponse(
        ref=booking.ref,
        status="pending_ewash_confirmation",
        price_dh=booking.price_dh,
        total_dh=total_dh,
        vehicle_label=vehicle_label,
        service_label=service_label,
        date_label=booking.date_label,
        slot_label=slot_label,
        location_label=location_label,
        line_items=[
            BookingLineItemOut(
                kind="main",
                service_id=booking.service,
                label=service_label,
                price_dh=booking.price_dh,
                regular_price_dh=server_regular if promo_code else None,
                sort_order=0,
            )
        ]
        + [
            BookingLineItemOut(
                kind="addon",
                service_id=addon_id,
                label=addon_label,
                price_dh=addon_price,
                regular_price_dh=addon_regular_price,
                sort_order=(index + 1) * 10,
            )
            for index, (
                addon_id,
                addon_label,
                addon_price,
                addon_regular_price,
            ) in enumerate(addons_resolved)
        ],
        bookings_token=returned_token,
        is_idempotent_replay=False,
    )


def _build_category_payload() -> list[CategoryOut]:
    """Project the static VEHICLE_CATEGORIES tuples into the API contract.

    `id` uses the pricing-category key ("A" / "B" / "C" / "MOTO") because that
    is what the rest of the API takes (BookingCreateRequest.category, the
    service_price lookups). `label` uses the clean API-facing names from
    VEHICLE_CATEGORY_LABEL (the bot's list-row titles embed the letter prefix
    and an emoji, neither of which the PWA wants).
    """
    payload: list[CategoryOut] = []
    for row_id, _list_title, sub in catalog.VEHICLE_CATEGORIES:
        category_key = catalog.VEHICLE_CATEGORY_KEY.get(row_id)
        if category_key is None:
            # Defensive: a future row added to VEHICLE_CATEGORIES without a
            # corresponding VEHICLE_CATEGORY_KEY entry would otherwise leak
            # through with a missing id.
            logger.warning("catalog.categories skipping row=%s missing category key", row_id)
            continue
        label = catalog.VEHICLE_CATEGORY_LABEL.get(category_key, category_key)
        kind = "moto" if category_key == catalog.MOTO_PRICE_CATEGORY else "car"
        payload.append(CategoryOut(id=category_key, label=label, sub=sub, kind=kind))
    return payload


def _collect_categories() -> list[CategoryOut]:
    return _build_category_payload()


@router.get("/catalog/categories", response_model=list[CategoryOut])
async def list_catalog_categories() -> list[CategoryOut]:
    """Vehicle categories: 3 car tiers + Moto/Scooter."""
    categories = _collect_categories()
    logger.info("catalog.categories listed count=%d", len(categories))
    return categories


def _collect_centers() -> list[CenterOut]:
    return [
        CenterOut(id=center_id, name=name, details=details or "")
        for center_id, name, details in catalog.active_centers()
    ]


@router.get("/catalog/centers", response_model=list[CenterOut])
@limiter.limit(settings.rate_limit_catalog_per_ip, key_func=get_remote_address)
async def list_catalog_centers(
    request: Request,
    response: Response,
) -> list[CenterOut]:
    """Active stand/center options for the location-picker step."""
    del request, response
    centers = _collect_centers()
    logger.info("catalog.centers listed count=%d", len(centers))
    return centers


_SLOT_HOUR_RE = re.compile(r"^slot_(\d+)_\d+$")


def _slots_with_lead_filter(
    *,
    date_iso: str | None,
    now: datetime | None = None,
) -> tuple[list[TimeSlotOut], datetime | None, int]:
    """Return (payload, cutoff, total_count).

    When `date_iso` is None: return every active slot, no filtering.
    When supplied: filter to slots whose start time is >= now + 2h in
    Africa/Casablanca. `now` is injectable for tests.
    """
    all_slots = catalog.active_time_slots()
    if date_iso is None:
        return (
            [TimeSlotOut(id=slot_id, label=label, period=period) for slot_id, label, period in all_slots],
            None,
            len(all_slots),
        )

    try:
        appointment_date = datetime.strptime(date_iso, "%Y-%m-%d").date()
    except ValueError as exc:
        raise api_validation.InvalidDate(f"date={date_iso} not parseable") from exc

    if now is None:
        now_tz = datetime.now(tz=api_validation.CASABLANCA_TZ)
    else:
        now_tz = now.astimezone(api_validation.CASABLANCA_TZ)
    cutoff = now_tz + timedelta(hours=api_validation.MIN_LEAD_HOURS)

    available: list[TimeSlotOut] = []
    for slot_id, label, period in all_slots:
        match = _SLOT_HOUR_RE.match(slot_id)
        if match is None:
            # Skip malformed slot ids defensively — `validate_slot_and_date`
            # is the authoritative gate for booking submissions.
            continue
        start_hour = int(match.group(1))
        candidate = datetime(
            appointment_date.year,
            appointment_date.month,
            appointment_date.day,
            start_hour,
            0,
            tzinfo=api_validation.CASABLANCA_TZ,
        )
        if candidate >= cutoff:
            available.append(TimeSlotOut(id=slot_id, label=label, period=period))
    return available, cutoff, len(all_slots)


def _collect_time_slots(date_iso: str | None) -> list[TimeSlotOut]:
    available, _cutoff, _total = _slots_with_lead_filter(date_iso=date_iso)
    return available


@router.get("/catalog/time-slots", response_model=list[TimeSlotOut])
@limiter.limit(settings.rate_limit_catalog_per_ip, key_func=get_remote_address)
async def list_catalog_time_slots(
    request: Request,
    response: Response,
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> list[TimeSlotOut]:
    """Slot rows for the booking flow. Optional `date=YYYY-MM-DD` filters out
    slots starting <2h after server-side `now` in Africa/Casablanca.

    The PWA's client-side 2-hour filter is decorative — a user with a
    tampered clock or wrong timezone could otherwise submit a slot in the
    past. This endpoint is the authoritative source.
    """
    del request, response
    available, cutoff, total = _slots_with_lead_filter(date_iso=date)
    logger.info(
        "catalog.time_slots listed date=%s total=%d returned=%d cutoff=%s",
        date or "-",
        total,
        len(available),
        cutoff.isoformat() if cutoff else "-",
    )
    return available


# ── Promo validation ──────────────────────────────────────────────────────


def _build_promo_discount_map(code: str, category: str) -> dict[str, int]:
    """Return ``{service_id: discounted_price}`` for every service whose
    discounted price under ``code`` is strictly less than the public price.

    Services that aren't in the partner's discount grid (or whose discount
    equals the public price) are omitted so the PWA only renders
    strike-through pricing where there's actually a saving to show.
    """
    if category == catalog.MOTO_PRICE_CATEGORY:
        services = catalog.SERVICES_MOTO
    else:
        services = catalog.SERVICES_CAR
    prices: dict[str, int] = {}
    for entry in services:
        service_id = entry[0]
        public = catalog.public_service_price(service_id, category)
        discounted = catalog.service_price(service_id, category, promo_code=code)
        if public is None or discounted is None:
            continue
        if discounted < public:
            prices[service_id] = discounted
    return prices


@router.post("/promos/validate", response_model=PromoValidateResponse)
@limiter.limit(settings.rate_limit_promo_per_ip, key_func=get_remote_address)
async def validate_promo(
    request: Request,
    response: Response,
    body: PromoValidateRequest,
) -> PromoValidateResponse:
    """Validate a promo code and surface its discounted prices for the
    customer's vehicle category.

    Always returns ``200`` — invalid / inactive codes get ``valid=false``
    rather than ``404``. A ``404`` here would let an attacker enumerate
    valid codes by probing the absence-of-404 channel.
    """
    del request, response
    code = catalog.normalize_promo_code(body.code)
    if not code:
        logger.info(
            "promos.validate code=- category=%s valid=False discounts_count=0",
            body.category,
        )
        return PromoValidateResponse(valid=False)

    label = catalog.promo_label(code)
    if not label:
        # The code looks well-formed but is unknown / inactive at the catalog
        # layer. Same 200/false response — no enumeration hint.
        logger.info(
            "promos.validate code=%s category=%s valid=False discounts_count=0",
            code,
            body.category,
        )
        return PromoValidateResponse(valid=False)

    discounts = _build_promo_discount_map(code, body.category)
    logger.info(
        "promos.validate code=%s category=%s valid=True discounts_count=%d",
        code,
        body.category,
        len(discounts),
    )
    return PromoValidateResponse(valid=True, label=label, discounted_prices=discounts)


@router.get("/catalog/closed-dates", response_model=list[str])
@limiter.limit(settings.rate_limit_catalog_per_ip, key_func=get_remote_address)
async def list_catalog_closed_dates(
    request: Request,
    response: Response,
) -> list[str]:
    """ISO-date strings the shop is closed (Eids, etc.), sorted ascending.

    The PWA calendar uses this to grey out the corresponding days. Static
    catalog entries and DB-persisted closures are merged by
    :func:`catalog.active_closed_dates`.
    """
    del request, response
    closed = sorted(catalog.active_closed_dates())
    logger.info(
        "catalog.closed_dates listed count=%d first=%s last=%s",
        len(closed),
        closed[0] if closed else "-",
        closed[-1] if closed else "-",
    )
    return closed


def _collect_closed_dates() -> list[str]:
    return sorted(catalog.active_closed_dates())


def _collect_staff_contact() -> StaffContactOut:
    config = notifications.get_booking_notification_settings()
    if config.phone_number:
        return StaffContactOut(whatsapp_phone=f"+{config.phone_number}", available=True)
    return StaffContactOut(whatsapp_phone="", available=False)


def _bootstrap_etag(
    *,
    catalog_version: str,
    category: str | None,
    promo_code: str | None,
) -> str:
    etag_input = f"{catalog_version}|{category or '-'}|{promo_code or '-'}"
    digest = hashlib.sha256(etag_input.encode("utf-8")).hexdigest()[:16]
    return f'W/"{digest}"'


def _etag_matches(if_none_match: str, etag: str) -> bool:
    return any(part.strip() == etag for part in if_none_match.split(","))


@router.get("/bootstrap", response_model=BootstrapResponse)
@limiter.limit(settings.rate_limit_catalog_per_ip, key_func=get_remote_address)
async def get_bootstrap(
    request: Request,
    response: Response,
    category: Literal["A", "B", "C", "MOTO"] | None = Query(None),
    promo: str | None = Query(None, max_length=40),
) -> BootstrapResponse | Response:
    """Single round-trip catalog hydration for the booking flow."""
    started = time.perf_counter()
    promo_code = catalog.normalize_promo_code(promo) if promo else None
    catalog_version = catalog.compute_catalog_etag_seed()
    etag = _bootstrap_etag(
        catalog_version=catalog_version,
        category=category,
        promo_code=promo_code,
    )

    if _etag_matches(request.headers.get("If-None-Match", ""), etag):
        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "catalog.bootstrap category=%s promo=%s etag=%s has_services=%s cache_hit=%d duration_ms=%.1f",
            category or "-",
            promo_code or "-",
            etag,
            str(bool(category)).lower(),
            304,
            duration_ms,
        )
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": _BOOTSTRAP_CACHE_CONTROL,
            },
        )

    services = _collect_services(category=category, promo=promo) if category else {}
    body = BootstrapResponse(
        categories=_collect_categories(),
        services=services,
        centers=_collect_centers(),
        time_slots=_collect_time_slots(date_iso=None),
        closed_dates=_collect_closed_dates(),
        staff_contact=_collect_staff_contact(),
        catalog_version=catalog_version,
    )
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = _BOOTSTRAP_CACHE_CONTROL
    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "catalog.bootstrap category=%s promo=%s etag=%s has_services=%s cache_hit=%d duration_ms=%.1f",
        category or "-",
        promo_code or "-",
        etag,
        str(bool(services)).lower(),
        200,
        duration_ms,
    )
    return body


# ── Bookings list (token-scoped read of customer's own bookings) ─────────


@router.get("/bookings", response_model=BookingsListResponse)
@limiter.limit(settings.rate_limit_bookings_list_per_token, key_func=_token_key_func)
@limiter.limit(settings.rate_limit_token_endpoints_per_ip, key_func=get_remote_address)
async def list_bookings(
    request: Request,
    response: Response,
    cursor: str | None = Query(None, max_length=512),
    limit: int = Query(20, ge=1, le=100),
) -> BookingsListResponse | JSONResponse:
    """Return the customer's recent bookings scoped to the X-Ewash-Token bearer.

    Phone enumeration is mechanically impossible: the route reads only the
    opaque token from the header and looks up the SHA-256 digest. There is no
    ``?phone=`` parameter — supplying one returns 400 ``phone_param_not_accepted``
    so the misuse is loud rather than silent.

    The ``response`` parameter has no body-side use; slowapi reads it from the
    handler signature to inject the ``X-RateLimit-*`` headers.
    """
    del response
    started = time.perf_counter()

    if "phone" in request.query_params:
        logger.warning(
            "bookings.list error=phone_param_not_accepted ip_hash=%s",
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(
            400,
            "phone_param_not_accepted",
            "Pass the customer token via X-Ewash-Token; phone enumeration is not supported.",
        )

    token = request.headers.get("X-Ewash-Token", "")
    if not token:
        logger.warning(
            "bookings.list error=missing_token ip_hash=%s",
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(401, "missing_token", "X-Ewash-Token required")

    try:
        items, next_cursor = persistence.list_bookings_for_token(
            token,
            limit=limit,
            cursor=cursor,
        )
    except ValueError:
        logger.warning(
            "bookings.list error=invalid_cursor token_prefix=%s ip_hash=%s",
            hash_token(token)[:8],
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(400, "invalid_cursor", "Invalid cursor", field="cursor")
    matched_phone = persistence.verify_customer_token(token)
    if matched_phone is None:
        logger.warning(
            "bookings.list error=invalid_token token_prefix=%s ip_hash=%s",
            hash_token(token)[:8],
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(401, "invalid_token", "Token not recognized")

    request.state.phone_normalized = matched_phone

    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "bookings.list phone_hash=%s count=%d ip_hash=%s duration_ms=%.1f",
        _hash_for_log(matched_phone),
        len(items),
        _hash_for_log(get_remote_address(request) or ""),
        duration_ms,
    )
    return BookingsListResponse(
        bookings=[BookingListItemOut(**item) for item in items],
        next_cursor=next_cursor,
    )


# ── Vehicle history (token-scoped, "use a previous car" quick-pick) ──────


@router.get("/me/vehicles", response_model=VehicleHistoryResponse)
@limiter.limit(settings.rate_limit_bookings_list_per_token, key_func=_token_key_func)
@limiter.limit(settings.rate_limit_token_endpoints_per_ip, key_func=get_remote_address)
async def list_my_vehicles(
    request: Request,
    response: Response,
) -> VehicleHistoryResponse | JSONResponse:
    """Return deduped past vehicles for the X-Ewash-Token bearer.

    Feeds the booking flow's CategoryStep so returning customers can tap
    a card instead of retyping make/color. Same auth model as
    GET /api/v1/bookings — token-only, no ``?phone=`` parameter.

    The category_label is injected here (not in persistence) so the
    storage layer stays agnostic of display text.
    """
    del response
    started = time.perf_counter()

    if "phone" in request.query_params:
        logger.warning(
            "me.vehicles error=phone_param_not_accepted ip_hash=%s",
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(
            400,
            "phone_param_not_accepted",
            "Pass the customer token via X-Ewash-Token; phone enumeration is not supported.",
        )

    token = request.headers.get("X-Ewash-Token", "")
    if not token:
        logger.warning(
            "me.vehicles error=missing_token ip_hash=%s",
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(401, "missing_token", "X-Ewash-Token required")

    matched_phone = persistence.verify_customer_token(token)
    if matched_phone is None:
        logger.warning(
            "me.vehicles error=invalid_token token_prefix=%s ip_hash=%s",
            hash_token(token)[:8],
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(401, "invalid_token", "Token not recognized")

    items = persistence.list_customer_vehicles_for_token(token)
    request.state.phone_normalized = matched_phone

    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "me.vehicles phone_hash=%s count=%d ip_hash=%s duration_ms=%.1f",
        _hash_for_log(matched_phone),
        len(items),
        _hash_for_log(get_remote_address(request) or ""),
        duration_ms,
    )
    return VehicleHistoryResponse(
        vehicles=[
            VehicleHistoryItem(
                category=item.category,
                category_label=catalog.VEHICLE_CATEGORY_LABEL.get(item.category, ""),
                make=item.make,
                color=item.color,
                label=item.label,
                last_used_at=item.last_used_at.isoformat() if item.last_used_at else None,
            )
            for item in items
        ],
    )


# ── Token revoke (PWA logout) ────────────────────────────────────────────


@router.post(
    "/tokens/revoke",
    response_model=TokenRevokeResponse,
    response_model_exclude_none=True,
)
@limiter.limit(settings.rate_limit_token_revoke_per_token, key_func=_token_key_func)
@limiter.limit(settings.rate_limit_token_endpoints_per_ip, key_func=get_remote_address)
async def revoke_token(
    request: Request,
    response: Response,
    body: TokenRevokeRequest,
) -> TokenRevokeResponse | JSONResponse:
    """Revoke the calling token (default) or every token for its phone (``all``).

    The token in ``X-Ewash-Token`` is verified just like a read endpoint — a
    missing or unknown token gets 401 with the same envelope shape so the PWA
    treats it identically. On success the rows are physically deleted; any
    later call carrying the same token will 401 with ``invalid_token``.
    """
    started = time.perf_counter()

    token = request.headers.get("X-Ewash-Token", "")
    if not token:
        logger.warning(
            "tokens.revoke error=missing_token ip_hash=%s",
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(401, "missing_token", "X-Ewash-Token required")

    phone = persistence.verify_customer_token(token)
    if phone is None:
        logger.warning(
            "tokens.revoke error=invalid_token token_prefix=%s ip_hash=%s",
            hash_token(token)[:8],
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(401, "invalid_token", "Token not recognized")

    request.state.phone_normalized = phone
    new_token = None
    if body.scope == "all":
        count = persistence.revoke_all_tokens_for_phone(phone)
        new_token = persistence.mint_customer_token(phone)
    else:
        count = persistence.revoke_token_by_hash(hash_token(token))

    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "tokens.revoked phone_hash=%s scope=%s count=%d duration_ms=%.1f",
        _hash_for_log(phone),
        body.scope,
        count,
        duration_ms,
    )
    return TokenRevokeResponse(revoked_count=count, new_token=new_token)


# ── Data erasure (Loi 09-08 / GDPR right-to-erasure) ─────────────────────


@router.delete("/me", response_model=MeDeleteResponse)
@limiter.limit(settings.rate_limit_me_delete_per_token, key_func=_token_key_func)
@limiter.limit(settings.rate_limit_token_endpoints_per_ip, key_func=get_remote_address)
async def delete_my_account(
    request: Request,
    response: Response,
    body: MeDeleteRequest = Body(...),
) -> MeDeleteResponse | JSONResponse:
    """Customer-initiated full data erasure under Loi 09-08 / GDPR.

    The request must carry both ``X-Ewash-Token`` and a JSON body containing
    the literal phrase ``"I confirm I want to delete my data"`` (the Pydantic
    ``Literal`` enforces the exact value — anything else yields 422 from the
    framework before we ever reach the handler body).

    On success every customer-side row (tokens, names, vehicles, conversation
    sessions) is deleted and every booking owned by the phone is anonymized
    in-place — the slot history survives for revenue accounting but no PII
    remains. An append-only ``data_erasure_audit`` row records the action so
    compliance reports can answer "how many erasures last quarter?" without
    revealing who.

    Rate-limited at 3/hour per token because legitimate use is a single tap;
    repeated calls would only happen during abuse or accidental retries.

    ``response`` is unused but the slowapi decorator requires it on the
    handler signature to inject the ``X-RateLimit-*`` headers.
    """
    del response
    started = time.perf_counter()

    token = request.headers.get("X-Ewash-Token", "")
    if not token:
        logger.warning(
            "me.delete error=missing_token ip_hash=%s",
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(401, "missing_token", "X-Ewash-Token required")

    phone = persistence.verify_customer_token(token)
    if phone is None:
        logger.warning(
            "me.delete error=invalid_token token_prefix=%s ip_hash=%s",
            hash_token(token)[:8],
            _hash_for_log(get_remote_address(request) or ""),
        )
        return _json_error(401, "invalid_token", "Token not recognized")

    request.state.phone_normalized = phone
    # `body` is intentionally not read further — the Pydantic ``Literal`` on
    # ``MeDeleteRequest.confirm`` rejected every value but the exact phrase
    # before this point.
    del body

    result = persistence.anonymize_customer(phone, actor="customer_self_serve")
    deleted_count = result["deleted_count"]
    anonymized_bookings = result["anonymized_bookings"]

    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "me.delete phone_hash=%s deleted_count=%d anonymized_bookings=%d duration_ms=%.1f",
        _hash_for_log(phone),
        deleted_count,
        anonymized_bookings,
        duration_ms,
    )
    return MeDeleteResponse(
        deleted_count=deleted_count,
        anonymized_bookings=anonymized_bookings,
    )


__all__ = [
    "_DOMAIN_EXC_MAP",
    "_slots_with_lead_filter",
    "api_exception_handler",
    "create_booking",
    "delete_my_account",
    "domain_error_response",
    "get_bootstrap",
    "get_services",
    "install_exception_handlers",
    "list_bookings",
    "list_catalog_categories",
    "list_catalog_centers",
    "list_catalog_closed_dates",
    "list_catalog_time_slots",
    "revoke_token",
    "router",
    "validate_promo",
]
