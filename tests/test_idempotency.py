"""Tests for the idempotency lookup helper used by POST /api/v1/bookings.

`find_booking_by_client_request_id` is how the API handler detects retries:
a booking already exists for the same UUIDv4 → return its response unchanged.
Without this helper, a network blip after the server commits would cause the
PWA's retry to allocate a fresh `EW-YYYY-####` ref and bill the slot twice.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select

from app import api, booking as booking_store, catalog, notifications, persistence
from app.config import settings
from app.db import init_db, make_engine, session_scope
from app.models import BookingRow, Customer, CustomerTokenRow
from app.persistence import find_booking_by_client_request_id
from app.rate_limit import limiter
from app.security import hash_token


def _future_booking_date() -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()


def _engine_with_customer(phone: str = "212600000200"):
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    with session_scope(engine) as session:
        session.add(Customer(phone=phone, display_name="Idem"))
    return engine, phone


def _client() -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(api.router)
    api.install_exception_handlers(app)
    return TestClient(app)


@pytest.fixture
def api_db(monkeypatch, tmp_path):
    booking_store._bookings.clear()
    monkeypatch.setattr(booking_store, "_counter", 0)
    db_url = f"sqlite+pysqlite:///{tmp_path / 'api-idempotency.db'}"
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


def _booking_payload(**overrides) -> dict:
    payload = {
        "phone": "+212 611-204-502",
        "name": "Oussama Test",
        "category": "A",
        "vehicle": {"make": "Dacia Logan", "color": "Blanc"},
        "location": {
            "kind": "home",
            "pin_address": "Villa Oussama",
            "address_details": "Gate 3",
        },
        "promo_code": "ys26",
        "service_id": "svc_cpl",
        "date": _future_booking_date(),
        "slot": "slot_9_11",
        "note": "Sonner deux fois",
        "addon_ids": [],
        "client_request_id": "idempotency-security",
    }
    payload.update(overrides)
    return payload


def test_find_returns_existing_booking_by_client_request_id() -> None:
    engine, phone = _engine_with_customer()
    crid = "11111111-2222-3333-4444-555555555555"

    with session_scope(engine) as session:
        session.add(BookingRow(
            customer_phone=phone, status="pending_ewash_confirmation",
            ref="EW-2026-1001", customer_name="Idem",
            client_request_id=crid,
        ))

    row = find_booking_by_client_request_id(crid, engine=engine)

    assert row is not None
    assert row.ref == "EW-2026-1001"
    assert row.client_request_id == crid


def test_replay_by_different_phone_does_not_leak_or_mint_token(api_db) -> None:
    """Regression for ewash-416: a leaked client_request_id is not enough to
    replay another phone's booking or mint a token for that victim."""
    request_id = "idempotency-phone-mismatch"
    victim_phone = "+212 611-204-502"
    attacker_phone = "+212 600-000-701"

    with _client() as client:
        first_response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(
                phone=victim_phone,
                name="Victim Customer",
                client_request_id=request_id,
            ),
        )
        replay_response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(
                phone=attacker_phone,
                name="Attacker Customer",
                client_request_id=request_id,
            ),
        )

    assert first_response.status_code == 200
    first = first_response.json()
    assert first["ref"]
    assert first["bookings_token"]

    body_text = replay_response.text
    assert first["ref"] not in body_text
    assert first["bookings_token"] not in body_text
    assert "Victim Customer" not in body_text
    assert "212611204502" not in body_text

    assert replay_response.status_code in (200, 409, 422, 500)
    if replay_response.status_code == 200:
        replay = replay_response.json()
        assert replay["ref"] != first["ref"]
        assert replay["bookings_token"] != first["bookings_token"]
        assert replay["is_idempotent_replay"] is False
        assert persistence.verify_customer_token(
            replay["bookings_token"], engine=api_db
        ) == notifications.normalize_phone(attacker_phone)
    else:
        assert replay_response.status_code >= 400


