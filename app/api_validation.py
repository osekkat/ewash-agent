"""Domain validators for the planned /api/v1/* router.

Server-side source of truth for the contract between the PWA and the backend.
Anything the PWA enforces in the browser must be re-enforced here because a
tampered client could bypass it. Slot freshness and the closed-date list both
live here, in Africa/Casablanca time.

Each validator raises an `APIValidationError` subclass with a stable
`error_code` attribute so the API layer can map exceptions → 400 responses
with a machine-readable code without re-introspecting the message string.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta
from typing import overload
from zoneinfo import ZoneInfo

from sqlalchemy import Engine

from .catalog import (
    SERVICES_CAR,
    SERVICES_DETAILING,
    SERVICES_MOTO,
    active_centers,
    active_closed_dates,
    active_time_slots,
)

log = logging.getLogger(__name__)

CASABLANCA_TZ = ZoneInfo("Africa/Casablanca")
MIN_LEAD_HOURS = 2
HOME_MIN_LEAD_WORKING_HOURS = 4
WORKING_DAY_START_HOUR = 9
WORKING_DAY_END_HOUR = 22
_SLOT_ID_PATTERN = re.compile(r"^slot_(\d+)_(\d+)$")
_HORIZONTAL_WHITESPACE_RUN = re.compile(r"[ \t]+")


class APIValidationError(ValueError):
    """Base for domain validation errors with stable error codes for API responses."""

    error_code: str = "validation_error"

    def __init__(self, message: str = "", *, error_code: str | None = None) -> None:
        super().__init__(message)
        if error_code:
            self.error_code = error_code


class ClosedDate(APIValidationError):
    error_code = "closed_date"


class UnknownSlot(APIValidationError):
    error_code = "unknown_slot"


class SlotTooSoon(APIValidationError):
    error_code = "slot_too_soon"


class InvalidDate(APIValidationError):
    error_code = "invalid_date"


class UnknownService(APIValidationError):
    error_code = "unknown_service"


class InvalidServiceForCategory(APIValidationError):
    error_code = "service_category_mismatch"


class UnknownAddon(APIValidationError):
    error_code = "unknown_addon"


class DuplicateAddon(APIValidationError):
    error_code = "duplicate_addon"


class NotADetailingService(APIValidationError):
    error_code = "not_a_detailing_service"


class CenterIdNotAllowed(APIValidationError):
    error_code = "center_id_not_allowed"


class MissingCenterId(APIValidationError):
    error_code = "missing_center_id"


class UnknownCenter(APIValidationError):
    error_code = "unknown_center"


_CAR_SERVICE_IDS: frozenset[str] = frozenset(sid for sid, *_ in SERVICES_CAR)
_MOTO_SERVICE_IDS: frozenset[str] = frozenset(sid for sid, *_ in SERVICES_MOTO)
_DETAILING_SERVICE_IDS: frozenset[str] = frozenset(sid for sid, *_ in SERVICES_DETAILING)


def validate_service_for_category(service_id: str, category: str) -> None:
    """Raise if `service_id` belongs to the wrong vehicle lane for `category`.

    A moto service paired with a car category (or vice versa) is rejected
    before any DB write. The static catalog lists (`SERVICES_CAR`,
    `SERVICES_MOTO`) are the source of truth for lane membership; admin
    pricing overrides don't change which list a service lives in.

    Parameters
    ----------
    service_id : str
        Service id from the catalog (e.g., "svc_cpl", "svc_moto").
    category : str
        Vehicle category — "A" / "B" / "C" for cars, "MOTO" for two-wheels.

    Raises
    ------
    UnknownService : if `service_id` is not in either static list.
    InvalidServiceForCategory : if the lanes don't match.
    """
    if service_id in _CAR_SERVICE_IDS:
        service_lane = "car"
    elif service_id in _MOTO_SERVICE_IDS:
        service_lane = "moto"
    else:
        log.info(
            "validation.rejection service=%s category=%s reason=unknown_service",
            service_id,
            category,
        )
        raise UnknownService(f"service_id={service_id} not found")

    expected_lane = "moto" if category == "MOTO" else "car"
    if service_lane != expected_lane:
        log.info(
            "validation.rejection service=%s category=%s reason=service_category_mismatch",
            service_id,
            category,
        )
        raise InvalidServiceForCategory(
            f"service={service_id} requires lane={service_lane}, "
            f"but category={category} is lane={expected_lane}"
        )


def _to_casablanca(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise TypeError("now must be timezone-aware")
    return now.astimezone(CASABLANCA_TZ)


def _business_lead_cutoff(
    now_local: datetime,
    *,
    working_hours: int,
    engine: Engine | None = None,
) -> datetime:
    """Return ``now_local`` + N operational hours, skipping closed dates.

    Home appointments need four *working* hours of notice. Ewash's customer
    service window follows the active slot grid (09:00–22:00) and closed dates;
    hours outside that window do not count toward the lead time.
    """
    closed_dates = set(active_closed_dates(engine=engine))
    cursor = now_local.astimezone(CASABLANCA_TZ)
    remaining = timedelta(hours=working_hours)
    guard_days = 0

    while remaining > timedelta(0):
        current_date = cursor.date()
        current_iso = current_date.isoformat()
        day_start = datetime(
            current_date.year,
            current_date.month,
            current_date.day,
            WORKING_DAY_START_HOUR,
            0,
            tzinfo=CASABLANCA_TZ,
        )
        day_end = datetime(
            current_date.year,
            current_date.month,
            current_date.day,
            WORKING_DAY_END_HOUR,
            0,
            tzinfo=CASABLANCA_TZ,
        )

        if current_iso in closed_dates or cursor >= day_end:
            cursor = day_start + timedelta(days=1)
            guard_days += 1
            if guard_days > 370:
                raise RuntimeError("unable to compute working-hours lead cutoff")
            continue
        if cursor < day_start:
            cursor = day_start

        available = day_end - cursor
        if available >= remaining:
            return cursor + remaining
        remaining -= available
        cursor = day_start + timedelta(days=1)
        guard_days += 1
        if guard_days > 370:
            raise RuntimeError("unable to compute working-hours lead cutoff")

    return cursor


def minimum_slot_start(
    *,
    now: datetime | None = None,
    location_kind: str | None = None,
    engine: Engine | None = None,
) -> datetime:
    """Earliest allowed appointment start for a location kind.

    Center bookings keep the historical 2h wall-clock freshness rule. Home
    bookings require 4 Ewash working hours (09:00–22:00, skipping closed dates).
    """
    if now is None:
        now_local = datetime.now(tz=CASABLANCA_TZ)
    else:
        now_local = _to_casablanca(now)

    if location_kind == "home":
        return _business_lead_cutoff(
            now_local,
            working_hours=HOME_MIN_LEAD_WORKING_HOURS,
            engine=engine,
        )
    return now_local + timedelta(hours=MIN_LEAD_HOURS)


def validate_slot_and_date(
    date_iso: str,
    slot_id: str,
    *,
    now: datetime | None = None,
    engine: Engine | None = None,
    location_kind: str | None = None,
) -> None:
    """Reject closed dates, unknown slots, and slots before the allowed cutoff.

    Center appointments keep the historical 2-hour wall-clock freshness rule.
    Home appointments require 4 Ewash working hours (09:00–22:00, skipping
    closed dates) before the slot start. This is the server-side source of
    truth — the PWA's client-side filtering is decorative and bypassable.

    Parameters
    ----------
    date_iso : str
        ISO date string (YYYY-MM-DD).
    slot_id : str
        Slot identifier from the catalog (e.g., "slot_9_11").
    now : datetime, optional
        For tests, pin the clock to a specific Africa/Casablanca instant.
        Must be tz-aware. Defaults to the wall-clock time.
    engine : Engine, optional
        Override the SQLAlchemy engine used to load closed dates / slots.

    Raises
    ------
    ClosedDate : if `date_iso` is in the active closed-date set.
    UnknownSlot : if `slot_id` is not an active slot.
    InvalidDate : if `date_iso` cannot be parsed as YYYY-MM-DD.
    SlotTooSoon : if the slot starts before the computed lead cutoff.
    """
    if now is None:
        now_local = datetime.now(tz=CASABLANCA_TZ)
    else:
        now_local = _to_casablanca(now)

    if date_iso in active_closed_dates(engine=engine):
        raise ClosedDate(f"date={date_iso} is in active_closed_dates")

    active_slot_ids = {entry[0] for entry in active_time_slots(engine=engine)}
    if slot_id not in active_slot_ids:
        raise UnknownSlot(f"slot_id={slot_id} not active")

    match = _SLOT_ID_PATTERN.match(slot_id)
    if match is None:
        raise UnknownSlot(f"slot_id={slot_id} doesn't match expected pattern")
    start_hour = int(match.group(1))

    try:
        appointment_date = datetime.strptime(date_iso, "%Y-%m-%d").date()
    except ValueError as exc:
        raise InvalidDate(f"date={date_iso} not parseable") from exc

    candidate = datetime(
        appointment_date.year,
        appointment_date.month,
        appointment_date.day,
        start_hour,
        0,
        tzinfo=CASABLANCA_TZ,
    )

    cutoff = minimum_slot_start(
        now=now_local,
        location_kind=location_kind,
        engine=engine,
    )
    if candidate < cutoff:
        log.info(
            "slot_too_soon: candidate=%s now=%s cutoff=%s location_kind=%s",
            candidate.isoformat(),
            now_local.isoformat(),
            cutoff.isoformat(),
            location_kind or "center",
        )
        raise SlotTooSoon(
            f"slot={slot_id} on date={date_iso} starts {candidate.isoformat()}, "
            f"before cutoff={cutoff.isoformat()} for location_kind={location_kind or 'center'}"
        )


@overload
def clean_text(value: None, *, max_len: int) -> None: ...
@overload
def clean_text(value: str, *, max_len: int) -> str | None: ...


def clean_text(value: str | None, *, max_len: int) -> str | None:
    """Sanitize a free-text user input field defensively.

    Applies four passes, in order:

    1. Strip ASCII/Unicode control characters (Unicode category ``Cc``), with
       ``\\n`` deliberately preserved so multi-line customer notes survive
       ("Sonner deux fois\\nÉtage 3, porte gauche").
    2. Collapse runs of horizontal whitespace (spaces and tabs) into a single
       space. Newlines pass through untouched.
    3. Trim leading and trailing whitespace.
    4. Truncate the result to ``max_len`` characters.

    Returns ``None`` for ``None`` input, and also ``None`` if the cleaned
    string is empty (e.g., input was just whitespace or control characters).
    This contract lets callers distinguish "user explicitly typed something"
    from "user left the field blank" without juggling empty strings.

    The Pydantic schemas in :mod:`app.api_schemas` already enforce
    ``max_length`` at the request boundary; calling :func:`clean_text`
    afterwards is a belt-and-suspenders defense for downstream code paths
    (logging, persistence, staff alert text) where surprising control chars
    would otherwise leak through.
    """
    if value is None:
        return None
    no_controls = "".join(
        ch for ch in value if ch == "\n" or unicodedata.category(ch) != "Cc"
    )
    collapsed = _HORIZONTAL_WHITESPACE_RUN.sub(" ", no_controls)
    trimmed = collapsed.strip()
    if not trimmed:
        return None
    return trimmed[:max_len]


def validate_addon_ids(addon_ids: list[str], *, main_service_id: str) -> list[str]:
    """Return ``addon_ids`` if every id is a known detailing-bucket service.

    Addons in the PWA booking flow are upsells from the ``ESTHÉTIQUE`` /
    detailing bucket (Polishing, Ceramic, Renovation, Lustre …). Wash-bucket
    or moto-bucket service ids are rejected — they belong in the main
    ``service_id`` field, not as addons.

    Detailing services are also valid main services (``SERVICES_CAR`` =
    ``SERVICES_WASH + SERVICES_DETAILING`` in :mod:`app.catalog`). Without an
    extra check, a payload like ``{service_id: "svc_pol", addon_ids:
    ["svc_pol"]}`` would pass both this validator and
    :func:`validate_service_for_category` independently and then persist two
    ``BookingLineItemRow`` rows — one as ``kind=main`` at full price, one as
    ``kind=addon`` at 20% off — double-charging the customer. Reject early.

    Parameters
    ----------
    addon_ids : list[str]
        Service ids from the catalog (e.g., ``["svc_cuir", "svc_plastq"]``).
        An empty list is valid (the customer didn't pick an upsell).
    main_service_id : str
        The booking's main ``service_id``. An addon that equals it is
        rejected as ``DuplicateAddon`` (see above).

    Returns
    -------
    list[str]
        The same list, unchanged, when every id passes.

    Raises
    ------
    DuplicateAddon : if the same id is listed twice, OR if any id equals
        ``main_service_id``.
    UnknownAddon : if an id is not in any static service catalog.
    NotADetailingService : if the id exists but lives in the wash or moto
        bucket — it cannot be used as an addon.
    """
    seen: set[str] = set()
    for addon_id in addon_ids:
        if addon_id == main_service_id:
            log.info(
                "validation.rejection addon_id=%s reason=duplicate_addon equals_main_service_id",
                addon_id,
            )
            raise DuplicateAddon(
                f"addon_id={addon_id} equals main service_id; "
                "the same service cannot be billed as both the main item and an addon"
            )
        if addon_id in seen:
            log.info(
                "validation.rejection addon_id=%s reason=duplicate_addon",
                addon_id,
            )
            raise DuplicateAddon(f"addon_id={addon_id} listed twice")
        seen.add(addon_id)

        if addon_id in _DETAILING_SERVICE_IDS:
            continue
        if addon_id in _CAR_SERVICE_IDS or addon_id in _MOTO_SERVICE_IDS:
            bucket = "moto" if addon_id in _MOTO_SERVICE_IDS else "wash"
            log.info(
                "validation.rejection addon_id=%s bucket=%s reason=not_a_detailing_service",
                addon_id,
                bucket,
            )
            raise NotADetailingService(
                f"addon_id={addon_id} is in bucket={bucket}, addons must be detailing services"
            )
        log.info(
            "validation.rejection addon_id=%s reason=unknown_addon",
            addon_id,
        )
        raise UnknownAddon(f"addon_id={addon_id} not found")

    return addon_ids


def validate_center_id(center_id: str | None, *, location_kind: str) -> None:
    """Cross-check ``center_id`` against ``location_kind`` and the active centers.

    A booking with ``location.kind == "center"`` must carry a ``center_id`` that
    matches one of the rows from :func:`catalog.active_centers`. A booking with
    ``location.kind == "home"`` must NOT carry a ``center_id`` — including one
    silently widens the contract and lets a tampered PWA backstop a home pin
    with a center label, which would confuse the staff alert.

    Parameters
    ----------
    center_id : str | None
        Caller-supplied center id (may be empty / None for home bookings).
    location_kind : str
        ``"home"`` or ``"center"`` — already validated upstream by Pydantic.

    Raises
    ------
    CenterIdNotAllowed : ``location_kind="home"`` but a ``center_id`` was sent.
    MissingCenterId : ``location_kind="center"`` but no ``center_id`` was sent.
    UnknownCenter : ``center_id`` is not in :func:`catalog.active_centers`.
    """
    if location_kind != "center":
        if center_id is not None:
            log.info(
                "validation.rejection center_id=%s location_kind=%s reason=center_id_not_allowed",
                center_id,
                location_kind,
            )
            raise CenterIdNotAllowed(
                f"center_id={center_id!r} sent but location.kind={location_kind!r}"
            )
        return
    if not center_id:
        log.info(
            "validation.rejection center_id=None location_kind=center reason=missing_center_id"
        )
        raise MissingCenterId("location.kind=center requires a center_id")
    active_ids = {cid for cid, *_ in active_centers()}
    if center_id not in active_ids:
        log.info(
            "validation.rejection center_id=%s reason=unknown_center",
            center_id,
        )
        raise UnknownCenter(f"center_id={center_id} not active")
