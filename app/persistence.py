"""Persistence service for confirmed WhatsApp bookings.

The WhatsApp flow still owns the deterministic customer experience. This module is
an adapter that mirrors confirmed in-memory Booking objects into the v0.3 CRM DB
so the admin dashboard can show real operational data.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, and_, delete, func, or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from .booking import Booking, all_bookings
from .admin_i18n import t as admin_t
from .config import settings
from .db import init_db, make_engine, session_scope
from .models import (
    BookingLineItemRow,
    BookingReminderRow,
    BookingRefCounterRow,
    BookingRow,
    BookingStatusEventRow,
    ConversationEventRow,
    ConversationSessionRow,
    Customer,
    CustomerName,
    CustomerTokenRow,
    CustomerVehicle,
    DataErasureAuditRow,
    ReminderRuleRow,
    WhatsappMessageRow,
    VehicleColor,
    VehicleModel,
)
from .security import generate_token, hash_token

log = logging.getLogger(__name__)
H2_REMINDER_KIND = "H-2"
H2_REMINDER_OFFSET_MINUTES = 120
REMINDER_CLAIM_LEASE_MINUTES = 5
CONVERSATION_ABANDON_AFTER_SECONDS = 60 * 60 * 2


class BookingLockBusy(RuntimeError):
    """Raised when a booking row is already locked by another admin action."""


@dataclass(frozen=True)
class RecentBooking:
    ref: str
    customer_name: str
    service_label: str
    status: str
    source: str = "whatsapp"


@dataclass(frozen=True)
class AdminBookingListItem:
    ref: str
    customer_name: str
    customer_phone: str
    vehicle_label: str
    service_label: str
    addon_service_label: str
    status: str
    date_label: str
    slot: str
    location_label: str
    price_dh: int
    source: str = "whatsapp"


@dataclass(frozen=True)
class AdminCustomerListItem:
    phone: str
    display_name: str
    booking_count: int
    vehicle_labels: tuple[str, ...]
    last_bot_stage: str = ""
    last_bot_stage_label: str = ""
    conversation_status: str = ""


@dataclass(frozen=True)
class ReturningCustomerProfile:
    phone: str
    display_name: str
    vehicle_label: str
    category: str
    model: str
    color: str


@dataclass(frozen=True)
class DashboardSummary:
    total_bookings: int = 0
    confirmed_bookings: int = 0
    awaiting_confirmation: int = 0
    pending_ewash_confirmation: int = 0
    customers: int = 0
    abandoned_conversations: int = 0
    pending_reminders: int = 0
    recent_bookings: tuple[RecentBooking, ...] = ()
    db_available: bool = False
    # Per-channel booking counts over the trailing 7 days (Africa/Casablanca).
    # Defaults to zero so the dashboard renders cleanly before any data exists.
    bookings_pwa_last_7d: int = 0
    bookings_whatsapp_last_7d: int = 0
    bookings_admin_last_7d: int = 0


# Lock-protected singleton replaces @lru_cache(maxsize=1). lru_cache is thread-safe
# for the lookup but not for the body — two threads on the very first call would
# both run init_db (Base.metadata.create_all + _seed_service_catalog + the
# backfills), colliding on duplicate-key INSERTs and raising IntegrityError on
# the loser. Since lru_cache does not memoize exceptions, the next call would
# race again. The lock-protected double-checked pattern below runs init_db
# exactly once on success and leaves the singleton unset on failure so the next
# caller retries — matching lru_cache's exception behavior, eliminating the race.
_engine_lock = threading.Lock()
_engine: Engine | None = None
_engine_initialized = False


def _configured_engine() -> Engine | None:
    global _engine, _engine_initialized
    if _engine_initialized:
        return _engine
    with _engine_lock:
        if _engine_initialized:
            return _engine
        if not settings.database_url:
            _engine = None
            _engine_initialized = True
            return None
        engine = make_engine(settings.database_url)
        init_db(engine)
        _engine = engine
        _engine_initialized = True
        return _engine


def _reset_configured_engine() -> None:
    global _engine, _engine_initialized
    with _engine_lock:
        _engine = None
        _engine_initialized = False


# Preserve the @lru_cache.cache_clear() surface so the 50+ tests that call
# ``_configured_engine.cache_clear()`` directly continue to work unchanged.
_configured_engine.cache_clear = _reset_configured_engine  # type: ignore[attr-defined]


def _engine_or_configured(engine: Engine | None = None) -> Engine | None:
    if engine is not None:
        return engine
    return _configured_engine()


def _now() -> datetime:
    return datetime.now(timezone.utc)


_BOT_STAGE_LABELS = {
    "IDLE": "Hors parcours",
    "RETURNING_CUSTOMER": "Confirmation client connu",
    "MENU": "Menu principal affiché",
    "HANDOFF": "Message à l'équipe",
    "BOOK_NAME": "Saisie du nom",
    "BOOK_VEHICLE": "Choix du véhicule",
    "BOOK_MODEL": "Saisie du modèle",
    "BOOK_COLOR": "Saisie de la couleur",
    "BOOK_WHERE": "Choix du lieu",
    "BOOK_CENTER": "Choix du stand Ewash",
    "BOOK_GEO": "Partage de localisation",
    "BOOK_ADDRESS": "Saisie de l'adresse",
    "BOOK_PROMO_ASK": "Question code promo",
    "BOOK_PROMO_CODE": "Saisie code promo",
    "BOOK_SERVICE": "Liste des prix affichée",
    "BOOK_WHEN": "Choix de la date",
    "BOOK_SLOT": "Choix du créneau",
    "BOOK_NOTE": "Question note client",
    "BOOK_NOTE_TEXT": "Saisie note client",
    "BOOK_CONFIRM": "Récap envoyé — attente client",
    "UPSELL_DETAILING": "Offre esthétique affichée",
    "UPSELL_DETAILING_PICK": "Liste upsell affichée",
}


def bot_stage_label(stage: str) -> str:
    return _BOT_STAGE_LABELS.get(stage or "", stage or "—")


_REF_RE = re.compile(r"^EW-(\d{4})-(\d+)$")


def _max_ref_counter(refs: Iterable[str], *, year: int) -> int:
    max_counter = 0
    for ref in refs:
        match = _REF_RE.match(ref or "")
        if not match or int(match.group(1)) != year:
            continue
        max_counter = max(max_counter, int(match.group(2)))
    return max_counter


def _booking_ref_counter_insert_stmt(dialect_name: str, *, year: int, existing_floor: int):
    table = BookingRefCounterRow.__table__
    values = {"year": year, "last_counter": existing_floor}
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=["year"],
        )
    if dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=["year"],
        )
    return None


def _ensure_booking_ref_counter_row(session, *, year: int, existing_floor: int) -> None:
    """Create the yearly ref-counter row without racing another first writer."""
    dialect_name = session.get_bind().dialect.name
    stmt = _booking_ref_counter_insert_stmt(
        dialect_name,
        year=year,
        existing_floor=existing_floor,
    )
    if stmt is not None:
        session.execute(stmt)
        session.flush()
        return

    if session.get(BookingRefCounterRow, year, with_for_update=True) is None:
        session.add(BookingRefCounterRow(year=year, last_counter=existing_floor))
        session.flush()


def _next_booking_ref_counter(session, *, year: int) -> int:
    refs = session.scalars(
        select(BookingRow.ref).where(BookingRow.ref.like(f"EW-{year}-%"))
    ).all()
    existing_floor = _max_ref_counter(refs, year=year)
    _ensure_booking_ref_counter_row(
        session,
        year=year,
        existing_floor=existing_floor,
    )

    dialect_name = session.get_bind().dialect.name
    floor_expression = (
        func.greatest(BookingRefCounterRow.last_counter, existing_floor)
        if dialect_name == "postgresql"
        else func.max(BookingRefCounterRow.last_counter, existing_floor)
    )
    next_counter = session.scalar(
        update(BookingRefCounterRow)
        .where(BookingRefCounterRow.year == year)
        .values(last_counter=floor_expression + 1)
        .returning(BookingRefCounterRow.last_counter)
    )
    if next_counter is not None:
        session.flush()
        return int(next_counter)

    counter = session.get(BookingRefCounterRow, year, with_for_update=True)
    if counter is None:
        counter = BookingRefCounterRow(year=year, last_counter=existing_floor)
        session.add(counter)
        session.flush()
    if counter.last_counter < existing_floor:
        counter.last_counter = existing_floor
    counter.last_counter += 1
    session.flush()
    return int(counter.last_counter)


def assign_booking_ref(
    booking: Booking,
    *,
    engine: Engine | None = None,
    session: Session | None = None,
    record_shadow: bool = True,
) -> str:
    """Assign a booking reference that is monotonic against persisted refs.

    The in-memory Booking counter resets on each Railway redeploy, while
    Postgres keeps old refs. Before confirming a booking, seed the in-memory
    counter from the DB max so a fresh process does not reuse EW-YYYY-0001 and
    get ignored as an existing persisted row.
    """
    if session is not None:
        year = _now().year
        next_counter = _next_booking_ref_counter(session, year=year)
        return booking.assign_ref(counter_value=next_counter, record_shadow=record_shadow)

    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return booking.assign_ref(record_shadow=record_shadow)

    year = _now().year
    try:
        with session_scope(db_engine) as session:
            next_counter = _next_booking_ref_counter(session, year=year)
    except Exception:
        log.exception("assign_booking_ref failed to reserve DB ref; falling back to process counter")
        return booking.assign_ref(record_shadow=record_shadow)
    return booking.assign_ref(counter_value=next_counter, record_shadow=record_shadow)


def _vehicle_label(booking: Booking) -> str:
    if booking.category == "MOTO":
        return booking.vehicle_type or "Moto"
    parts = [booking.car_model.strip(), booking.color.strip()]
    label = " — ".join(p for p in parts if p)
    return label or booking.vehicle_type or "Véhicule"


def _normalize_vehicle_value(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _find_or_create_vehicle_model(session, *, category: str, model: str) -> VehicleModel | None:
    normalized = _normalize_vehicle_value(model)
    if not normalized:
        return None
    existing = session.scalars(
        select(VehicleModel).where(
            VehicleModel.category == category,
            VehicleModel.normalized_name == normalized,
        )
    ).first()
    if existing is not None:
        existing.last_seen_at = _now()
        return existing
    row = VehicleModel(category=category, name=model.strip(), normalized_name=normalized, active=True, last_seen_at=_now())
    session.add(row)
    session.flush()
    return row


def _find_or_create_vehicle_color(session, *, color: str) -> VehicleColor | None:
    normalized = _normalize_vehicle_value(color)
    if not normalized:
        return None
    existing = session.scalars(
        select(VehicleColor).where(VehicleColor.normalized_name == normalized)
    ).first()
    if existing is not None:
        existing.last_seen_at = _now()
        return existing
    row = VehicleColor(name=color.strip(), normalized_name=normalized, active=True, last_seen_at=_now())
    session.add(row)
    session.flush()
    return row


def _find_or_create_customer(session, booking: Booking) -> Customer:
    customer = session.get(Customer, booking.phone)
    if customer is None:
        customer = Customer(phone=booking.phone, display_name=booking.name or "")
        session.add(customer)
        session.flush()
    elif booking.name:
        customer.display_name = booking.name
    customer.last_seen_at = _now()
    customer.booking_count = (customer.booking_count or 0) + 1
    if booking.name:
        _upsert_customer_name(session, customer, booking.name)
    return customer


def _normalize_customer_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _upsert_customer_name(session, customer: Customer, display_name: str) -> CustomerName | None:
    cleaned = re.sub(r"\s+", " ", (display_name or "").strip())[:120]
    normalized = _normalize_customer_name(cleaned)
    if not normalized:
        return None
    row = session.scalars(
        select(CustomerName).where(
            CustomerName.customer_phone == customer.phone,
            CustomerName.normalized_name == normalized,
        )
    ).first()
    if row is None:
        row = CustomerName(
            customer_phone=customer.phone,
            display_name=cleaned,
            normalized_name=normalized,
            last_used_at=_now(),
        )
        session.add(row)
        session.flush()
    else:
        row.display_name = cleaned
        row.last_used_at = _now()
    customer.display_name = cleaned
    customer.last_seen_at = _now()
    return row


def persist_customer_name(
    phone: str,
    display_name: str,
    *,
    engine: Engine | None = None,
    session: Session | None = None,
) -> CustomerName | None:
    """Record a name used by a phone number and mark it as the latest one."""
    phone = str(phone or "").strip()
    cleaned = re.sub(r"\s+", " ", (display_name or "").strip())[:120]
    if not phone or not cleaned:
        return None
    if session is not None:
        customer = session.get(Customer, phone)
        if customer is None:
            customer = Customer(phone=phone, display_name=cleaned)
            session.add(customer)
            session.flush()
        row = _upsert_customer_name(session, customer, cleaned)
        session.flush()
        return row
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return None
    with session_scope(db_engine) as db_session:
        customer = db_session.get(Customer, phone)
        if customer is None:
            customer = Customer(phone=phone, display_name=cleaned)
            db_session.add(customer)
            db_session.flush()
        row = _upsert_customer_name(db_session, customer, cleaned)
        db_session.flush()
        if row is None:
            return None
        db_session.expunge(row)
        return row


def _contact_profile_name(contact: dict | None) -> str:
    if not contact:
        return ""
    profile = contact.get("profile") or {}
    return str(profile.get("name") or "").strip()


def _contact_wa_id(contact: dict | None) -> str:
    if not contact:
        return ""
    return str(contact.get("wa_id") or "").strip()


def persist_customer_contact(
    phone: str,
    contact: dict | None = None,
    *,
    engine: Engine | None = None,
) -> Customer | None:
    """Create/update a customer row immediately from Meta's contact envelope."""
    phone = str(phone or "").strip()
    if not phone:
        return None
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return None

    profile_name = _contact_profile_name(contact)
    wa_id = _contact_wa_id(contact) or phone
    with session_scope(db_engine) as session:
        customer = session.get(Customer, phone)
        if customer is None:
            customer = Customer(
                phone=phone,
                display_name=profile_name,
                whatsapp_profile_name=profile_name,
                whatsapp_wa_id=wa_id,
            )
            session.add(customer)
            session.flush()
        else:
            if profile_name:
                customer.whatsapp_profile_name = profile_name
                if not customer.display_name:
                    customer.display_name = profile_name
            if wa_id:
                customer.whatsapp_wa_id = wa_id
        customer.last_seen_at = _now()
        session.flush()
        session.expunge(customer)
        return customer