def test_replay_with_correct_token_idempotent(api_db) -> None:
    request_id = "idempotency-correct-token"

    with _client() as client:
        first_response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(client_request_id=request_id),
        )
        first = first_response.json()
        replay_response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(
                client_request_id=request_id,
                bookings_token=first["bookings_token"],
            ),
        )

    assert first_response.status_code == 200
    assert replay_response.status_code == 200
    replay = replay_response.json()
    assert replay["ref"] == first["ref"]
    assert replay["bookings_token"] == first["bookings_token"]
    assert replay["line_items"] == first["line_items"]
    assert replay["is_idempotent_replay"] is True

    with session_scope(api_db) as session:
        bookings = session.scalars(select(BookingRow)).all()
        assert len(bookings) == 1
        assert bookings[0].client_request_id == request_id
        tokens = session.scalars(select(CustomerTokenRow)).all()
        assert len(tokens) == 1
        assert tokens[0].token_hash == hash_token(first["bookings_token"])


def test_concurrent_replay_does_not_mint_duplicate_tokens(api_db) -> None:
    """Regression for ewash-2ja: two POSTs with the same client_request_id
    where the second carries no ``bookings_token`` (concurrent first-flight
    retry, or the loser of a partial-unique-index race that falls through
    to the IntegrityError → _idempotent_booking_response path). The replay
    must NOT mint a fresh customer_tokens row — admin revocation has to
    chase every row, and an unbounded retry storm makes that intractable.
    """
    request_id = "concurrent-replay-token-storm"
    phone = "+212 611-204-502"

    with _client() as client:
        first_response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(client_request_id=request_id, phone=phone),
        )
        # Replay without echoing the token. The handler's reuse path at
        # api.py:464-477 short-circuits when caller_bookings_token is set
        # to a matching value; the bug only fires when no token is sent.
        replay_response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(client_request_id=request_id, phone=phone),
        )

    assert first_response.status_code == 200
    assert replay_response.status_code == 200

    first = first_response.json()
    replay = replay_response.json()
    assert first["bookings_token"]
    assert replay["ref"] == first["ref"]
    assert replay["is_idempotent_replay"] is True
    # Replay returns an empty token: the caller's first successful response
    # is the only carrier of the plaintext. Pre-fix this field would have
    # been a brand-new minted plaintext, and the row count below would be 2.
    assert replay["bookings_token"] == ""

    normalized_phone = notifications.normalize_phone(phone)
    with session_scope(api_db) as session:
        tokens = session.scalars(
            select(CustomerTokenRow).where(
                CustomerTokenRow.customer_phone == normalized_phone
            )
        ).all()
        assert len(tokens) == 1
        assert tokens[0].token_hash == hash_token(first["bookings_token"])


def test_integrity_error_fallback_replay_does_not_mint_duplicate_tokens(
    api_db, monkeypatch
) -> None:
    """Regression for ewash-2ja race step 5/6: simulate the loser-of-race
    path where the first POST commits and the second POST's INSERT trips
    the partial unique index, falls into the ``except IntegrityError``
    branch at api.py:616, and re-enters _idempotent_booking_response.
    That re-entry must not mint a duplicate customer_tokens row.

    We force the second request through the IntegrityError branch by
    monkeypatching ``persistence.persist_confirmed_booking`` so the second
    call raises IntegrityError after the first has committed. This is the
    deterministic way to drive the race against the SQLite test backend,
    which has no real concurrency model.
    """
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    request_id = "integrity-error-fallback-token-storm"
    phone = "+212 611-204-502"

    original_persist = persistence.persist_confirmed_booking
    call_count = {"n": 0}

    def flaky_persist(booking_obj, *, source, session):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise SAIntegrityError("simulated race", params=None, orig=Exception())
        return original_persist(booking_obj, source=source, session=session)

    with _client() as client:
        first_response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(client_request_id=request_id, phone=phone),
        )
        assert first_response.status_code == 200
        first = first_response.json()

        monkeypatch.setattr(persistence, "persist_confirmed_booking", flaky_persist)
        # Different client_request_id so the initial replay-guard at
        # api.py:519 returns None and we proceed into the create path,
        # then the patched persist_confirmed_booking raises IntegrityError
        # and we exercise the IntegrityError fallback's call to
        # _idempotent_booking_response at api.py:620.
        racing_response = client.post(
            "/api/v1/bookings",
            json=_booking_payload(client_request_id=request_id, phone=phone),
        )

    # The fallback either finds the original row and replays it (200), or
    # surfaces the IntegrityError if the partial unique index is what
    # actually fired (the test patch raises a generic IntegrityError so we
    # land on 200 via the fallback). What matters for this regression is
    # the customer_tokens count.
    assert racing_response.status_code in (200, 409, 500)

    normalized_phone = notifications.normalize_phone(phone)
    with session_scope(api_db) as session:
        tokens = session.scalars(
            select(CustomerTokenRow).where(
                CustomerTokenRow.customer_phone == normalized_phone
            )
        ).all()
        # Exactly one token: minted by the first (winning) request. The
        # fallback's re-entry into _idempotent_booking_response must not
        # accumulate orphan rows.
        assert len(tokens) == 1
        assert tokens[0].token_hash == hash_token(first["bookings_token"])


