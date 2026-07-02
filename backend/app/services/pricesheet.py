"""Microsoft price-sheet ingest (PRD Section 8).

Parses the new-commerce license-based price list (CSV). The parser maps by
column NAME, not position, because Microsoft adds columns over time
(PreviousValues, ChangeIndicator, LastUpdatedDate are recent additions).
Tolerates their presence or absence.

The same parser serves the day-one CSV path (8.1) and the phase-two Partner
Center pricing API stream (8.2) — the API client just decompresses to CSV text
and hands it here.

Annualization (8.3) happens here so the engine never sees mixed periods.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

# Canonical column names we read. Lookup is case-insensitive and whitespace
# tolerant so header drift across price-sheet revisions doesn't break us.
_COLUMNS = {
    "product_title": "ProductTitle",
    "product_id": "ProductId",
    "sku_id": "SkuId",
    "sku_title": "SkuTitle",
    "term_duration": "TermDuration",
    "billing_plan": "BillingPlan",
    "market": "Market",
    "currency": "Currency",
    "unit_price": "UnitPrice",
    "erp_price": "ERP Price",
    "segment": "Segment",
    "effective_start": "EffectiveStartDate",
    "effective_end": "EffectiveEndDate",
}

_TERM_MONTHS = {"P1M": 1, "P1Y": 12, "P3Y": 36}


class PriceSheetError(ValueError):
    pass


def _norm(name: str) -> str:
    return name.strip().lower().replace(" ", "")


def _build_header_index(header: list[str]) -> dict[str, int]:
    idx = {_norm(h): i for i, h in enumerate(header)}
    resolved: dict[str, int] = {}
    for logical, source in _COLUMNS.items():
        pos = idx.get(_norm(source))
        if pos is not None:
            resolved[logical] = pos
    required = {"product_id", "sku_id", "term_duration", "unit_price"}
    missing = required - set(resolved)
    if missing:
        raise PriceSheetError(
            f"Price sheet missing required columns: {sorted(missing)}. "
            f"Got header: {header}"
        )
    return resolved


def _get(row: list[str], idx: dict[str, int], key: str, default: str = "") -> str:
    pos = idx.get(key)
    if pos is None or pos >= len(row):
        return default
    return (row[pos] or default).strip()


def _to_decimal(value: str) -> Decimal:
    if value in ("", None):
        return Decimal("0")
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _to_date(value: str):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "")).date()
    except ValueError:
        return None


def _annualize(monthly: Decimal, term: str) -> tuple[Decimal, Decimal]:
    """Return (monthly, annual) per 8.3.

    The sheet UnitPrice/ERP is the price for the term in TermDuration. For P1Y
    the listed figure is the annual per-seat price; for P1M multiply by 12. We
    normalize the listed price to a per-month figure and a per-year figure.
    """
    months = _TERM_MONTHS.get(term.upper(), 1)
    # Listed price is for the whole term. Per-month = listed / months.
    per_month = (monthly / Decimal(months)).quantize(Decimal("0.0001"))
    per_year = (per_month * Decimal(12)).quantize(Decimal("0.0001"))
    return per_month, per_year


def _detect_delimiter(header_line: str) -> str:
    """Pick the delimiter that splits the header into the most fields. Handles
    comma, tab, and semicolon exports (Partner Center / Excel round-trips)."""
    best, best_count = ",", 0
    for candidate in (",", "\t", ";", "|"):
        count = len(header_line.split(candidate))
        if count > best_count:
            best, best_count = candidate, count
    return best


def parse_rows(text: str) -> Iterable[dict]:
    """Yield normalized SKU dicts from price-sheet text (Commercial only).

    Delimiter is auto-detected from the header line so comma-, tab-, or
    semicolon-delimited exports all work.
    """
    first_line = text.split("\n", 1)[0]
    delimiter = _detect_delimiter(first_line)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise PriceSheetError("Empty price sheet")
    idx = _build_header_index(header)

    for raw in reader:
        if not raw or not any(cell.strip() for cell in raw):
            continue
        segment = _get(raw, idx, "segment", "Commercial")
        if segment and segment.lower() != "commercial":
            continue  # v1 filters to Commercial (8.1)

        term = _get(raw, idx, "term_duration", "P1Y") or "P1Y"
        unit_price = _to_decimal(_get(raw, idx, "unit_price"))
        erp_price = _to_decimal(_get(raw, idx, "erp_price"))

        unit_month, unit_year = _annualize(unit_price, term)
        erp_month, erp_year = _annualize(erp_price, term)

        yield {
            "product_id": _get(raw, idx, "product_id"),
            "sku_id": _get(raw, idx, "sku_id"),
            "product_title": _get(raw, idx, "product_title"),
            "sku_title": _get(raw, idx, "sku_title"),
            "term_duration": term,
            "billing_plan": _get(raw, idx, "billing_plan", "Annual") or "Annual",
            "segment": segment or "Commercial",
            "unit_price_monthly": unit_month,
            "erp_price_monthly": erp_month,
            "annual_unit_price": unit_year,
            "annual_erp_price": erp_year,
            "effective_start_date": _to_date(_get(raw, idx, "effective_start")),
            "effective_end_date": _to_date(_get(raw, idx, "effective_end")),
            "market": _get(raw, idx, "market", "US") or "US",
            "currency": _get(raw, idx, "currency", "USD") or "USD",
        }


def import_price_sheet(db: Session, text: str, catalog_version: str) -> dict:
    """Upsert SKU rows by natural key (8.1). Keeps the latest active row per
    product+sku+term+billing+market by EffectiveEndDate."""
    inserted = updated = skipped = 0
    for rec in parse_rows(text):
        if not rec["product_id"] or not rec["sku_id"]:
            skipped += 1
            continue

        existing = db.execute(
            select(models.MicrosoftSku).where(
                models.MicrosoftSku.product_id == rec["product_id"],
                models.MicrosoftSku.sku_id == rec["sku_id"],
                models.MicrosoftSku.term_duration == rec["term_duration"],
                models.MicrosoftSku.billing_plan == rec["billing_plan"],
                models.MicrosoftSku.market == rec["market"],
            )
        ).scalar_one_or_none()

        if existing is None:
            db.add(models.MicrosoftSku(catalog_version=catalog_version, **rec))
            inserted += 1
        else:
            # Keep the latest active row by EffectiveEndDate.
            new_end = rec["effective_end_date"]
            old_end = existing.effective_end_date
            if old_end is None or (new_end is not None and new_end >= old_end):
                for k, v in rec.items():
                    setattr(existing, k, v)
                existing.catalog_version = catalog_version
                updated += 1
            else:
                skipped += 1

    db.commit()
    return {
        "catalog_version": catalog_version,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }
