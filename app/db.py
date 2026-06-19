"""Database engine/session helpers for Ewash v0.3 persistence."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import re

from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base, BookingRefCounterRow, BookingLineItemRow, BookingRow, CustomerVehicle, ServiceRow, VehicleColor, VehicleModel


def normalize_database_url(database_url: str) -> str:
    """Normalize provider URLs for SQLAlchemy 2.

    Railway and Heroku-style Postgres URLs often start with `postgres://`, which
    SQLAlchemy does not treat as a dialect. Prefer psycopg v3 explicitly.
    """
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def make_engine(database_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine.

    Tests pass an explicit SQLite URL. Production uses `DATABASE_URL` from the
    environment once Railway Postgres is provisioned.
    """
    raw_url = database_url or settings.database_url
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not configured")
    url = normalize_database_url(raw_url)

    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)


def init_db(engine: Engine) -> None:
    """Create all v0.3 tables. Alembic can replace this after MVP."""
    Base.metadata.create_all(bind=engine)
    _ensure_customer_contact_columns(engine)
    _ensure_customer_bot_stage_columns(engine)
    _ensure_customer_vehicle_reference_columns(engine)
    _ensure_booking_operational_columns(engine)
    _seed_service_catalog(engine)
    _backfill_vehicle_reference_data(engine)
    _backfill_booking_line_items(engine)
    _backfill_booking_ref_counters(engine)


def _ensure_customer_contact_columns(engine: Engine) -> None:
    """Add WhatsApp contact columns for customer rows created before this slice."""
    inspector = inspect(engine)
    if "customers" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("customers")}
    statements: list[str] = []
    if "whatsapp_profile_name" not in columns:
        statements.append("ALTER TABLE customers ADD COLUMN whatsapp_profile_name VARCHAR(120) DEFAULT ''")
    if "whatsapp_wa_id" not in columns:
        statements.append("ALTER TABLE customers ADD COLUMN whatsapp_wa_id VARCHAR(32) DEFAULT ''")
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_customer_bot_stage_columns(engine: Engine) -> None:
    """Add WhatsApp funnel-stage columns for customer rows created before this slice."""
    inspector = inspect(engine)
    if "customers" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("customers")}
    statements: list[str] = []
    if "last_bot_stage" not in columns:
        statements.append("ALTER TABLE customers ADD COLUMN last_bot_stage VARCHAR(60) DEFAULT ''")
    if "last_bot_stage_label" not in columns:
        statements.append("ALTER TABLE customers ADD COLUMN last_bot_stage_label VARCHAR(160) DEFAULT ''")
    if "last_bot_stage_at" not in columns:
        statements.append(f"ALTER TABLE customers ADD COLUMN last_bot_stage_at {_datetime_column_sql(engine)}")
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _datetime_column_sql(engine: Engine) -> str:
    if engine.dialect.name == "postgresql":
        return "TIMESTAMP WITH TIME ZONE"
    return "DATETIME"


def _date_column_sql(engine: Engine) -> str:
    if engine.dialect.name == "postgresql":
        return "DATE"
    return "DATE"


def _float_column_sql(engine: Engine) -> str:
    if engine.dialect.name == "postgresql":
        return "DOUBLE PRECISION"
    return "FLOAT"


