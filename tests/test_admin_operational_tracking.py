from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import admin
from app.config import settings
from app.db import init_db, make_engine, session_scope
from app.operational_tracking import OperationalServiceRecordInput, create_operational_service_record


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin.router)
    return TestClient(app)


def _login(client: TestClient) -> None:
    response = client.post("/admin", data={"password": settings.admin_password}, follow_redirects=False)
    assert response.status_code == 303


def test_admin_tracking_page_shows_hertz_summary_and_recent_records(monkeypatch, tmp_path):
    db_path = tmp_path / "tracking-admin.sqlite3"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    init_db(engine)
    monkeypatch.setattr(admin, "_configured_engine", lambda: engine)

    with session_scope(engine) as session:
        create_operational_service_record(
            session,
            OperationalServiceRecordInput(
                service_date=date.today(),
                client_name="Hertz",
                site_name="Casa Aéroport",
                source_channel="whatsapp",
                source_chat_id="120363145509981428@g.us",
                source_order=1,
                vehicle_model="Geely GX3 Pro",
                matricule="28429 ي 6",
                category="B",
                prestation="lavage",
                unit_price_ht=50,
                photo_reference="/media/hertz/1.jpg",
            ),
        )
        create_operational_service_record(
            session,
            OperationalServiceRecordInput(
                service_date=date.today(),
                client_name="Hertz",
                site_name="Fès Aéroport",
                source_channel="whatsapp",
                source_chat_id="120363315757708016@g.us",
                source_order=1,
                vehicle_model="Opel Corsa blanche",
                matricule="",
                category="B",
                prestation="lavage",
                unit_price_ht=50,
                photo_reference="/media/hertz/2.jpg",
            ),
        )

    client = _client()
    _login(client)

    page = client.get("/admin/tracking")

    assert page.status_code == 200
    assert "Suivi opérationnel" in page.text
    assert "Hertz Casa Aéroport" in page.text
    assert "Hertz Fès Aéroport" in page.text
    assert "Total HT aujourd" in page.text
    assert "100 MAD" in page.text
    assert "Geely GX3 Pro" in page.text
    assert "28429 ي 6" in page.text
    assert "missing_matricule" in page.text
