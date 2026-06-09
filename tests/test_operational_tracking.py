from datetime import date

from sqlalchemy import select

from app.db import init_db, make_engine, session_scope
from app.models import OperationalServiceRecordRow
from app.operational_tracking import (
    OperationalServiceRecordInput,
    create_operational_service_record,
    operational_summary_for_day,
    recent_operational_service_records,
)


def test_create_operational_record_normalizes_status_and_summarizes_day():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    service_date = date(2026, 6, 5)

    with session_scope(engine) as session:
        first = create_operational_service_record(
            session,
            OperationalServiceRecordInput(
                service_date=service_date,
                client_name="Hertz",
                site_name="Casa Aéroport",
                source_channel="whatsapp",
                source_chat_id="120363145509981428@g.us",
                source_message_id="wamid.hertz.1",
                source_order=1,
                sender_id="251990143709184@lid",
                vehicle_model="Geely GX3 Pro",
                matricule="28429 ي 6",
                category="B",
                prestation="lavage",
                unit_price_ht=50,
                photo_reference="/media/hertz/1.jpg",
                raw_payload={"caption": "Plaque : 28429 ي 6"},
            ),
        )
        second = create_operational_service_record(
            session,
            OperationalServiceRecordInput(
                service_date=service_date,
                client_name="Hertz",
                site_name="Casa Aéroport",
                source_channel="whatsapp",
                source_chat_id="120363145509981428@g.us",
                source_message_id="wamid.hertz.2",
                source_order=2,
                sender_id="251990143709184@lid",
                vehicle_model="Kia Seltos rouge",
                matricule="",
                category="B",
                prestation="lavage",
                unit_price_ht=50,
                photo_reference="/media/hertz/2.jpg",
            ),
        )

        assert first.status == "complete"
        assert second.status == "missing_matricule"
        summary = operational_summary_for_day(
            session,
            client_name="Hertz",
            site_name="Casa Aéroport",
            business_date=service_date,
        )
        assert summary.record_count == 2
        assert summary.total_ht_dh == 100
        assert summary.needs_review_count == 1

    with session_scope(engine) as session:
        rows = session.scalars(select(OperationalServiceRecordRow).order_by(OperationalServiceRecordRow.source_order)).all()
        assert [row.vehicle_model for row in rows] == ["Geely GX3 Pro", "Kia Seltos rouge"]
        assert rows[0].matricule == "28429 ي 6"
        assert rows[0].raw_payload_json == '{"caption": "Plaque : 28429 ي 6"}'


def test_recent_operational_records_filters_by_client_and_site_preserving_latest_first():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)

    with session_scope(engine) as session:
        create_operational_service_record(
            session,
            OperationalServiceRecordInput(
                service_date=date(2026, 6, 5),
                client_name="Hertz",
                site_name="Casa Aéroport",
                source_order=1,
                vehicle_model="Geely GX3 Pro",
                matricule="28429 ي 6",
                prestation="lavage",
                unit_price_ht=50,
                photo_reference="/media/1.jpg",
            ),
        )
        create_operational_service_record(
            session,
            OperationalServiceRecordInput(
                service_date=date(2026, 6, 6),
                client_name="Hertz",
                site_name="Fès Aéroport",
                source_order=1,
                vehicle_model="Opel Corsa",
                matricule="75754 ط 6",
                prestation="lavage",
                unit_price_ht=50,
                photo_reference="/media/2.jpg",
            ),
        )
        create_operational_service_record(
            session,
            OperationalServiceRecordInput(
                service_date=date(2026, 6, 6),
                client_name="CityCar",
                site_name="Casa",
                source_order=1,
                vehicle_model="Dacia",
                matricule="11111 ب 6",
                prestation="lavage",
                unit_price_ht=40,
                photo_reference="/media/3.jpg",
            ),
        )

        rows = recent_operational_service_records(session, client_name="Hertz", limit=10)

    assert [(row.client_name, row.site_name, row.matricule) for row in rows] == [
        ("Hertz", "Fès Aéroport", "75754 ط 6"),
        ("Hertz", "Casa Aéroport", "28429 ي 6"),
    ]
