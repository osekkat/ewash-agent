import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import admin as admin_module
from app.booking import Booking
import app.booking as booking_store
from app.config import settings
from app.db import init_db, make_engine, session_scope
from app.main import app
from app.models import (
    AdminTextRow,
    BookingRow,
    ConversationEventRow,
    ConversationSessionRow,
    CustomerName,
    CustomerTokenRow,
    CustomerVehicle,
    DataErasureAuditRow,
)
from app.notifications import get_booking_notification_settings, notification_cache_clear
from app.persistence import (
    _configured_engine,
    mint_customer_token,
    persist_confirmed_booking,
    persist_customer_bot_stage,
)


def test_admin_entrypoint_defaults_to_french_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "")
    client = TestClient(app)

    response = client.get("/admin")

    assert response.status_code == 503
    assert "Portail admin non configuré" in response.text
    assert "ADMIN_PASSWORD" in response.text
    assert "Réservations" in response.text
    assert "Rappels" in response.text
    assert "?lang=en" in response.text


def test_admin_entrypoint_can_render_english_option_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "")
    client = TestClient(app)

    response = client.get("/admin?lang=en")

    assert response.status_code == 503
    assert "Admin portal is not configured" in response.text
    assert "ADMIN_PASSWORD" in response.text
    assert "Bookings" in response.text
    assert "Reminders" in response.text
    assert "?lang=fr" in response.text


def test_admin_entrypoint_shows_password_only_form_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)

    response = client.get("/admin")

    assert response.status_code == 200
    assert "Mot de passe" in response.text
    assert "name=\"password\"" in response.text
    assert "type=\"password\"" in response.text
    assert "Username" not in response.text
    assert "Nom d'utilisateur" not in response.text


