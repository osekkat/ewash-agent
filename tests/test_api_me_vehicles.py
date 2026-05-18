"""Tests for GET /api/v1/me/vehicles — token-scoped past-vehicle list.

The PWA's CategoryStep calls this endpoint when the booking flow opens
so returning customers can tap a previously-used vehicle instead of
re-typing make/color. Auth is X-Ewash-Token only — the same opaque
token contract as GET /api/v1/bookings, no ?phone= parameter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import api, persistence
from app.booking import Booking
from app.db import init_db, make_engine
from app.persistence import mint_customer_token, persist_confirmed_booking
from app.rate_limit import limiter


@pytest.fixture
def engine(monkeypatch, tmp_path):
    db_path = tmp_path / "api-vehicles.db"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    init_db(engine)
    monkeypatch.setattr(persistence, "_configured_engine", lambda: engine)
    return engine


@pytest.fixture
def client():
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(api.router)
    api.install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def _seed_booking(
    engine,
    *,
    phone: str,
    ref: str,
    category: str,
    car_model: str,
    color: str,
    created_at: datetime | None = None,
) -> None:
    booking = Booking(phone=phone)
    booking.name = "Test Client"
    booking.category = category
    booking.vehicle_type = f"{category} — Test"
    booking.car_model = car_model
    booking.color = color
    booking.service = "svc_cpl"
    booking.service_bucket = "wash"
    booking.service_label = "Le Complet — 125 DH"
    booking.price_dh = 125
    booking.price_regular_dh = 125
    booking.location_mode = "home"
    booking.location_address = "Bouskoura"
    booking.address = "Bouskoura"
    booking.date_iso = "2026-06-15"
    booking.date_label = "Lundi 15 juin 2026"
    booking.slot_id = "slot_9_11"
    booking.slot = "09h – 11h"
    booking.ref = ref
    row = persist_confirmed_booking(booking, engine=engine, source="api")
    assert row is not None
    if created_at is not None:
        with persistence.session_scope(engine) as session:
            from app.models import BookingRow, CustomerVehicle
            from sqlalchemy import update
            session.execute(
                update(BookingRow).where(BookingRow.ref == ref).values(created_at=created_at)
            )
            # Mirror created_at onto the vehicle row's last_used_at so
            # the ordering test is deterministic.
            session.execute(
                update(CustomerVehicle)
                .where(
                    CustomerVehicle.customer_phone == phone,
                    CustomerVehicle.category == category,
                    CustomerVehicle.model == car_model,
                )
                .values(last_used_at=created_at)
            )


def test_missing_token_returns_401(engine, client):
    response = client.get("/api/v1/me/vehicles")
    assert response.status_code == 401
    assert response.json()["error_code"] == "missing_token"


def test_invalid_token_returns_401(engine, client):
    response = client.get(
        "/api/v1/me/vehicles", headers={"X-Ewash-Token": "definitely-not-real"}
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "invalid_token"


def test_phone_query_param_rejected(engine, client):
    response = client.get(
        "/api/v1/me/vehicles?phone=212611204502",
        headers={"X-Ewash-Token": "anything"},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "phone_param_not_accepted"


def test_valid_token_with_no_bookings_returns_empty_list(engine, client):
    token = mint_customer_token("212611204502", engine=engine)

    response = client.get("/api/v1/me/vehicles", headers={"X-Ewash-Token": token})

    assert response.status_code == 200
    assert response.json() == {"vehicles": []}


def test_valid_token_returns_past_vehicles(engine, client):
    phone = "212611204502"
    token = mint_customer_token(phone, engine=engine)
    _seed_booking(
        engine,
        phone=phone,
        ref="EW-2026-0001",
        category="B",
        car_model="Audi Q7",
        color="Blanc",
    )

    response = client.get("/api/v1/me/vehicles", headers={"X-Ewash-Token": token})

    assert response.status_code == 200
    vehicles = response.json()["vehicles"]
    assert len(vehicles) == 1
    item = vehicles[0]
    assert item["category"] == "B"
    assert item["category_label"] == "Berline / SUV"
    assert item["make"] == "Audi Q7"
    assert item["color"] == "Blanc"
    assert "Audi Q7" in item["label"] and "Blanc" in item["label"]


def test_repeated_bookings_with_same_vehicle_dedupe_to_single_row(engine, client):
    phone = "212611204502"
    token = mint_customer_token(phone, engine=engine)
    base = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    _seed_booking(
        engine,
        phone=phone,
        ref="EW-2026-0001",
        category="B",
        car_model="Audi Q7",
        color="Blanc",
        created_at=base,
    )
    _seed_booking(
        engine,
        phone=phone,
        ref="EW-2026-0002",
        category="B",
        car_model="Audi Q7",
        color="Blanc",
        created_at=base + timedelta(days=7),
    )

    response = client.get("/api/v1/me/vehicles", headers={"X-Ewash-Token": token})

    assert response.status_code == 200
    vehicles = response.json()["vehicles"]
    # _find_or_create_vehicle dedupes by (phone, category, model, color) so
    # repeated bookings on the same car must collapse to one item.
    assert len(vehicles) == 1
    assert vehicles[0]["make"] == "Audi Q7"


def test_distinct_vehicles_returned_most_recently_used_first(engine, client):
    phone = "212611204502"
    token = mint_customer_token(phone, engine=engine)
    base = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    _seed_booking(
        engine,
        phone=phone,
        ref="EW-2026-0001",
        category="A",
        car_model="Fiat 500",
        color="Gris",
        created_at=base,
    )
    _seed_booking(
        engine,
        phone=phone,
        ref="EW-2026-0002",
        category="B",
        car_model="Audi Q7",
        color="Blanc",
        created_at=base + timedelta(days=14),
    )

    response = client.get("/api/v1/me/vehicles", headers={"X-Ewash-Token": token})

    assert response.status_code == 200
    makes = [item["make"] for item in response.json()["vehicles"]]
    assert makes == ["Audi Q7", "Fiat 500"]


def test_other_users_vehicles_not_returned(engine, client):
    phone_a = "212611204502"
    phone_b = "212600000099"
    token_a = mint_customer_token(phone_a, engine=engine)
    _seed_booking(
        engine,
        phone=phone_b,
        ref="EW-2026-0001",
        category="B",
        car_model="Tesla Model Y",
        color="Rouge",
    )

    response = client.get("/api/v1/me/vehicles", headers={"X-Ewash-Token": token_a})

    assert response.status_code == 200
    # Token A's owner has no bookings; token must not leak phone B's history.
    assert response.json() == {"vehicles": []}
