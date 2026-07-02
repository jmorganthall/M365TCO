"""Uniform CRUD + freshness for the pricing-catalog provenance record.

Every successful pricing load (manual CSV upload OR Partner Center price-sync)
records one `CatalogImport` row here. `pricing_freshness()` then classifies the
NEWEST successful load across *both* that record and the price-sync sheet on
disk — so whichever source ran most recently and worked is the one the Readout
badge and the staleness banner reflect. This is what lets a CSV-only operator
stop seeing "not set · stale" after a good upload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..pricesync import config as ps_config, freshness, storage

# Human labels for the freshness "data_source" field.
_SOURCE_LABELS = {
    "CsvUpload": "CSV upload",
    "PriceSyncApi": "price-sync (Partner Center)",
}


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def record_import(
    db: Session,
    *,
    source: str,
    sku_count: int,
    catalog_version: str = "",
    data_month: Optional[str] = None,
    file_name: str = "",
    sha256: str = "",
) -> models.CatalogImport:
    """Record one successful catalog load. `data_month` defaults to the current
    calendar month (a hand-uploaded sheet is treated as priced as-of now)."""
    row = models.CatalogImport(
        source=source,
        sku_count=sku_count,
        catalog_version=catalog_version or "",
        data_month=data_month or _current_month(),
        file_name=file_name or "",
        sha256=sha256 or "",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def latest(db: Session) -> Optional[models.CatalogImport]:
    """The most recent successful catalog load, or None."""
    return db.execute(
        select(models.CatalogImport).order_by(models.CatalogImport.imported_at.desc())
    ).scalars().first()


def _best_signal(cfg: ps_config.PriceSyncConfig, db: Session):
    """(fetched_at, data_month, source_label) for the NEWEST successful pricing
    load across the CSV-upload record and the price-sync sheet on disk."""
    candidates = []
    meta = storage.read_latest(cfg)
    if meta and meta.get("fetched_at"):
        candidates.append((
            meta["fetched_at"], meta.get("data_month"),
            _SOURCE_LABELS["PriceSyncApi"],
        ))
    imp = latest(db)
    if imp:
        candidates.append((
            imp.imported_at, imp.data_month,
            _SOURCE_LABELS.get(imp.source, imp.source),
        ))
    if not candidates:
        return None, None, None

    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    return max(candidates, key=lambda c: freshness.to_utc(c[0]) or _epoch)


def pricing_freshness(cfg: ps_config.PriceSyncConfig, db: Session):
    """(Freshness, source_label) for the newest successful load across sources."""
    fetched_at, data_month, label = _best_signal(cfg, db)
    fr = freshness.classify(
        fetched_at, data_month,
        aging_days=cfg.aging_days, stale_days=cfg.stale_days,
        use_month_rule=cfg.use_month_rule,
    )
    return fr, label
