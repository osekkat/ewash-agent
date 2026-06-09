"""Persistent booking domain models for the v0.3 admin/CRM layer.

This module starts with framework-light models and pure helpers so the booking
lifecycle/reminder rules are testable before wiring SQLAlchemy sessions and the
admin UI around them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

BOOKING_STATUSES = (
    "draft",
    "awaiting_confirmation",
    "pending_ewash_confirmation",
    "confirmed",
    "rescheduled",
    "customer_cancelled",
    "admin_cancelled",
    "expired",
    "no_show",
    "technician_en_route",
    "arrived",
    "in_progress",
    "completed",
    "completed_with_issue",
    "refunded",
)

FINAL_BOOKING_STATUSES = (
    "customer_cancelled",
    "admin_cancelled",
    "expired",
    "no_show",
    "completed",
    "completed_with_issue",
    "refunded",
)

# Guardrails for the operational workflow. This intentionally allows some admin
# recovery transitions (e.g. rescheduled -> confirmed) while preventing nonsense
# like completed -> in_progress.
ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    # `pending_ewash_confirmation` is reachable directly from `draft` because the
    # live customer-confirm flow (WhatsApp `BOOK_CONFIRM` and the planned PWA
    # `POST /api/v1/bookings`) writes the row with `status='pending_ewash_confirmation'`
    # in a single transaction — there is no separate "awaiting customer confirm"
    # step server-side once the customer has clicked through the recap. The
    # `awaiting_confirmation` intermediate is kept reachable so admin imports /
    # future flows can park rows in that bucket (the admin dashboard already
    # surfaces a count of it), but is not part of the customer happy path.
    "draft": {"awaiting_confirmation", "pending_ewash_confirmation", "expired", "customer_cancelled", "admin_cancelled"},
    "awaiting_confirmation": {"pending_ewash_confirmation", "expired", "customer_cancelled", "admin_cancelled"},
    "pending_ewash_confirmation": {"confirmed", "customer_cancelled", "admin_cancelled", "expired"},
    "confirmed": {
        "rescheduled",
        "customer_cancelled",
        "admin_cancelled",
        "no_show",
        "technician_en_route",
        "arrived",
        "in_progress",
        "completed",
        "completed_with_issue",
    },
    "rescheduled": {"confirmed", "customer_cancelled", "admin_cancelled", "expired"},
    "technician_en_route": {"arrived", "in_progress", "completed", "no_show", "admin_cancelled"},
    "arrived": {"in_progress", "completed", "completed_with_issue", "no_show"},
    "in_progress": {"completed", "completed_with_issue"},
    "completed": {"completed_with_issue", "refunded"},
    "completed_with_issue": {"refunded"},
    "customer_cancelled": set(),
    "admin_cancelled": set(),
    "expired": set(),
    "no_show": set(),
    "refunded": set(),
}

REMINDER_ELIGIBLE_STATUSES = ("confirmed", "rescheduled")


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for v0.3 persistence tables."""


