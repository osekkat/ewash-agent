from fastapi.testclient import TestClient

from app.booking import Booking
from app.config import settings
from app.db import init_db, make_engine
from app.main import app
from app.persistence import _configured_engine, persist_confirmed_booking


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
    assert "Les pages opérationnelles arrivent dans les prochains lots" in dashboard.text
    assert "class=\"metric-grid\"" in dashboard.text
    assert "class=\"empty-panel\"" in dashboard.text
    assert 'href="/admin/bookings"' in dashboard.text
    assert 'href="/admin/customers"' in dashboard.text
    assert 'href="/admin/prices"' in dashboard.text


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


def test_admin_sidebar_pages_are_clickable_placeholders(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)
    client.post(
        "/admin",
        content="password=secret-pass",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    expected_pages = {
        "/admin/prices": "Prix",
        "/admin/promos": "Promos",
        "/admin/reminders": "Rappels",
        "/admin/closed-dates": "Fermetures",
        "/admin/time-slots": "Créneaux",
        "/admin/centers": "Centres",
        "/admin/copy": "Textes",
    }
    for path, title in expected_pages.items():
        response = client.get(path)
        assert response.status_code == 200
        assert title in response.text
        assert "Cette page arrive dans le prochain lot" in response.text
        assert f'href="{path}"' in response.text


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