def persist_whatsapp_inbound_message(message: dict, contact: dict | None = None, *, engine: Engine | None = None) -> bool:
    """Insert an inbound WhatsApp message once.

    Returns False when the message id already exists, allowing webhook retries
    to be acknowledged without running the booking state machine twice.
    """
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return True
    persist_customer_contact(str(message.get("from") or ""), contact, engine=db_engine)
    message_id = str(message.get("id") or "").strip()
    if not message_id:
        return True
    payload = {"message": message, "contact": contact or {}}
    try:
        with session_scope(db_engine) as session:
            session.add(
                WhatsappMessageRow(
                    message_id=message_id,
                    phone=str(message.get("from") or ""),
                    direction="inbound",
                    message_type=str(message.get("type") or ""),
                    payload_json=json.dumps(payload, ensure_ascii=False, default=str),
                    status="processed",
                    processed_at=_now(),
                )
            )
    except IntegrityError:
        return False
    return True


def persist_customer_bot_stage(
    phone: str,
    stage: str,
    *,
    display_name: str = "",
    engine: Engine | None = None,
) -> Customer | None:
    """Persist the latest WhatsApp funnel stage for a phone number.

    This intentionally creates a customer row before confirmation so abandoned
    leads still show where they dropped in the admin portal.
    """
    db_engine = _engine_or_configured(engine)
    if db_engine is None or not phone:
        return None

    label = bot_stage_label(stage)
    with session_scope(db_engine) as session:
        customer = session.get(Customer, phone)
        if customer is None:
            customer = Customer(phone=phone, display_name=display_name or "")
            session.add(customer)
            session.flush()
        elif display_name and not customer.display_name:
            customer.display_name = display_name
        customer.last_seen_at = _now()
        customer.last_bot_stage = stage or ""
        customer.last_bot_stage_label = label
        customer.last_bot_stage_at = _now()
        conversation = session.scalars(
            select(ConversationSessionRow).where(
                ConversationSessionRow.customer_phone == phone,
                ConversationSessionRow.status == "open",
            ).order_by(ConversationSessionRow.last_event_at.desc())
        ).first()
        if conversation is None:
            conversation = ConversationSessionRow(
                customer_phone=phone,
                status="open",
                current_stage=stage or "",
                last_event_at=_now(),
            )
            session.add(conversation)
            session.flush()
        else:
            conversation.current_stage = stage or ""
            conversation.last_event_at = _now()
        session.add(
            ConversationEventRow(
                session_id=conversation.id,
                customer_phone=phone,
                stage=stage or "",
                stage_label=label,
                event_type="stage_seen",
            )
        )
        session.flush()
        session.expunge(customer)
        return customer


def _abandoned_label(stage: str) -> str:
    return f"Abandonné - {bot_stage_label(stage)}"


