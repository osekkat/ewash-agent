"""Persistence service for confirmed WhatsApp bookings.

The WhatsApp flow still owns the deterministic customer experience. This module is
an adapter that mirrors confirmed in-memory Booking objects into the v0.3 CRM DB
so the admin dashboard can show real operational data.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Iterable

from sqlalchemy import Engine, func, select

from .booking import Booking
from .config import settings
from .db import init_db, make_engine, session_scope
from .models import (
    BookingReminderRow,
    BookingRow,
    BookingStatusEventRow,
    Customer,
    CustomerVehicle,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecentBooking:
    ref: str
    customer_name: str
    service_label: str
    status: str


@dataclass(frozen=True)
class DashboardSummary:
    total_bookings: int = 0
    confirmed_bookings: int = 0
    awaiting_confirmation: int = 0
    customers: int = 0
    pending_reminders: int = 0
    recent_bookings: tuple[RecentBooking, ...] = ()
    db_available: bool = False


@lru_cache(maxsize=1)
def _configured_engine() -> Engine | None:
    if not settings.database_url:
        return None
    engine = make_engine(settings.database_url)
    init_db(engine)
    return engine


def _engine_or_configured(engine: Engine | None = None) -> Engine | None:
    if engine is not None:
        return engine
    return _configured_engine()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _vehicle_label(booking: Booking) -> str:
    if booking.category == "MOTO":
        return booking.vehicle_type or "Moto"
    parts = [booking.car_model.strip(), booking.color.strip()]
    label = " — ".join(p for p in parts if p)
    return label or booking.vehicle_type or "Véhicule"


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
    return customer


def _find_or_create_vehicle(session, booking: Booking) -> CustomerVehicle | None:
    if not booking.phone:
        return None
    model = "" if booking.category == "MOTO" else (booking.car_model or "").strip()
    color = "" if booking.category == "MOTO" else (booking.color or "").strip()
    category = booking.category or ""
    existing = session.scalars(
        select(CustomerVehicle).where(
            CustomerVehicle.customer_phone == booking.phone,
            CustomerVehicle.category == category,
            CustomerVehicle.model == model,
            CustomerVehicle.color == color,
            CustomerVehicle.active.is_(True),
        )
    ).first()
    if existing is not None:
        existing.label = existing.label or _vehicle_label(booking)
        existing.last_used_at = _now()
        return existing

    vehicle = CustomerVehicle(
        customer_phone=booking.phone,
        category=category,
        model=model,
        color=color,
        label=_vehicle_label(booking),
        active=True,
        last_used_at=_now(),
    )
    session.add(vehicle)
    session.flush()
    return vehicle


def persist_confirmed_booking(booking: Booking, *, engine: Engine | None = None) -> BookingRow | None:
    """Mirror a confirmed WhatsApp booking into the CRM database.

    Returns the persisted BookingRow when a DB is configured. If the app is run
    without DATABASE_URL (local demos/tests that do not pass an engine), this is
    a safe no-op so the WhatsApp flow keeps working.
    """
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        log.info("persist_confirmed_booking skipped: DATABASE_URL not configured ref=%s", booking.ref)
        return None

    if not booking.ref:
        booking.assign_ref()

    with session_scope(db_engine) as session:
        existing = session.scalars(select(BookingRow).where(BookingRow.ref == booking.ref)).first()
        if existing is not None:
            return existing

        customer = _find_or_create_customer(session, booking)
        vehicle = _find_or_create_vehicle(session, booking)
        row = BookingRow(
            ref=booking.ref,
            customer_phone=customer.phone,
            customer_vehicle_id=vehicle.id if vehicle else None,
            status="confirmed",
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
            geo=booking.geo,
            address=booking.address,
            date_label=booking.date_label,
            slot=booking.slot,
            note=booking.note,
            addon_service=booking.addon_service,
            addon_service_label=booking.addon_service_label,
            addon_price_dh=booking.addon_price_dh,
            raw_booking_json=json.dumps(asdict(booking), ensure_ascii=False, default=str),
        )
        session.add(row)
        session.flush()
        session.add(
            BookingStatusEventRow(
                booking_id=row.id,
                from_status="awaiting_confirmation",
                to_status="confirmed",
                actor="customer",
                note="Confirmation WhatsApp",
            )
        )
        session.flush()
        session.expunge(row)
        return row


def persist_booking_addon(
    ref: str,
    *,
    addon_service: str,
    addon_service_label: str,
    addon_price_dh: int,
    engine: Engine | None = None,
) -> None:
    db_engine = _engine_or_configured(engine)
    if db_engine is None or not ref:
        return
    with session_scope(db_engine) as session:
        row = session.scalars(select(BookingRow).where(BookingRow.ref == ref)).first()
        if row is None:
            log.warning("persist_booking_addon: ref=%s not found", ref)
            return
        row.addon_service = addon_service
        row.addon_service_label = addon_service_label
        row.addon_price_dh = addon_price_dh


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
            customers = session.scalar(select(func.count()).select_from(Customer)) or 0
            reminders = session.scalar(
                select(func.count()).select_from(BookingReminderRow).where(BookingReminderRow.status == "pending")
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
                )
                for row in rows
            )
            return DashboardSummary(
                total_bookings=total,
                confirmed_bookings=confirmed,
                awaiting_confirmation=awaiting,
                customers=customers,
                pending_reminders=reminders,
                recent_bookings=recent,
                db_available=True,
            )
    except Exception:
        log.exception("admin_dashboard_summary failed")
        return DashboardSummary()
