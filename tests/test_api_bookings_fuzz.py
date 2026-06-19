"""Adversarial corpus tests for POST /api/v1/bookings.

These are structure-aware fuzz seeds for the PWA booking boundary: malformed
JSON, hostile scalar values, schema smuggling attempts, token/idempotency edge
cases, and rate-limit interactions. The invariant for rejected requests is:
never 500, always JSON, and no BookingRow side effects.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from limits import parse
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, select

from app import api, api_validation, booking as booking_store, catalog, notifications, persistence
from app.config import settings
from app.db import init_db, make_engine, session_scope
from app.models import BookingRow
from app.rate_limit import (
    PerPhoneRateLimitExceeded,
    limiter,
    per_phone_rate_limit_handler,
    rate_limit_exceeded_handler,
)


REJECTION_STATUSES = {400, 401, 403, 422, 429, 503}


def _client() -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(PerPhoneRateLimitExceeded, per_phone_rate_limit_handler)
    app.include_router(api.router)
    api.install_exception_handlers(app)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def api_db(monkeypatch, tmp_path):
    booking_store._bookings.clear()
    monkeypatch.setattr(booking_store, "_counter", 0)
    monkeypatch.setattr(settings, "rate_limit_bookings_per_phone", "1000/hour")

    _pin_validator_now(
        monkeypatch,
        datetime(2026, 6, 14, 18, 0, tzinfo=api_validation.CASABLANCA_TZ),
    )

    async def noop_staff_alert(booking, *, event_label):
        return None

    monkeypatch.setattr(
        notifications,
        "notify_booking_confirmation_safe",
        noop_staff_alert,
    )

    db_url = f"sqlite+pysqlite:///{tmp_path / 'api-bookings-fuzz.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    persistence._configured_engine.cache_clear()
    catalog.catalog_cache_clear()
    notifications.notification_cache_clear()
    limiter.reset()
    try:
        yield engine
    finally:
        limiter.reset()
        persistence._configured_engine.cache_clear()
        catalog.catalog_cache_clear()
        notifications.notification_cache_clear()
        booking_store._bookings.clear()


def _pin_validator_now(monkeypatch, fixed_now: datetime) -> None:
    real_datetime = api_validation.datetime

    class _FakeDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(api_validation, "datetime", _FakeDateTime)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phone": "+212 611-204-502",
        "name": "Oussama Test",
        "category": "A",
        "vehicle": {"make": "Dacia Logan", "color": "Blanc"},
        "location": {"kind": "home", "pin_address": "Villa X"},
        "promo_code": "ys26",
        "service_id": "svc_cpl",
        "date": "2026-06-15",
        "slot": "slot_9_11",
        "addon_ids": [],
        "client_request_id": "fuzz-booking-0001",
    }
    payload.update(overrides)
    return payload


def _nested(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"leaf": "value"}
    for index in range(depth):
        node = {f"level_{index}": node}
    return node


def _booking_count(engine) -> int:
    with session_scope(engine) as session:
        return session.scalar(select(func.count()).select_from(BookingRow)) or 0


def _booking_rows(engine) -> list[BookingRow]:
    with session_scope(engine) as session:
        return list(session.scalars(select(BookingRow).order_by(BookingRow.id)))


def _json_body(response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:  # pragma: no cover - failure path gives clearer output.
        raise AssertionError(f"response was not JSON: {response.text[:500]}") from exc
    assert isinstance(body, dict), body
    return body


def _assert_json_error_response(response) -> dict[str, Any]:
    assert response.status_code in REJECTION_STATUSES, response.text
    assert response.status_code != 500, response.text
    body = _json_body(response)
    if "error_code" in body:
        assert isinstance(body["error_code"], str) and body["error_code"]
        assert isinstance(body.get("message", ""), str)
    else:
        # FastAPI emits its built-in validation envelope for JSON parse and
        # Pydantic contract errors before the route body runs.
        assert response.status_code == 422, body
        assert isinstance(body.get("detail"), list) and body["detail"]
    return body


def _assert_rejected_without_booking(engine, response) -> dict[str, Any]:
    body = _assert_json_error_response(response)
    assert _booking_count(engine) == 0
    return body


@contextmanager
def _temporary_route_limit(endpoint_name: str, limit_str: str):
    route_key = f"app.api.{endpoint_name}"
    route_limit = limiter._route_limits[route_key][0]
    original = route_limit.limit
    route_limit.limit = parse(limit_str)
    limiter.reset()
    try:
        yield
    finally:
        route_limit.limit = original
        limiter.reset()


MALFORMED_BODIES = [
    pytest.param({"content": "", "headers": {"content-type": "application/json"}}, id="empty-body"),
    pytest.param(
        {"content": '{"phone": "+212 611"', "headers": {"content-type": "application/json"}},
        id="partial-json",
    ),
    pytest.param({"json": []}, id="array-top-level"),
    pytest.param({"json": 123}, id="number-top-level"),
    pytest.param({"json": {}}, id="empty-object"),
    pytest.param({"json": _nested(10)}, id="deeply-nested-object"),
    pytest.param({"json": _payload(name="x" * 10_000)}, id="very-long-string"),
    pytest.param(
        {"content": json.dumps(_payload(service_id="svc_cpl\u0000")), "headers": {"content-type": "application/json"}},
        id="null-byte-service-id",
    ),
    pytest.param({"json": _payload(service_id="svc_cpl\u202e")}, id="unicode-rtl-service-id"),
    pytest.param({"json": _payload(location=[])}, id="array-where-location-object-expected"),
]


@pytest.mark.parametrize("request_kwargs", MALFORMED_BODIES)
def test_malformed_json_and_body_shapes_do_not_500_or_persist(api_db, request_kwargs):
    with _client() as client:
        response = client.post("/api/v1/bookings", **request_kwargs)

    _assert_rejected_without_booking(api_db, response)


PHONE_REJECTION_CASES = [
    pytest.param("1234567", id="too-short-digits"),
    pytest.param(" " * 8, id="spaces-only"),
    pytest.param("abcdefgh", id="non-digit-text"),
    pytest.param("++++++++", id="plus-signs-only"),
    pytest.param("1e12-0000", id="scientific-notation-looking"),
    pytest.param("+999 123456789012345678", id="non-moroccan-too-long"),
    pytest.param("+212 (06)", id="parens-too-short"),
    pytest.param("0" * 21, id="very-long-digits"),
]


@pytest.mark.parametrize("phone", PHONE_REJECTION_CASES)
def test_phone_validation_rejects_adversarial_numbers_without_persisting(api_db, phone):
    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload(phone=phone))

    _assert_rejected_without_booking(api_db, response)


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        pytest.param("0611204502", "212611204502", id="local-leading-zero"),
        pytest.param("+212 (611) 204-502", "212611204502", id="plus-spaces-parens"),
    ],
)
def test_supported_phone_formats_still_normalize_under_fuzzing(api_db, phone, expected):
    with _client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(phone=phone, client_request_id=f"phone-ok-{expected[-4:]}"),
        )

    assert response.status_code == 200, response.text
    rows = _booking_rows(api_db)
    assert len(rows) == 1
    assert rows[0].customer_phone == expected


DATE_SLOT_REJECTION_CASES = [
    pytest.param({"date": "2025-01-01"}, id="past-date"),
    pytest.param({"date": "2026/06/15"}, id="slashes-not-iso-date"),
    pytest.param({"date": "2026-13-01"}, id="invalid-month"),
    pytest.param({"date": "2027-02-29"}, id="non-leap-day"),
    pytest.param({"date": "2024-02-29"}, id="leap-day-in-past"),
    pytest.param({"date": "2036-06-15T00:00:00+01:00"}, id="ten-year-future-datetime"),
    pytest.param({"date": "2026-03-29T02:30:00+01:00"}, id="dst-like-timezone-string"),
    pytest.param({"slot": "slot_24_26"}, id="unknown-slot"),
    pytest.param({"slot": "slot_9_11;DROP TABLE bookings"}, id="sql-flavored-slot"),
]


@pytest.mark.parametrize("overrides", DATE_SLOT_REJECTION_CASES)
def test_date_and_slot_edges_reject_without_persisting(api_db, overrides):
    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload(**overrides))

    _assert_rejected_without_booking(api_db, response)


PRICE_ABUSE_CASES = [
    pytest.param({"quantity": -1}, id="negative-quantity-extra"),
    pytest.param({"quantity": 0}, id="zero-quantity-extra"),
    pytest.param({"quantity": 10**12}, id="huge-quantity-extra"),
    pytest.param({"quantity": 1.5}, id="float-quantity-extra"),
    pytest.param({"quantity": "1e9"}, id="scientific-quantity-extra"),
    pytest.param({"price_dh": -1}, id="negative-price-extra"),
    pytest.param({"price_dh": "NaN"}, id="nan-price-string-extra"),
    pytest.param({"total_dh": "Infinity"}, id="infinity-total-string-extra"),
    pytest.param({"line_items": [{"service_id": "svc_cpl", "quantity": -1}]}, id="line-item-smuggling"),
]


@pytest.mark.parametrize("overrides", PRICE_ABUSE_CASES)
def test_quantity_and_price_smuggling_is_rejected_without_persisting(api_db, overrides):
    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload(**overrides))

    _assert_rejected_without_booking(api_db, response)


SERVICE_ADDON_REJECTION_CASES = [
    pytest.param({"service_id": "svc_nope"}, id="unknown-service"),
    pytest.param({"service_id": ""}, id="empty-service"),
    pytest.param({"service_id": "svc_cpl'; DROP TABLE bookings; --"}, id="sql-service"),
    pytest.param({"service_id": "550e8400-e29b-41d4-a716-446655440000"}, id="uuid-service"),
    pytest.param({"service_id": "svc_" + "x" * 100}, id="very-long-service"),
    pytest.param({"addon_ids": ["svc_nope"]}, id="unknown-addon"),
    pytest.param({"addon_ids": [""]}, id="empty-addon"),
    pytest.param({"addon_ids": ["svc_cpl"]}, id="addon-equals-main"),
    pytest.param({"addon_ids": ["svc_cuir", "svc_cuir"]}, id="duplicate-addon"),
    pytest.param({"addon_ids": ["x" * 100]}, id="very-long-addon"),
]


@pytest.mark.parametrize("overrides", SERVICE_ADDON_REJECTION_CASES)
def test_service_and_addon_id_abuse_rejects_without_persisting(api_db, overrides):
    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload(**overrides))

    _assert_rejected_without_booking(api_db, response)


PROMO_REJECTION_CASES = [
    pytest.param("Y" * 41, id="too-long-promo"),
    pytest.param({"code": "YS26"}, id="object-promo"),
    pytest.param(["YS26"], id="array-promo"),
]


@pytest.mark.parametrize("promo_code", PROMO_REJECTION_CASES)
def test_schema_rejects_promo_code_shape_abuse_without_persisting(api_db, promo_code):
    with _client() as client:
        response = client.post("/api/v1/bookings", json=_payload(promo_code=promo_code))

    _assert_rejected_without_booking(api_db, response)


@pytest.mark.parametrize(
    ("promo_code", "expected_price"),
    [
        pytest.param("expired2020", catalog.public_service_price("svc_cpl", "A"), id="expired-looking"),
        pytest.param("YS26!@#", catalog.public_service_price("svc_cpl", "A"), id="special-chars"),
        pytest.param("", catalog.public_service_price("svc_cpl", "A"), id="empty-promo"),
        pytest.param("ys26", catalog.service_price("svc_cpl", "A", promo_code="YS26"), id="lowercase-valid"),
    ],
)
def test_promo_code_fuzz_inputs_do_not_let_client_set_price(api_db, promo_code, expected_price):
    with _client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(
                promo_code=promo_code,
                client_request_id=f"promo-{promo_code or 'empty'}".replace("!", "x").replace("@", "x").replace("#", "x"),
            ),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["price_dh"] == expected_price
    rows = _booking_rows(api_db)
    assert len(rows) == 1
    assert rows[0].price_dh == expected_price


CLIENT_REQUEST_ID_REJECTION_CASES = [
    pytest.param("", id="empty-client-request-id"),
    pytest.param("short", id="too-short-client-request-id"),
    pytest.param("x" * 65, id="too-long-client-request-id"),
    pytest.param("abc';drop", id="sql-client-request-id"),
    pytest.param("booking-\u212a", id="unicode-client-request-id"),
]


@pytest.mark.parametrize("client_request_id", CLIENT_REQUEST_ID_REJECTION_CASES)
def test_client_request_id_shape_abuse_rejects_without_persisting(api_db, client_request_id):
    with _client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id=client_request_id),
        )

    _assert_rejected_without_booking(api_db, response)


def test_same_client_request_id_replays_without_duplicate_booking(api_db):
    request_id = "fuzz-same-request-id"

    with _client() as client:
        first_response = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id=request_id),
        )
        first = first_response.json()
        replay_response = client.post(
            "/api/v1/bookings",
            json=_payload(
                client_request_id=request_id,
                bookings_token=first["bookings_token"],
            ),
        )

    assert first_response.status_code == 200, first_response.text
    assert replay_response.status_code == 200, replay_response.text
    replay = replay_response.json()
    assert replay["ref"] == first["ref"]
    assert replay["bookings_token"] == first["bookings_token"]
    assert replay["is_idempotent_replay"] is True
    assert _booking_count(api_db) == 1


TOKEN_REJECTION_CASES = [
    pytest.param("x" * 129, id="too-long-token"),
    pytest.param({"token": "opaque"}, id="object-token"),
    pytest.param(["opaque"], id="array-token"),
]


@pytest.mark.parametrize("bookings_token", TOKEN_REJECTION_CASES)
def test_bookings_token_shape_abuse_rejects_without_persisting(api_db, bookings_token):
    with _client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_payload(bookings_token=bookings_token),
        )

    _assert_rejected_without_booking(api_db, response)


def test_missing_or_malformed_token_on_fresh_booking_does_not_crash(api_db):
    with _client() as client:
        missing = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="token-missing-ok"),
        )
        malformed = client.post(
            "/api/v1/bookings",
            json=_payload(
                client_request_id="token-malformed-jwt-looking",
                bookings_token="eyJhbGciOiJIUzI1NiJ9.expired.signature",
            ),
        )

    assert missing.status_code == 200, missing.text
    assert malformed.status_code == 200, malformed.text
    assert missing.json()["bookings_token"]
    assert malformed.json()["bookings_token"]
    assert malformed.json()["bookings_token"] != "eyJhbGciOiJIUzI1NiJ9.expired.signature"
    assert _booking_count(api_db) == 2


def test_mismatched_phone_token_mints_fresh_token_for_new_phone(api_db):
    with _client() as client:
        first_response = client.post(
            "/api/v1/bookings",
            json=_payload(
                phone="+212 611-204-502",
                client_request_id="token-owner-phone",
            ),
        )
        first_token = first_response.json()["bookings_token"]
        second_response = client.post(
            "/api/v1/bookings",
            json=_payload(
                phone="+212 600-000-701",
                client_request_id="token-other-phone",
                bookings_token=first_token,
            ),
        )

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert second["bookings_token"]
    assert second["bookings_token"] != first_token
    assert persistence.verify_customer_token(second["bookings_token"], engine=api_db) == "212600000701"
    assert _booking_count(api_db) == 2


def test_per_ip_rate_limit_rejects_rapid_fire_posts_without_extra_persist(api_db, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_bookings_per_phone", "1000/hour")

    with _client() as client, _temporary_route_limit("create_booking", "2/hour"):
        first = client.post(
            "/api/v1/bookings",
            json=_payload(phone="212611200001", client_request_id="ip-fuzz-1"),
        )
        second = client.post(
            "/api/v1/bookings",
            json=_payload(phone="212611200002", client_request_id="ip-fuzz-2"),
        )
        limited = client.post(
            "/api/v1/bookings",
            json=_payload(phone="212611200003", client_request_id="ip-fuzz-3"),
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    body = _assert_json_error_response(limited)
    assert body["error_code"] == "rate_limit_exceeded"
    assert _booking_count(api_db) == 2


def test_per_phone_rate_limit_allows_post_after_short_window_lifts(api_db, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_bookings_per_phone", "1/second")

    with _client() as client:
        first = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="phone-window-1"),
        )
        limited = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="phone-window-2"),
        )
        assert first.status_code == 200, first.text
        body = _assert_json_error_response(limited)
        assert body["error_code"] == "rate_limit_exceeded"
        assert body["scope"] == "per_phone"
        assert _booking_count(api_db) == 1

        time.sleep(1.1)
        after_lift = client.post(
            "/api/v1/bookings",
            json=_payload(client_request_id="phone-window-3"),
        )

    assert after_lift.status_code == 200, after_lift.text
    assert _booking_count(api_db) == 2
