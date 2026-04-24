from sqlalchemy import select

from app.booking import Booking
from app.db import init_db, make_engine, session_scope
from app.models import BookingRow, BookingStatusEventRow, Customer, CustomerVehicle
from app.persistence import (
    admin_dashboard_summary,
    persist_confirmed_booking,
    persist_booking_addon,
)


def _sample_booking(phone: str = "212665883062") -> Booking:
    booking = Booking(phone=phone)
    booking.name = "Oussama"
    booking.vehicle_type = "B — Berline / SUV"
    booking.category = "B"
    booking.car_model = "BMW 330i"
    booking.color = "Noir"
    booking.service = "svc_cpl"
    booking.service_bucket = "wash"
    booking.service_label = "Le Complet — 110 DH"
    booking.price_dh = 110
    booking.price_regular_dh = 125
    booking.promo_code = "YS26"
    booking.promo_label = "Yasmine Signature"
    booking.location_mode = "home"
    booking.geo = "📍 33.5, -7.6"
    booking.address = "Bouskoura, portail bleu"
    booking.date_label = "Demain"
    booking.slot = "09h – 11h"
    booking.note = "Appeler en arrivant"
    booking.assign_ref()
    return booking


def test_persist_confirmed_booking_upserts_customer_vehicle_and_status_event():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    booking = _sample_booking()

    row = persist_confirmed_booking(booking, engine=engine)

    assert row is not None
    assert row.ref == booking.ref
    assert row.status == "confirmed"

    with session_scope(engine) as session:
        customer = session.get(Customer, "212665883062")
        assert customer is not None
        assert customer.display_name == "Oussama"
        assert customer.booking_count == 1

        vehicle = session.scalars(select(CustomerVehicle)).one()
        assert vehicle.customer_phone == "212665883062"
        assert vehicle.category == "B"
        assert vehicle.model == "BMW 330i"
        assert vehicle.color == "Noir"
        assert vehicle.label == "BMW 330i — Noir"

        saved = session.scalars(select(BookingRow)).one()
        assert saved.customer_phone == "212665883062"
        assert saved.customer_vehicle_id == vehicle.id
        assert saved.customer_name == "Oussama"
        assert saved.service_id == "svc_cpl"
        assert saved.price_dh == 110
        assert saved.price_regular_dh == 125
        assert saved.promo_code == "YS26"
        assert saved.location_mode == "home"
        assert saved.geo == "📍 33.5, -7.6"
        assert saved.address == "Bouskoura, portail bleu"
        assert saved.note == "Appeler en arrivant"
        assert "BMW 330i" in saved.raw_booking_json

        event = session.scalars(select(BookingStatusEventRow)).one()
        assert event.booking_id == saved.id
        assert event.from_status == "awaiting_confirmation"
        assert event.to_status == "confirmed"
        assert event.actor == "customer"


def test_persist_confirmed_booking_reuses_existing_vehicle_for_repeat_customer():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    first = _sample_booking()
    persist_confirmed_booking(first, engine=engine)

    second = _sample_booking()
    persist_confirmed_booking(second, engine=engine)

    with session_scope(engine) as session:
        assert len(session.scalars(select(CustomerVehicle)).all()) == 1
        assert len(session.scalars(select(BookingRow)).all()) == 2
        customer = session.get(Customer, "212665883062")
        assert customer.booking_count == 2


def test_persist_booking_addon_updates_confirmed_booking_row():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    booking = _sample_booking()
    persist_confirmed_booking(booking, engine=engine)

    persist_booking_addon(
        booking.ref,
        addon_service="svc_pol",
        addon_service_label="Le Polissage — 770 DH (-10%)",
        addon_price_dh=770,
        engine=engine,
    )

    with session_scope(engine) as session:
        saved = session.scalars(select(BookingRow)).one()
        assert saved.addon_service == "svc_pol"
        assert saved.addon_service_label == "Le Polissage — 770 DH (-10%)"
        assert saved.addon_price_dh == 770


def test_admin_dashboard_summary_counts_db_rows_and_recent_bookings():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    booking = _sample_booking()
    persist_confirmed_booking(booking, engine=engine)

    summary = admin_dashboard_summary(engine=engine)

    assert summary.total_bookings == 1
    assert summary.confirmed_bookings == 1
    assert summary.customers == 1
    assert summary.pending_reminders == 0
    assert len(summary.recent_bookings) == 1
    assert summary.recent_bookings[0].customer_name == "Oussama"
    assert summary.recent_bookings[0].service_label == "Le Complet — 110 DH"