def _normalize_reference_value(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _get_or_create_vehicle_model(session: Session, *, category: str, model: str) -> VehicleModel | None:
    normalized = _normalize_reference_value(model)
    if not normalized:
        return None
    existing = session.query(VehicleModel).filter_by(category=category, normalized_name=normalized).first()
    if existing is not None:
        return existing
    row = VehicleModel(category=category, name=model.strip(), normalized_name=normalized, active=True)
    session.add(row)
    session.flush()
    return row


def _get_or_create_vehicle_color(session: Session, *, color: str) -> VehicleColor | None:
    normalized = _normalize_reference_value(color)
    if not normalized:
        return None
    existing = session.query(VehicleColor).filter_by(normalized_name=normalized).first()
    if existing is not None:
        return existing
    row = VehicleColor(name=color.strip(), normalized_name=normalized, active=True)
    session.add(row)
    session.flush()
    return row


def _backfill_vehicle_reference_data(engine: Engine) -> None:
    with Session(engine, expire_on_commit=False, future=True) as session:
        vehicles = session.query(CustomerVehicle).all()
        changed = False
        for vehicle in vehicles:
            if vehicle.category == "MOTO":
                continue
            if vehicle.model and vehicle.model_id is None:
                model = _get_or_create_vehicle_model(session, category=vehicle.category or "", model=vehicle.model)
                if model is not None:
                    vehicle.model_id = model.id
                    changed = True
            if vehicle.color and vehicle.color_id is None:
                color = _get_or_create_vehicle_color(session, color=vehicle.color)
                if color is not None:
                    vehicle.color_id = color.id
                    changed = True
        if changed:
            session.commit()


def _ensure_customer_vehicle_reference_columns(engine: Engine) -> None:
    """Add normalized vehicle FK columns for DBs created before this schema slice.

    `create_all()` creates the columns for fresh databases, but does not alter
    existing Railway Postgres tables. Keep this deliberately tiny/idempotent
    until the project needs Alembic.
    """
    inspector = inspect(engine)
    if "customer_vehicles" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("customer_vehicles")}
    statements: list[str] = []
    if "model_id" not in columns:
        statements.append("ALTER TABLE customer_vehicles ADD COLUMN model_id INTEGER")
    if "color_id" not in columns:
        statements.append("ALTER TABLE customer_vehicles ADD COLUMN color_id INTEGER")
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_booking_operational_columns(engine: Engine) -> None:
    """Add normalized booking fields for databases created before this schema slice."""
    inspector = inspect(engine)
    if "bookings" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("bookings")}
    column_defs = {
        "center_id": "VARCHAR(40) DEFAULT ''",
        "address_text": "TEXT DEFAULT ''",
        "location_name": "VARCHAR(160) DEFAULT ''",
        "location_address": "TEXT DEFAULT ''",
        "latitude": _float_column_sql(engine),
        "longitude": _float_column_sql(engine),
        "appointment_date": _date_column_sql(engine),
        "slot_id": "VARCHAR(40) DEFAULT ''",
        "total_price_dh": "INTEGER DEFAULT 0",
    }
    statements = [
        f"ALTER TABLE bookings ADD COLUMN {column_name} {column_sql}"
        for column_name, column_sql in column_defs.items()
        if column_name not in columns
    ]
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _seed_service_catalog(engine: Engine) -> None:
    """Mirror the code catalog into the DB service table without overwriting admin state."""
    from . import catalog

    rows: list[tuple[str, str, str, str, str, int]] = []
    order = 0
    for bucket, services in (("wash", catalog.SERVICES_WASH), ("detailing", catalog.SERVICES_DETAILING)):
        for service_id, name, description, _prices in services:
            rows.append((service_id, name, description, bucket, "car", order))
            order += 1
    for service_id, name, description, _price in catalog.SERVICES_MOTO:
        rows.append((service_id, name, description, "wash", "moto", order))
        order += 1

    with Session(engine, expire_on_commit=False, future=True) as session:
        changed = False
        for service_id, name, description, bucket, vehicle_lane, sort_order in rows:
            service = session.get(ServiceRow, service_id)
            if service is None:
                session.add(
                    ServiceRow(
                        service_id=service_id,
                        name=name,
                        description=description,
                        bucket=bucket,
                        vehicle_lane=vehicle_lane,
                        active=True,
                        sort_order=sort_order,
                    )
                )
                changed = True
            else:
                service.name = service.name or name
                service.description = service.description or description
                service.bucket = service.bucket or bucket
                service.vehicle_lane = service.vehicle_lane or vehicle_lane
        if changed:
            session.commit()


def _backfill_booking_line_items(engine: Engine) -> None:
    """Create normalized line items for bookings persisted before the table existed."""
    with Session(engine, expire_on_commit=False, future=True) as session:
        bookings = session.scalars(select(BookingRow)).all()
        changed = False
        for booking in bookings:
            if not booking.total_price_dh:
                booking.total_price_dh = (booking.price_dh or 0) + (booking.addon_price_dh or 0)
                changed = True
            if not booking.address_text and booking.address:
                booking.address_text = booking.address
                changed = True
            existing = session.scalars(
                select(BookingLineItemRow).where(BookingLineItemRow.booking_id == booking.id)
            ).first()
            if existing is not None:
                continue
            if booking.service_id:
                session.add(
                    BookingLineItemRow(
                        booking_id=booking.id,
                        kind="main",
                        service_id=booking.service_id,
                        service_bucket=booking.service_bucket,
                        label_snapshot=booking.service_label or booking.service_id,
                        quantity=1,
                        unit_price_dh=booking.price_dh or 0,
                        regular_price_dh=booking.price_regular_dh or booking.price_dh or 0,
                        total_price_dh=booking.price_dh or 0,
                        sort_order=0,
                    )
                )
                changed = True
            if booking.addon_service:
                session.add(
                    BookingLineItemRow(
                        booking_id=booking.id,
                        kind="addon",
                        service_id=booking.addon_service,
                        service_bucket="detailing",
                        label_snapshot=booking.addon_service_label or booking.addon_service,
                        quantity=1,
                        unit_price_dh=booking.addon_price_dh or 0,
                        regular_price_dh=booking.addon_price_dh or 0,
                        total_price_dh=booking.addon_price_dh or 0,
                        discount_label="-20%",
                        sort_order=10,
                    )
                )
                changed = True
        if changed:
            session.commit()


def _backfill_booking_ref_counters(engine: Engine) -> None:
    """Seed DB-backed yearly booking ref counters from existing rows."""
    ref_re = re.compile(r"^EW-(\d{4})-(\d+)$")
    with Session(engine, expire_on_commit=False, future=True) as session:
        refs = session.scalars(select(BookingRow.ref)).all()
        counters: dict[int, int] = {}
        for ref in refs:
            match = ref_re.match(ref or "")
            if not match:
                continue
            year = int(match.group(1))
            counters[year] = max(counters.get(year, 0), int(match.group(2)))
        changed = False
        for year, max_counter in counters.items():
            row = session.get(BookingRefCounterRow, year)
            if row is None:
                session.add(BookingRefCounterRow(year=year, last_counter=max_counter))
                changed = True
            elif row.last_counter < max_counter:
                row.last_counter = max_counter
                changed = True
        if changed:
            session.commit()


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Provide a transactional session that commits or rolls back."""
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