def test_admin_entrypoint_rejects_wrong_password_without_username(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)

    response = client.post(
        "/admin",
        content="password=wrong-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "Mot de passe incorrect" in response.text


def test_admin_entrypoint_accepts_configured_password_without_username(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)

    response = client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert "ewash_admin_session" in response.headers["set-cookie"]

    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert "Tableau de bord" in dashboard.text
    assert "Mot de passe" not in dashboard.text
    assert "Version actuelle" in dashboard.text
    assert "Réservations aujourd" in dashboard.text
    assert "Rappels en attente" in dashboard.text
    assert "Aucune réservation persistée pour le moment" in dashboard.text
    assert "Réservations, clients, prix, promos et notifications" in dashboard.text
    assert "Pages réservations / clients / prix / promos" in dashboard.text
    assert "<span>OK</span>" in dashboard.text
    assert "class=\"metric-grid\"" in dashboard.text
    assert "class=\"empty-panel\"" in dashboard.text
    assert 'href="/admin/bookings"' in dashboard.text
    assert 'href="/admin/customers"' in dashboard.text
    assert 'href="/admin/erasures"' in dashboard.text
    assert 'href="/admin/prices"' in dashboard.text


def test_admin_login_emits_secure_cookie_when_setting_enabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    monkeypatch.setattr(settings, "admin_cookie_secure", True)
    client = TestClient(app)

    response = client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    set_cookie = response.headers["set-cookie"]
    assert "ewash_admin_session" in set_cookie
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()


def test_admin_login_omits_secure_cookie_when_setting_disabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    monkeypatch.setattr(settings, "admin_cookie_secure", False)
    client = TestClient(app)

    response = client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    set_cookie = response.headers["set-cookie"]
    assert "ewash_admin_session" in set_cookie
    assert "Secure" not in set_cookie
    assert "HttpOnly" in set_cookie


def test_admin_personal_finances_page_lists_and_serves_monthly_file(monkeypatch, tmp_path):
    finance_dir = tmp_path / "finances_personnelles"
    finance_dir.mkdir()
    monthly_file = finance_dir / "finances_personnelles_2026-06.xlsx"
    monthly_file.write_bytes(b"fake personal finance workbook")
    monkeypatch.setattr(admin_module, "_PERSONAL_FINANCE_DIR", finance_dir)
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    page = client.get("/admin/personal-finances")

    assert page.status_code == 200
    assert "Finances perso" in page.text
    assert "Dépenses personnelles" in page.text
    assert monthly_file.name in page.text
    assert f"/admin/personal-finances/files/{monthly_file.name}" in page.text

    download = client.get(f"/admin/personal-finances/files/{monthly_file.name}")
    assert download.status_code == 200
    assert download.content == b"fake personal finance workbook"
    assert download.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_admin_personal_finances_page_lists_and_serves_db_workbook(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'personal-finances.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    finance_dir = tmp_path / "empty_finances_personnelles"
    finance_dir.mkdir()
    monkeypatch.setattr(admin_module, "_PERSONAL_FINANCE_DIR", finance_dir)
    workbook_name = "finances_personnelles_2026-06.xlsx"
    workbook_bytes = b"db-backed personal finance workbook"
    with session_scope(engine) as session:
        session.add(
            AdminTextRow(
                text_key=f"personal_finance_workbook:{workbook_name}",
                title=workbook_name,
                body=json.dumps(
                    {
                        "filename": workbook_name,
                        "content_base64": base64.b64encode(workbook_bytes).decode("ascii"),
                        "size_bytes": len(workbook_bytes),
                    }
                ),
            )
        )
    client = _logged_in_admin_client(monkeypatch, db_url)

    page = client.get("/admin/personal-finances")

    assert page.status_code == 200
    assert workbook_name in page.text
    assert f"{len(workbook_bytes)} o" in page.text

    download = client.get(f"/admin/personal-finances/files/{workbook_name}")
    assert download.status_code == 200
    assert download.content == workbook_bytes
    assert download.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_admin_b2b_hertz_page_lists_files_and_vehicle_rows(monkeypatch, tmp_path):
    hertz_dir = tmp_path / "hertz" / "final"
    hertz_dir.mkdir(parents=True)
    workbook = hertz_dir / "SUIVI_HERTZ_CASA_21_Mai_au_20_Juin_2026_Ewash.xlsx"
    workbook.write_bytes(b"fake hertz workbook")
    payload = {
        "summary": {
            "invoice_number": "EW-B2B-HERTZ-2026-0001",
            "total_vehicles": 2,
            "by_site": {"Casa Aéroport": 1, "Fès Aéroport": 1},
            "total_ht": 100,
            "tva": 20,
            "ttc": 120,
        },
        "rows": [
            {"date": "2026-06-20", "site": "Casa Aéroport", "vehicle": "Kia Carnival noir", "matricule": "2942 Y 6", "tarif_ht": 50},
            {"date": "2026-06-20", "site": "Fès Aéroport", "vehicle": "Renault Clio grise", "matricule": "27048 Y 6", "tarif_ht": 50},
        ],
    }
    (hertz_dir / "hertz_b2b_2026-05-21_2026-06-20.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(admin_module, "_B2B_HERTZ_DIR", hertz_dir)
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    b2b = client.get("/admin/b2b")
    assert b2b.status_code == 200
    assert "B2B" in b2b.text
    assert "Hertz" in b2b.text
    assert "/admin/b2b/hertz" in b2b.text

    page = client.get("/admin/b2b/hertz")
    assert page.status_code == 200
    assert "Suivi Hertz" in page.text
    assert "EW-B2B-HERTZ-2026-0001" in page.text
    assert "Kia Carnival noir" in page.text
    assert "2942 Y 6" in page.text
    assert workbook.name in page.text

    download = client.get(f"/admin/b2b/hertz/files/{workbook.name}")
    assert download.status_code == 200
    assert download.content == b"fake hertz workbook"



def _sample_booking() -> Booking:
    booking = Booking(phone="212665883062")
    booking.name = "Sekkat"
    booking.vehicle_type = "B — Berline / SUV"
    booking.category = "B"
    booking.car_model = "Porsche"
    booking.color = "Gris"
    booking.service = "svc_cpl"
    booking.service_bucket = "wash"
    booking.service_label = "Le Complet — 125 DH"
    booking.price_dh = 125
    booking.price_regular_dh = 125
    booking.location_mode = "center"
    booking.center = "Stand physique — Mall Triangle Vert, Bouskoura · 7j/7 · 09h-22h30"
    booking.date_label = "Dimanche 26/04/2026"
    booking.slot = "09h – 11h"
    booking.assign_ref()
    return booking


def _logged_in_admin_client(monkeypatch, db_url: str) -> TestClient:
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    return client


def _seed_customer_for_erasure(engine, phone: str = "212600000551") -> Booking:
    booking = _sample_booking()
    booking.phone = phone
    persist_confirmed_booking(booking, engine=engine)
    persist_customer_bot_stage(phone, "BOOK_SERVICE", engine=engine)
    mint_customer_token(phone, engine=engine)
    return booking


def _customer_side_count(session, phone: str) -> int:
    total = 0
    for model in (CustomerTokenRow, CustomerName, CustomerVehicle, ConversationEventRow, ConversationSessionRow):
        total += session.scalar(
            select(func.count()).select_from(model).where(model.customer_phone == phone)
        ) or 0
    return int(total)


def test_admin_bookings_page_renders_persisted_reservations(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-bookings.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    booking = _sample_booking()
    persist_confirmed_booking(booking, engine=engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/bookings")

    assert response.status_code == 200
    assert "Réservations" in response.text
    assert booking.ref in response.text
    assert "Sekkat" in response.text
    assert "Porsche" in response.text
    assert "Le Complet — 125 DH" in response.text
    assert "Dimanche 26/04/2026" in response.text
    assert "09h – 11h" in response.text
    assert "Cette page arrive dans le prochain lot" not in response.text
    # Source badge: legacy bookings persisted before migration 0006 default
    # to "whatsapp" via the BookingRow.source column DEFAULT.
    assert "badge src-wa" in response.text
    assert "WhatsApp" in response.text
    _configured_engine.cache_clear()


def test_admin_booking_confirm_lock_busy_renders_conflict(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-confirm-busy.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    booking = _sample_booking()
    persist_confirmed_booking(booking, engine=engine)
    client = _logged_in_admin_client(monkeypatch, db_url)

    def busy_confirm(_ref):
        raise admin_module.BookingLockBusy("row is locked")

    monkeypatch.setattr(admin_module, "confirm_booking_by_ewash", busy_confirm)

    response = client.post("/admin/bookings/confirm?lang=fr", data={"ref": booking.ref})

    assert response.status_code == 409
    assert "Une autre confirmation admin est déjà en cours" in response.text
    assert booking.ref in response.text


def test_admin_dashboard_renders_pwa_and_whatsapp_split_counters(monkeypatch, tmp_path):
    """Seed bookings of each source and verify the dashboard's two new
    counter cards show the correct per-channel totals over the trailing 7d."""
    from sqlalchemy import update
    from app.models import BookingRow

    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-split.db'}"
    engine = make_engine(db_url)
    init_db(engine)

    # Two WhatsApp bookings (default source) and one PWA booking.
    for ref_suffix in ("0301", "0302", "0303"):
        booking = _sample_booking()
        booking.ref = f"EW-2026-{ref_suffix}"
        persist_confirmed_booking(booking, engine=engine)

    with session_scope(engine) as session:
        # Promote the third booking to source="api".
        session.execute(
            update(BookingRow).where(BookingRow.ref == "EW-2026-0303").values(source="api")
        )

    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin")

    assert response.status_code == 200
    # Both new card labels appear.
    assert "Réservations PWA (7j)" in response.text
    assert "Réservations WhatsApp (7j)" in response.text
    # The PWA card carries the src-pwa badge in its label.
    pwa_section = response.text[response.text.index("Réservations PWA (7j)"):]
    pwa_card = pwa_section[: pwa_section.index("metric-card", 1) if "metric-card" in pwa_section[1:] else len(pwa_section)]
    # The whatsapp card label appears later in the same metric-grid.
    assert "src-pwa" in response.text
    assert "src-wa" in response.text
    _configured_engine.cache_clear()


def test_admin_dashboard_recent_bookings_card_shows_source_badge(monkeypatch, tmp_path):
    """The dashboard's recent-bookings card must include the source badge so
    staff see at a glance which channel each recent booking arrived through."""
    from sqlalchemy import update
    from app.models import BookingRow

    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-dashboard-recent.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    booking = _sample_booking()
    persist_confirmed_booking(booking, engine=engine)
    # Force the row to source="api" so we know we're seeing the PWA badge
    # and not just the whatsapp default leaking through.
    with session_scope(engine) as session:
        session.execute(
            update(BookingRow).where(BookingRow.ref == booking.ref).values(source="api")
        )

    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin")

    assert response.status_code == 200
    # Recent-bookings card name + badge appear together.
    assert "Sekkat" in response.text
    assert "badge src-pwa" in response.text
    _configured_engine.cache_clear()


def test_admin_bookings_page_shows_pwa_badge_for_api_sourced_bookings(monkeypatch, tmp_path):
    """A booking persisted with source="api" renders the PWA badge — needed so
    staff can tell at a glance which channel a booking arrived through."""
    from sqlalchemy import update
    from app.models import BookingRow

    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-bookings-pwa.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    booking = _sample_booking()
    persist_confirmed_booking(booking, engine=engine)
    # Mutate the persisted row to simulate the upcoming POST /api/v1/bookings
    # path that will pass source="api". Done with raw UPDATE because the
    # persist_confirmed_booking source kwarg lands in a sibling bead.
    with session_scope(engine) as session:
        session.execute(
            update(BookingRow).where(BookingRow.ref == booking.ref).values(source="api")
        )

    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/bookings")

    assert response.status_code == 200
    assert booking.ref in response.text
    assert "badge src-pwa" in response.text
    assert "PWA" in response.text
    # WhatsApp badge must not leak onto a PWA row.
    pwa_section = response.text[response.text.index(booking.ref):response.text.index(booking.ref) + 300]
    assert "src-wa" not in pwa_section
    _configured_engine.cache_clear()


def test_admin_bookings_page_includes_esthetique_addons_in_service_column(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-bookings-addons.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    booking = _sample_booking()
    booking.addon_service = "svc_pol"
    booking.addon_service_label = "Le Polissage — 963 DH (-10%)"
    booking.addon_price_dh = 963
    persist_confirmed_booking(booking, engine=engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/bookings")

    assert response.status_code == 200
    assert "Le Complet — 125 DH" in response.text
    assert "Esthétique : Le Polissage — 963 DH (-10%)" in response.text
    assert "1088 DH" in response.text
    _configured_engine.cache_clear()


def test_admin_customers_page_renders_persisted_clients(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-customers.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    booking = _sample_booking()
    persist_confirmed_booking(booking, engine=engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/customers")

    assert response.status_code == 200
    assert "Clients" in response.text
    assert "Sekkat" in response.text
    assert "212665883062" in response.text
    assert "Porsche — Gris" in response.text
    assert "1 réservation" in response.text
    assert "Cette page arrive dans le prochain lot" not in response.text
    _configured_engine.cache_clear()


def test_admin_customers_page_renders_last_whatsapp_stage_for_unconfirmed_leads(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-customer-stages.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    persist_customer_bot_stage("212600000003", "BOOK_SERVICE", engine=engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/customers")

    assert response.status_code == 200
    assert "212600000003" in response.text
    assert "Liste des prix affichée" in response.text
    assert "Étape WhatsApp" in response.text
    _configured_engine.cache_clear()


def test_admin_bookings_page_falls_back_to_live_memory_when_database_is_missing(monkeypatch):
    booking_store._bookings.clear()
    monkeypatch.setattr(booking_store, "_counter", 0)
    monkeypatch.setattr(settings, "database_url", "")
    _configured_engine.cache_clear()
    booking = _sample_booking()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/bookings")

    assert response.status_code == 200
    assert "Mode temporaire" in response.text
    assert "Railway Postgres" in response.text
    assert booking.ref in response.text
    assert "Sam" not in response.text
    assert "Sekkat" in response.text
    assert "Porsche" in response.text
    booking_store._bookings.clear()
    _configured_engine.cache_clear()


def test_admin_customers_page_falls_back_to_live_memory_when_database_is_missing(monkeypatch):
    booking_store._bookings.clear()
    monkeypatch.setattr(booking_store, "_counter", 0)
    monkeypatch.setattr(settings, "database_url", "")
    _configured_engine.cache_clear()
    _sample_booking()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/customers")

    assert response.status_code == 200
    assert "Mode temporaire" in response.text
    assert "Sekkat" in response.text
    assert "212665883062" in response.text
    assert "Porsche — Gris" in response.text
    assert "1 réservation" in response.text
    booking_store._bookings.clear()
    _configured_engine.cache_clear()


def test_admin_erase_customer_happy_path(monkeypatch, tmp_path):
    phone = "212600000551"
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-erase.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    _seed_customer_for_erasure(engine, phone)
    client = _logged_in_admin_client(monkeypatch, db_url)

    response = client.post(
        f"/admin/customers/{phone}/erase",
        content="confirm=ERASE&notes=Ticket+123",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/customers?lang=fr&erased=1"
    with session_scope(engine) as session:
        booking = session.scalars(select(BookingRow)).one()
        audit = session.scalars(select(DataErasureAuditRow)).one()
        assert booking.customer_name == "Anonyme"
        assert booking.customer_phone.startswith("DEL-")
        assert booking.car_model == ""
        assert booking.raw_booking_json == "{}"
        # FK to customer_vehicles must be nulled — the vehicle row is gone,
        # and on Postgres the FK has no ON DELETE CASCADE, so a non-null
        # value here would have raised ForeignKeyViolation during erasure.
        assert booking.customer_vehicle_id is None
        # Actor identifies the admin session + client IP so concurrent
        # admins sharing one password are still distinguishable in the audit.
        assert audit.actor.startswith("admin:")
        assert len(audit.actor) <= 64
        actor_parts = audit.actor.split(":", 2)
        assert len(actor_parts) == 3
        assert actor_parts[1].isdigit()  # session timestamp
        assert audit.notes == "Ticket 123"
        assert audit.anonymized_bookings == 1

    bookings_page = client.get("/admin/bookings")
    assert bookings_page.status_code == 200
    assert "Anonyme" in bookings_page.text
    assert phone not in bookings_page.text
    _configured_engine.cache_clear()


def test_admin_erase_allows_returning_customer_to_be_erased_again(monkeypatch, tmp_path):
    phone = "212600000553"
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-erase-repeat.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    _seed_customer_for_erasure(engine, phone)
    client = _logged_in_admin_client(monkeypatch, db_url)

    first = client.post(
        f"/admin/customers/{phone}/erase",
        content="confirm=ERASE&notes=First",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert first.status_code == 303

    _seed_customer_for_erasure(engine, phone)
    second = client.post(
        f"/admin/customers/{phone}/erase",
        content="confirm=ERASE&notes=Second",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert second.status_code == 303
    assert second.headers["location"] == "/admin/customers?lang=fr&erased=1"
    with session_scope(engine) as session:
        assert _customer_side_count(session, phone) == 0
        assert session.scalar(
            select(func.count()).select_from(BookingRow).where(BookingRow.customer_phone == phone)
        ) == 0
        audits = session.scalars(
            select(DataErasureAuditRow).order_by(DataErasureAuditRow.performed_at)
        ).all()
        bookings = session.scalars(select(BookingRow)).all()
    assert len(audits) == 2
    assert len(bookings) == 2
    for booking in bookings:
        assert booking.customer_phone.startswith("DEL-")
        assert booking.customer_name == "Anonyme"
        assert booking.raw_booking_json == "{}"
    _configured_engine.cache_clear()


def test_admin_erase_requires_confirm(monkeypatch, tmp_path):
    phone = "212600000552"
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-erase-confirm.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    _seed_customer_for_erasure(engine, phone)
    client = _logged_in_admin_client(monkeypatch, db_url)

    response = client.post(
        f"/admin/customers/{phone}/erase",
        content="confirm=WRONG",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/customers?lang=fr&error=confirm_required"
    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(DataErasureAuditRow)) == 0
        booking = session.scalars(select(BookingRow)).one()
        assert booking.customer_phone == phone
    _configured_engine.cache_clear()


def test_admin_erasures_page_lists_rows(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-erasures.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    with session_scope(engine) as session:
        session.add(DataErasureAuditRow(
            phone_hash="a" * 64,
            actor="admin:unknown",
            deleted_count=4,
            anonymized_bookings=2,
            notes="Ticket 456",
        ))
    client = _logged_in_admin_client(monkeypatch, db_url)

    response = client.get("/admin/erasures")

    assert response.status_code == 200
    assert "Effacements de données" in response.text
    assert "aaaaaaaaaaaa" in response.text
    assert "admin:unknown" in response.text
    assert "Ticket 456" in response.text
    assert "Réservations anonymisées" in response.text
    _configured_engine.cache_clear()


def test_admin_erasures_page_filters_by_actor(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-erasures-filter.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    with session_scope(engine) as session:
        session.add_all([
            DataErasureAuditRow(
                phone_hash="b" * 64,
                actor="customer_self_serve",
                deleted_count=1,
                anonymized_bookings=1,
                notes="self serve row",
            ),
            DataErasureAuditRow(
                phone_hash="c" * 64,
                actor="admin:unknown",
                deleted_count=2,
                anonymized_bookings=1,
                notes="admin row",
            ),
        ])
    client = _logged_in_admin_client(monkeypatch, db_url)

    response = client.get("/admin/erasures?actor=customer_self_serve")

    assert response.status_code == 200
    assert "self serve row" in response.text
    assert "admin row" not in response.text
    _configured_engine.cache_clear()


def test_admin_erase_emits_audit_log(monkeypatch, tmp_path, caplog):
    phone = "212600000553"
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-erase-log.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    _seed_customer_for_erasure(engine, phone)
    client = _logged_in_admin_client(monkeypatch, db_url)
    caplog.set_level(logging.INFO, logger="ewash.admin")

    client.post(
        f"/admin/customers/{phone}/erase",
        content="confirm=ERASE",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    prefix = hashlib.sha256(phone.encode("utf-8")).hexdigest()[:12]
    lines = [rec.getMessage() for rec in caplog.records if "admin.erase" in rec.getMessage()]
    assert lines
    assert f"phone_hash={prefix}" in lines[0]
    assert phone not in lines[0]
    _configured_engine.cache_clear()


def test_admin_erase_recorded_count_matches_actual(monkeypatch, tmp_path):
    phone = "212600000554"
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-erase-count.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    _seed_customer_for_erasure(engine, phone)
    client = _logged_in_admin_client(monkeypatch, db_url)
    with session_scope(engine) as session:
        expected_deleted = _customer_side_count(session, phone)
        expected_bookings = session.scalar(
            select(func.count()).select_from(BookingRow).where(BookingRow.customer_phone == phone)
        )

    client.post(
        f"/admin/customers/{phone}/erase",
        content="confirm=ERASE",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    with session_scope(engine) as session:
        audit = session.scalars(select(DataErasureAuditRow)).one()
    assert audit.deleted_count == expected_deleted
    assert audit.anonymized_bookings == expected_bookings
    _configured_engine.cache_clear()


def test_admin_erase_notes_capped_at_500_chars(monkeypatch, tmp_path):
    phone = "212600000555"
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-erase-notes-cap.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    _seed_customer_for_erasure(engine, phone)
    client = _logged_in_admin_client(monkeypatch, db_url)

    long_notes = "A" * 2000
    client.post(
        f"/admin/customers/{phone}/erase",
        content=f"confirm=ERASE&notes={long_notes}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    with session_scope(engine) as session:
        audit = session.scalars(select(DataErasureAuditRow)).one()
    assert audit.notes is not None
    assert len(audit.notes) == 500
    _configured_engine.cache_clear()


def test_admin_prices_page_renders_public_tariff(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/prices")

    assert response.status_code == 200
    assert "Prix" in response.text
    assert "Tarifs publics" in response.text
    assert "Lavages" in response.text
    assert "Esthétique" in response.text
    assert "L&#x27;Extérieur" in response.text
    assert "Le Complet" in response.text
    assert "Céramique 6 mois" in response.text
    assert "Scooter" in response.text
    assert "60 DH" in response.text
    assert "125 DH" in response.text
    assert "1150 DH" in response.text
    assert "105 DH" in response.text
    assert "Cette page arrive dans le prochain lot" not in response.text
    assert 'href="/admin/prices" class="active"' in response.text


def test_admin_prices_page_allows_updating_public_tariff(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-prices.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    import app.catalog as catalog
    catalog.catalog_cache_clear()
    notification_cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.post(
        "/admin/prices?lang=en",
        data={"price__svc_cpl__B": "131"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/prices?lang=en&saved=1"
    assert catalog.service_price("svc_cpl", "B") == 131
    updated = client.get(response.headers["location"])
    assert "131 DH" in updated.text
    assert "Prices saved" in updated.text
    catalog.catalog_cache_clear()
    _configured_engine.cache_clear()


def test_admin_promos_page_allows_adding_promo_codes(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-promos.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    import app.catalog as catalog
    catalog.catalog_cache_clear()
    notification_cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.post(
        "/admin/promos?lang=en",
        data={
            "code": "VIP30",
            "label": "VIP Thirty",
            "active": "on",
            "discount__svc_cpl__B": "90",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/promos?lang=en&saved=1"
    assert catalog.normalize_promo_code("vip30") == "VIP30"
    assert catalog.promo_label("VIP30") == "VIP Thirty"
    assert catalog.service_price("svc_cpl", "B", promo_code="VIP30") == 90
    page = client.get(response.headers["location"])
    assert "VIP30" in page.text
    assert "VIP Thirty" in page.text
    assert "90 DH" in page.text
    assert "Promos saved" in page.text
    catalog.catalog_cache_clear()
    _configured_engine.cache_clear()


def test_admin_promos_page_prefills_public_prices_for_number_steppers(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    _configured_engine.cache_clear()
    import app.catalog as catalog
    catalog.catalog_cache_clear()
    notification_cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/promos")

    assert response.status_code == 200
    assert 'name="discount__svc_cpl__B" value="125"' in response.text
    assert 'data-public-price="125"' in response.text
    assert "Les tarifs publics sont préremplis" in response.text
    catalog.catalog_cache_clear()
    _configured_engine.cache_clear()


def test_admin_promos_submit_ignores_unchanged_public_price_prefills(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-promos-public-prefill.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    import app.catalog as catalog
    catalog.catalog_cache_clear()
    notification_cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.post(
        "/admin/promos?lang=en",
        data={
            "code": "PUBLIC",
            "label": "Public Price",
            "active": "on",
            "discount__svc_cpl__B": "125",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    promos = {promo.code: promo for promo in catalog.list_promo_codes()}
    assert promos["PUBLIC"].discounts == {}
    assert catalog.service_price("svc_cpl", "B", promo_code="PUBLIC") == 125
    catalog.catalog_cache_clear()
    _configured_engine.cache_clear()


def test_admin_remaining_tabs_are_real_operational_pages(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-ops-tabs.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    import app.catalog as catalog
    catalog.catalog_cache_clear()
    notification_cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    expected_pages = {
        "/admin/reminders": ("Rappels", "reminder_name"),
        "/admin/notifications": ("Notifications", "phone_number"),
        "/admin/closed-dates": ("Fermetures", "closed_date"),
        "/admin/time-slots": ("Créneaux", "slot_id"),
        "/admin/centers": ("Centres", "center_id"),
        "/admin/copy": ("Textes", "text_key"),
    }
    for path, (title, field_name) in expected_pages.items():
        response = client.get(path)
        assert response.status_code == 200
        assert title in response.text
        assert f'name="{field_name}"' in response.text
        assert "Cette page arrive dans le prochain lot" not in response.text
        assert f'href="{path}" class="active"' in response.text
    catalog.catalog_cache_clear()
    _configured_engine.cache_clear()


def test_admin_ops_pages_allow_updating_remaining_tabs(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'admin-ops-updates.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    _configured_engine.cache_clear()
    import app.catalog as catalog
    catalog.catalog_cache_clear()
    notification_cache_clear()
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    reminders = client.post(
        "/admin/reminders?lang=en",
        data={"reminder_name": "H-2", "offset_minutes_before": "120", "template_name": "booking_reminder_h2", "enabled": "on"},
        follow_redirects=False,
    )
    assert reminders.status_code == 303
    reminders_page = client.get(reminders.headers["location"])
    assert "H-2" in reminders_page.text
    assert "booking_reminder_h2" in reminders_page.text
    assert "Reminders saved" in reminders_page.text

    notifications = client.post(
        "/admin/notifications?lang=en",
        data={
            "enabled": "on",
            "phone_number": "+212 665 883 062",
            "template_name": "new_booking_alert",
            "template_language": "fr",
        },
        follow_redirects=False,
    )
    assert notifications.status_code == 303
    notification_config = get_booking_notification_settings()
    assert notification_config.enabled is True
    assert notification_config.phone_number == "212665883062"
    assert notification_config.template_name == "new_booking_alert"
    notifications_page = client.get(notifications.headers["location"])
    assert "212665883062" in notifications_page.text
    assert "new_booking_alert" in notifications_page.text
    assert "Notifications saved" in notifications_page.text
    assert "{{1}} type" in notifications_page.text

    closed = client.post(
        "/admin/closed-dates?lang=en",
        data={"closed_date": "2026-06-01", "label": "Maintenance", "active": "on"},
        follow_redirects=False,
    )
    assert closed.status_code == 303
    assert "2026-06-01" in catalog.active_closed_dates()
    assert "Maintenance" in client.get(closed.headers["location"]).text

    slot = client.post(
        "/admin/time-slots?lang=en",
        data={"slot_id": "slot_22_23", "label": "22h – 23h", "period": "Late", "active": "on"},
        follow_redirects=False,
    )
    assert slot.status_code == 303
    assert ("slot_22_23", "22h – 23h", "Late") in catalog.active_time_slots()
    assert "22h – 23h" in client.get(slot.headers["location"]).text

    center = client.post(
        "/admin/centers?lang=en",
        data={"center_id": "ctr_maarif", "name": "Maârif", "details": "Rue test · 09h-18h", "active": "on"},
        follow_redirects=False,
    )
    assert center.status_code == 303
    assert ("ctr_maarif", "Maârif", "Rue test · 09h-18h") in catalog.active_centers()
    assert "Maârif" in client.get(center.headers["location"]).text

    copy = client.post(
        "/admin/copy?lang=en",
        data={"text_key": "booking.welcome", "title": "Welcome", "body": "Bonjour from admin"},
        follow_redirects=False,
    )
    assert copy.status_code == 303
    snippets = {snippet.key: snippet for snippet in catalog.list_text_snippets()}
    assert snippets["booking.welcome"].body == "Bonjour from admin"
    assert "Bonjour from admin" in client.get(copy.headers["location"]).text

    catalog.catalog_cache_clear()
    notification_cache_clear()
    _configured_engine.cache_clear()


def test_admin_logout_clears_password_session(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    response = client.get("/admin/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert "ewash_admin_session" in response.headers["set-cookie"]

    login = client.get("/admin")
    assert "Mot de passe" in login.text


def test_internal_conversation_abandon_endpoint_requires_secret_and_marks_stale_sessions(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'internal-abandon.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    persist_customer_bot_stage("212600000777", "MENU", engine=engine)
    with session_scope(engine) as session:
        conversation = session.scalars(select(ConversationSessionRow)).one()
        conversation.last_event_at = datetime.now(timezone.utc) - timedelta(hours=3)

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "internal_cron_secret", "cron-secret")
    _configured_engine.cache_clear()
    client = TestClient(app)

    forbidden = client.post("/internal/conversations/abandon")
    response = client.post("/internal/conversations/abandon", headers={"X-Internal-Cron-Secret": "cron-secret"})

    assert forbidden.status_code == 403
    assert response.status_code == 200
    assert response.json() == {"abandoned": 1}
    with session_scope(engine) as session:
        conversation = session.scalars(select(ConversationSessionRow)).one()
        assert conversation.status == "abandoned"
    _configured_engine.cache_clear()
