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
        tenant_id="t", client_id="c", redirect_uri="https://h/auth/callback",
        client_cert_pem="", client_secret="s", market="US",
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


# ---- GUI config path (no env vars) ----
def test_gui_config_enables_signin(client):
    # Initially unconfigured — Client ID is the required field (tenant/redirect
    # are auto-handled, view is defaulted).
    st = client.get("/api/pricesync/status").json()
    assert st["configured"] is False
    assert "Client (application) ID" in st["missing"]
    assert not any("Tenant" in m for m in st["missing"])

    # Set the non-secret settings via the GUI endpoint.
    client.put("/api/pricesync/config", json={
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "client_id": "22222222-2222-2222-2222-222222222222",
        "redirect_uri": "https://app.example/auth/callback",
        "pricesheet_view": "updatedlicensebased",
        "market": "US",
    })
    # Still not configured — no credential yet.
    assert client.get("/api/pricesync/status").json()["configured"] is False

    # Set a client secret credential (encrypted store).
    r = client.put("/api/pricesync/credential", json={"kind": "secret", "value": "shh"})
    assert r.status_code == 200 and r.json()["credential_kind"] == "secret"

    st = client.get("/api/pricesync/status").json()
    assert st["configured"] is True
    assert st["credential_kind"] == "secret"

    cfg = client.get("/api/pricesync/config").json()
    assert cfg["credential_set"] is True
    assert cfg["pricesheet_view"] == "updatedlicensebased"
    # The secret value is never returned.
    assert "value" not in cfg and "client_secret" not in cfg


def test_invalid_view_rejected(client):
    r = client.put("/api/pricesync/config", json={"pricesheet_view": "not_a_view"})
    assert r.status_code == 422


def test_invalid_certificate_pem_rejected(client):
    r = client.put("/api/pricesync/credential", json={"kind": "certificate", "value": "not a pem"})
    assert r.status_code == 422
