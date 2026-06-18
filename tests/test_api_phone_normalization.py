"""Tests for cross-channel phone normalization & customer-row dedup.

A customer who reaches Ewash via PWA with ``"+212 6 11 20 45 02"`` must dedupe
to the same ``customers.phone`` row that the WhatsApp bot sees when Meta
delivers ``"212611204502"``. Both channels funnel through
:func:`app.notifications.normalize_phone`, which strips to digits and
validates 8–20 chars; the resulting canonical form is the primary key on
``customers``.

These tests pin the contract end-to-end:

- Cross-channel dedup (PWA-then-WA and WA-then-PWA produce one row).
- Multiple PWA input formats all collapse to the same canonical phone.
- Length rejections at the Pydantic boundary (too short) and the
  ``normalize_phone`` boundary (too long after stripping).
- Both ``bookings`` and ``customer_tokens`` carry the canonical phone.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import TestCase

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import api as pwa_api, catalog, notifications, persistence
from app.config import settings
from app.db import init_db, make_engine, session_scope
from app.models import BookingRow, Customer, CustomerTokenRow
from app.rate_limit import limiter

case = TestCase()

CANONICAL_PHONE = "212611204502"


def _future_booking_date() -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()


@pytest.fixture
def api_db(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'api-phone-norm.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    persistence._configured_engine.cache_clear()
    catalog.catalog_cache_clear()
    notifications.notification_cache_clear()
    try:
        yield engine
    finally:
        persistence._configured_engine.cache_clear()
        catalog.catalog_cache_clear()
        notifications.notification_cache_clear()


def _pwa_client() -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(pwa_api.router)
    pwa_api.install_exception_handlers(app)
    return TestClient(app)


def _payload(**overrides) -> dict:
    payload = {
        "phone": "+212 6 11 20 45 02",
        "name": "Dedupe Test",
        "category": "A",
        "vehicle": {"make": "Clio", "color": "Bleu"},
        "location": {"kind": "home", "pin_address": "Villa X"},
        "service_id": "svc_cpl",
        "date": _future_booking_date(),
        "slot": "slot_9_11",
        "addon_ids": [],
    }
    payload.update(overrides)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Cross-channel dedup
# ─────────────────────────────────────────────────────────────────────────────


def test_pwa_then_whatsapp_dedupe(api_db):
    # PWA submits first with a free-text phone shape.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(phone="+212 6 11 20 45 02"),
        )
    case.assertEqual(response.status_code, 200)

    # Then the WhatsApp side. The bot's handlers eventually call
    # `persist_customer_name(phone, name, ...)` with the digits-only phone
    # Meta delivers — simulate that without spinning up the full handler.
    persistence.persist_customer_name(CANONICAL_PHONE, "WhatsApp Test", engine=api_db)

    with session_scope(api_db) as session:
        customers = session.scalars(select(Customer)).all()
        case.assertEqual(len(customers), 1)
        case.assertEqual(customers[0].phone, CANONICAL_PHONE)


def test_whatsapp_then_pwa_dedupe(api_db):
    # WhatsApp lands first.
    persistence.persist_customer_name(CANONICAL_PHONE, "WhatsApp Test", engine=api_db)

    # Then a PWA booking with a formatted phone.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(phone="+212 6 11 20 45 02"),
        )
    case.assertEqual(response.status_code, 200)

    with session_scope(api_db) as session:
        customers = session.scalars(select(Customer)).all()
        case.assertEqual(len(customers), 1)
        case.assertEqual(customers[0].phone, CANONICAL_PHONE)


def test_various_input_formats_dedupe(api_db):
    # Five superficially different inputs that all normalize to 212611204502.
    # Each pays for its own BookingRow (no idempotency key) but they all hit
    # the same `customers.phone` PK.
    formats = [
        "+212 6 11 20 45 02",
        "+212611204502",
        "212611204502",
        "212-611-204-502",
        " (212) 611-204-502 ",
    ]
    with _pwa_client() as client:
        for idx, phone in enumerate(formats):
            slot = ("slot_9_11", "slot_11_13", "slot_14_16", "slot_16_18", "slot_18_20")[idx]
            response = client.post(
                "/api/v1/bookings",
                json=_payload(phone=phone, slot=slot),
            )
            case.assertEqual(
                response.status_code,
                200,
                msg=f"Format {phone!r} produced {response.status_code} {response.text}",
            )

    with session_scope(api_db) as session:
        customers = session.scalars(select(Customer)).all()
        case.assertEqual(len(customers), 1)
        case.assertEqual(customers[0].phone, CANONICAL_PHONE)
        bookings = session.scalars(select(BookingRow)).all()
        case.assertEqual(len(bookings), 5)
        for row in bookings:
            case.assertEqual(row.customer_phone, CANONICAL_PHONE)


# ─────────────────────────────────────────────────────────────────────────────
# Length rejections at both layers
# ─────────────────────────────────────────────────────────────────────────────


def test_phone_below_min_length_rejected(api_db):
    # BookingCreateRequest.phone has Field(min_length=8). A 3-char input trips
    # Pydantic before `normalize_phone` even runs → 422.
    with _pwa_client() as client:
        response = client.post("/api/v1/bookings", json=_payload(phone="123"))
    case.assertEqual(response.status_code, 422)


def test_phone_above_max_length_rejected(api_db):
    # 30 ASCII digits passes Pydantic max_length=32 but fails normalize_phone
    # (30 > 20-digit ceiling) → 400 invalid_phone with the typed envelope.
    with _pwa_client() as client:
        response = client.post("/api/v1/bookings", json=_payload(phone="1" * 30))
    case.assertEqual(response.status_code, 400)
    body = response.json()
    case.assertEqual(body["error_code"], "invalid_phone")
    case.assertEqual(body["field"], "phone")


# ─────────────────────────────────────────────────────────────────────────────
# Canonical phone in derived rows
# ─────────────────────────────────────────────────────────────────────────────


def test_normalized_phone_in_booking_row(api_db):
    # PWA submits the formatted shape; the persisted BookingRow stores the
    # digits-only canonical phone, not the raw input.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(phone="+212 6 11 20 45 02"),
        )
    case.assertEqual(response.status_code, 200)
    ref = response.json()["ref"]

    with session_scope(api_db) as session:
        row = session.scalars(select(BookingRow).where(BookingRow.ref == ref)).one()
        case.assertEqual(row.customer_phone, CANONICAL_PHONE)


def test_normalized_phone_in_customer_tokens(api_db):
    # The contract: callers normalize first, then `mint_customer_token` stores
    # the canonical phone verbatim. Asserts both legs of the contract:
    # (1) `normalize_phone` produces the expected canonical, and
    # (2) the row written by `mint_customer_token` carries that canonical.
    normalized = notifications.normalize_phone("+212 6 11 20 45 02")
    case.assertEqual(normalized, CANONICAL_PHONE)
    persistence.mint_customer_token(normalized, engine=api_db)

    with session_scope(api_db) as session:
        rows = session.scalars(select(CustomerTokenRow)).all()
        case.assertEqual(len(rows), 1)
        case.assertEqual(rows[0].customer_phone, CANONICAL_PHONE)
