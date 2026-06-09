from datetime import date

from fastapi.testclient import TestClient

from app.config import settings
from app.db import init_db, make_engine, session_scope
from app.main import app
from app.models import OperationalServiceRecordRow
from app.persistence import _configured_engine


def test_internal_operational_tracking_endpoint_requires_configured_secret(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'tracking-internal.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "internal_cron_secret", "track-secret")
    _configured_engine.cache_clear()
    client = TestClient(app)

    response = client.post(
        "/internal/operational-tracking/records",
        headers={"X-Internal-Cron-Secret": "wrong"},
        json={"records": []},
    )

    assert response.status_code == 403
    _configured_engine.cache_clear()


def test_internal_operational_tracking_endpoint_ingests_records(monkeypatch, tmp_path):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'tracking-internal-ingest.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "internal_cron_secret", "track-secret")
    _configured_engine.cache_clear()
    client = TestClient(app)

    response = client.post(
        "/internal/operational-tracking/records",
        headers={"X-Internal-Cron-Secret": "track-secret"},
        json={
            "records": [
                {
                    "service_date": "2026-06-05",
                    "client_name": "Hertz",
                    "site_name": "Casa Aéroport",
                    "source_channel": "whatsapp",
                    "source_chat_id": "120363145509981428@g.us",
                    "source_message_id": "wamid.hertz.1",
                    "source_order": 1,
                    "sender_id": "251990143709184@lid",
                    "vehicle_model": "Geely GX3 Pro",
                    "matricule": "28429 ي 6",
                    "category": "B",
                    "prestation": "lavage",
                    "unit_price_ht": 50,
                    "photo_reference": "/media/hertz/1.jpg",
                    "raw_payload": {"caption": "Plaque : 28429 ي 6"},
                },
                {
                    "service_date": "2026-06-05",
                    "client_name": "Hertz",
                    "site_name": "Casa Aéroport",
                    "source_order": 2,
                    "vehicle_model": "Kia Seltos rouge",
                    "matricule": "",
                    "prestation": "lavage",
                    "unit_price_ht": 50,
                    "photo_reference": "/media/hertz/2.jpg",
                },
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["created"] == 2
    assert len(response.json()["record_ids"]) == 2
    with session_scope(engine) as session:
        rows = session.query(OperationalServiceRecordRow).order_by(OperationalServiceRecordRow.source_order).all()
        assert len(rows) == 2
        assert rows[0].client_name == "Hertz"
        assert rows[0].service_date == date(2026, 6, 5)
        assert rows[0].status == "complete"
        assert rows[1].status == "missing_matricule"
    _configured_engine.cache_clear()
