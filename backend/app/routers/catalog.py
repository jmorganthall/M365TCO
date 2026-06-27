"""Microsoft SKU catalog: listing + price-sheet import (PRD Section 8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..services import partner_center, pricesheet

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
        }
        for r in rows
    ]


@router.get("/version")
def catalog_version(db: Session = Depends(get_db)):
    version = db.execute(select(models.MicrosoftSku.catalog_version).limit(1)).scalar()
    count = db.execute(select(models.MicrosoftSku.id)).scalars().all()
    return {"catalog_version": version or "", "sku_count": len(count),
            "partner_center_configured": partner_center.is_configured()}


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
        return pricesheet.import_price_sheet(db, text, version)
    except pricesheet.PriceSheetError as exc:
        raise HTTPException(422, str(exc))


@router.post("/refresh-partner-center")
def refresh_partner_center(
    market: str = "US", timeline: str = "current", month: str | None = None,
    db: Session = Depends(get_db),
):
    """Phase-two path (8.2): pull the price sheet from the Partner Center API and
    feed the same parser."""
    if not partner_center.is_configured():
        raise HTTPException(
            400, "Partner Center not configured. Complete operator consent first."
        )
    try:
        text = partner_center.fetch_price_sheet(market=market, timeline=timeline, month=month)
    except partner_center.PartnerCenterNotConfigured as exc:
        raise HTTPException(400, str(exc))
    if not text:
        return {"status": "no-change", "detail": "No price sheet returned (404 on future = no upcoming change)."}
    version = f"pc-{timeline}-{month or ''}".strip("-")
    return pricesheet.import_price_sheet(db, text, version or "partner-center")
