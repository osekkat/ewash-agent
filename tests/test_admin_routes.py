from base64 import b64encode

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def _basic_auth(password: str, username: str = "admin") -> dict[str, str]:
    token = b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


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


def test_admin_entrypoint_prompts_for_password_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)

    response = client.get("/admin")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="Ewash Admin"'


def test_admin_entrypoint_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)

    response = client.get("/admin", headers=_basic_auth("wrong-pass"))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="Ewash Admin"'


def test_admin_entrypoint_accepts_configured_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret-pass")
    client = TestClient(app)

    response = client.get("/admin", headers=_basic_auth("secret-pass"))

    assert response.status_code == 200
    assert "Tableau de bord" in response.text
    assert "Portail admin non configuré" not in response.text
