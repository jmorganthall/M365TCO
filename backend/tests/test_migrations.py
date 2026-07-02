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
