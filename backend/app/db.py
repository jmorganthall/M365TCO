"""Database session/engine wiring.

SQLAlchemy keeps a Postgres swap as configuration (DATABASE_URL) not code.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        # Ensure the sqlite directory exists for file-based URLs.
        path = url.split("sqlite:///")[-1]
        if path and path not in (":memory:",):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return create_engine(url, connect_args=connect_args, future=True)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # Import models so they register on Base.metadata, then create tables.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