def mark_abandoned_conversations(
    *,
    stale_after_seconds: int = CONVERSATION_ABANDON_AFTER_SECONDS,
    now: datetime | None = None,
    engine: Engine | None = None,
) -> int:
    """Mark inactive open conversation sessions as abandoned leads."""
    if stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be positive")
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return 0

    current_time = now or _now()
    cutoff = current_time - timedelta(seconds=stale_after_seconds)
    count = 0
    with session_scope(db_engine) as session:
        conversations = session.scalars(
            select(ConversationSessionRow).where(
                ConversationSessionRow.status == "open",
                ConversationSessionRow.last_event_at <= cutoff,
            )
        ).all()
        for conversation in conversations:
            stage = conversation.current_stage or ""
            conversation.status = "abandoned"
            conversation.last_event_at = current_time
            customer = session.get(Customer, conversation.customer_phone)
            if customer is not None:
                customer.last_seen_at = current_time
                customer.last_bot_stage = stage
                customer.last_bot_stage_label = _abandoned_label(stage)
                customer.last_bot_stage_at = current_time
            session.add(
                ConversationEventRow(
                    session_id=conversation.id,
                    customer_phone=conversation.customer_phone,
                    stage=stage,
                    stage_label=bot_stage_label(stage),
                    event_type="abandoned",
                    payload_json=json.dumps(
                        {
                            "reason": "inactivity",
                            "stale_after_seconds": stale_after_seconds,
                        },
                        ensure_ascii=False,
                    ),
                    created_at=current_time,
                )
            )
            count += 1
    return count


def _find_or_create_vehicle(session, booking: Booking) -> CustomerVehicle | None:
    if not booking.phone:
        return None
    model = "" if booking.category == "MOTO" else (booking.car_model or "").strip()
    color = "" if booking.category == "MOTO" else (booking.color or "").strip()
    category = booking.category or ""
    vehicle_model = _find_or_create_vehicle_model(session, category=category, model=model) if model else None
    vehicle_color = _find_or_create_vehicle_color(session, color=color) if color else None
    conditions = [
        CustomerVehicle.customer_phone == booking.phone,
        CustomerVehicle.category == category,
        CustomerVehicle.active.is_(True),
    ]
    if vehicle_model is not None:
        conditions.append(or_(CustomerVehicle.model_id == vehicle_model.id, CustomerVehicle.model == model))
    else:
        conditions.append(CustomerVehicle.model == model)
    if vehicle_color is not None:
        conditions.append(or_(CustomerVehicle.color_id == vehicle_color.id, CustomerVehicle.color == color))
    else:
        conditions.append(CustomerVehicle.color == color)
    existing = session.scalars(select(CustomerVehicle).where(*conditions)).first()
    if existing is not None:
        existing.model_id = existing.model_id or (vehicle_model.id if vehicle_model else None)
        existing.color_id = existing.color_id or (vehicle_color.id if vehicle_color else None)
        existing.label = existing.label or _vehicle_label(booking)
        existing.last_used_at = _now()
        return existing

    vehicle = CustomerVehicle(
        customer_phone=booking.phone,
        category=category,
        model_id=vehicle_model.id if vehicle_model else None,
        color_id=vehicle_color.id if vehicle_color else None,
        model=model,
        color=color,
        label=_vehicle_label(booking),
        active=True,
        last_used_at=_now(),
    )
    session.add(vehicle)
    session.flush()
    return vehicle


def _ensure_customer_identity(session, *, phone: str, display_name: str = "") -> Customer:
    customer = session.get(Customer, phone)
    if customer is None:
        customer = Customer(phone=phone, display_name=display_name or "")
        session.add(customer)
        session.flush()
    elif display_name:
        customer.display_name = display_name
    customer.last_seen_at = _now()
    if display_name:
        _upsert_customer_name(session, customer, display_name)
    return customer


def persist_booking_identity(
    booking: Booking,
    *,
    engine: Engine | None = None,
) -> CustomerVehicle | None:
    """Persist the latest customer name and vehicle before booking confirmation."""
    if not booking.phone:
        return None
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return None
    category = booking.category or ""
    if not category:
        if booking.name:
            persist_customer_name(booking.phone, booking.name, engine=db_engine)
        return None
    if category != "MOTO" and not ((booking.car_model or "").strip() and (booking.color or "").strip()):
        if booking.name:
            persist_customer_name(booking.phone, booking.name, engine=db_engine)
        return None

    with session_scope(db_engine) as session:
        _ensure_customer_identity(session, phone=booking.phone, display_name=booking.name or "")
        vehicle = _find_or_create_vehicle(session, booking)
        session.flush()
        if vehicle is None:
            return None
        session.expunge(vehicle)
        return vehicle


def get_returning_customer_profile(
    phone: str,
    *,
    engine: Engine | None = None,
) -> ReturningCustomerProfile | None:
    """Return the latest known name and active vehicle for a phone number."""
    phone = str(phone or "").strip()
    if not phone:
        return None
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return None
    with session_scope(db_engine) as session:
        customer = session.get(Customer, phone)
        if customer is None:
            return None
        name_row = session.scalars(
            select(CustomerName)
            .where(CustomerName.customer_phone == phone)
            .order_by(CustomerName.last_used_at.desc(), CustomerName.id.desc())
        ).first()
        vehicle = session.scalars(
            select(CustomerVehicle)
            .where(
                CustomerVehicle.customer_phone == phone,
                CustomerVehicle.active.is_(True),
            )
            .order_by(CustomerVehicle.last_used_at.desc(), CustomerVehicle.id.desc())
        ).first()
        if vehicle is None:
            return None
        label = vehicle.label or " — ".join(part for part in (vehicle.model, vehicle.color) if part) or vehicle.category
        return ReturningCustomerProfile(
            phone=phone,
            display_name=(
                (name_row.display_name if name_row is not None else "")
                or customer.display_name
                or customer.whatsapp_profile_name
                or ""
            ),
            vehicle_label=label,
            category=vehicle.category or "",
            model=vehicle.model or "",
            color=vehicle.color or "",
        )


def _slot_hours(slot_id: str, slot_label: str) -> tuple[int, int] | None:
    match = re.match(r"^slot_(\d{1,2})_(\d{1,2})$", slot_id or "")
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"(\d{1,2})h?\s*[–-]\s*(\d{1,2})h?", slot_label or "")
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _appointment_bounds(booking: Booking) -> tuple[date | None, datetime | None, datetime | None]:
    if not booking.date_iso:
        return None, None, None
    try:
        appointment_date = date.fromisoformat(booking.date_iso)
    except ValueError:
        return None, None, None
    hours = _slot_hours(booking.slot_id, booking.slot)
    if hours is None:
        return appointment_date, None, None
    start_hour, end_hour = hours
    tz = ZoneInfo("Africa/Casablanca")
    start_at = datetime.combine(appointment_date, time(hour=start_hour), tzinfo=tz)
    end_at = datetime.combine(appointment_date, time(hour=end_hour), tzinfo=tz)
    return appointment_date, start_at, end_at


def _add_booking_line_items(session, row: BookingRow, booking: Booking) -> None:
    if booking.service:
        session.add(
            BookingLineItemRow(
                booking_id=row.id,
                kind="main",
                service_id=booking.service,
                service_bucket=booking.service_bucket,
                label_snapshot=booking.service_label or booking.service,
                quantity=1,
                unit_price_dh=booking.price_dh or 0,
                regular_price_dh=booking.price_regular_dh or booking.price_dh or 0,
                total_price_dh=booking.price_dh or 0,
                discount_label=booking.promo_label or "",
                sort_order=0,
            )
        )
    if booking.addon_service:
        session.add(
            BookingLineItemRow(
                booking_id=row.id,
                kind="addon",
                service_id=booking.addon_service,
                service_bucket="detailing",
                label_snapshot=booking.addon_service_label or booking.addon_service,
                quantity=1,
                unit_price_dh=booking.addon_price_dh or 0,
                regular_price_dh=booking.addon_price_dh or 0,
                total_price_dh=booking.addon_price_dh or 0,
                discount_label="-10%",
                sort_order=10,
            )
        )


def _persist_confirmed_booking_in_session(
    session: Session,
    booking: Booking,
    *,
    source: str = "whatsapp",
) -> BookingRow | None:
    existing = session.scalars(select(BookingRow).where(BookingRow.ref == booking.ref)).first()
    if existing is not None:
        return existing

    customer = _find_or_create_customer(session, booking)
    vehicle = _find_or_create_vehicle(session, booking)
    appointment_date, appointment_start_at, appointment_end_at = _appointment_bounds(booking)
    row = BookingRow(
        ref=booking.ref,
        customer_phone=customer.phone,
        customer_vehicle_id=vehicle.id if vehicle else None,
        status="pending_ewash_confirmation",
        customer_name=booking.name,
        vehicle_type=booking.vehicle_type,
        car_model=booking.car_model,
        color=booking.color,
        service_id=booking.service,
        service_bucket=booking.service_bucket,
        service_label=booking.service_label,
        price_dh=booking.price_dh,
        price_regular_dh=booking.price_regular_dh,
        promo_code=booking.promo_code,
        promo_label=booking.promo_label,
        location_mode=booking.location_mode,
        center=booking.center,
        center_id=booking.center_id,
        geo=booking.geo,
        address=booking.address,
        address_text=booking.address,
        location_name=booking.location_name,
        location_address=booking.location_address,
        latitude=booking.latitude,
        longitude=booking.longitude,
        date_label=booking.date_label,
        slot=booking.slot,
        appointment_date=appointment_date,
        slot_id=booking.slot_id,
        note=booking.note,
        addon_service=booking.addon_service,
        addon_service_label=booking.addon_service_label,
        addon_price_dh=booking.addon_price_dh,
        total_price_dh=(booking.price_dh or 0) + (booking.addon_price_dh or 0),
        appointment_start_at=appointment_start_at,
        appointment_end_at=appointment_end_at,
        client_request_id=booking.client_request_id,
        source=source,
        raw_booking_json=json.dumps(asdict(booking), ensure_ascii=False, default=str),
    )
    session.add(row)
    session.flush()
    _add_booking_line_items(session, row, booking)
    session.add(
        BookingStatusEventRow(
            booking_id=row.id,
            from_status="draft",
            to_status="pending_ewash_confirmation",
            actor="customer",
            note="Confirmation PWA" if source == "api" else "Confirmation WhatsApp",
        )
    )
    session.flush()
    return row


