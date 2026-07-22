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


def _default_sql(column) -> str | None:
    """SQL literal for a column's scalar Python default, or None (callable /
    no default -> add a nullable column)."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    arg = default.arg
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, (int, float)):
        return str(arg)
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    return None


def _auto_add_missing_columns(target_engine=None) -> None:
    """Additive schema reconciliation: ALTER TABLE ADD COLUMN for any model
    column missing from an existing table. create_all() never alters existing
    tables, so a persisted DB from an earlier version would otherwise be missing
    newer columns and every query against that table would fail."""
    from sqlalchemy import inspect, text

    eng = target_engine or engine
    inspector = inspect(eng)
    with eng.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            if not inspector.has_table(table_name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table_name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}'
                default_sql = _default_sql(column)
                if default_sql is not None:
                    ddl += f" DEFAULT {default_sql}"
                conn.execute(text(ddl))


# Columns retired from the model, by explicit (table, column) — the mirror of
# additive reconciliation. A retired column must be physically dropped: the ORM
# no longer writes it, so a legacy NOT NULL (client-side default only) column
# would reject every new row, and a value the GUI can no longer surface would
# violate the no-hidden-data rule.
_RETIRED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("current_microsoft_licenses", "price_basis"),
    ("engagements", "modeling_horizon_years"),          # no multi-year math in v1
    ("global_defaults", "default_modeling_horizon_years"),
    ("third_party_products", "commitment_term_months"),  # collected, never read
    ("price_sync_settings", "redirect_uri"),             # never referenced at all
)


def _drop_retired_columns(target_engine=None) -> None:
    from sqlalchemy import inspect, text

    eng = target_engine or engine
    inspector = inspect(eng)
    with eng.begin() as conn:
        for table_name, column_name in _RETIRED_COLUMNS:
            if not inspector.has_table(table_name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table_name)}
            model_table = Base.metadata.tables.get(table_name)
            # Defensive: never drop a column the current model still maps.
            if column_name not in existing or (
                model_table is not None and column_name in model_table.columns
            ):
                continue
            conn.execute(text(f'ALTER TABLE "{table_name}" DROP COLUMN "{column_name}"'))


def init_db() -> None:
    # Import models so they register on Base.metadata, then create tables.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _auto_add_missing_columns()
    _drop_retired_columns()
