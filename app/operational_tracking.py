"""Operational service tracking for WhatsApp-first client workflows.

This module is deliberately generic: Hertz airport washes are the first use case,
but the platform should be able to track any B2B/operations client where field
teams send vehicle/service evidence through WhatsApp and the back office needs a
durable recap source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import OperationalServiceRecordRow

TRACKING_REVIEW_STATUSES = {"missing_matricule", "missing_photo", "needs_review"}
TRACKING_STATUSES = TRACKING_REVIEW_STATUSES | {"complete", "voided"}


@dataclass(frozen=True)
class OperationalServiceRecordInput:
    service_date: date
    client_name: str
    site_name: str
    source_channel: str = "whatsapp"
    source_chat_id: str = ""
    source_message_id: str = ""
    source_order: int = 0
    sender_id: str = ""
    vehicle_model: str = ""
    matricule: str = ""
    category: str = ""
    prestation: str = "lavage"
    unit_price_ht: int = 0
    currency: str = "MAD"
    photo_reference: str = ""
    status: str = ""
    notes: str = ""
    raw_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class OperationalDaySummary:
    business_date: date
    client_name: str
    site_name: str
    record_count: int
    total_ht_dh: int
    needs_review_count: int


def _clean(value: str | None, *, limit: int) -> str:
    return " ".join((value or "").strip().split())[:limit]


def _json(data: dict[str, Any] | None) -> str:
    return json.dumps(data or {}, ensure_ascii=False)


def _status_for(data: OperationalServiceRecordInput) -> str:
    explicit = _clean(data.status, limit=40)
    if explicit:
        if explicit not in TRACKING_STATUSES:
            raise ValueError(f"unknown operational tracking status: {explicit}")
        return explicit
    if not _clean(data.matricule, limit=120):
        return "missing_matricule"
    if not _clean(data.photo_reference, limit=500):
        return "missing_photo"
    return "complete"


def operational_record_input_from_payload(payload: dict[str, Any]) -> OperationalServiceRecordInput:
    if not isinstance(payload, dict):
        raise ValueError("record payload must be an object")
    service_date_raw = payload.get("service_date")
    if isinstance(service_date_raw, date):
        service_date = service_date_raw
    elif isinstance(service_date_raw, str):
        service_date = date.fromisoformat(service_date_raw)
    else:
        raise ValueError("service_date is required")

    return OperationalServiceRecordInput(
        service_date=service_date,
        client_name=str(payload.get("client_name") or ""),
        site_name=str(payload.get("site_name") or ""),
        source_channel=str(payload.get("source_channel") or "whatsapp"),
        source_chat_id=str(payload.get("source_chat_id") or ""),
        source_message_id=str(payload.get("source_message_id") or ""),
        source_order=int(payload.get("source_order") or 0),
        sender_id=str(payload.get("sender_id") or ""),
        vehicle_model=str(payload.get("vehicle_model") or ""),
        matricule=str(payload.get("matricule") or ""),
        category=str(payload.get("category") or ""),
        prestation=str(payload.get("prestation") or "lavage"),
        unit_price_ht=int(payload.get("unit_price_ht") or 0),
        currency=str(payload.get("currency") or "MAD"),
        photo_reference=str(payload.get("photo_reference") or ""),
        status=str(payload.get("status") or ""),
        notes=str(payload.get("notes") or ""),
        raw_payload=payload.get("raw_payload") if isinstance(payload.get("raw_payload"), dict) else {},
    )


def create_operational_service_record(
    session: Session,
    data: OperationalServiceRecordInput,
) -> OperationalServiceRecordRow:
    if data.unit_price_ht < 0:
        raise ValueError("unit_price_ht must be non-negative")
    client_name = _clean(data.client_name, limit=120)
    site_name = _clean(data.site_name, limit=120)
    if not client_name:
        raise ValueError("client_name is required")
    if not site_name:
        raise ValueError("site_name is required")

    row = OperationalServiceRecordRow(
        service_date=data.service_date,
        client_name=client_name,
        site_name=site_name,
        source_channel=_clean(data.source_channel, limit=40) or "whatsapp",
        source_chat_id=_clean(data.source_chat_id, limit=160),
        source_message_id=_clean(data.source_message_id, limit=160),
        source_order=max(0, int(data.source_order or 0)),
        sender_id=_clean(data.sender_id, limit=160),
        vehicle_model=_clean(data.vehicle_model, limit=160),
        matricule=_clean(data.matricule, limit=120),
        category=_clean(data.category, limit=40),
        prestation=_clean(data.prestation, limit=120) or "lavage",
        unit_price_ht=int(data.unit_price_ht or 0),
        currency=_clean(data.currency, limit=8) or "MAD",
        photo_reference=_clean(data.photo_reference, limit=500),
        status=_status_for(data),
        notes=_clean(data.notes, limit=500),
        raw_payload_json=_json(data.raw_payload),
    )
    session.add(row)
    session.flush()
    return row


def operational_summary_for_day(
    session: Session,
    *,
    business_date: date,
    client_name: str = "",
    site_name: str = "",
) -> OperationalDaySummary:
    query = select(
        func.count(OperationalServiceRecordRow.id),
        func.coalesce(func.sum(OperationalServiceRecordRow.unit_price_ht), 0),
    ).where(
        OperationalServiceRecordRow.service_date == business_date,
        OperationalServiceRecordRow.status != "voided",
    )
    review_query = select(func.count(OperationalServiceRecordRow.id)).where(
        OperationalServiceRecordRow.service_date == business_date,
        OperationalServiceRecordRow.status.in_(TRACKING_REVIEW_STATUSES),
    )
    if client_name:
        query = query.where(OperationalServiceRecordRow.client_name == client_name)
        review_query = review_query.where(OperationalServiceRecordRow.client_name == client_name)
    if site_name:
        query = query.where(OperationalServiceRecordRow.site_name == site_name)
        review_query = review_query.where(OperationalServiceRecordRow.site_name == site_name)

    record_count, total_ht = session.execute(query).one()
    needs_review_count = int(session.scalar(review_query) or 0)
    return OperationalDaySummary(
        business_date=business_date,
        client_name=client_name,
        site_name=site_name,
        record_count=int(record_count or 0),
        total_ht_dh=int(total_ht or 0),
        needs_review_count=needs_review_count,
    )


def recent_operational_service_records(
    session: Session,
    *,
    client_name: str = "",
    site_name: str = "",
    limit: int = 100,
) -> list[OperationalServiceRecordRow]:
    query = select(OperationalServiceRecordRow).where(OperationalServiceRecordRow.status != "voided")
    if client_name:
        query = query.where(OperationalServiceRecordRow.client_name == client_name)
    if site_name:
        query = query.where(OperationalServiceRecordRow.site_name == site_name)
    query = query.order_by(
        OperationalServiceRecordRow.service_date.desc(),
        OperationalServiceRecordRow.source_chat_id.asc(),
        OperationalServiceRecordRow.source_order.asc(),
        OperationalServiceRecordRow.id.desc(),
    ).limit(max(1, min(int(limit or 100), 500)))
    return list(session.scalars(query).all())
