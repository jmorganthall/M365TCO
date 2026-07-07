"""Offline tests for the price-sheet sync module (PRD §10 acceptance criteria
that don't require a live Partner Center tenant)."""

import json
import os
from datetime import datetime, timedelta, timezone

from app.pricesync import freshness
from app.pricesync.config import PriceSyncConfig
from app.pricesync import storage

UTC = timezone.utc


def _cfg(tmp_path) -> PriceSyncConfig:
    return PriceSyncConfig(
        tenant_id="t", client_id="c", client_cert_pem="", client_secret="s",
        refresh_token="rt", market="US",
        pricesheet_view="updatedlicensebased", timeline="current",
        data_dir=str(tmp_path), aging_days=25, stale_days=30,
        use_month_rule=True, retention_count=2, notify_webhook_url="",
    )


# ---- Freshness (FR-AGE) ----
def test_no_cached_sheet_is_stale():
    fr = freshness.classify(None, None, now=datetime(2026, 7, 15, tzinfo=UTC))
    assert fr.state == "stale"
    assert fr.age_days is None


def test_ac3_26_days_old_is_aging():
    now = datetime(2026, 7, 27, tzinfo=UTC)
    fetched = now - timedelta(days=26)
    fr = freshness.classify(
        fetched.isoformat(), now.strftime("%Y-%m"), now=now,
        aging_days=25, stale_days=30, use_month_rule=True,
    )
    assert fr.age_days == 26
    assert fr.state == "aging"


def test_over_stale_threshold_is_stale():
    now = datetime(2026, 7, 27, tzinfo=UTC)
    fetched = now - timedelta(days=31)
    fr = freshness.classify(fetched.isoformat(), "2026-07", now=now)
    assert fr.state == "stale"


def test_ac4_prior_month_is_at_least_aging():
    now = datetime(2026, 7, 2, tzinfo=UTC)
    fetched = now - timedelta(days=3)  # fresh by day rule
    fr = freshness.classify(
        fetched.isoformat(), "2026-06", now=now, use_month_rule=True,
    )
    assert fr.day_state == "fresh"
    assert fr.state == "aging"  # month rule escalates


def test_stricter_state_wins():
    now = datetime(2026, 7, 5, tzinfo=UTC)
    fetched = now - timedelta(days=31)  # stale by day
    fr = freshness.classify(fetched.isoformat(), "2026-06", now=now)  # aging by month
    assert fr.state == "stale"  # stricter of {stale, aging}


def test_month_rule_can_be_disabled():
    now = datetime(2026, 7, 2, tzinfo=UTC)
    fetched = now - timedelta(days=1)
    fr = freshness.classify(
        fetched.isoformat(), "2026-06", now=now, use_month_rule=False,
    )
    assert fr.state == "fresh"