class Customer(Base):
    __tablename__ = "customers"

    phone: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    whatsapp_profile_name: Mapped[str] = mapped_column(String(120), default="")
    whatsapp_wa_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    booking_count: Mapped[int] = mapped_column(Integer, default=0)
    last_bot_stage: Mapped[str] = mapped_column(String(60), default="", index=True)
    last_bot_stage_label: Mapped[str] = mapped_column(String(160), default="")
    last_bot_stage_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    names: Mapped[list["CustomerName"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    vehicles: Mapped[list["CustomerVehicle"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    bookings: Mapped[list["BookingRow"]] = relationship(back_populates="customer")
    conversation_sessions: Mapped[list["ConversationSessionRow"]] = relationship(back_populates="customer")
    tokens: Mapped[list["CustomerTokenRow"]] = relationship(back_populates="customer", cascade="all, delete-orphan")


class CustomerName(Base):
    __tablename__ = "customer_names"
    __table_args__ = (UniqueConstraint("customer_phone", "normalized_name", name="uq_customer_names_phone_normalized"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_phone: Mapped[str] = mapped_column(ForeignKey("customers.phone"), index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    normalized_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    first_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)

    customer: Mapped[Customer] = relationship(back_populates="names")


class VehicleModel(Base):
    __tablename__ = "vehicle_models"
    __table_args__ = (UniqueConstraint("category", "normalized_name", name="uq_vehicle_model_category_normalized"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(16), default="", index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    normalized_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vehicles: Mapped[list["CustomerVehicle"]] = relationship(back_populates="vehicle_model")


class VehicleColor(Base):
    __tablename__ = "vehicle_colors"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_vehicle_color_normalized"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), default="")
    normalized_name: Mapped[str] = mapped_column(String(60), default="", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vehicles: Mapped[list["CustomerVehicle"]] = relationship(back_populates="vehicle_color")


class CustomerVehicle(Base):
    __tablename__ = "customer_vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_phone: Mapped[str] = mapped_column(ForeignKey("customers.phone"), index=True)
    category: Mapped[str] = mapped_column(String(16), default="")
    model_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_models.id"), nullable=True, index=True)
    color_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_colors.id"), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(120), default="")
    color: Mapped[str] = mapped_column(String(60), default="")
    label: Mapped[str] = mapped_column(String(180), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    customer: Mapped[Customer] = relationship(back_populates="vehicles")
    vehicle_model: Mapped[VehicleModel | None] = relationship(back_populates="vehicles")
    vehicle_color: Mapped[VehicleColor | None] = relationship(back_populates="vehicles")
    bookings: Mapped[list["BookingRow"]] = relationship(back_populates="vehicle")


class ServicePriceRow(Base):
    __tablename__ = "service_prices"
    __table_args__ = (UniqueConstraint("service_id", "category", name="uq_service_price_service_category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.service_id"), index=True)
    category: Mapped[str] = mapped_column(String(8), index=True)
    price_dh: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    service: Mapped["ServiceRow"] = relationship(back_populates="prices")


class ServiceRow(Base):
    __tablename__ = "services"

    service_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(String(240), default="")
    bucket: Mapped[str] = mapped_column(String(40), default="", index=True)
    vehicle_lane: Mapped[str] = mapped_column(String(40), default="car", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    prices: Mapped[list[ServicePriceRow]] = relationship(back_populates="service", cascade="all, delete-orphan")
    promo_discounts: Mapped[list["PromoDiscountRow"]] = relationship(back_populates="service")
    booking_line_items: Mapped[list["BookingLineItemRow"]] = relationship(back_populates="service")


class PromoCodeRow(Base):
    __tablename__ = "promo_codes"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    discounts: Mapped[list["PromoDiscountRow"]] = relationship(back_populates="promo", cascade="all, delete-orphan")


class PromoDiscountRow(Base):
    __tablename__ = "promo_discounts"
    __table_args__ = (UniqueConstraint("promo_code", "service_id", "category", name="uq_promo_discount_code_service_category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promo_code: Mapped[str] = mapped_column(ForeignKey("promo_codes.code"), index=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.service_id"), index=True)
    category: Mapped[str] = mapped_column(String(8), index=True)
    price_dh: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    promo: Mapped[PromoCodeRow] = relationship(back_populates="discounts")
    service: Mapped[ServiceRow] = relationship(back_populates="promo_discounts")


class ClosedDateRow(Base):
    __tablename__ = "closed_dates"

    date_iso: Mapped[str] = mapped_column(String(10), primary_key=True)
    label: Mapped[str] = mapped_column(String(160), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TimeSlotRow(Base):
    __tablename__ = "time_slots"

    slot_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(80), default="")
    period: Mapped[str] = mapped_column(String(80), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CenterRow(Base):
    __tablename__ = "centers"

    center_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    details: Mapped[str] = mapped_column(String(240), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AdminTextRow(Base):
    __tablename__ = "admin_texts"

    text_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(160), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BookingNotificationSettingRow(Base):
    __tablename__ = "booking_notification_settings"

    settings_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(32), default="")
    template_name: Mapped[str] = mapped_column(String(160), default="")
    template_language: Mapped[str] = mapped_column(String(16), default="fr")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BookingRow(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("ref", name="uq_bookings_ref"),
        CheckConstraint(
            "status IN ('draft','awaiting_confirmation','pending_ewash_confirmation','confirmed','rescheduled','customer_cancelled','admin_cancelled','expired','no_show','technician_en_route','arrived','in_progress','completed','completed_with_issue','refunded')",
            name="ck_bookings_status",
        ),
        CheckConstraint("price_dh >= 0", name="ck_bookings_price_nonnegative"),
        CheckConstraint("price_regular_dh >= 0", name="ck_bookings_regular_price_nonnegative"),
        CheckConstraint("addon_price_dh >= 0", name="ck_bookings_addon_price_nonnegative"),
    )
    # Note: `ck_bookings_source` is added by migration 0006 on Postgres only.
    # SQLite enforcement of `source IN (...)` is left to Pydantic + persistence
    # validation per the same defensive pattern as `ck_bookings_status` — see
    # migration 0003. Declaring it here would block the migration's downgrade
    # path on SQLite (DROP COLUMN refuses if a CHECK references it).

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(String(32), default="", index=True)
    customer_phone: Mapped[str] = mapped_column(ForeignKey("customers.phone"), index=True)
    customer_vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("customer_vehicles.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    vehicle_type: Mapped[str] = mapped_column(String(120), default="")
    car_model: Mapped[str] = mapped_column(String(120), default="")
    color: Mapped[str] = mapped_column(String(60), default="")
    service_id: Mapped[str] = mapped_column(String(40), default="")
    service_bucket: Mapped[str] = mapped_column(String(40), default="")
    service_label: Mapped[str] = mapped_column(String(160), default="")
    price_dh: Mapped[int] = mapped_column(Integer, default=0)
    price_regular_dh: Mapped[int] = mapped_column(Integer, default=0)
    promo_code: Mapped[str] = mapped_column(String(40), default="")
    promo_label: Mapped[str] = mapped_column(String(120), default="")
    location_mode: Mapped[str] = mapped_column(String(40), default="")
    center: Mapped[str] = mapped_column(String(80), default="")
    center_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    geo: Mapped[str] = mapped_column(Text, default="")
    address: Mapped[str] = mapped_column(Text, default="")
    address_text: Mapped[str] = mapped_column(Text, default="")
    location_name: Mapped[str] = mapped_column(String(160), default="")
    location_address: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    date_label: Mapped[str] = mapped_column(String(80), default="")
    slot: Mapped[str] = mapped_column(String(80), default="")
    appointment_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    slot_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    addon_service: Mapped[str] = mapped_column(String(40), default="")
    addon_service_label: Mapped[str] = mapped_column(String(160), default="")
    addon_price_dh: Mapped[int] = mapped_column(Integer, default=0)
    total_price_dh: Mapped[int] = mapped_column(Integer, default=0)
    appointment_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    appointment_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone_name: Mapped[str] = mapped_column(String(80), default="Africa/Casablanca")
    # `unique=True` / `index=True` are deliberately omitted on `client_request_id`.
    # Migration 0006 adds a Postgres partial unique index
    # (`ix_bookings_client_request_id_partial WHERE client_request_id IS NOT NULL`)
    # which is the production enforcement path. Adding an unnamed unique index
    # in the model would survive `Base.metadata.create_all` on SQLite and then
    # block migration 0006's downgrade (`ALTER TABLE DROP COLUMN` refuses if any
    # index references the column). The persistence-level idempotency lookup
    # (`find_booking_by_client_request_id`) handles dedup logic in code.
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="whatsapp", default="whatsapp", index=True)
    raw_booking_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    customer: Mapped[Customer] = relationship(back_populates="bookings")
    vehicle: Mapped[CustomerVehicle | None] = relationship(back_populates="bookings")
    status_events: Mapped[list["BookingStatusEventRow"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", order_by="BookingStatusEventRow.created_at"
    )
    reminders: Mapped[list["BookingReminderRow"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", order_by="BookingReminderRow.scheduled_for"
    )
    line_items: Mapped[list["BookingLineItemRow"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", order_by="BookingLineItemRow.sort_order"
    )


class BookingLineItemRow(Base):
    __tablename__ = "booking_line_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_booking_line_items_quantity_positive"),
        CheckConstraint("unit_price_dh >= 0", name="ck_booking_line_items_unit_price_nonnegative"),
        CheckConstraint("regular_price_dh >= 0", name="ck_booking_line_items_regular_price_nonnegative"),
        CheckConstraint("total_price_dh >= 0", name="ck_booking_line_items_total_price_nonnegative"),
        Index("ix_booking_line_items_booking_kind", "booking_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="main", index=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.service_id"), index=True)
    service_bucket: Mapped[str] = mapped_column(String(40), default="")
    label_snapshot: Mapped[str] = mapped_column(String(180), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_dh: Mapped[int] = mapped_column(Integer, default=0)
    regular_price_dh: Mapped[int] = mapped_column(Integer, default=0)
    total_price_dh: Mapped[int] = mapped_column(Integer, default=0)
    discount_label: Mapped[str] = mapped_column(String(120), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    booking: Mapped[BookingRow] = relationship(back_populates="line_items")
    service: Mapped[ServiceRow] = relationship(back_populates="booking_line_items")


class BookingRefCounterRow(Base):
    __tablename__ = "booking_ref_counters"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_counter: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BookingStatusEventRow(Base):
    __tablename__ = "booking_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    from_status: Mapped[str] = mapped_column(String(40), default="")
    to_status: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    booking: Mapped[BookingRow] = relationship(back_populates="status_events")


class ReminderRuleRow(Base):
    __tablename__ = "reminder_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    offset_minutes_before: Mapped[int] = mapped_column(Integer)
    max_sends: Mapped[int] = mapped_column(Integer, default=1)
    min_minutes_between_sends: Mapped[int] = mapped_column(Integer, default=0)
    template_name: Mapped[str] = mapped_column(String(160), default="")
    channel: Mapped[str] = mapped_column(String(40), default="whatsapp_template")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    reminders: Mapped[list["BookingReminderRow"]] = relationship(back_populates="rule")


class BookingReminderRow(Base):
    __tablename__ = "booking_reminders"
    __table_args__ = (
        CheckConstraint("status IN ('pending','sent','cancelled','failed','skipped')", name="ck_booking_reminders_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("reminder_rules.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(120))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    booking: Mapped[BookingRow] = relationship(back_populates="reminders")
    rule: Mapped[ReminderRuleRow | None] = relationship(back_populates="reminders")


class WhatsappMessageRow(Base):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        CheckConstraint("direction IN ('inbound','outbound')", name="ck_whatsapp_messages_direction"),
        UniqueConstraint("message_id", name="uq_whatsapp_messages_message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(160), default="", index=True)
    phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    direction: Mapped[str] = mapped_column(String(16), default="inbound", index=True)
    message_type: Mapped[str] = mapped_column(String(40), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(40), default="received", index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OperationalServiceRecordRow(Base):
    __tablename__ = "operational_service_records"
    __table_args__ = (
        CheckConstraint("unit_price_ht >= 0", name="ck_operational_service_records_unit_price_ht_nonnegative"),
        CheckConstraint(
            "status IN ('complete','missing_matricule','missing_photo','needs_review','voided')",
            name="ck_operational_service_records_status",
        ),
        Index("ix_operational_service_client_site_date", "client_name", "site_name", "service_date"),
        Index("ix_operational_service_source_message", "source_channel", "source_message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_date: Mapped[date] = mapped_column(Date, index=True)
    client_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    site_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    source_channel: Mapped[str] = mapped_column(String(40), default="whatsapp", index=True)
    source_chat_id: Mapped[str] = mapped_column(String(160), default="", index=True)
    source_message_id: Mapped[str] = mapped_column(String(160), default="", index=True)
    source_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    sender_id: Mapped[str] = mapped_column(String(160), default="", index=True)
    vehicle_model: Mapped[str] = mapped_column(String(160), default="")
    matricule: Mapped[str] = mapped_column(String(120), default="", index=True)
    category: Mapped[str] = mapped_column(String(40), default="", index=True)
    prestation: Mapped[str] = mapped_column(String(120), default="lavage", index=True)
    unit_price_ht: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="MAD")
    photo_reference: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="complete", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    raw_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CashLedgerEntryRow(Base):
    __tablename__ = "cash_ledger_entries"
    __table_args__ = (
        CheckConstraint("direction IN ('cash_in','cash_out','cash_transfer','cash_adjustment')", name="ck_cash_ledger_entries_direction"),
        CheckConstraint("amount_minor >= 0", name="ck_cash_ledger_entries_amount_nonnegative"),
        CheckConstraint("status IN ('recorded','needs_review','corrected','voided')", name="ck_cash_ledger_entries_status"),
        Index("ix_cash_ledger_owner_occurred", "owner_phone", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    direction: Mapped[str] = mapped_column(String(24), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="MAD")
    transaction_type: Mapped[str] = mapped_column(String(60), default="unknown", index=True)
    category: Mapped[str] = mapped_column(String(60), default="uncategorized", index=True)
    counterparty: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    raw_message_text: Mapped[str] = mapped_column(Text, default="")
    source_channel: Mapped[str] = mapped_column(String(40), default="whatsapp", index=True)
    source_message_id: Mapped[str] = mapped_column(String(160), default="", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(40), default="recorded", index=True)
    review_reason: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    audit_events: Mapped[list["CashLedgerAuditEventRow"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="CashLedgerAuditEventRow.occurred_at"
    )


class CashLedgerAuditEventRow(Base):
    __tablename__ = "cash_ledger_audit_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('created','updated','corrected','voided','reviewed')", name="ck_cash_ledger_audit_events_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("cash_ledger_entries.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    actor_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    before_json: Mapped[str] = mapped_column(Text, default="{}")
    after_json: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str] = mapped_column(Text, default="")
    source_message_id: Mapped[str] = mapped_column(String(160), default="", index=True)

    entry: Mapped[CashLedgerEntryRow] = relationship(back_populates="audit_events")


class CashReconciliationRow(Base):
    __tablename__ = "cash_reconciliations"
    __table_args__ = (
        CheckConstraint("status IN ('pending','confirmed','discrepancy')", name="ck_cash_reconciliations_status"),
        UniqueConstraint("owner_phone", "business_date", name="uq_cash_reconciliations_owner_business_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    opening_balance_minor: Mapped[int] = mapped_column(Integer, default=0)
    cash_in_minor: Mapped[int] = mapped_column(Integer, default=0)
    cash_out_minor: Mapped[int] = mapped_column(Integer, default=0)
    expected_closing_balance_minor: Mapped[int] = mapped_column(Integer, default=0)
    reported_closing_balance_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difference_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationSessionRow(Base):
    __tablename__ = "conversation_sessions"
    __table_args__ = (
        Index("ix_conversation_sessions_phone_status", "customer_phone", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_phone: Mapped[str] = mapped_column(ForeignKey("customers.phone"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="open", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    current_stage: Mapped[str] = mapped_column(String(60), default="", index=True)

    customer: Mapped[Customer] = relationship(back_populates="conversation_sessions")
    events: Mapped[list["ConversationEventRow"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ConversationEventRow.created_at"
    )


class ConversationEventRow(Base):
    __tablename__ = "conversation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("conversation_sessions.id"), index=True)
    customer_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    stage: Mapped[str] = mapped_column(String(60), default="", index=True)
    stage_label: Mapped[str] = mapped_column(String(160), default="")
    event_type: Mapped[str] = mapped_column(String(60), default="stage_seen", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[ConversationSessionRow] = relationship(back_populates="events")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require_valid_status(status: str) -> None:
    if status not in BOOKING_STATUSES:
        raise ValueError(f"Unknown booking status: {status}")


@dataclass
class BookingStatusEvent:
    from_status: str
    to_status: str
    actor: str
    note: str = ""
    created_at: datetime = field(default_factory=utcnow)
    booking_id: int | None = None


@dataclass
class BookingRecord:
    phone: str
    status: str = "draft"
    id: int | None = None
    ref: str = ""
    customer_name: str = ""
    customer_vehicle_id: int | None = None
    vehicle_label: str = ""
    service_id: str = ""
    service_label: str = ""
    price_dh: int = 0
    promo_code: str = ""
    location_mode: str = ""
    appointment_start_at: datetime | None = None
    appointment_end_at: datetime | None = None
    timezone_name: str = "Africa/Casablanca"
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    status_events: list[BookingStatusEvent] = field(default_factory=list)
    reminders: list["BookingReminder"] = field(default_factory=list)

    def __post_init__(self) -> None:
        require_valid_status(self.status)


@dataclass
class ReminderRule:
    name: str
    offset_minutes_before: int
    id: int | None = None
    enabled: bool = True
    max_sends: int = 1
    min_minutes_between_sends: int = 0
    template_name: str = ""
    channel: str = "whatsapp_template"
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.offset_minutes_before <= 0:
            raise ValueError("offset_minutes_before must be positive")
        if self.max_sends < 1:
            raise ValueError("max_sends must be >= 1")
        if self.min_minutes_between_sends < 0:
            raise ValueError("min_minutes_between_sends must be >= 0")


@dataclass
class BookingReminder:
    booking_id: int | None
    rule_id: int | None
    kind: str
    scheduled_for: datetime
    status: str = "pending"
    sent_at: datetime | None = None
    attempt_count: int = 0
    error: str = ""
    created_at: datetime = field(default_factory=utcnow)


def transition_booking_status(
    booking: BookingRecord | BookingRow,
    new_status: str,
    *,
    actor: str,
    note: str = "",
    now: datetime | None = None,
) -> BookingStatusEvent | BookingStatusEventRow:
    """Move a booking through a guarded lifecycle and append an audit event."""
    require_valid_status(new_status)
    if not actor:
        raise ValueError("actor is required")

    current = booking.status
    if current == new_status:
        raise ValueError(f"Booking is already {new_status}")

    allowed = ALLOWED_STATUS_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise ValueError(f"Invalid booking status transition: {current} -> {new_status}")

    event_at = now or utcnow()
    event: BookingStatusEvent | BookingStatusEventRow
    if isinstance(booking, BookingRow):
        event = BookingStatusEventRow(
            booking_id=booking.id,
            from_status=current,
            to_status=new_status,
            actor=actor,
            note=note,
            created_at=event_at,
        )
    else:
        event = BookingStatusEvent(
            booking_id=booking.id,
            from_status=current,
            to_status=new_status,
            actor=actor,
            note=note,
            created_at=event_at,
        )
    booking.status = new_status
    booking.updated_at = event_at
    booking.status_events.append(event)

    if new_status in FINAL_BOOKING_STATUSES:
        cancel_pending_reminders(booking, reason=f"booking_status:{new_status}")

    return event


def cancel_pending_reminders(booking: BookingRecord | BookingRow, *, reason: str) -> int:
    """Cancel pending reminders when the booking can no longer receive them."""
    count = 0
    for reminder in booking.reminders:
        if reminder.status == "pending":
            reminder.status = "cancelled"
            reminder.error = reason
            count += 1
    return count


def create_reminders_for_booking(
    booking: BookingRecord,
    rules: Iterable[ReminderRule],
    *,
    now: datetime | None = None,
) -> list[BookingReminder]:
    """Generate concrete reminder rows from active rules for a confirmed booking.

    Rules are applied only to confirmed/rescheduled bookings with a precise
    appointment start datetime. Reminder times already in the past are skipped.
    """
    if booking.status not in REMINDER_ELIGIBLE_STATUSES:
        return []
    if booking.appointment_start_at is None:
        return []

    baseline = now or utcnow()
    existing_rule_ids = {r.rule_id for r in booking.reminders if r.rule_id is not None}
    generated: list[BookingReminder] = []

    for rule in rules:
        if not rule.enabled:
            continue
        if rule.id is not None and rule.id in existing_rule_ids:
            continue
        scheduled_for = booking.appointment_start_at - timedelta_minutes(rule.offset_minutes_before)
        if scheduled_for <= baseline:
            continue
        reminder = BookingReminder(
            booking_id=booking.id,
            rule_id=rule.id,
            kind=rule.name,
            scheduled_for=scheduled_for,
        )
        booking.reminders.append(reminder)
        generated.append(reminder)

    return generated


def timedelta_minutes(minutes: int):
    # Local wrapper keeps imports explicit near pure scheduling logic and makes
    # future timezone-aware changes easier to isolate.
    from datetime import timedelta

    return timedelta(minutes=minutes)


class CustomerTokenRow(Base):
    """Opaque session tokens for the PWA Bookings tab.

    Stored as SHA-256 hex; the plaintext is returned to the PWA exactly once
    at booking creation. A DB dump never yields an active session token.

    Multiple rows per customer_phone are allowed (one per device that received
    a fresh-mint token). Old rows remain valid until an admin revocation pass
    purges them — out of scope for v1.

    Index names match Alembic migration 0006 so the SQLite test path
    (`Base.metadata.create_all`) and the Postgres production path produce
    indexes with identical names.
    """

    __tablename__ = "customer_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_customer_tokens_token_hash"),
        Index("ix_customer_tokens_phone", "customer_phone"),
        Index("ix_customer_tokens_last_used", "last_used_at"),
    )

    # Integer (not BigInteger) — the existing convention across this models module.
    # SQLite needs INTEGER PRIMARY KEY for autoincrement; the migration emits
    # BIGSERIAL on Postgres which adapts cleanly at read time.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_phone: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("customers.phone", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped["Customer"] = relationship(back_populates="tokens")


class DataErasureAuditRow(Base):
    """Append-only audit log of data erasure events (GDPR / Loi 09-08).

    Stores `phone_hash` (SHA-256 hex), NOT raw phone — the audit log itself
    is privacy-preserving and cannot re-introduce PII. `actor` distinguishes
    customer self-serve deletions from admin-initiated ones; the counts let
    compliance reports state "X customers anonymized in period Y".

    Convention: this table is append-only. No UPDATE / DELETE statements
    should touch existing rows.
    """

    __tablename__ = "data_erasure_audit"
    __table_args__ = (
        Index("ix_data_erasure_audit_performed_at", "performed_at"),
    )

    # See note on CustomerTokenRow.id for why Integer instead of BigInteger.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    deleted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    anonymized_bookings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