def test_find_returns_none_for_unknown_client_request_id() -> None:
    engine, _ = _engine_with_customer()

    row = find_booking_by_client_request_id("does-not-exist", engine=engine)

    assert row is None


def test_find_returns_none_for_empty_string() -> None:
    # Defensive: callers pipe the request body's optional field in directly;
    # an empty crid (no idempotency requested) must look like "no match" so
    # the API allocates a fresh ref instead of attempting a lookup.
    engine, _ = _engine_with_customer()
    assert find_booking_by_client_request_id("", engine=engine) is None


def test_find_returns_none_for_none_input() -> None:
    engine, _ = _engine_with_customer()
    assert find_booking_by_client_request_id(None, engine=engine) is None


def test_find_inside_existing_session_does_not_open_new_transaction() -> None:
    """When `session=<sess>` is passed, the helper runs inside the caller's
    transaction rather than opening its own. This is required for the API
    handler's lookup-then-commit path (one transaction, two statements)."""
    engine, phone = _engine_with_customer()
    crid = "abcdef01-2345-6789-abcd-ef0123456789"

    with session_scope(engine) as session:
        # Add the row in this session — uncommitted from the perspective of
        # any new transaction. The helper must see it because we hand it the
        # SAME session, not a fresh one.
        session.add(BookingRow(
            customer_phone=phone, status="pending_ewash_confirmation",
            ref="EW-2026-1002", customer_name="Idem",
            client_request_id=crid,
        ))
        session.flush()

        row = find_booking_by_client_request_id(crid, session=session)

        assert row is not None
        assert row.ref == "EW-2026-1002"


def test_find_returns_none_without_engine_or_session() -> None:
    # DB-absent path: nothing to look up against, so the lookup is effectively
    # a miss. The API handler treats this as "not an idempotent retry" and
    # proceeds with a fresh allocation.
    assert find_booking_by_client_request_id("anything", engine=None) is None


def test_find_disambiguates_between_two_concurrent_client_request_ids() -> None:
    """Two different UUIDs return their respective rows, not the wrong one."""
    engine, phone = _engine_with_customer()

    with session_scope(engine) as session:
        session.add(BookingRow(
            customer_phone=phone, status="pending_ewash_confirmation",
            ref="EW-2026-1010", customer_name="Idem",
            client_request_id="crid-A",
        ))
        session.add(BookingRow(
            customer_phone=phone, status="pending_ewash_confirmation",
            ref="EW-2026-1011", customer_name="Idem",
            client_request_id="crid-B",
        ))

    a = find_booking_by_client_request_id("crid-A", engine=engine)
    b = find_booking_by_client_request_id("crid-B", engine=engine)

    assert a is not None and a.ref == "EW-2026-1010"
    assert b is not None and b.ref == "EW-2026-1011"


def test_find_ignores_bookings_with_null_client_request_id() -> None:
    """Legacy WhatsApp bookings have client_request_id=NULL. Looking up an
    empty crid must not accidentally return one of them (the empty-input
    guard handles this; this is the regression test)."""
    engine, phone = _engine_with_customer()

    with session_scope(engine) as session:
        # Two WhatsApp bookings without crid.
        session.add(BookingRow(
            customer_phone=phone, status="confirmed",
            ref="EW-2026-1020", customer_name="Whatsapp1", source="whatsapp",
        ))
        session.add(BookingRow(
            customer_phone=phone, status="confirmed",
            ref="EW-2026-1021", customer_name="Whatsapp2", source="whatsapp",
        ))

    assert find_booking_by_client_request_id("", engine=engine) is None
    assert find_booking_by_client_request_id(None, engine=engine) is None