# ---- Storage (FR-STORE) ----
def _stage_csv(cfg, text="ProductId,SkuId\nX,Y\n"):
    path = os.path.join(cfg.data_dir, ".staging.csv")
    os.makedirs(cfg.data_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_commit_writes_sheet_metadata_and_latest(tmp_path):
    cfg = _cfg(tmp_path)
    meta = storage.commit_sheet(
        cfg, _stage_csv(cfg), data_month="2026-07",
        compressed_on_wire=True, mfa_compliant=True,
    )
    assert meta["file_name"] == "pricesheet_updatedlicensebased_US_202607.csv"
    assert os.path.exists(os.path.join(cfg.data_dir, meta["file_name"]))
    assert os.path.exists(os.path.join(cfg.data_dir, meta["file_name"] + ".json"))
    latest = storage.read_latest(cfg)
    assert latest["sha256"] == meta["sha256"]
    assert len(meta["sha256"]) == 64
    assert meta["mfa_compliant"] is True
    assert meta["compressed_on_wire"] is True


def test_retention_keeps_only_newest_n(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.retention_count = 2
    for i, month in enumerate(["2026-05", "2026-06", "2026-07"]):
        # touch mtimes in order by committing sequentially
        storage.commit_sheet(
            cfg, _stage_csv(cfg, f"data{i}"), data_month=month,
            compressed_on_wire=False, mfa_compliant=None,
        )
    sheets = [f for f in os.listdir(cfg.data_dir) if f.startswith("pricesheet_") and f.endswith(".csv")]
    assert len(sheets) == 2  # only the newest 2 retained
    assert "pricesheet_updatedlicensebased_US_202605.csv" not in sheets


def test_ac8_failed_fetch_leaves_previous_intact(tmp_path):
    # Commit a good sheet, then simulate a fetch that fails BEFORE commit: the
    # staged temp file is discarded and the good sheet + latest.json remain.
    cfg = _cfg(tmp_path)
    good = storage.commit_sheet(
        cfg, _stage_csv(cfg, "GOOD"), data_month="2026-07",
        compressed_on_wire=False, mfa_compliant=True,
    )
    # Simulate a partial download that never gets committed.
    staged = os.path.join(cfg.data_dir, ".staging.download")
    with open(staged, "wb") as fh:
        fh.write(b"partial")
    os.remove(staged)  # cleanup path on failure

    latest = storage.read_latest(cfg)
    assert latest["file_name"] == good["file_name"]
    with open(os.path.join(cfg.data_dir, good["file_name"])) as fh:
        assert fh.read() == "GOOD"


# ---- CSP config path (GUI, no env vars) ----
def test_gui_csp_config_enables_refresh(client):
    # Initially unconfigured.
    st = client.get("/api/pricesync/status").json()
    assert st["configured"] is False
    for field in ("Partner tenant ID", "Client (application) ID", "Consent refresh token"):
        assert field in st["missing"]

    # Non-secret settings.
    client.put("/api/pricesync/config", json={
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "client_id": "22222222-2222-2222-2222-222222222222",
        "pricesheet_view": "updatedlicensebased", "market": "US",
    })
    assert client.get("/api/pricesync/status").json()["configured"] is False

    # App credential (secret).
    r = client.put("/api/pricesync/credential", json={"kind": "secret", "value": "shh"})
    assert r.status_code == 200
    assert client.get("/api/pricesync/status").json()["configured"] is False  # still no token

    # Consent refresh token.
    r = client.put("/api/pricesync/refresh-token", json={"value": "the-refresh-token"})
    assert r.status_code == 200 and r.json()["refresh_token_set"] is True

    st = client.get("/api/pricesync/status").json()
    assert st["configured"] is True

    cfg = client.get("/api/pricesync/config").json()
    assert cfg["credential_set"] is True
    assert cfg["refresh_token_set"] is True
    # Neither the secret nor the refresh token is ever returned.
    assert "client_secret" not in cfg and "refresh_token" not in cfg


# ---- Catalog-derived freshness floor + provenance reconciliation ----
# A catalog loaded before provenance recording existed populates MicrosoftSku but
# writes no CatalogImport row. Freshness must still reflect the loaded catalog
# rather than reading "not set · stale" (the two data sources cannot disagree).
def _clear_catalog(db):
    from app import models

    db.query(models.CatalogImport).delete()
    db.query(models.MicrosoftSku).delete()
    db.commit()


def _add_sku(db, *, version="", effective_start=None):
    import uuid as _uuid
    from datetime import date

    from app import models

    row = models.MicrosoftSku(
        product_id=f"P-{_uuid.uuid4().hex[:8]}", sku_id="S1",
        product_title="Microsoft 365 E3", sku_title="Microsoft 365 E3",
        catalog_version=version,
        effective_start_date=date.fromisoformat(effective_start) if effective_start else None,
    )
    db.add(row)
    db.commit()


def test_derive_catalog_signal_reads_month_from_version(client):
    from app.db import SessionLocal
    from app.services import catalog_provenance

    db = SessionLocal()
    try:
        _clear_catalog(db)
        assert catalog_provenance.derive_catalog_signal(db) is None  # empty catalog
        _add_sku(db, version="2026-06")
        anchor, data_month, version = catalog_provenance.derive_catalog_signal(db)
        assert data_month == "2026-06"
        assert version == "2026-06"
        assert anchor is not None  # first-of-month anchor when only the month is known
    finally:
        _clear_catalog(db)
        db.close()


def test_derive_catalog_signal_falls_back_to_effective_date(client):
    from app.db import SessionLocal
    from app.services import catalog_provenance

    db = SessionLocal()
    try:
        _clear_catalog(db)
        # A filename version carries no month; the SKU effective date supplies it.
        _add_sku(db, version="Jul_NCE_LicenseBasedPL_GA_UM.csv", effective_start="2026-06-01")
        anchor, data_month, version = catalog_provenance.derive_catalog_signal(db)
        assert data_month == "2026-06"
        assert anchor is not None
    finally:
        _clear_catalog(db)
        db.close()


def test_reconcile_creates_provenance_and_is_idempotent(client):
    from app.db import SessionLocal
    from app.services import catalog_provenance

    db = SessionLocal()
    try:
        _clear_catalog(db)
        _add_sku(db, version="2026-06", effective_start="2026-06-01")
        assert catalog_provenance.latest(db) is None  # catalog present, no provenance

        row = catalog_provenance.reconcile_missing_provenance(db)
        assert row is not None
        assert row.source == "Reconciled"
        assert row.data_month == "2026-06"
        assert row.sku_count == 1

        # Idempotent: a second pass creates nothing more.
        assert catalog_provenance.reconcile_missing_provenance(db) is None
        assert catalog_provenance.catalog_sku_count(db) == 1
    finally:
        _clear_catalog(db)
        db.close()


def test_status_never_reads_not_set_with_a_loaded_catalog(client):
    """Regression: a catalog with SKUs but no CatalogImport row must not surface
    as 'not set' — the badge is tied to the catalog that actually feeds pricing."""
    from app.db import SessionLocal
    from app.services import catalog_provenance

    db = SessionLocal()
    try:
        _clear_catalog(db)
        _add_sku(db, version="2026-06", effective_start="2026-06-01")
    finally:
        db.close()

    st = client.get("/api/pricesync/status").json()
    assert st["catalog_sku_count"] >= 1
    assert st["data_month"] == "2026-06"  # derived from the catalog, not "not set"

    db = SessionLocal()
    try:
        _clear_catalog(db)
    finally:
        db.close()


def test_invalid_view_rejected(client):
    r = client.put("/api/pricesync/config", json={"pricesheet_view": "not_a_view"})
    assert r.status_code == 422


def test_invalid_certificate_pem_rejected(client):
    r = client.put("/api/pricesync/credential", json={"kind": "certificate", "value": "not a pem"})
    assert r.status_code == 422


def test_empty_refresh_token_rejected(client):
    r = client.put("/api/pricesync/refresh-token", json={"value": "   "})
    assert r.status_code == 422
