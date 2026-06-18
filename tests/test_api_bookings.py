"""Tests for POST /api/v1/bookings."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging
from unittest import TestCase

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import api, booking as booking_store, catalog, main as main_module, notifications, persistence
from app.config import settings
from app.db import init_db, make_engine, session_scope
from app.models import (
    BookingLineItemRow,
    BookingRefCounterRow,
    BookingRow,
    BookingStatusEventRow,
    Customer,
    CustomerName,
    CustomerTokenRow,
)
from app.rate_limit import limiter
from app.security import hash_token

case = TestCase()


def _future_booking_date() -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()


def _client(
    *,
    raise_server_exceptions: bool = True,
    access_logging: bool = False,
) -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    if access_logging:
        main_module._configure_access_logging(app)
    app.include_router(api.router)
    api.install_exception_handlers(app)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def api_db(monkeypatch, tmp_path):
    booking_store._bookings.clear()
    monkeypatch.setattr(booking_store, "_counter", 0)
    db_url = f"sqlite+pysqlite:///{tmp_path / 'api-bookings.db'}"
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
        booking_store._bookings.clear()


def _payload(**overrides) -> dict:
    payload = {
        "phone": "+212 611-204-502",
        "name": "  Oussama\u0000   Test  ",
        "category": "A",
        "vehicle": {"make": "Dacia   Logan", "color": " Blanc\u0000 "},
        "location": {
            "kind": "home",
            "pin_address": "Villa\u0000 Oussama",
            "address_details": "  Gate   3  ",
        },
        "promo_code": "ys26",
        "service_id": "svc_cpl",
        "date": _future_booking_date(),
        "slot": "slot_9_11",
        "note": "  Sonner   deux fois\u0000  ",
        "addon_ids": [],
        "client_request_id": "booking-1234",
    }
    payload.update(overrides)
    return payload


def _booking_count(engine) -> int:
    with session_scope(engine) as session:
        return session.scalar(select(func.count()).select_from(BookingRow)) or 0


def _ref_counter_value(engine) -> int:
    year = datetime.now(timezone.utc).year
    with session_scope(engine) as session:
        value = session.scalar(
            select(BookingRefCounterRow.last_counter).where(
                BookingRefCounterRow.year == year
            )
        )
        return value or 0


def test_create_booking_happy_path_persists_pending_api_booking(api_db, caplog):
    caplog.set_level(logging.INFO, logger="ewash.api")

    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_ewash_confirmation"
    assert body["ref"].startswith("EW-")
    assert body["price_dh"] == catalog.service_price("svc_cpl", "A", promo_code="YS26")
    assert body["total_dh"] == body["price_dh"]
    assert body["bookings_token"]
    case.assertEqual(
        body["line_items"],
        [
            {
                "kind": "main",
                "service_id": "svc_cpl",
                "label": body["service_label"],
                "price_dh": body["price_dh"],
                "regular_price_dh": catalog.public_service_price("svc_cpl", "A"),
                "sort_order": 0,
            }
        ],
    )

    with session_scope(api_db) as session:
        row = session.scalars(select(BookingRow)).one()
        assert row.ref == body["ref"]
        assert row.status == "pending_ewash_confirmation"
        assert row.source == "api"
        assert row.customer_phone == "212611204502"
        assert row.customer_name == "Oussama Test"
        assert row.car_model == "Dacia Logan"
        assert row.color == "Blanc"
        assert row.address == "Gate 3"
        assert row.address_text == "Gate 3"
        assert row.location_address == "Villa Oussama"
        assert row.note == "Sonner deux fois"
        assert row.client_request_id == "booking-1234"
        assert row.appointment_date.isoformat() == _future_booking_date()
        assert row.slot_id == "slot_9_11"
        assert row.appointment_start_at.hour == 9
        assert row.appointment_end_at.hour == 11

        customer = session.get(Customer, "212611204502")
        assert customer is not None
        assert customer.display_name == "Oussama Test"
        name = session.scalars(select(CustomerName)).one()
        assert name.display_name == "Oussama Test"
        token = session.scalars(select(CustomerTokenRow)).one()
        assert token.customer_phone == "212611204502"
        assert token.token_hash == hash_token(body["bookings_token"])

        line_item = session.scalars(select(BookingLineItemRow)).one()
        assert line_item.booking_id == row.id
        assert line_item.kind == "main"
        assert line_item.service_id == "svc_cpl"
        assert line_item.unit_price_dh == body["price_dh"]

        event = session.scalars(select(BookingStatusEventRow)).one()
        assert event.booking_id == row.id
        assert event.from_status == "draft"
        assert event.to_status == "pending_ewash_confirmation"
        assert event.actor == "customer"
        assert event.note == "Confirmation PWA"

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "ewash.api.bookings.create ref=" in messages
    assert "bookings.token minted ref=" in messages
    assert "source=api" in messages
    assert "phone_hash=" in messages
    assert "212611204502" not in messages


def test_create_booking_minimal_home_payload_persists_empty_strings_not_null(api_db):
    """Regression for ewash-len: a PWA booking that omits every optional
    string field (vehicle, pin_address, address_details, note, promo,
    addons, client_request_id) must persist with `""` on the matching
    nullable=False columns. SQLite is permissive about NULLs in these
    columns, but Postgres would 23502 on the first such request."""
    minimal = {
        "phone": "+212 611-204-502",
        "name": "Minimal Test",
        "category": "A",
        "location": {"kind": "home"},
        "service_id": "svc_cpl",
        "date": _future_booking_date(),
        "slot": "slot_9_11",
    }

    with _client() as client:
        response = client.post("/api/v1/bookings", json=minimal)

    assert response.status_code == 200, response.text

    with session_scope(api_db) as session:
        row = session.scalars(select(BookingRow)).one()

        # Every str column on BookingRow that maps to a Booking str field
        # must be a non-None string. The bug would surface as None on any
        # of these.
        for column_name in (
            "ref",
            "customer_name",
            "vehicle_type",
            "car_model",
            "color",
            "service_id",
            "service_bucket",
            "service_label",
            "promo_code",
            "promo_label",
            "location_mode",
            "center",
            "center_id",
            "geo",
            "address",
            "address_text",
            "location_name",
            "location_address",
            "date_label",
            "slot",
            "slot_id",
            "note",
            "addon_service",
            "addon_service_label",
        ):
            value = getattr(row, column_name)
            assert value is not None, f"BookingRow.{column_name} was NULL"
            assert isinstance(value, str), (
                f"BookingRow.{column_name} expected str, got {type(value).__name__}"
            )

        # int columns must be 0 (not None either)
        assert row.addon_price_dh == 0
        assert row.total_price_dh == row.price_dh

        # Nullable columns may legitimately be None — just sanity check
        # the booking actually landed.
        assert row.status == "pending_ewash_confirmation"
        assert row.customer_phone == "212611204502"


def test_create_booking_refs_are_monotonic(api_db):
    with _client() as client:
        first = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="booking-ref-1"),
        )
        second = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="booking-ref-2"),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    first_ref = first.json()["ref"]
    second_ref = second.json()["ref"]
    assert first_ref.startswith(f"EW-{datetime.now(timezone.utc).year}-")
    first_counter = int(first_ref.split("-")[-1])
    second_counter = int(second_ref.split("-")[-1])
    assert second_counter == first_counter + 1


def test_create_booking_customer_names_history_updates(api_db):
    with _client() as client:
        first = client.post(
            "/api/v1/bookings",
            json=_payload(name="Foo", client_request_id="booking-name-foo"),
        )
        second = client.post(
            "/api/v1/bookings",
            json=_payload(name="Bar", client_request_id="booking-name-bar"),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    with session_scope(api_db) as session:
        names = session.scalars(
            select(CustomerName).where(CustomerName.customer_phone == "212611204502")
        ).all()
        labels = {name.display_name for name in names}
        assert {"Foo", "Bar"}.issubset(labels)
        customer = session.get(Customer, "212611204502")
        assert customer is not None
        assert customer.display_name == "Bar"


def test_create_booking_pricing_matches_catalog_for_car_services(api_db, monkeypatch):
    # 18 POSTs share the default phone in _payload(), which would otherwise
    # trip the 5/hour per-phone cap on the 6th iteration if conftest's
    # generous env default ever drops. Pin a wide cap explicitly so this
    # test stays robust independent of conftest changes (ewash-zfi).
    monkeypatch.setattr(settings, "rate_limit_bookings_per_phone", "1000/hour")
    with _client() as client:
        for category in ("A", "B", "C"):
            for service_id in ("svc_ext", "svc_cpl", "svc_sal"):
                for promo_code in (None, "YS26"):
                    response = client.post(
                        "/api/v1/bookings",
                        json=_payload(
                            category=category,
                            service_id=service_id,
                            promo_code=promo_code,
                            client_request_id=(
                                "booking-price-"
                                f"{category}-{service_id.replace('_', '-')}-{promo_code or 'none'}"
                            ),
                        ),
                    )
                    assert response.status_code == 200
                    body = response.json()
                    assert body["price_dh"] == catalog.service_price(
                        service_id,
                        category,
                        promo_code=promo_code,
                    )
                    assert body["service_label"] == catalog.service_label(
                        service_id,
                        category,
                        promo_code=promo_code,
                    )


def test_create_booking_reuses_existing_same_phone_token(api_db, caplog):
    caplog.set_level(logging.INFO, logger="ewash.api")

    with _client() as client:
        first = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="booking-token-1"),
        ).json()
        second_response = client.post(
            "/api/v1/bookings",
            json=_payload(
                bookings_token=first["bookings_token"],
                client_request_id="booking-token-2",
            ),
        )

    assert second_response.status_code == 200
    assert second_response.json()["bookings_token"] == first["bookings_token"]

    with session_scope(api_db) as session:
        rows = session.scalars(select(CustomerTokenRow)).all()
        assert len(rows) == 1
        assert rows[0].customer_phone == "212611204502"

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "bookings.token reused ref=" in messages
    assert "212611204502" not in messages


def test_create_booking_mints_fresh_token_for_bogus_request_token(api_db, caplog):
    caplog.set_level(logging.INFO, logger="ewash.api")
    bogus = "not-a-real-token-12345"

    with _client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(
                bookings_token=bogus,
                client_request_id="booking-token-bogus",
            ),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["bookings_token"]
    assert body["bookings_token"] != bogus

    with session_scope(api_db) as session:
        rows = session.scalars(select(CustomerTokenRow)).all()
        assert len(rows) == 1
        assert rows[0].token_hash == hash_token(body["bookings_token"])
        assert rows[0].customer_phone == "212611204502"

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "bookings.token minted ref=" in messages
    assert bogus not in messages


def test_create_booking_mints_fresh_token_for_other_phone_token(api_db, caplog):
    other_phone = "212699999999"
    with session_scope(api_db) as session:
        session.add(Customer(phone=other_phone, display_name="Other"))
    other_token = persistence.mint_customer_token(other_phone, engine=api_db)
    caplog.set_level(logging.INFO, logger="ewash.api")

    with _client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(
                bookings_token=other_token,
                client_request_id="booking-token-other-phone",
            ),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["bookings_token"]
    assert body["bookings_token"] != other_token

    with session_scope(api_db) as session:
        rows = session.scalars(
            select(CustomerTokenRow).order_by(CustomerTokenRow.customer_phone)
        ).all()
        assert [row.customer_phone for row in rows] == ["212611204502", other_phone]
        assert any(row.token_hash == hash_token(body["bookings_token"]) for row in rows)
        assert any(row.token_hash == hash_token(other_token) for row in rows)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "bookings.token minted ref=" in messages
    assert other_phone not in messages


def test_create_booking_replays_same_client_request_id_for_same_caller(
    api_db,
    caplog,
):
    """The legitimate retry case: same phone + same client_request_id within
    the same device hits the replay path and gets back the original ref.
    A retry carrying the device's existing bookings_token echoes that token
    instead of creating an orphaned customer_tokens row."""
    caplog.set_level(logging.INFO, logger="ewash.api")

    with _client() as client:
        first = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="booking-idempotent-1"),
        ).json()
        same_response = client.post(
            "/api/v1/bookings",
            json=_payload(
                client_request_id="booking-idempotent-1",
                bookings_token=first["bookings_token"],
            ),
        )

    assert same_response.status_code == 200
    same = same_response.json()
    assert same["ref"] == first["ref"]
    assert same["line_items"] == first["line_items"]
    assert same["is_idempotent_replay"] is True
    assert same["bookings_token"]
    assert same["bookings_token"] == first["bookings_token"]

    with session_scope(api_db) as session:
        rows = session.scalars(select(BookingRow)).all()
        assert len(rows) == 1
        assert rows[0].client_request_id == "booking-idempotent-1"
        tokens = session.scalars(select(CustomerTokenRow)).all()
        assert len(tokens) == 1
        assert tokens[0].token_hash == hash_token(first["bookings_token"])

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "ewash.api.bookings.create ref=" in messages
    assert "ewash.api.idempotent_hit client_request_id=booking-idempotent-1" in messages


def test_create_booking_rejects_replay_from_different_caller_phone(api_db, caplog):
    """Security regression for ewash-416. A caller who learns a victim's
    ``client_request_id`` (e.g. from leaked logs) must NOT be able to replay
    the booking with a different ``body.phone`` and walk away with a fresh
    bookings_token bound to the victim's phone. The replay path now compares
    ``notifications.normalize_phone(body.phone)`` to ``row.customer_phone``
    and falls through on mismatch — the attacker hits the normal validation
    envelope without learning whether the ``client_request_id`` existed."""
    caplog.set_level(logging.INFO, logger="ewash.api")

    victim_phone = "+212 611-204-502"
    victim_phone_normalized = notifications.normalize_phone(victim_phone)
    other_phone = "+212 600-000-700"

    with _client() as client:
        first = client.post(
            "/api/v1/bookings",
            json=_payload(
                phone=victim_phone,
                client_request_id="booking-idempotent-attack-1",
            ),
        ).json()

        # Attacker knows the client_request_id but submits a different phone.
        # The replay path must reject; the fresh-create path then runs and
        # succeeds with a separate ref (different request from the server's
        # point of view), bound to the attacker's own phone.
        attack_response = client.post(
            "/api/v1/bookings",
            json=_payload(
                phone=other_phone,
                client_request_id="booking-idempotent-attack-1",
            ),
        )

        # And the same defense applies when the attacker submits a malformed
        # phone — no information is leaked about the existence of the id.
        malformed_response = client.post(
            "/api/v1/bookings",
            json=_payload(
                phone="definitely-not-a-phone",
                client_request_id="booking-idempotent-attack-1",
            ),
        )

    # The legitimate booking is untouched.
    assert first["ref"]
    assert first["bookings_token"]

    # The attacker's payload created a *fresh* booking under their own phone,
    # OR returned an integrity error from the partial unique index on
    # client_request_id (production Postgres). On SQLite (test path) the
    # column is not uniquely constrained at create_all time, so the request
    # succeeds with a new ref. Either way, the response MUST NOT contain the
    # victim's ref or a token bound to the victim's phone.
    assert attack_response.status_code in (200, 409, 422, 500)
    if attack_response.status_code == 200:
        attack = attack_response.json()
        assert attack["ref"] != first["ref"]
        assert attack["is_idempotent_replay"] is False
        # The minted token is bound to the attacker's phone, not the victim's.
        attack_token_phone = persistence.verify_customer_token(
            attack["bookings_token"], engine=api_db
        )
        assert attack_token_phone == notifications.normalize_phone(other_phone)
        assert attack_token_phone != victim_phone_normalized

    # Malformed phone returns the normal validation envelope (no special
    # treatment that would leak the id's existence).
    assert malformed_response.status_code == 400
    assert malformed_response.json()["error_code"] == "invalid_phone"

    # Neither attacker request should have produced an "idempotent_hit" log
    # line for the victim's id (that would tell an attacker their id-guess
    # landed). The "idempotent_mismatch" line is fine — it's hashed.
    messages = "\n".join(record.getMessage() for record in caplog.records)
    hit_lines = [
        line for line in messages.splitlines()
        if "ewash.api.idempotent_hit" in line
        and "booking-idempotent-attack-1" in line
    ]
    assert hit_lines == [], f"replay leaked via idempotent_hit log: {hit_lines}"


def test_create_booking_without_client_request_id_creates_distinct_bookings(api_db):
    with _client() as client:
        first = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id=None),
        )
        second = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id=None),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["ref"] != second.json()["ref"]
    assert first.json()["is_idempotent_replay"] is False
    assert second.json()["is_idempotent_replay"] is False

    with session_scope(api_db) as session:
        rows = session.scalars(select(BookingRow)).all()
        assert len(rows) == 2
        assert [row.client_request_id for row in rows] == [None, None]


def test_create_booking_integrity_error_replays_existing_client_request_id(
    api_db,
    monkeypatch,
):
    with _client() as client:
        first_response = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="booking-race-1"),
        )
        first = first_response.json()

        real_find = persistence.find_booking_by_client_request_id
        calls = {"find": 0}

        def flaky_find(client_request_id, *, session=None, engine=None):
            calls["find"] += 1
            if calls["find"] == 1:
                return None
            return real_find(client_request_id, session=session, engine=engine)

        def duplicate_write(*args, **kwargs):
            raise IntegrityError(
                "INSERT INTO bookings",
                {},
                Exception("duplicate client_request_id"),
            )

        monkeypatch.setattr(persistence, "find_booking_by_client_request_id", flaky_find)
        monkeypatch.setattr(persistence, "persist_confirmed_booking", duplicate_write)

        replay_response = client.post(
            "/api/v1/bookings",
            json=_payload(
                client_request_id="booking-race-1",
                bookings_token=first["bookings_token"],
            ),
        )

    assert replay_response.status_code == 200
    replay = replay_response.json()
    assert replay["ref"] == first["ref"]
    assert replay["is_idempotent_replay"] is True
    assert replay["bookings_token"]
    assert replay["bookings_token"] == first["bookings_token"]
    assert calls["find"] == 2

    with session_scope(api_db) as session:
        rows = session.scalars(select(BookingRow)).all()
        assert len(rows) == 1
        assert rows[0].client_request_id == "booking-race-1"
        tokens = session.scalars(select(CustomerTokenRow)).all()
        assert len(tokens) == 1


def test_create_booking_single_addon_persists_legacy_and_line_item(api_db):
    addon_id = "svc_cuir"
    addon_regular = catalog.service_price(addon_id, "A", promo_code="YS26")
    expected_addon_price = round(addon_regular * 0.9)

    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload(addon_ids=[addon_id]))

    assert response.status_code == 200
    body = response.json()
    assert body["total_dh"] == body["price_dh"] + expected_addon_price
    case.assertEqual(
        body["line_items"][1],
        {
            "kind": "addon",
            "service_id": addon_id,
            "label": f"{catalog.service_name(addon_id)} — {expected_addon_price} DH (-10%)",
            "price_dh": expected_addon_price,
            "regular_price_dh": addon_regular,
            "sort_order": 10,
        },
    )

    with session_scope(api_db) as session:
        row = session.scalars(select(BookingRow)).one()
        assert row.addon_service == addon_id
        assert row.addon_service_label == body["line_items"][1]["label"]
        assert row.addon_price_dh == expected_addon_price
        assert row.total_price_dh == body["total_dh"]

        line_items = session.scalars(
            select(BookingLineItemRow).order_by(BookingLineItemRow.sort_order)
        ).all()
        assert [(item.kind, item.service_id, item.total_price_dh) for item in line_items] == [
            ("main", "svc_cpl", body["price_dh"]),
            ("addon", addon_id, expected_addon_price),
        ]
        assert line_items[1].regular_price_dh == addon_regular
        assert line_items[1].discount_label == "-10% Esthétique"


def test_create_booking_multiple_addons_appends_all_and_denormalizes_first(
    api_db,
    caplog,
):
    addon_ids = ["svc_cuir", "svc_plastq", "svc_cer6m"]
    addon_prices = [
        round(catalog.service_price(addon_id, "A", promo_code="YS26") * 0.9)
        for addon_id in addon_ids
    ]
    caplog.set_level(logging.INFO, logger="ewash.api")

    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload(addon_ids=addon_ids))

    assert response.status_code == 200
    body = response.json()
    assert body["total_dh"] == body["price_dh"] + sum(addon_prices)
    assert [item["service_id"] for item in body["line_items"]] == ["svc_cpl"] + addon_ids
    assert [item["price_dh"] for item in body["line_items"][1:]] == addon_prices
    assert [item["sort_order"] for item in body["line_items"]] == [0, 10, 20, 30]

    with session_scope(api_db) as session:
        row = session.scalars(select(BookingRow)).one()
        assert row.addon_service == addon_ids[0]
        assert row.addon_price_dh == addon_prices[0]
        assert row.total_price_dh == body["total_dh"]

        line_items = session.scalars(
            select(BookingLineItemRow).order_by(BookingLineItemRow.sort_order)
        ).all()
        assert [(item.kind, item.service_id, item.total_price_dh) for item in line_items] == [
            ("main", "svc_cpl", body["price_dh"]),
            ("addon", addon_ids[0], addon_prices[0]),
            ("addon", addon_ids[1], addon_prices[1]),
            ("addon", addon_ids[2], addon_prices[2]),
        ]

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "bookings.addons added ref=" in messages
    assert "count=3" in messages
    assert "addon_ids=svc_cuir,svc_plastq,svc_cer6m" in messages
    assert f"total_dh={body['total_dh']}" in messages


def test_create_booking_invalid_input_does_not_advance_ref_counter(api_db):
    before = _ref_counter_value(api_db)

    with _client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(
                service_id="svc_moto",
                category="A",
                client_request_id="booking-invalid-ref",
            ),
        )

    assert response.status_code == 400
    assert response.headers["X-Ewash-Error-Code"] == "service_category_mismatch"
    assert _ref_counter_value(api_db) == before
    assert _booking_count(api_db) == 0


def test_create_booking_domain_rejection_returns_stable_error(api_db):
    with _client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(service_id="svc_moto"),
        )

    assert response.status_code == 400
    assert response.headers["X-Ewash-Error-Code"] == "service_category_mismatch"
    body = response.json()
    assert body["error_code"] == "service_category_mismatch"
    assert body["field"] == "service_id"

    with session_scope(api_db) as session:
        assert session.scalars(select(BookingRow)).all() == []


def test_create_booking_rolls_back_if_late_write_fails(api_db, monkeypatch):
    def fail_name_write(phone, display_name, *, engine=None, session=None):
        raise RuntimeError("name history failed")

    monkeypatch.setattr(persistence, "persist_customer_name", fail_name_write)

    with _client(raise_server_exceptions=False) as client:
        response = client.post("/api/v1/bookings", json=_payload())

    assert response.status_code == 500
    assert response.json()["error_code"] == "internal_error"
    with session_scope(api_db) as session:
        assert session.scalars(select(BookingRow)).all() == []
        assert session.scalars(select(BookingLineItemRow)).all() == []
        assert session.scalars(select(BookingStatusEventRow)).all() == []
        assert session.scalars(select(CustomerName)).all() == []
    assert booking_store.all_bookings() == []
    assert persistence.admin_booking_list(engine=api_db) == ()


def test_create_booking_returns_503_when_database_absent(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    persistence._configured_engine.cache_clear()
    catalog.catalog_cache_clear()

    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload())

    assert response.status_code == 503
    assert response.headers["X-Ewash-Error-Code"] == "db_unavailable"
    assert response.json()["error_code"] == "db_unavailable"


def test_create_booking_access_log_includes_ref_and_phone_hash(api_db, caplog):
    caplog.set_level(logging.INFO, logger="ewash.api")

    with _client(access_logging=True) as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="booking-access-log"),
        )

    assert response.status_code == 200
    body = response.json()
    records = [
        record
        for record in caplog.records
        if record.getMessage().startswith("ewash.api endpoint=/api/v1/bookings")
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "status=200" in message
    assert "phone_hash=" in message
    assert "phone_hash=-" not in message
    assert f"ref={body['ref']}" in message
    assert "212611204502" not in message


def test_create_booking_rate_limited_per_phone(api_db, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_bookings_per_phone", "3/hour")

    with _client() as client:
        for index in range(3):
            response = client.post(
                "/api/v1/bookings",
                json=_payload(client_request_id=f"booking-phone-limit-{index}"),
            )
            assert response.status_code == 200
        limited = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="booking-phone-limit-3"),
        )

    assert limited.status_code == 429
    assert limited.headers.get("Retry-After") is not None
    # Per-phone 429 envelope now matches the slowapi-keyed per-IP shape.
    assert limited.headers.get("X-Ewash-Error-Code") == "rate_limit_exceeded"
    assert limited.json()["error_code"] == "rate_limit_exceeded"
    assert limited.json()["scope"] == "per_phone"
    assert _booking_count(api_db) == 3


def test_create_booking_concurrent_writes_get_unique_refs(api_db, monkeypatch):
    async def noop_staff_alert(booking, *, event_label):
        return None

    monkeypatch.setattr(notifications, "notify_booking_confirmation_safe", noop_staff_alert)

    with _client() as client:
        seed = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="booking-concurrent-seed"),
        )
    assert seed.status_code == 200
    seed_counter = int(seed.json()["ref"].split("-")[-1])

    def hit(index: int) -> str:
        with _client() as client:
            response = client.post(
                "/api/v1/bookings",
                json=_payload(
                    phone=f"+212 611-204-51{index}",
                    client_request_id=f"booking-concurrent-{index}",
                ),
            )
            assert response.status_code == 200
            return response.json()["ref"]

    with ThreadPoolExecutor(max_workers=5) as executor:
        refs = list(executor.map(hit, range(5)))

    counters = sorted(int(ref.split("-")[-1]) for ref in refs)
    assert len(set(refs)) == 5
    assert counters == list(range(seed_counter + 1, seed_counter + 6))
    assert _booking_count(api_db) == 6


def test_create_booking_schedules_staff_alert_after_commit(api_db, monkeypatch):
    calls = []

    async def fake_staff_alert(booking, *, event_label):
        with session_scope(api_db) as session:
            row = session.scalars(select(BookingRow).where(BookingRow.ref == booking.ref)).one()
            calls.append((booking.ref, event_label, row.status, row.source))

    monkeypatch.setattr(notifications, "notify_booking_confirmation_safe", fake_staff_alert)

    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload())

    assert response.status_code == 200
    assert calls == [
        (
            response.json()["ref"],
            "Nouvelle réservation PWA",
            "pending_ewash_confirmation",
            "api",
        )
    ]


def test_create_booking_staff_alert_failure_does_not_fail_response(api_db, monkeypatch, caplog):
    async def failing_staff_alert(booking, *, event_label):
        raise RuntimeError("meta down")

    monkeypatch.setattr(notifications, "notify_booking_confirmation", failing_staff_alert)
    caplog.set_level(logging.ERROR, logger="app.notifications")

    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload())

    assert response.status_code == 200
    with session_scope(api_db) as session:
        row = session.scalars(select(BookingRow)).one()
        assert row.ref == response.json()["ref"]
        assert row.status == "pending_ewash_confirmation"

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "notifications.staff_alert failed ref=" in messages
    assert "event=Nouvelle réservation PWA" in messages


def test_non_api_path_exception_does_not_leak_pwa_json_envelope():
    """Regression for ewash-72z: the API ``Exception`` handler must not
    intercept ``/admin/*`` or other non-API routes. Before the fix, any
    unhandled exception on a non-API route was caught by
    ``api_exception_handler`` and returned the PWA envelope
    ``{"error_code": "internal_error", ...}`` with content-type
    ``application/json`` — confusing the admin operator and preempting
    each route's own redirect-with-flash behaviour.
    """
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(api.router)
    api.install_exception_handlers(app)

    @app.post("/admin/bookings/confirm")
    async def _explode_admin():  # pragma: no cover - body never returns
        raise RuntimeError("boom from admin path")

    @app.post("/api/v1/__diag_boom")
    async def _explode_api():  # pragma: no cover - body never returns
        raise RuntimeError("boom from api path")

    with TestClient(app, raise_server_exceptions=False) as client:
        admin_response = client.post("/admin/bookings/confirm")
        api_response = client.post("/api/v1/__diag_boom")

    assert admin_response.status_code == 500
    assert admin_response.text == "Internal Server Error"
    admin_content_type = admin_response.headers.get("content-type", "")
    assert "application/json" not in admin_content_type
    assert "X-Ewash-Error-Code" not in admin_response.headers

    assert api_response.status_code == 500
    assert api_response.headers.get("content-type", "").startswith("application/json")
    api_body = api_response.json()
    assert api_body["error_code"] == "internal_error"
    assert api_response.headers.get("X-Ewash-Error-Code") == "internal_error"