def persist_confirmed_booking(
    booking: Booking,
    *,
    source: str = "whatsapp",
    engine: Engine | None = None,
    session: Session | None = None,
) -> BookingRow | None:
    """Mirror a confirmed booking into the CRM database.

    Returns the persisted BookingRow when a DB is configured. If the app is run
    without DATABASE_URL (local demos/tests that do not pass an engine), this is
    a safe no-op so the WhatsApp flow keeps working.

    `source` records the origin channel for split-counter admin queries and the
    source-badge admin UI. The default `"whatsapp"` keeps every existing caller
    working without code changes; the `/api/v1/bookings` handler passes
    `source="api"`, and admin-created bookings pass `source="admin"`. The
    Postgres CHECK constraint (migration 0006) rejects unknown values; Pydantic
    validation in the API layer should reject them earlier.
    """
    if not booking.ref:
        booking.assign_ref()

    if session is not None:
        return _persist_confirmed_booking_in_session(session, booking, source=source)

    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        log.info("persist_confirmed_booking skipped: DATABASE_URL not configured ref=%s", booking.ref)
        return None

    with session_scope(db_engine) as db_session:
        row = _persist_confirmed_booking_in_session(db_session, booking, source=source)
        if row is not None:
            db_session.expunge(row)
        return row


def persist_booking_addon(
    ref: str,
    *,
    addon_service: str,
    addon_service_label: str,
    addon_price_dh: int,
    regular_price_dh: int | None = None,
    discount_label: str = "-10%",
    denormalize_to_legacy: bool = True,
    engine: Engine | None = None,
    session: Session | None = None,
) -> None:
    """Attach a single addon to an existing booking row.

    Two modes via ``denormalize_to_legacy``:

    * ``True`` (default — WhatsApp single-addon flow): writes the addon to the
      legacy ``bookings.addon_*`` columns AND upserts a single line item with
      ``kind="addon"``. Re-picking an addon updates the existing row. Total is
      ``price_dh + addon_price_dh``.

    * ``False`` (PWA multi-addon flow, calls 2+): leaves the legacy columns
      alone (they already hold the first addon) and APPENDS a new line item.
      Total accumulates: ``total_price_dh += addon_price_dh``.

    The legacy ``bookings.addon_*`` columns are kept only for the staff alert
    template (which reads a single addon) and historical denormalization. The
    line-item table is the authoritative multi-addon record.
    """
    if not ref:
        return
    addon_regular_price_dh = (
        regular_price_dh if regular_price_dh is not None else addon_price_dh
    )
    if session is not None:
        row = session.scalars(select(BookingRow).where(BookingRow.ref == ref)).first()
        if row is None:
            log.warning("persist_booking_addon: ref=%s not found", ref)
            return
        if denormalize_to_legacy:
            row.addon_service = addon_service
            row.addon_service_label = addon_service_label
            row.addon_price_dh = addon_price_dh
            row.total_price_dh = (row.price_dh or 0) + (row.addon_price_dh or 0)
            existing = session.scalars(
                select(BookingLineItemRow).where(
                    BookingLineItemRow.booking_id == row.id,
                    BookingLineItemRow.kind == "addon",
                )
            ).first()
            if existing is None:
                session.add(
                    BookingLineItemRow(
                        booking_id=row.id,
                        kind="addon",
                        service_id=addon_service,
                        service_bucket="detailing",
                        label_snapshot=addon_service_label,
                        quantity=1,
                        unit_price_dh=addon_price_dh,
                        regular_price_dh=addon_regular_price_dh,
                        total_price_dh=addon_price_dh,
                        discount_label=discount_label,
                        sort_order=10,
                    )
                )
            else:
                existing.service_id = addon_service
                existing.label_snapshot = addon_service_label
                existing.unit_price_dh = addon_price_dh
                existing.regular_price_dh = addon_regular_price_dh
                existing.total_price_dh = addon_price_dh
                existing.discount_label = discount_label
            return
        # Multi-addon append path: leave legacy columns + first line item
        # untouched and accumulate total + a new line item per call.
        current_max_sort = session.scalar(
            select(func.max(BookingLineItemRow.sort_order)).where(
                BookingLineItemRow.booking_id == row.id,
            )
        )
        next_sort = (current_max_sort or 0) + 10
        row.total_price_dh = (row.total_price_dh or row.price_dh or 0) + addon_price_dh
        session.add(
            BookingLineItemRow(
                booking_id=row.id,
                kind="addon",
                service_id=addon_service,
                service_bucket="detailing",
                label_snapshot=addon_service_label,
                quantity=1,
                unit_price_dh=addon_price_dh,
                regular_price_dh=addon_regular_price_dh,
                total_price_dh=addon_price_dh,
                discount_label=discount_label,
                sort_order=next_sort,
            )
        )
        return

    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return
    with session_scope(db_engine) as db_session:
        persist_booking_addon(
            ref,
            addon_service=addon_service,
            addon_service_label=addon_service_label,
            addon_price_dh=addon_price_dh,
            regular_price_dh=regular_price_dh,
            discount_label=discount_label,
            denormalize_to_legacy=denormalize_to_legacy,
            session=db_session,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _h2_reminder_rule(session) -> ReminderRuleRow | None:
    exact = session.scalars(
        select(ReminderRuleRow).where(ReminderRuleRow.name == H2_REMINDER_KIND)
    ).first()
    if exact is not None:
        return exact
    return session.scalars(
        select(ReminderRuleRow)
        .where(
            ReminderRuleRow.enabled.is_(True),
            ReminderRuleRow.offset_minutes_before == H2_REMINDER_OFFSET_MINUTES,
        )
        .order_by(ReminderRuleRow.id.desc())
    ).first()


def _booking_has_h2_reminder(session, booking_id: int) -> bool:
    existing = session.scalars(
        select(BookingReminderRow)
        .outerjoin(ReminderRuleRow, BookingReminderRow.rule_id == ReminderRuleRow.id)
        .where(
            BookingReminderRow.booking_id == booking_id,
            or_(
                BookingReminderRow.kind == H2_REMINDER_KIND,
                ReminderRuleRow.offset_minutes_before == H2_REMINDER_OFFSET_MINUTES,
            ),
        )
        .limit(1)
    ).first()
    return existing is not None


def _is_nowait_lock_error(exc: DBAPIError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = (getattr(orig, "sqlstate", "") or getattr(orig, "pgcode", "") or "").upper()
    if sqlstate == "55P03":
        return True
    text = " ".join(
        str(part).lower()
        for part in (exc, orig)
        if part is not None
    )
    return any(
        marker in text
        for marker in (
            "could not obtain lock",
            "lock not available",
            "nowait",
            "database is locked",
            "database table is locked",
        )
    )


def _create_h2_reminder_for_confirmed_booking(
    session,
    booking: BookingRow,
    *,
    now: datetime | None = None,
) -> bool:
    if booking.id is None or booking.appointment_start_at is None:
        return False
    if _booking_has_h2_reminder(session, int(booking.id)):
        return False

    scheduled_for = booking.appointment_start_at - timedelta(minutes=H2_REMINDER_OFFSET_MINUTES)
    if _as_utc(scheduled_for) <= _as_utc(now or _now()):
        return False

    rule = _h2_reminder_rule(session)
    session.add(
        BookingReminderRow(
            booking_id=int(booking.id),
            rule_id=rule.id if rule is not None else None,
            kind=H2_REMINDER_KIND,
            scheduled_for=scheduled_for,
            status="pending",
        )
    )
    return True


def confirm_booking_by_ewash(
    ref: str,
    *,
    engine: Engine | None = None,
    now: datetime | None = None,
) -> BookingRow:
    """Mark a customer-confirmed booking as accepted by Ewash staff."""
    normalized_ref = (ref or "").strip()
    if not normalized_ref:
        raise ValueError("Référence réservation manquante")

    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        raise RuntimeError("DATABASE_URL is not configured")

    with session_scope(db_engine) as session:
        try:
            row = session.scalars(
                select(BookingRow).where(BookingRow.ref == normalized_ref).with_for_update(nowait=True)
            ).first()
        except DBAPIError as exc:
            if _is_nowait_lock_error(exc):
                raise BookingLockBusy(
                    f"Réservation {normalized_ref} déjà en cours de confirmation"
                ) from exc
            raise
        if row is None:
            raise ValueError(f"Réservation introuvable: {normalized_ref}")
        if row.status != "pending_ewash_confirmation":
            raise ValueError(f"La réservation {normalized_ref} n'est pas à confirmer par eWash")

        previous_status = row.status
        row.status = "confirmed"
        session.add(
            BookingStatusEventRow(
                booking_id=row.id,
                from_status=previous_status,
                to_status="confirmed",
                actor="admin",
                note="Confirmation eWash depuis le portail admin",
            )
        )
        reminder_created = _create_h2_reminder_for_confirmed_booking(session, row, now=now)
        log.info("booking confirmed by ewash ref=%s reminder_created=%s", row.ref, reminder_created)
        session.flush()
        session.expunge(row)
        return row


@dataclass(frozen=True)
class ReminderDispatchCandidate:
    """Snapshot of a reminder row claimed for dispatch, decoupled from the ORM.

    Built by :func:`claim_next_due_reminder` after the row is atomically claimed
    (attempt_count incremented, sent_at stamped) and the session has closed, so
    the caller can issue the Meta API call without holding a transaction open.
    """
    reminder_id: int
    booking_ref: str
    customer_phone: str
    customer_name: str
    vehicle_label: str
    service_label: str
    location_label: str
    date_label: str
    slot: str
    template_name: str
    template_language: str
    kind: str
    scheduled_for: datetime
    attempt_count: int
    max_sends: int


def _reminder_vehicle_label(row: BookingRow) -> str:
    parts = [str(row.car_model or "").strip(), str(row.color or "").strip()]
    label = " — ".join(p for p in parts if p)
    return label or str(row.vehicle_type or "").strip() or "Véhicule"


def _reminder_location_label(row: BookingRow) -> str:
    if row.location_mode == "center":
        return str(row.center or row.location_name or "Stand Ewash").strip()
    return str(row.address or row.location_address or row.geo or "").strip() or "-"


def claim_next_due_reminder(
    *,
    now: datetime | None = None,
    engine: Engine | None = None,
    exclude_ids: Iterable[int] | None = None,
) -> ReminderDispatchCandidate | None:
    """Atomically claim the next pending reminder due for dispatch.

    Picks the oldest eligible row (status in ``pending``/``failed``, booking
    still ``confirmed``, ``scheduled_for <= now``) and stamps it with an
    incremented ``attempt_count`` plus ``sent_at = now`` to act as an
    in-flight marker. Concurrent dispatch invocations race safely:

    - Postgres uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so a second caller
      reading at the same instant skips the row another worker just locked.
    - Once committed, the row's ``sent_at`` is non-null. Later sweeps only
      consider fresh ``pending`` rows where ``sent_at IS NULL``, stale pending
      rows whose claim lease has elapsed, or ``failed`` rows with sends
      remaining; failed rows still inside their cooldown are skipped so they
      cannot hide later eligible reminders in the queue.

    ``exclude_ids`` is the in-memory set of rows the caller has already
    handled in the current dispatch loop — passed in so a single endpoint
    invocation never retries a row it just failed (retries happen on the
    *next* cron firing, per ewash-b8w).

    Returns ``None`` when no rows are eligible. The caller is expected to
    loop on this helper until it returns ``None`` (or it hits a batch cap).
    """
    current = _as_utc(now or _now())
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return None

    skip_set = {int(rid) for rid in exclude_ids} if exclude_ids else set()
    stale_claim_before = current - timedelta(minutes=REMINDER_CLAIM_LEASE_MINUTES)
    with session_scope(db_engine) as session:
        while True:
            max_sends_expr = func.coalesce(ReminderRuleRow.max_sends, 1)
            attempts_expr = func.coalesce(BookingReminderRow.attempt_count, 0)
            stmt = (
                select(BookingReminderRow)
                .join(BookingRow, BookingReminderRow.booking_id == BookingRow.id)
                .outerjoin(ReminderRuleRow, BookingReminderRow.rule_id == ReminderRuleRow.id)
                .where(
                    BookingReminderRow.scheduled_for <= current,
                    BookingRow.status == "confirmed",
                    or_(
                        and_(
                            BookingReminderRow.status == "pending",
                            or_(
                                BookingReminderRow.sent_at.is_(None),
                                BookingReminderRow.sent_at <= stale_claim_before,
                            ),
                        ),
                        and_(
                            BookingReminderRow.status == "failed",
                            attempts_expr < max_sends_expr,
                        ),
                    ),
                )
                .order_by(BookingReminderRow.scheduled_for.asc(), BookingReminderRow.id.asc())
                .limit(1)
            )
            if skip_set:
                stmt = stmt.where(BookingReminderRow.id.notin_(skip_set))
            if db_engine.dialect.name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)

            row = session.scalars(stmt).first()
            if row is None:
                return None

            rule = row.rule
            max_sends = int(rule.max_sends) if rule is not None and rule.max_sends else 1
            min_minutes = int(rule.min_minutes_between_sends) if rule is not None and rule.min_minutes_between_sends else 0

            if (row.attempt_count or 0) >= max_sends:
                row.status = "failed"
                if not row.error:
                    row.error = "max_sends exhausted"
                skip_set.add(int(row.id))
                continue

            if row.status == "failed" and row.sent_at is not None and min_minutes > 0:
                cooldown_end = _as_utc(row.sent_at) + timedelta(minutes=min_minutes)
                if cooldown_end > current:
                    skip_set.add(int(row.id))
                    continue

            booking = row.booking
            row.attempt_count = (row.attempt_count or 0) + 1
            row.sent_at = current
            if row.status == "failed":
                row.status = "pending"

            template_name = (
                rule.template_name.strip()
                if rule is not None and rule.template_name and rule.template_name.strip()
                else "booking_reminder_h2"
            )
            return ReminderDispatchCandidate(
                reminder_id=int(row.id),
                booking_ref=str(booking.ref or ""),
                customer_phone=str(booking.customer_phone or ""),
                customer_name=str(booking.customer_name or ""),
                vehicle_label=_reminder_vehicle_label(booking),
                service_label=str(booking.service_label or booking.service_id or "").strip(),
                location_label=_reminder_location_label(booking),
                date_label=str(booking.date_label or "").strip(),
                slot=str(booking.slot or "").strip(),
                template_name=template_name,
                template_language="fr",
                kind=str(row.kind or ""),
                scheduled_for=_as_utc(row.scheduled_for),
                attempt_count=int(row.attempt_count),
                max_sends=max_sends,
            )


def mark_reminder_sent(
    reminder_id: int,
    *,
    now: datetime | None = None,
    engine: Engine | None = None,
) -> None:
    """Mark a previously-claimed reminder row as successfully delivered.

    Called by ``reminders._send_one`` ONLY after ``meta.send_template``
    returned 200 — at that point Meta has accepted the message and the
    customer is going to receive (or has received) the WhatsApp. The row
    MUST therefore move out of ``pending`` regardless of whether the
    booking flipped to a cancelled status mid-flight; leaving it
    ``pending`` would let the stale-claim recovery branch in
    :func:`claim_next_due_reminder` re-pick the row after the lease
    elapses and double-send if the booking is later re-confirmed
    (ewash-44j). When the booking *was* cancelled between the Meta
    success and this call we still stamp ``status='sent'`` and record
    the blocking booking status in ``error`` so the operator audit
    trail reflects the unusual lifecycle.
    """
    current = _as_utc(now or _now())
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return
    with session_scope(db_engine) as session:
        row = session.get(BookingReminderRow, reminder_id)
        if row is None:
            return
        if row.status != "pending":
            return
        booking_status = str(row.booking.status or "") if row.booking is not None else ""
        row.status = "sent"
        row.sent_at = current
        row.error = (
            ""
            if booking_status == "confirmed"
            else f"sent_after_status_change:{booking_status or 'missing'}"
        )


def skip_reminder_if_booking_not_sendable(
    reminder_id: int,
    *,
    allowed_booking_statuses: Iterable[str] = ("confirmed",),
    engine: Engine | None = None,
) -> str | None:
    """Cancel/skip an in-flight reminder when its booking left sendable status.

    Returns the blocking reason when the reminder was moved out of the retry
    pool; returns ``None`` when the booking is still eligible to receive the
    reminder.
    """
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return None
    allowed = {status for status in allowed_booking_statuses if status}
    with session_scope(db_engine) as session:
        row = session.get(BookingReminderRow, reminder_id)
        if row is None:
            return "missing_reminder"
        if row.status != "pending":
            return f"reminder_status:{row.status}"
        booking_status = str(row.booking.status or "") if row.booking is not None else ""
        if booking_status in allowed:
            return None
        row.status = "cancelled" if booking_status in {"customer_cancelled", "admin_cancelled"} else "skipped"
        row.error = f"booking_status:{booking_status or 'missing'}"
        return row.error


def mark_reminder_failed(
    reminder_id: int,
    *,
    error: str,
    engine: Engine | None = None,
) -> None:
    """Mark a previously-claimed reminder row as failed.

    Sets status='failed' unconditionally; the next dispatch sweep re-evaluates
    eligibility based on ``attempt_count < rule.max_sends`` and the cooldown.
    """
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return
    with session_scope(db_engine) as session:
        row = session.get(BookingReminderRow, reminder_id)
        if row is None:
            return
        if row.status != "pending" or row.booking.status != "confirmed":
            return
        row.status = "failed"
        row.error = (error or "")[:2000]


def _booking_dict_to_admin_item(row: dict) -> AdminBookingListItem:
    vehicle_label = " — ".join(str(row.get(part, "")).strip() for part in ("car_model", "color") if str(row.get(part, "")).strip())
    if not vehicle_label:
        vehicle_label = str(row.get("vehicle_type") or "")
    location_mode = str(row.get("location_mode") or "")
    location_label = str(row.get("center") or "") if location_mode == "center" else str(row.get("address") or row.get("geo") or location_mode)
    total_price_dh = int(row.get("price_dh") or 0) + int(row.get("addon_price_dh") or 0)
    return AdminBookingListItem(
        ref=str(row.get("ref") or ""),
        customer_name=str(row.get("name") or row.get("phone") or ""),
        customer_phone=str(row.get("phone") or ""),
        vehicle_label=vehicle_label,
        service_label=str(row.get("service_label") or row.get("service") or ""),
        addon_service_label=str(row.get("addon_service_label") or ""),
        status="pending_ewash_confirmation" if row.get("ref") else "draft",
        date_label=str(row.get("date_label") or ""),
        slot=str(row.get("slot") or ""),
        location_label=location_label,
        price_dh=total_price_dh,
        source=str(row.get("source") or "whatsapp"),
    )


def _memory_booking_items(limit: int = 100) -> tuple[AdminBookingListItem, ...]:
    return tuple(_booking_dict_to_admin_item(row) for row in reversed(all_bookings()[-limit:]))


def _memory_customer_items(limit: int = 100) -> tuple[AdminCustomerListItem, ...]:
    grouped: dict[str, dict] = {}
    for row in all_bookings():
        phone = str(row.get("phone") or "")
        if not phone:
            continue
        item = grouped.setdefault(
            phone,
            {"display_name": str(row.get("name") or phone), "booking_count": 0, "vehicle_labels": set()},
        )
        item["booking_count"] += 1
        vehicle_label = " — ".join(str(row.get(part, "")).strip() for part in ("car_model", "color") if str(row.get(part, "")).strip())
        if not vehicle_label:
            vehicle_label = str(row.get("vehicle_type") or "")
        if vehicle_label:
            item["vehicle_labels"].add(vehicle_label)
    return tuple(
        AdminCustomerListItem(
            phone=phone,
            display_name=data["display_name"],
            booking_count=data["booking_count"],
            vehicle_labels=tuple(sorted(data["vehicle_labels"])),
        )
        for phone, data in list(grouped.items())[:limit]
    )


def admin_booking_list(*, engine: Engine | None = None, limit: int = 100) -> tuple[AdminBookingListItem, ...]:
    memory_items = _memory_booking_items(limit)
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return memory_items
    try:
        with session_scope(db_engine) as session:
            rows = session.scalars(
                select(BookingRow).order_by(BookingRow.created_at.desc()).limit(limit)
            ).all()
            items: list[AdminBookingListItem] = []
            for row in rows:
                vehicle_label = " — ".join(part for part in (row.car_model, row.color) if part) or row.vehicle_type
                location_label = row.center if row.location_mode == "center" else (row.address or row.geo or row.location_mode)
                line_items = list(row.line_items or [])
                main_line = next((item for item in line_items if item.kind == "main"), None)
                addon_labels = [item.label_snapshot for item in line_items if item.kind == "addon" and item.label_snapshot]
                addon_label = " + ".join(addon_labels) or row.addon_service_label or ""
                total_price_dh = sum(item.total_price_dh or 0 for item in line_items) if line_items else ((row.price_dh or 0) + (row.addon_price_dh or 0))
                items.append(
                    AdminBookingListItem(
                        ref=row.ref,
                        customer_name=row.customer_name or row.customer_phone,
                        customer_phone=row.customer_phone,
                        vehicle_label=vehicle_label,
                        service_label=(main_line.label_snapshot if main_line else "") or row.service_label or row.service_id,
                        addon_service_label=addon_label,
                        status=row.status,
                        date_label=row.date_label,
                        slot=row.slot,
                        location_label=location_label,
                        price_dh=total_price_dh,
                        source=getattr(row, "source", None) or "whatsapp",
                    )
                )
            db_refs = {item.ref for item in items if item.ref}
            items.extend(item for item in memory_items if item.ref not in db_refs)
            return tuple(items[:limit])
    except Exception:
        log.exception("admin_booking_list failed; falling back to live memory")
        return memory_items


def admin_customer_list(*, engine: Engine | None = None, limit: int = 100) -> tuple[AdminCustomerListItem, ...]:
    memory_items = _memory_customer_items(limit)
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return memory_items
    try:
        with session_scope(db_engine) as session:
            customers = session.scalars(
                select(Customer).order_by(Customer.last_seen_at.desc()).limit(limit)
            ).all()
            items: list[AdminCustomerListItem] = []
            for customer in customers:
                vehicles = session.scalars(
                    select(CustomerVehicle).where(
                        CustomerVehicle.customer_phone == customer.phone,
                        CustomerVehicle.active.is_(True),
                    )
                ).all()
                labels = tuple(vehicle.label or " — ".join(part for part in (vehicle.model, vehicle.color) if part) for vehicle in vehicles)
                conversation = session.scalars(
                    select(ConversationSessionRow)
                    .where(ConversationSessionRow.customer_phone == customer.phone)
                    .order_by(ConversationSessionRow.last_event_at.desc(), ConversationSessionRow.id.desc())
                ).first()
                conversation_status = conversation.status if conversation is not None else ""
                stage_label = customer.last_bot_stage_label or bot_stage_label(customer.last_bot_stage or "")
                if conversation_status == "abandoned" and not stage_label.startswith("Abandonné"):
                    stage_label = _abandoned_label(customer.last_bot_stage or (conversation.current_stage if conversation else ""))
                items.append(
                    AdminCustomerListItem(
                        phone=customer.phone,
                        display_name=customer.display_name or customer.whatsapp_profile_name or customer.phone,
                        booking_count=customer.booking_count or 0,
                        vehicle_labels=tuple(label for label in labels if label),
                        last_bot_stage=customer.last_bot_stage or "",
                        last_bot_stage_label=stage_label,
                        conversation_status=conversation_status,
                    )
                )
            db_phones = {item.phone for item in items if item.phone}
            items.extend(item for item in memory_items if item.phone not in db_phones)
            return tuple(items[:limit])
    except Exception:
        log.exception("admin_customer_list failed; falling back to live memory")
        return memory_items


def admin_dashboard_summary(*, engine: Engine | None = None, recent_limit: int = 5) -> DashboardSummary:
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return DashboardSummary()

    try:
        with session_scope(db_engine) as session:
            total = session.scalar(select(func.count()).select_from(BookingRow)) or 0
            confirmed = session.scalar(
                select(func.count()).select_from(BookingRow).where(BookingRow.status == "confirmed")
            ) or 0
            awaiting = session.scalar(
                select(func.count()).select_from(BookingRow).where(BookingRow.status == "awaiting_confirmation")
            ) or 0
            pending_ewash = session.scalar(
                select(func.count()).select_from(BookingRow).where(BookingRow.status == "pending_ewash_confirmation")
            ) or 0
            customers = session.scalar(select(func.count()).select_from(Customer)) or 0
            reminders = session.scalar(
                select(func.count()).select_from(BookingReminderRow).where(BookingReminderRow.status == "pending")
            ) or 0
            abandoned = session.scalar(
                select(func.count()).select_from(ConversationSessionRow).where(ConversationSessionRow.status == "abandoned")
            ) or 0
            rows = session.scalars(
                select(BookingRow).order_by(BookingRow.created_at.desc()).limit(recent_limit)
            ).all()
            recent = tuple(
                RecentBooking(
                    ref=row.ref,
                    customer_name=row.customer_name or row.customer_phone,
                    service_label=row.service_label or row.service_id,
                    status=row.status,
                    source=getattr(row, "source", None) or "whatsapp",
                )
                for row in rows
            )
            # Per-channel counts over the trailing 7 days. Africa/Casablanca
            # boundary so the counter doesn't tick over at midnight UTC.
            seven_days_ago = datetime.now(tz=ZoneInfo("Africa/Casablanca")) - timedelta(days=7)
            try:
                source_rows = session.execute(
                    select(BookingRow.source, func.count(BookingRow.id))
                    .where(BookingRow.created_at >= seven_days_ago)
                    .group_by(BookingRow.source)
                ).all()
                source_counts = {row[0] or "whatsapp": int(row[1] or 0) for row in source_rows}
            except Exception:
                # Migration 0006 adds the column on Postgres; SQLite test
                # tables also carry it via Base.metadata.create_all. If
                # somehow the column is missing (mid-deploy), fall back to
                # zeros instead of breaking the whole dashboard.
                log.exception("admin_dashboard_summary failed to aggregate sources")
                source_counts = {}
            return DashboardSummary(
                total_bookings=total,
                confirmed_bookings=confirmed,
                awaiting_confirmation=awaiting,
                pending_ewash_confirmation=pending_ewash,
                customers=customers,
                abandoned_conversations=abandoned,
                pending_reminders=reminders,
                recent_bookings=recent,
                db_available=True,
                bookings_pwa_last_7d=source_counts.get("api", 0),
                bookings_whatsapp_last_7d=source_counts.get("whatsapp", 0),
                bookings_admin_last_7d=source_counts.get("admin", 0),
            )
    except Exception:
        log.exception("admin_dashboard_summary failed")
        return DashboardSummary()


# ── Customer tokens (PWA opaque session auth) ───────────────────────────────


def mint_customer_token(
    customer_phone: str,
    *,
    engine: Engine | None = None,
) -> str:
    """Mint a fresh opaque token for the customer and persist its SHA-256.

    Returns the plaintext exactly once. The PWA stores it in localStorage;
    subsequent `GET /api/v1/bookings` calls submit it via `X-Ewash-Token`.
    A DB dump never yields an active token because only the hash is stored.

    Multiple tokens per phone are allowed by design — every fresh booking
    that doesn't carry an existing token mints a new one. Idempotent replays
    should echo a caller-provided valid token instead of calling this helper
    again and creating avoidable orphan rows.

    If no engine is configured (DB-absent test paths), the plaintext is
    still returned so the API response shape stays valid; the PWA's
    follow-up read will then 401 since nothing was persisted.
    """
    plaintext, digest = generate_token()
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return plaintext
    with session_scope(db_engine) as session:
        session.add(CustomerTokenRow(
            token_hash=digest,
            customer_phone=customer_phone,
        ))
    return plaintext


def verify_customer_token(
    plaintext: str | None,
    *,
    expected_phone: str | None = None,
    engine: Engine | None = None,
) -> str | None:
    """Look up the customer phone owning `plaintext`, or None.

    Hashes the input and queries `customer_tokens.token_hash` (indexed,
    unique). If `expected_phone` is provided, the row's `customer_phone`
    must match — an attacker who stole a token cannot use it to attribute
    new bookings to someone else's phone.

    Side effect on a successful match: `last_used_at` is bumped to now, so
    a future admin revocation pass can identify stale tokens.

    Timing-attack note: SQL `=` against an indexed column is O(1) and
    doesn't leak byte-by-byte timing the way a Python string compare can.
    """
    if not plaintext:
        return None
    digest = hash_token(plaintext)
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return None
    with session_scope(db_engine) as session:
        row = session.scalar(
            select(CustomerTokenRow).where(CustomerTokenRow.token_hash == digest)
        )
        if row is None:
            return None
        if expected_phone is not None and row.customer_phone != expected_phone:
            return None
        row.last_used_at = datetime.now(timezone.utc)
        return row.customer_phone


def revoke_token_by_hash(
    token_hash: str,
    *,
    engine: Engine | None = None,
) -> int:
    """Delete one customer_tokens row matching the SHA-256 hash.

    Returns the number of rows deleted (0 or 1). The caller is expected to have
    already hashed the plaintext via `security.hash_token` — accepting the hash
    directly keeps the plaintext out of this function's parameters so it does
    not appear in tracebacks or string-formatted log lines.
    """
    if not token_hash:
        return 0
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return 0
    with session_scope(db_engine) as session:
        result = session.execute(
            delete(CustomerTokenRow).where(CustomerTokenRow.token_hash == token_hash)
        )
        return int(result.rowcount or 0)


def revoke_all_tokens_for_phone(
    customer_phone: str,
    *,
    engine: Engine | None = None,
) -> int:
    """Delete every customer_tokens row belonging to a phone. Returns the count.

    Used by the "panic" logout scope when a customer reports a lost phone — all
    tokens are revoked across every device. The customer's next booking mints
    a fresh token from scratch.
    """
    if not customer_phone:
        return 0
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return 0
    with session_scope(db_engine) as session:
        result = session.execute(
            delete(CustomerTokenRow).where(
                CustomerTokenRow.customer_phone == customer_phone
            )
        )
        return int(result.rowcount or 0)


_CUSTOMER_BOOKING_LIST_LIMIT_MAX = 100


def list_bookings_for_token(
    token_plaintext: str | None,
    *,
    limit: int = 20,
    cursor: str | None = None,
    engine: Engine | None = None,
) -> tuple[list[dict], str | None]:
    """Return the customer-safe recent-booking view for a PWA token bearer."""
    if not token_plaintext:
        return [], None
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return [], None
    phone = verify_customer_token(token_plaintext, engine=db_engine)
    if phone is None:
        return [], None

    bounded_limit = max(0, min(int(limit), _CUSTOMER_BOOKING_LIST_LIMIT_MAX))
    if bounded_limit == 0:
        return [], None
    cursor_anchor = _decode_customer_bookings_cursor(cursor)
    with session_scope(db_engine) as session:
        stmt = select(BookingRow).where(BookingRow.customer_phone == phone)
        if cursor_anchor is not None:
            cursor_created_at, cursor_id = cursor_anchor
            stmt = stmt.where(
                or_(
                    BookingRow.created_at < cursor_created_at,
                    and_(
                        BookingRow.created_at == cursor_created_at,
                        BookingRow.id < cursor_id,
                    ),
                )
            )
        rows = session.scalars(
            stmt
            .order_by(BookingRow.created_at.desc(), BookingRow.id.desc())
            .limit(bounded_limit + 1)
        ).all()
        page_rows = rows[:bounded_limit]
        next_cursor = (
            _encode_customer_bookings_cursor(page_rows[-1])
            if len(rows) > bounded_limit and page_rows
            else None
        )
        return [_to_customer_view(row) for row in page_rows], next_cursor


@dataclass(frozen=True)
class CustomerVehicleHistoryItem:
    """Token-scoped view of a customer's previously-used vehicle.

    Fed back to the PWA on the booking flow's first step so returning
    customers can tap a card instead of re-typing make/color. Only fields
    safe to expose to the device are included — no internal IDs, no
    timestamps beyond `last_used_at` for ordering.
    """

    category: str  # A | B | C | MOTO
    make: str  # normalized model string the customer typed previously
    color: str  # normalized color string (empty for moto)
    label: str  # pre-rendered display string ("Audi Q7 — Blanc")
    last_used_at: datetime | None


def list_customer_vehicles_for_token(
    token_plaintext: str | None,
    *,
    limit: int = 10,
    engine: Engine | None = None,
) -> list[CustomerVehicleHistoryItem]:
    """Return the active customer_vehicles rows owned by the token bearer.

    Pattern mirrors `list_bookings_for_token`: token → phone via
    `verify_customer_token` (which also bumps last_used_at on the token
    row), then a single query against customer_vehicles ordered by
    last_used_at DESC. No `?phone=` parameter exists at the route level,
    so phone enumeration is mechanically impossible.

    The query returns at most `limit` rows; the table is already deduped
    by (customer_phone, category, model, color) via _find_or_create_vehicle
    so no further dedup is required here.
    """
    if not token_plaintext:
        return []
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return []
    phone = verify_customer_token(token_plaintext, engine=db_engine)
    if phone is None:
        return []

    bounded_limit = max(0, min(int(limit), 20))
    if bounded_limit == 0:
        return []

    with session_scope(db_engine) as session:
        rows = session.scalars(
            select(CustomerVehicle)
            .where(
                CustomerVehicle.customer_phone == phone,
                CustomerVehicle.active.is_(True),
            )
            .order_by(
                CustomerVehicle.last_used_at.desc().nullslast(),
                CustomerVehicle.id.desc(),
            )
            .limit(bounded_limit)
        ).all()
        return [
            CustomerVehicleHistoryItem(
                category=(row.category or "").upper(),
                make=row.model or "",
                color=row.color or "",
                label=row.label or "",
                last_used_at=row.last_used_at,
            )
            for row in rows
        ]


def _encode_customer_bookings_cursor(row: BookingRow) -> str:
    created_at = row.created_at.isoformat() if row.created_at else ""
    payload = f"{created_at}|{row.id}"
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_customer_bookings_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        created_at_raw, row_id_raw = decoded.rsplit("|", 1)
        created_at = datetime.fromisoformat(created_at_raw)
        row_id = int(row_id_raw)
    except Exception as exc:
        raise ValueError("invalid customer bookings cursor") from exc
    if row_id < 1:
        raise ValueError("invalid customer bookings cursor")
    return created_at, row_id


def _to_customer_view(row: BookingRow) -> dict:
    """Project a booking row to fields safe for the customer API."""
    slot_start_hour, slot_end_hour = _customer_booking_slot_hours(row.slot_id or "")
    return {
        "ref": row.ref,
        "status": row.status,
        "status_label": admin_t(f"status.{row.status}", "fr"),
        "service_label": row.service_label or row.service_id or "",
        "service_id": row.service_id or "",
        "vehicle_label": row.vehicle_type or "",
        "date_iso": row.appointment_date.isoformat() if row.appointment_date else "",
        "date_label": row.date_label or "",
        "slot_id": row.slot_id or "",
        "slot_label": row.slot or "",
        "slot_start_hour": slot_start_hour,
        "slot_end_hour": slot_end_hour,
        "location_label": _customer_booking_location_label(row),
        "total_price_dh": row.total_price_dh or row.price_dh or 0,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def _customer_booking_slot_hours(slot_id: str) -> tuple[int, int]:
    match = re.match(r"^slot_(\d+)_(\d+)$", slot_id or "")
    if match is None:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def _customer_booking_location_label(row: BookingRow) -> str:
    if row.location_mode == "home":
        return "À domicile"
    return row.center or row.location_name or "Au stand"


def find_booking_by_client_request_id(
    client_request_id: str | None,
    *,
    session=None,
    engine: Engine | None = None,
) -> BookingRow | None:
    """Look up a booking by its idempotency key.

    Returns the matching BookingRow or None. On Postgres the partial unique
    index `ix_bookings_client_request_id_partial` makes this an indexed lookup;
    on SQLite (tests) the column has no index and the scan is small enough that
    it doesn't matter.

    Callers fall into two shapes:

    * **Inside an existing transaction** — the API handler that's about to
      `persist_confirmed_booking` first does the lookup. Pass `session=<sess>`
      so we don't open a second transaction (which would deadlock on Postgres
      under heavy concurrency).
    * **Standalone** — pass nothing; we resolve the configured engine and
      open our own short-lived session.

    Returns None for empty / None input so callers can pipe the request body
    straight in without a None check.
    """
    if not client_request_id:
        return None

    if session is not None:
        return session.scalar(
            select(BookingRow).where(BookingRow.client_request_id == client_request_id)
        )

    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return None
    with session_scope(db_engine) as sess:
        row = sess.scalar(
            select(BookingRow).where(BookingRow.client_request_id == client_request_id)
        )
        if row is not None:
            sess.expunge(row)
        return row


def anonymize_customer(
    customer_phone: str,
    *,
    actor: str = "customer_self_serve",
    notes: str = "",
    engine: Engine | None = None,
) -> dict:
    """Self-serve data erasure (Loi 09-08 / GDPR right-to-erasure).

    Deletes every customer-side row (tokens, names, vehicles, conversation
    sessions) and anonymizes the customer's bookings in-place: phone replaced
    with ``DEL-<sha256[:12]>``, identifying fields scrubbed, but the row
    itself preserved so admin views still see the slot was booked. Writes an
    append-only audit entry to ``data_erasure_audit`` for compliance reporting.

    Returns ``{"deleted_count": int, "anonymized_bookings": int}``. A configured
    engine that resolves to ``None`` (e.g. test path without DATABASE_URL)
    yields zeros — the caller treats that as "nothing to delete".

    Dialect notes
    -------------
    * On Postgres (production), migration 0006 declares
      ``ON UPDATE CASCADE`` on ``bookings.customer_phone``. Renaming the
      ``customers.phone`` primary key automatically rewrites the matching
      booking rows.
    * On SQLite (tests), FK actions are not enforced and CASCADE is unavailable.
      The helper follows the customer-rename with an explicit
      ``UPDATE bookings SET customer_phone = <anon>`` so both dialects converge
      on the same final state.

    The audit row uses the full SHA-256 hex digest (64 chars) rather than the
    truncated 12-char prefix used for the FK pivot — the audit log is meant to
    survive standalone, and the longer digest stays collision-resistant for
    long-tail historical reporting.
    """
    if not customer_phone:
        return {"deleted_count": 0, "anonymized_bookings": 0}
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return {"deleted_count": 0, "anonymized_bookings": 0}

    phone_hash_full = hashlib.sha256(customer_phone.encode("utf-8")).hexdigest()
    anon_phone = "DEL-" + phone_hash_full[:12]

    with session_scope(db_engine) as session:
        deleted = 0
        target_session_ids = select(ConversationSessionRow.id).where(
            ConversationSessionRow.customer_phone == customer_phone
        )
        event_result = session.execute(
            delete(ConversationEventRow).where(
                or_(
                    ConversationEventRow.customer_phone == customer_phone,
                    ConversationEventRow.session_id.in_(target_session_ids),
                )
            )
        )
        deleted += int(event_result.rowcount or 0)
        # bookings.customer_vehicle_id FK has no ON DELETE CASCADE, so the
        # customer_vehicles delete below would raise ForeignKeyViolation on
        # Postgres if a booking still pointed at the vehicle. Null the
        # reference first; the booking row itself is preserved and scrubbed
        # of PII further down. SQLite test paths don't enforce FKs and so
        # quietly tolerated the wrong ordering until prod tripped it.
        session.execute(
            update(BookingRow)
            .where(BookingRow.customer_phone == customer_phone)
            .values(customer_vehicle_id=None)
        )
        for model in (
            CustomerTokenRow,
            CustomerName,
            CustomerVehicle,
            ConversationSessionRow,
        ):
            result = session.execute(
                delete(model).where(model.customer_phone == customer_phone)
            )
            deleted += int(result.rowcount or 0)

        bookings = session.scalars(
            select(BookingRow).where(BookingRow.customer_phone == customer_phone)
        ).all()
        anonymized = len(bookings)
        for booking_row in bookings:
            booking_row.customer_name = "Anonyme"
            booking_row.car_model = ""
            booking_row.color = ""
            booking_row.address = ""
            booking_row.address_text = ""
            booking_row.location_name = ""
            booking_row.location_address = ""
            booking_row.note = ""
            booking_row.latitude = None
            booking_row.longitude = None
            booking_row.raw_booking_json = "{}"
        session.flush()

        existing_anon_customer = session.scalar(
            select(Customer).where(Customer.phone == anon_phone)
        )
        customer = session.scalar(
            select(Customer).where(Customer.phone == customer_phone)
        )
        if customer is not None:
            customer.display_name = "Anonyme"
            customer.whatsapp_profile_name = ""
            customer.whatsapp_wa_id = ""
            customer.last_bot_stage = ""
            customer.last_bot_stage_label = ""
            customer.last_bot_stage_at = None
            if existing_anon_customer is None:
                customer.phone = anon_phone
            else:
                existing_anon_customer.booking_count = (
                    (existing_anon_customer.booking_count or 0)
                    + (customer.booking_count or 0)
                )
                session.execute(
                    update(BookingRow)
                    .where(BookingRow.customer_phone == customer_phone)
                    .values(customer_phone=anon_phone)
                )
                session.delete(customer)
            session.flush()

        # SQLite has no ON UPDATE CASCADE; explicitly migrate any bookings that
        # still reference the original phone over to the anonymized value. On
        # Postgres this WHERE matches zero rows because the CASCADE already
        # rewrote them during the customers.phone update above.
        session.execute(
            update(BookingRow)
            .where(BookingRow.customer_phone == customer_phone)
            .values(customer_phone=anon_phone)
        )

        session.add(
            DataErasureAuditRow(
                phone_hash=phone_hash_full,
                actor=(actor or "")[:64],
                deleted_count=deleted,
                anonymized_bookings=anonymized,
                notes=(notes or "").strip() or None,
            )
        )

        return {"deleted_count": deleted, "anonymized_bookings": anonymized}


def recent_erasures(
    *,
    limit: int = 100,
    actor: str | None = None,
    engine: Engine | None = None,
) -> list[dict]:
    """Return recent data-erasure audit rows for the admin portal.

    The audit table intentionally stores only a phone hash. This display helper
    keeps that privacy boundary by returning a truncated hash prefix rather than
    rehydrating customer PII.
    """
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return []
    safe_limit = max(1, min(int(limit or 100), 500))
    with session_scope(db_engine) as session:
        stmt = select(DataErasureAuditRow).order_by(DataErasureAuditRow.performed_at.desc())
        if actor:
            stmt = stmt.where(DataErasureAuditRow.actor == actor)
        rows = session.scalars(stmt.limit(safe_limit)).all()
        return [
            {
                "phone_hash": row.phone_hash[:12],
                "actor": row.actor,
                "deleted_count": row.deleted_count,
                "anonymized_bookings": row.anonymized_bookings,
                "performed_at": row.performed_at.isoformat() if row.performed_at else "",
                "notes": row.notes or "",
            }
            for row in rows
        ]
