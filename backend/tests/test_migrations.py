"""Additive column auto-migration (upgrade a persisted DB missing new columns)."""

from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401  register tables on Base.metadata
from app.db import _auto_add_missing_columns


def test_adds_missing_columns_to_existing_table(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    # Simulate an old global_defaults table missing later-added columns.
    with eng.begin() as c:
        c.execute(text(
            "CREATE TABLE global_defaults ("
            "id VARCHAR PRIMARY KEY, default_tooling_pct NUMERIC)"
        ))
        c.execute(text(
            "INSERT INTO global_defaults (id, default_tooling_pct) "
            "VALUES ('singleton', 0.3)"
        ))

    _auto_add_missing_columns(eng)

    cols = {c["name"] for c in inspect(eng).get_columns("global_defaults")}
    assert "openrouter_model" in cols
    assert "openrouter_web_search" in cols
    assert "sanity_check_web_search" in cols
    assert "default_modeling_horizon_years" in cols
    # Existing data preserved.
    with eng.connect() as c:
        row = c.execute(text(
            "SELECT default_tooling_pct FROM global_defaults WHERE id='singleton'"
        )).one()
    assert float(row[0]) == 0.3


def test_reconcile_is_noop_on_current_schema(tmp_path):
    from app.db import Base

    eng = create_engine(f"sqlite:///{tmp_path}/new.db")
    Base.metadata.create_all(eng)
    # Should not raise and should leave a full table intact.
    _auto_add_missing_columns(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("price_sync_settings")}
    assert "signed_in_user" in cols


def test_drops_retired_price_basis_column(tmp_path):
    """A legacy DB carries current_microsoft_licenses.price_basis as NOT NULL with
    no server default — after retirement the ORM never writes it, so it must be
    physically dropped or every new row would violate the constraint."""
    from app.db import Base, _drop_retired_columns

    eng = create_engine(f"sqlite:///{tmp_path}/legacy.db")
    Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(text(
            'ALTER TABLE "current_microsoft_licenses" '
            'ADD COLUMN "price_basis" VARCHAR(9) NOT NULL DEFAULT \'Unknown\''
        ))

    _drop_retired_columns(eng)

    cols = {c["name"] for c in inspect(eng).get_columns("current_microsoft_licenses")}
    assert "price_basis" not in cols
    # Idempotent: a second run is a no-op.
    _drop_retired_columns(eng)


def test_drop_retired_is_noop_without_the_column(tmp_path):
    from app.db import Base, _drop_retired_columns

    eng = create_engine(f"sqlite:///{tmp_path}/fresh.db")
    Base.metadata.create_all(eng)
    _drop_retired_columns(eng)  # nothing to drop; must not raise
    cols = {c["name"] for c in inspect(eng).get_columns("current_microsoft_licenses")}
    assert "price_basis" not in cols
