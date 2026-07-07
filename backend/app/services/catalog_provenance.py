"""Uniform CRUD + freshness for the pricing-catalog provenance record.

Every successful pricing load (manual CSV upload OR Partner Center price-sync)
records one `CatalogImport` row here. `pricing_freshness()` then classifies the
NEWEST successful load across *three* signals: that record, the price-sync sheet
on disk, and — as a floor — the loaded catalog itself. Deriving a signal from the
catalog means freshness can never contradict a catalog that is demonstrably
present: a `MicrosoftSku` table with rows can never read "not set · stale" just
because its `CatalogImport` row is missing (e.g. a catalog imported before
provenance recording existed). This is what lets a CSV-only operator stop seeing
"not set · stale" after a good upload — and reconciles catalogs loaded before the
provenance record was introduced.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..pricesync import config as ps_config, freshness, storage

# Human labels for the freshness "data_source" field.
_SOURCE_LABELS = {
    "CsvUpload": "CSV upload",
    "PriceSyncApi": "price-sync (Partner Center)",
    "Reconciled": "existing catalog (provenance reconciled)",
}

_MONTH_RE = re.compile(r"(20\d{2})[-_/.]?(0[1-9]|1[0-2])")


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def catalog_sku_count(db: Session) -> int:
    """How many priced SKU rows the catalog holds right now — the ground truth
    for "is pricing loaded at all", independent of any provenance record."""
    return int(db.execute(select(func.count(models.MicrosoftSku.id))).scalar() or 0)


def derive_catalog_signal(db: Session):
    """(anchor_datetime, data_month, catalog_version) inferred from the loaded
    catalog itself, or None when the catalog is empty. Used both as the freshness
    floor and by the startup reconciliation.

    The catalog carries no import timestamp, so freshness is anchored to the best
    date the catalog *does* carry: the month named in its `catalog_version`
    (e.g. "2026-06"), else the newest SKU effective-start date. This is inferred,
    not observed — the "Reconciled" source label says so on the readout."""
    count = catalog_sku_count(db)
    if count == 0:
        return None

    version = db.execute(
        select(models.MicrosoftSku.catalog_version)
        .where(models.MicrosoftSku.catalog_version != "")
        .limit(1)
    ).scalar() or ""

    newest_effective = db.execute(
        select(func.max(models.MicrosoftSku.effective_start_date))
    ).scalar()

    data_month: Optional[str] = None
    m = _MONTH_RE.search(version)
    if m:
        data_month = f"{m.group(1)}-{m.group(2)}"
    elif newest_effective is not None:
        data_month = newest_effective.strftime("%Y-%m")

    # Anchor the day-rule to a real date the catalog carries: the effective-start
    # date, else the first of the derived data month. Falls back to None only when
    # the catalog reports neither (freshness then relies on the month rule alone).
    anchor: Optional[datetime] = None
    if newest_effective is not None:
        anchor = datetime(
            newest_effective.year, newest_effective.month, newest_effective.day,
            tzinfo=timezone.utc,
        )
    elif data_month:
        year, month = (int(x) for x in data_month.split("-"))
        anchor = datetime(year, month, 1, tzinfo=timezone.utc)

    return anchor, data_month, version


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
    """Record one successful catalog load. `data_month` should be the sheet's
    own reported month (from LastUpdatedDate for CSV, or the API metadata for
    price-sync); it falls back to the current calendar month only when the sheet
    reports no date at all."""
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
    """(fetched_at, data_month, source_label) for the NEWEST pricing signal across
    the price-sync sheet on disk, the CatalogImport record, and — as a floor — the
    loaded catalog itself. A real load (with a true `fetched_at`/`imported_at`)
    outranks the catalog-derived floor when present, because it is newer; the
    floor only wins when no load record exists, guaranteeing a populated catalog
    is never classified as absent."""
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
    derived = derive_catalog_signal(db)
    if derived is not None:
        anchor, data_month, _version = derived
        candidates.append((anchor, data_month, _SOURCE_LABELS["Reconciled"]))
    if not candidates:
        return None, None, None

    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    return max(candidates, key=lambda c: freshness.to_utc(c[0]) or _epoch)


def reconcile_missing_provenance(db: Session) -> Optional[models.CatalogImport]:
    """One-time migration: if the catalog holds SKUs but no CatalogImport row
    exists (a catalog loaded before provenance recording was introduced),
    materialize one from the catalog's own state so the provenance history and
    the loaded catalog agree. Idempotent — a no-op once any row exists. Returns
    the created row, or None when nothing needed reconciling."""
    if latest(db) is not None:
        return None
    derived = derive_catalog_signal(db)
    if derived is None:  # empty catalog — nothing to reconcile
        return None
    anchor, data_month, version = derived
    row = models.CatalogImport(
        source="Reconciled",
        sku_count=catalog_sku_count(db),
        catalog_version=version or "",
        data_month=data_month or "",
        file_name="",
        sha256="",
    )
    if anchor is not None:
        row.imported_at = anchor
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def pricing_freshness(cfg: ps_config.PriceSyncConfig, db: Session):
    """(Freshness, source_label) for the newest successful load across sources."""
    fetched_at, data_month, label = _best_signal(cfg, db)
    fr = freshness.classify(
        fetched_at, data_month,
        aging_days=cfg.aging_days, stale_days=cfg.stale_days,
        use_month_rule=cfg.use_month_rule,
    )
    return fr, label
