"""Database engine/session helpers for Ewash v0.3 persistence."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base


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
