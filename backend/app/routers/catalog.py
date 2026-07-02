"""Microsoft SKU catalog: listing + price-sheet import (PRD Section 8)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..services import bundles as bundles_service
from ..services import catalog_provenance, pricesheet

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/skus")
def list_skus(
    q: str | None = None,
    term: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    stmt = select(models.MicrosoftSku)
    if term:
        stmt = stmt.where(models.MicrosoftSku.term_duration == term)
    rows = db.execute(stmt.limit(limit)).scalars().all()
    if q:
        ql = q.lower()
        rows = [
            r for r in rows
            if ql in (r.product_title or "").lower() or ql in (r.sku_title or "").lower()
        ]
    return [
        {
            "id": r.id, "product_id": r.product_id, "sku_id": r.sku_id,
            "product_title": r.product_title, "sku_title": r.sku_title,
            "term_duration": r.term_duration, "billing_plan": r.billing_plan,
            "annual_unit_price": float(r.annual_unit_price),
            "annual_erp_price": float(r.annual_erp_price),
            "catalog_version": r.catalog_version,
            "bundle_id": r.bundle_id,
        }
        for r in rows
    ]


@router.get("/bundles")
def list_bundles(db: Session = Depends(get_db)):
    """The staple bundle library (the SKU → Bundle → Outcomes spine)."""
    rows = bundles_service.list_bundles(db)
    by_id = {b.id: b for b in rows}
    return [
        {
            "id": b.id, "key": b.key, "name": b.name, "kind": b.kind,
            "base_bundle_id": b.base_bundle_id,
            "base_name": by_id[b.base_bundle_id].name if b.base_bundle_id in by_id else None,
            "sort_order": b.sort_order,
        }
        for b in rows
    ]


@router.patch("/skus/{sku_id}/bundle")
def set_sku_bundle(sku_id: str, bundle_id: str | None = Body(None, embed=True),
                   db: Session = Depends(get_db)):
    """Map a catalog SKU onto a staple bundle (or clear with null) — the SKU →
    Bundle link the import-time AI mapper will fill; editable here."""
    row = db.get(models.MicrosoftSku, sku_id)
    if row is None:
        raise HTTPException(404, "SKU not found")
    if bundle_id is not None and db.get(models.Bundle, bundle_id) is None:
        raise HTTPException(422, "Unknown bundle.")
    row.bundle_id = bundle_id
    db.commit()
    return {"id": row.id, "bundle_id": row.bundle_id}


@router.get("/version")
def catalog_version(db: Session = Depends(get_db)):
    version = db.execute(select(models.MicrosoftSku.catalog_version).limit(1)).scalar()
    count = db.execute(select(models.MicrosoftSku.id)).scalars().all()
    return {"catalog_version": version or "", "sku_count": len(count)}


@router.post("/import-csv")
async def import_csv(
    file: UploadFile = File(...),
    catalog_version: str = Form(""),
    db: Session = Depends(get_db),
):
    """Day-one path (8.1): import the new-commerce license-based price list CSV."""
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    version = catalog_version or (file.filename or "manual-import")
    try:
        result = pricesheet.import_price_sheet(db, text, version)
    except pricesheet.PriceSheetError as exc:
        raise HTTPException(422, str(exc))
    # Record provenance so freshness (Readout badge / staleness banner) counts
    # this successful upload — a CSV operator should never read "not set · stale".
    catalog_provenance.record_import(
        db, source="CsvUpload",
        sku_count=result["inserted"] + result["updated"],
        catalog_version=version, data_month=result.get("data_month"),
    )
    return result

# Automated price-sheet acquisition now lives in the price-sync module
# (app/pricesync/, interactive login, no stored token). This router keeps only
# the manual CSV import path (the permanent fallback) plus catalog listing.
