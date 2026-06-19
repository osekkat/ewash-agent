"""Tests for app.api_validation — the server-side contract validators.

Each test uses a pinned `now` to keep the freshness check deterministic.
The default closed-date set comes from the static catalog (2026-05-27 and
2026-05-28 — Eid al-Adha). The default active slots come from `SLOTS` in
`app/catalog.py`. No database fixture is required: when no engine is configured
the catalog helpers fall back to the static data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api as pwa_api, catalog, notifications, persistence
from app.api_validation import (
    APIValidationError,
    CASABLANCA_TZ,
    CenterIdNotAllowed,
    ClosedDate,
    DuplicateAddon,
    InvalidDate,
    InvalidServiceForCategory,
    MissingCenterId,
    NotADetailingService,
    SlotTooSoon,
    UnknownAddon,
    UnknownCenter,
    UnknownService,
    UnknownSlot,
    validate_addon_ids,
    validate_center_id,
    validate_service_for_category,
    validate_slot_and_date,
)
from app.config import settings
from app.db import init_db, make_engine
from app.notifications import InvalidPhone
from app.rate_limit import limiter


def test_closed_date_raises_closed_date() -> None:
    # 2026-05-27 is Eid al-Adha in CLOSED_DATES — must be rejected even though
    # the slot itself is valid and the time is far enough in the future.
    with pytest.raises(ClosedDate) as exc_info:
        validate_slot_and_date(
            "2026-05-27",
            "slot_9_11",
            now=datetime(2026, 5, 20, 9, 0, tzinfo=CASABLANCA_TZ),
        )
    assert exc_info.value.error_code == "closed_date"


def test_unknown_slot_raises_unknown_slot() -> None:
    with pytest.raises(UnknownSlot) as exc_info:
        validate_slot_and_date(
            "2026-06-15",
            "slot_99_99",
            now=datetime(2026, 6, 14, 9, 0, tzinfo=CASABLANCA_TZ),
        )
    assert exc_info.value.error_code == "unknown_slot"


def test_slot_30_minutes_in_future_raises_too_soon() -> None:
    # slot_9_11 starts at 09:00 Casablanca. With now=08:30 the lead: 30 min.
    with pytest.raises(SlotTooSoon) as exc_info:
        validate_slot_and_date(
            "2026-06-15",
            "slot_9_11",
            now=datetime(2026, 6, 15, 8, 30, tzinfo=CASABLANCA_TZ),
        )
    assert exc_info.value.error_code == "slot_too_soon"


def test_slot_2h_1min_in_future_passes() -> None:
    # slot_11_13 starts at 11:00 Casablanca. With now=08:59 the lead: 2h 1min.
    # No exception should be raised.
    validate_slot_and_date(
        "2026-06-15",
        "slot_11_13",
        now=datetime(2026, 6, 15, 8, 59, tzinfo=CASABLANCA_TZ),
    )


def test_slot_5h_in_future_passes() -> None:
    # slot_14_16 starts at 14:00 Casablanca. With now=09:00 the lead: 5h.
    validate_slot_and_date(
        "2026-06-15",
        "slot_14_16",
        now=datetime(2026, 6, 15, 9, 0, tzinfo=CASABLANCA_TZ),
    )


def test_home_slot_less_than_4_working_hours_rejected() -> None:
    # Home booking rule: now=10:30, slot_14_16 starts at 14:00 → 3h30 working
    # lead, which is below the required 4 operational hours.
    with pytest.raises(SlotTooSoon) as exc_info:
        validate_slot_and_date(
            "2026-06-15",
            "slot_14_16",
            now=datetime(2026, 6, 15, 10, 30, tzinfo=CASABLANCA_TZ),
            location_kind="home",
        )
    assert exc_info.value.error_code == "slot_too_soon"


def test_home_slot_exactly_4_working_hours_passes() -> None:
    validate_slot_and_date(
        "2026-06-15",
        "slot_14_16",
        now=datetime(2026, 6, 15, 10, 0, tzinfo=CASABLANCA_TZ),
        location_kind="home",
    )


def test_home_working_hours_skip_after_hours() -> None:
    # At 21:00, only 1 working hour remains today (until 22:00), so the
    # 4-working-hour cutoff lands tomorrow at 12:00. The 11h slot is too soon;
    # the 14h slot is acceptable.
    with pytest.raises(SlotTooSoon):
        validate_slot_and_date(
            "2026-06-16",
            "slot_11_13",
            now=datetime(2026, 6, 15, 21, 0, tzinfo=CASABLANCA_TZ),
            location_kind="home",
        )
    validate_slot_and_date(
        "2026-06-16",
        "slot_14_16",
        now=datetime(2026, 6, 15, 21, 0, tzinfo=CASABLANCA_TZ),
        location_kind="home",
    )


def test_bad_date_format_raises_invalid_date() -> None:
    with pytest.raises(InvalidDate) as exc_info:
        validate_slot_and_date(
            "2026/06/15",
            "slot_9_11",
            now=datetime(2026, 6, 14, 9, 0, tzinfo=CASABLANCA_TZ),
        )
    assert exc_info.value.error_code == "invalid_date"


def test_utc_now_converted_to_casablanca_tz() -> None:
    # 06:00 UTC means 07:00 Casablanca (UTC+1, no DST mid-summer). With slot_18_20
    # on 2026-07-15 that's an 11-hour lead — well above the 2h threshold. The
    # test asserts the function accepts a UTC `now` and converts it, instead of
    # crashing on a non-Casablanca tz input.
    validate_slot_and_date(
        "2026-07-15",
        "slot_18_20",
        now=datetime(2026, 7, 15, 6, 0, tzinfo=timezone.utc),
    )

    # Boundary check the other way: 09:00 UTC = 10:00 Casablanca. slot_11_13
    # starts at 11:00 Casablanca, lead = 1h → too soon. Confirms the tz math
    # is applied before the freshness comparison.
    with pytest.raises(SlotTooSoon):
        validate_slot_and_date(
            "2026-07-15",
            "slot_11_13",
            now=datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
        )


def test_exceptions_are_value_error_subclasses() -> None:
    # Epic 6pa.3 acceptance criterion: validators raise ValueError with a
    # stable `error_code` attribute. APIValidationError subclasses ValueError,
    # and each concrete error class sets `error_code` at the class level so
    # the API layer can read it without instantiating.
    assert issubclass(APIValidationError, ValueError)
    for cls, code in (
        (ClosedDate, "closed_date"),
        (UnknownSlot, "unknown_slot"),
        (SlotTooSoon, "slot_too_soon"),
        (InvalidDate, "invalid_date"),
    ):
        assert issubclass(cls, APIValidationError)
        assert cls.error_code == code


def test_naive_now_rejected() -> None:
    # Defensive: a naive `now` would silently use local-tz semantics inside
    # `.astimezone()`, which is platform-dependent and flaky. Document that
    # callers must pass tz-aware datetimes.
    with pytest.raises((TypeError, ValueError)):
        validate_slot_and_date(
            "2026-06-15",
            "slot_9_11",
            now=datetime(2026, 6, 15, 8, 30),
        )


def test_static_closed_dates_uses_eid_2026_day_2() -> None:
    # Belt-and-suspenders: confirm CLOSED_DATES includes the second Eid day too,
    # so a bug that only checks the first day would still be caught.
    with pytest.raises(ClosedDate):
        validate_slot_and_date(
            "2026-05-28",
            "slot_9_11",
            now=datetime(2026, 5, 20, 9, 0, tzinfo=CASABLANCA_TZ),
        )


def test_exact_2h_boundary_passes() -> None:
    # slot_9_11 starts at 09:00 Casablanca. With now=07:00 the lead is exactly
    # 2h. The check is `candidate < now + 2h`, so exactly 2h is OK.
    validate_slot_and_date(
        "2026-06-15",
        "slot_9_11",
        now=datetime(2026, 6, 15, 7, 0, tzinfo=CASABLANCA_TZ),
    )


def test_double_digit_slot_id_parses() -> None:
    # slot_20_22 has double-digit hours on both sides. now=10:00 → lead=10h → ok.
    validate_slot_and_date(
        "2026-06-15",
        "slot_20_22",
        now=datetime(2026, 6, 15, 10, 0, tzinfo=CASABLANCA_TZ),
    )


def test_passing_now_in_arbitrary_tz_works() -> None:
    # Tokyo is UTC+9; 2026-07-15 17:00 Tokyo = 09:00 Casablanca.
    # slot_14_16 starts at 14:00 Casablanca → lead=5h → ok.
    tokyo = ZoneInfo("Asia/Tokyo")
    validate_slot_and_date(
        "2026-07-15",
        "slot_14_16",
        now=datetime(2026, 7, 15, 17, 0, tzinfo=tokyo),
    )


# ─────────────────────────────────────────────────────────────────────────────
# validate_service_for_category
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_service_for_category_car_service_with_car_category() -> None:
    # svc_cpl is a car wash service; category B is a car category.
    validate_service_for_category("svc_cpl", "B")


def test_validate_service_for_category_moto_service_with_moto_category() -> None:
    validate_service_for_category("svc_moto", "MOTO")
    validate_service_for_category("svc_scooter", "MOTO")


def test_validate_service_for_category_moto_service_with_car_rejected() -> None:
    with pytest.raises(InvalidServiceForCategory) as exc_info:
        validate_service_for_category("svc_moto", "A")
    assert exc_info.value.error_code == "service_category_mismatch"


def test_validate_service_for_category_car_service_with_moto_rejected() -> None:
    # svc_cpl is car-only; pairing with MOTO must raise.
    with pytest.raises(InvalidServiceForCategory) as exc_info:
        validate_service_for_category("svc_cpl", "MOTO")
    assert exc_info.value.error_code == "service_category_mismatch"


def test_validate_service_for_category_unknown_service_raises() -> None:
    with pytest.raises(UnknownService) as exc_info:
        validate_service_for_category("svc_does_not_exist", "A")
    assert exc_info.value.error_code == "unknown_service"


def test_validate_service_for_category_all_car_categories_accepted() -> None:
    # Every car category (A/B/C) should pair cleanly with a car detailing service.
    for category in ("A", "B", "C"):
        validate_service_for_category("svc_pol", category)


def test_validate_service_for_category_logs_rejection(caplog) -> None:
    # Staff might want to grep "validation.rejection" to spot stale PWA clients
    # hammering invalid combos. Confirm the INFO line is emitted.
    import logging

    caplog.set_level(logging.INFO, logger="app.api_validation")
    with pytest.raises(InvalidServiceForCategory):
        validate_service_for_category("svc_cpl", "MOTO")
    assert any("validation.rejection" in rec.message for rec in caplog.records)


def test_service_validation_exceptions_are_api_validation_errors() -> None:
    # Same hierarchy contract as the slot/date validators.
    assert issubclass(UnknownService, APIValidationError)
    assert issubclass(InvalidServiceForCategory, APIValidationError)
    assert UnknownService.error_code == "unknown_service"
    assert InvalidServiceForCategory.error_code == "service_category_mismatch"


def test_validate_addon_ids_empty_list_returns_empty() -> None:
    # Customer skipped the upsell. Empty input is the common case, must pass.
    assert validate_addon_ids([], main_service_id="svc_cpl") == []


def test_validate_addon_ids_returns_same_list_for_all_valid_detailing() -> None:
    # Two real detailing services from SERVICES_DETAILING — should round-trip.
    addons = ["svc_cuir", "svc_plastq"]
    assert validate_addon_ids(addons, main_service_id="svc_cpl") == addons


def test_validate_addon_ids_rejects_unknown_id() -> None:
    with pytest.raises(UnknownAddon) as exc_info:
        validate_addon_ids(["svc_does_not_exist"], main_service_id="svc_cpl")
    assert exc_info.value.error_code == "unknown_addon"


def test_validate_addon_ids_rejects_wash_service() -> None:
    # svc_cpl is wash-bucket — has its own pricing per category, must not
    # be used as a free-form addon. main_service_id is a detailing id so the
    # equals-main check doesn't shadow the wash-bucket rejection.
    with pytest.raises(NotADetailingService) as exc_info:
        validate_addon_ids(["svc_cpl"], main_service_id="svc_pol")
    assert exc_info.value.error_code == "not_a_detailing_service"
    assert "bucket=wash" in str(exc_info.value)


def test_validate_addon_ids_rejects_moto_service() -> None:
    # svc_moto is in SERVICES_MOTO — also not a detailing service.
    with pytest.raises(NotADetailingService) as exc_info:
        validate_addon_ids(["svc_moto"], main_service_id="svc_cpl")
    assert exc_info.value.error_code == "not_a_detailing_service"
    assert "bucket=moto" in str(exc_info.value)


def test_validate_addon_ids_rejects_duplicates() -> None:
    # Two BookingLineItemRow rows with the same service_id would be confusing
    # admin-side and add no value to the customer's upsell list.
    with pytest.raises(DuplicateAddon) as exc_info:
        validate_addon_ids(["svc_cuir", "svc_cuir"], main_service_id="svc_cpl")
    assert exc_info.value.error_code == "duplicate_addon"


def test_addon_id_equal_to_service_id_rejected() -> None:
    # Detailing services live in BOTH SERVICES_CAR (so they pass
    # validate_service_for_category) AND SERVICES_DETAILING (so they pass
    # validate_addon_ids). A payload like {service_id: "svc_pol", addon_ids:
    # ["svc_pol"]} would persist two BookingLineItemRow rows — main at full
    # price plus addon at 20% off — and double-charge the customer for one
    # service. The validator must reject this before persistence.
    with pytest.raises(DuplicateAddon) as exc_info:
        validate_addon_ids(["svc_pol"], main_service_id="svc_pol")
    assert exc_info.value.error_code == "duplicate_addon"
    assert "equals main service_id" in str(exc_info.value)


def test_addon_id_equal_to_service_id_rejected_even_with_other_valid_addons() -> None:
    # The duplicate-against-main check must scan the full list, not just
    # short-circuit on a singleton. svc_cuir is a legitimate addon; svc_pol
    # collides with the main service and is what we expect to be flagged.
    with pytest.raises(DuplicateAddon) as exc_info:
        validate_addon_ids(["svc_cuir", "svc_pol"], main_service_id="svc_pol")
    assert exc_info.value.error_code == "duplicate_addon"
    assert "svc_pol" in str(exc_info.value)


def test_validate_addon_ids_logs_rejection(caplog) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="app.api_validation")
    with pytest.raises(UnknownAddon):
        validate_addon_ids(["svc_unknown"], main_service_id="svc_cpl")
    assert any(
        "validation.rejection" in rec.message and "addon_id=svc_unknown" in rec.message
        for rec in caplog.records
    )


def test_validate_addon_ids_exceptions_are_api_validation_errors() -> None:
    # Stable error_code contract for all three addon-validation exceptions.
    assert issubclass(UnknownAddon, APIValidationError)
    assert issubclass(DuplicateAddon, APIValidationError)
    assert issubclass(NotADetailingService, APIValidationError)
    assert UnknownAddon.error_code == "unknown_addon"
    assert DuplicateAddon.error_code == "duplicate_addon"
    assert NotADetailingService.error_code == "not_a_detailing_service"


def test_validate_center_id_home_with_no_center_passes() -> None:
    # Home delivery: center_id absent is the happy path.
    validate_center_id(None, location_kind="home")


def test_validate_center_id_home_with_center_rejected() -> None:
    # Home delivery shouldn't carry a center_id; widening the contract here
    # would let a tampered PWA confuse the staff alert.
    with pytest.raises(CenterIdNotAllowed) as exc_info:
        validate_center_id("ctr_casa", location_kind="home")
    assert exc_info.value.error_code == "center_id_not_allowed"


def test_validate_center_id_center_missing_rejected() -> None:
    # location.kind=center must come with a center_id; empty/None is rejected.
    with pytest.raises(MissingCenterId) as exc_info:
        validate_center_id(None, location_kind="center")
    assert exc_info.value.error_code == "missing_center_id"


def test_validate_center_id_center_with_unknown_rejected() -> None:
    # center_id must match one of catalog.active_centers().
    with pytest.raises(UnknownCenter) as exc_info:
        validate_center_id("ctr_nope", location_kind="center")
    assert exc_info.value.error_code == "unknown_center"


def test_validate_center_id_center_with_valid_static_id_passes() -> None:
    # The static catalog ships a single active center, "ctr_casa".
    validate_center_id("ctr_casa", location_kind="center")


def test_validate_center_id_empty_string_with_center_kind_rejected() -> None:
    # Empty string is not "missing" in Python's truthy sense — assert we treat
    # it the same as None so the JSON contract is consistent.
    with pytest.raises(MissingCenterId):
        validate_center_id("", location_kind="center")


def test_validate_center_id_exceptions_are_api_validation_errors() -> None:
    # Stable error_code contract.
    assert issubclass(CenterIdNotAllowed, APIValidationError)
    assert issubclass(MissingCenterId, APIValidationError)
    assert issubclass(UnknownCenter, APIValidationError)
    assert CenterIdNotAllowed.error_code == "center_id_not_allowed"
    assert MissingCenterId.error_code == "missing_center_id"
    assert UnknownCenter.error_code == "unknown_center"


# ─────────────────────────────────────────────────────────────────────────────
# app.api error envelope
# ─────────────────────────────────────────────────────────────────────────────


def _api_response_for(exc: Exception):
    app = FastAPI()
    pwa_api.install_exception_handlers(app)

    # Route must live under /api/v1 — the Exception handler is path-scoped
    # to the PWA surface (ewash-72z) so non-/api/v1 routes fall through to
    # Starlette's plain 500.
    @app.get("/api/v1/__diag_raise")
    def raise_exception():
        raise exc

    with TestClient(app, raise_server_exceptions=False) as client:
        return client.get("/api/v1/__diag_raise")


def test_api_router_exists_with_v1_prefix() -> None:
    assert pwa_api.router.prefix == "/api/v1"
    assert "pwa-api" in pwa_api.router.tags


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ClosedDate("closed"), "closed_date"),
        (UnknownSlot("unknown slot"), "unknown_slot"),
        (SlotTooSoon("too soon"), "slot_too_soon"),
        (InvalidDate("bad date"), "invalid_date"),
        (UnknownService("unknown service"), "unknown_service"),
        (InvalidServiceForCategory("bad lane"), "service_category_mismatch"),
        (UnknownAddon("unknown addon"), "unknown_addon"),
        (DuplicateAddon("duplicate addon"), "duplicate_addon"),
        (NotADetailingService("wrong bucket"), "not_a_detailing_service"),
        (UnknownCenter("unknown center"), "unknown_center"),
        (MissingCenterId("missing center"), "missing_center_id"),
        (CenterIdNotAllowed("not allowed"), "center_id_not_allowed"),
        (InvalidPhone("invalid phone"), "invalid_phone"),
    ],
)
def test_api_domain_errors_surface_stable_error_envelope(exc, code, caplog) -> None:
    caplog.set_level(logging.INFO, logger="ewash.api")

    response = _api_response_for(exc)

    assert response.status_code == 400
    assert response.headers["X-Ewash-Error-Code"] == code
    assert response.json()["error_code"] == code
    assert response.json()["message"] == str(exc)
    assert any(
        "ewash.api domain_error" in rec.message and f"error_code={code}" in rec.message
        for rec in caplog.records
    )


def test_api_unhandled_exception_returns_generic_500() -> None:
    response = _api_response_for(RuntimeError("database password leaked here"))

    assert response.status_code == 500
    assert response.headers["X-Ewash-Error-Code"] == "internal_error"
    assert response.json() == {
        "error_code": "internal_error",
        "message": "",
        "field": None,
        "details": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTTP-level rejection tests via POST /api/v1/bookings
#
# The unit tests above isolate each validator function. The integration tests
# below exercise the full validation contract end-to-end — the PWA sends the
# request, FastAPI parses the body, the route invokes the validators, the
# exception handlers in `app.api` translate domain exceptions into the stable
# envelope. Every documented rejection is asserted at both the status-code and
# error_code level.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def api_db(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'api-validation.db'}"
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


def _booking_payload(**overrides) -> dict:
    payload = {
        "phone": "+212 611-204-502",
        "name": "Oussama Test",
        "category": "A",
        "vehicle": {"make": "Dacia Logan", "color": "Blanc"},
        "location": {"kind": "home", "pin_address": "Villa X"},
        "service_id": "svc_cpl",
        "date": "2026-06-15",
        "slot": "slot_9_11",
        "addon_ids": [],
    }
    payload.update(overrides)
    return payload


def _pin_validator_now(monkeypatch, fixed_now: datetime) -> None:
    """Pin ``datetime.now`` inside ``app.api_validation`` to a fixed instant.

    Used by slot-freshness boundary tests below. The route calls
    ``validate_slot_and_date`` without an explicit ``now=`` so the validator
    reads ``datetime.now(tz=CASABLANCA_TZ)`` — monkeypatching the module-level
    ``datetime`` symbol lets us control that read deterministically without
    pulling in ``freezegun``.
    """
    import app.api_validation as _av

    real_datetime = _av.datetime

    class _FakeDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(_av, "datetime", _FakeDateTime)


def test_http_unknown_service_for_category(api_db):
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(service_id="svc_moto", category="A"),
        )

    assert response.status_code == 400
    assert response.headers["X-Ewash-Error-Code"] == "service_category_mismatch"
    body = response.json()
    assert body["error_code"] == "service_category_mismatch"
    assert body["field"] == "service_id"


def test_http_unknown_addon_id(api_db):
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(addon_ids=["svc_nope"]),
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "unknown_addon"
    assert body["field"] == "addon_ids"


def test_http_duplicate_addon(api_db):
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(addon_ids=["svc_cuir", "svc_cuir"]),
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "duplicate_addon"


def test_http_addon_id_equal_to_service_id_rejected(api_db):
    # End-to-end exploit guard: svc_pol is a detailing service that also lives
    # in SERVICES_CAR, so {service_id: "svc_pol", addon_ids: ["svc_pol"]}
    # passes validate_service_for_category as a main service AND would pass
    # validate_addon_ids as a detailing addon without the equals-main check.
    # Persistence would write two BookingLineItemRow rows (main + addon at
    # -20%), double-charging the customer. Must surface as a 400 with
    # error_code=duplicate_addon mapped to field=addon_ids.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(service_id="svc_pol", addon_ids=["svc_pol"]),
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "duplicate_addon"
    assert body["field"] == "addon_ids"


def test_http_addon_must_be_detailing(api_db):
    # svc_cpl is a wash-bucket service; it has its own pricing per category and
    # would be confusing as a free-form upsell add-on. Use svc_sal as the main
    # service so the addon doesn't collide with it (which would trip the
    # equals-main check first).
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(service_id="svc_sal", addon_ids=["svc_cpl"]),
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "not_a_detailing_service"


def test_http_unknown_center_id(api_db):
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(location={"kind": "center", "center_id": "ctr_bogus"}),
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "unknown_center"
    assert body["field"] == "location.center_id"


def test_http_center_id_required_when_kind_center(api_db):
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(location={"kind": "center"}),
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "missing_center_id"


def test_http_center_id_not_allowed_for_home(api_db):
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(
                location={"kind": "home", "pin_address": "Villa X", "center_id": "ctr_casa"},
            ),
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "center_id_not_allowed"


def test_http_closed_date_rejected(api_db):
    # 2026-05-27 is Eid al-Adha day 1, a static CLOSED_DATES entry.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(date="2026-05-27"),
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "closed_date"
    assert body["field"] == "date"


def test_http_unknown_slot(api_db):
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(slot="slot_99_99"),
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "unknown_slot"
    assert body["field"] == "slot"


def test_http_slot_too_soon(api_db, monkeypatch):
    # now = 08:30 CSB, slot_9_11 starts at 09:00 → lead 30min < 2h → reject.
    _pin_validator_now(
        monkeypatch,
        datetime(2026, 6, 15, 8, 30, tzinfo=CASABLANCA_TZ),
    )

    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(date="2026-06-15", slot="slot_9_11"),
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "slot_too_soon"


def test_http_slot_exactly_2h_ahead_accepted(api_db, monkeypatch):
    # Center boundary: now=07:00 CSB and slot_9_11 starts at 09:00 → lead=exactly 2h.
    # The validator uses strict `<`, so exactly 2h is OK for center bookings.
    _pin_validator_now(
        monkeypatch,
        datetime(2026, 6, 15, 7, 0, tzinfo=CASABLANCA_TZ),
    )

    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(
                date="2026-06-15",
                slot="slot_9_11",
                location={"kind": "center", "center_id": "ctr_casa"},
            ),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_ewash_confirmation"
    assert body["ref"].startswith("EW-")


def test_http_home_slot_under_4_working_hours_rejected(api_db, monkeypatch):
    # Home boundary: now=10:30 CSB and slot_14_16 starts at 14:00 → 3h30
    # working lead, below the required 4 operational hours.
    _pin_validator_now(
        monkeypatch,
        datetime(2026, 6, 15, 10, 30, tzinfo=CASABLANCA_TZ),
    )

    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(date="2026-06-15", slot="slot_14_16"),
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "slot_too_soon"


def test_http_home_slot_exactly_4_working_hours_accepted(api_db, monkeypatch):
    _pin_validator_now(
        monkeypatch,
        datetime(2026, 6, 15, 10, 0, tzinfo=CASABLANCA_TZ),
    )

    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(date="2026-06-15", slot="slot_14_16"),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "pending_ewash_confirmation"


def test_http_oversize_note_rejected(api_db):
    # BookingCreateRequest.note has max_length=500. 600 chars trips Pydantic
    # validation (422) before the route's domain validators run.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(note="a" * 600),
        )

    assert response.status_code == 422


def test_http_invalid_date_format(api_db):
    # The Pydantic pattern r"^\d{4}-\d{2}-\d{2}$" rejects slashes before the
    # `validate_slot_and_date` domain validator ever runs.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(date="2026/06/15"),
        )

    assert response.status_code == 422


def test_http_missing_phone(api_db):
    # Empty phone trips Pydantic Field(min_length=8) → 422.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(phone=""),
        )

    assert response.status_code == 422


def test_http_unparseable_phone(api_db):
    # 8-char phone passes Pydantic length but fails normalize_phone → 400 with
    # the typed InvalidPhone error_code.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(phone="abcdefgh"),
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "invalid_phone"
    assert body["field"] == "phone"


def test_http_error_response_has_stable_shape(api_db):
    # The cross-rejection shape contract: every 400 from a domain validator
    # produces an ErrorResponse envelope with exactly these four keys, with
    # `error_code` and `message` as non-empty strings. This is what the PWA
    # parses to render localized error messages.
    with _pwa_client() as client:
        response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(service_id="svc_moto"),
        )

    assert response.status_code == 400
    body = response.json()
    assert set(body.keys()) == {"error_code", "message", "field", "details"}
    assert isinstance(body["error_code"], str) and body["error_code"]
    assert isinstance(body["message"], str)
    assert isinstance(body["details"], dict)
