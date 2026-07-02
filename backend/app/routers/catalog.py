"""Microsoft SKU catalog: listing + price-sheet import (PRD Section 8)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..db import get_db
from ..services import ai, ai_prompts, catalog_provenance, defaults, pricesheet
from ..services import bundles as bundles_service


def _resolved_model(db: Session) -> str:
    """Operator's chosen model (defaults table) or the configured fallback."""
    return defaults.get_defaults(db).openrouter_model or settings.openrouter_model

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/skus")
def list_skus(
    q: str | None = None,
    term: str | None = None,
    unmapped: bool = False,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    stmt = select(models.MicrosoftSku)
    if term:
        stmt = stmt.where(models.MicrosoftSku.term_duration == term)
    if unmapped:  # not yet accepted onto a staple bundle (the mapper's work-list)
        stmt = stmt.where(models.MicrosoftSku.bundle_id.is_(None))
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
            "suggested_bundle_id": r.suggested_bundle_id,
            "bundle_suggestion_reason": r.bundle_suggestion_reason,
        }
        for r in rows
    ]


def _bundle_list_price(db: Session, name: str) -> float:
    """Best-effort catalog list price for a bundle name (annual, prefer P1Y)."""
    like = f"%{name}%"
    row = db.execute(
        select(models.MicrosoftSku)
        .where(
            (models.MicrosoftSku.sku_title.ilike(like))
            | (models.MicrosoftSku.product_title.ilike(like))
        )
        .order_by(models.MicrosoftSku.term_duration.desc())
    ).scalars().first()
    return float(row.annual_unit_price) if row else 0.0


@router.get("/bundles")
def list_bundles(db: Session = Depends(get_db)):
    """The staple bundle library (the SKU → Bundle → Outcomes spine), with a
    best-effort catalog list price so the UI can auto-fill target/add-on prices."""
    rows = bundles_service.list_bundles(db)
    by_id = {b.id: b for b in rows}
    return [
        {
            "id": b.id, "key": b.key, "name": b.name, "kind": b.kind,
            "base_bundle_id": b.base_bundle_id,
            "base_name": by_id[b.base_bundle_id].name if b.base_bundle_id in by_id else None,
            "list_price_annual": _bundle_list_price(db, b.name),
            "sort_order": b.sort_order,
        }
        for b in rows
    ]


@router.patch("/skus/{sku_id}/bundle")
def set_sku_bundle(sku_id: str, bundle_id: str | None = Body(None, embed=True),
                   db: Session = Depends(get_db)):
    """Accept/set a SKU's staple bundle (or clear with null). This is the ratified
    mapping; accepting resolves and clears any pending AI suggestion."""
    row = db.get(models.MicrosoftSku, sku_id)
    if row is None:
        raise HTTPException(404, "SKU not found")
    if bundle_id is not None and db.get(models.Bundle, bundle_id) is None:
        raise HTTPException(422, "Unknown bundle.")
    row.bundle_id = bundle_id
    row.suggested_bundle_id = None  # a decision was made; suggestion is consumed
    row.bundle_suggestion_reason = ""
    db.commit()
    return {"id": row.id, "bundle_id": row.bundle_id}


@router.post("/skus/{sku_id}/reject-suggestion")
def reject_sku_bundle_suggestion(sku_id: str, db: Session = Depends(get_db)):
    """Dismiss the AI's bundle suggestion for a SKU without mapping it — leaves the
    SKU unmapped so it resurfaces for manual mapping or a later AI pass."""
    row = db.get(models.MicrosoftSku, sku_id)
    if row is None:
        raise HTTPException(404, "SKU not found")
    row.suggested_bundle_id = None
    row.bundle_suggestion_reason = ""
    db.commit()
    return {"id": row.id, "bundle_id": row.bundle_id}


@router.post("/skus/suggest-bundles")
def suggest_sku_bundles(limit: int = 150, db: Session = Depends(get_db)):
    """Import-time AI mapper: classify catalog SKUs that aren't mapped to a staple
    bundle yet onto one. Writes an UNRATIFIED suggested_bundle_id + reason; the
    operator accepts (into bundle_id) or rejects in Settings → Staple bundles.
    Nothing here enters the SKU → Bundle → Outcomes spine until accepted."""
    if not ai.is_enabled():
        raise HTTPException(400, "AI assist disabled: set the OpenRouter API key.")
    unmapped = db.execute(
        select(models.MicrosoftSku).where(models.MicrosoftSku.bundle_id.is_(None))
    ).scalars().all()
    total_unmapped = len(unmapped)
    batch = unmapped[:limit]  # cap the prompt size; report the remainder
    if not batch:
        return {"classified": 0, "suggested": 0, "unmapped_remaining": 0, "capped": False}

    bundle_rows = bundles_service.list_bundles(db)
    key_to_id = {b.key: b.id for b in bundle_rows}
    sku_dicts = [
        {"id": s.id, "product_title": s.product_title, "sku_title": s.sku_title}
        for s in batch
    ]
    bundle_dicts = [{"key": b.key, "name": b.name, "kind": b.kind} for b in bundle_rows]
    try:
        mappings = ai.suggest_bundle_mappings(
            sku_dicts, bundle_dicts,
            instructions=ai_prompts.get_instructions(db, "sku_bundle_map"),
            model=_resolved_model(db),
        )
    except Exception as exc:  # network/model errors surface cleanly
        raise HTTPException(502, f"AI bundle mapping failed: {exc}")

    by_id = {s.id: s for s in batch}
    suggested = 0
    for m in mappings:
        row = by_id.get(m["sku_id"])
        if row is None:
            continue
        bid = key_to_id.get(m["bundle_key"]) if m["bundle_key"] else None
        row.suggested_bundle_id = bid
        row.bundle_suggestion_reason = m["reason"]
        if bid:
            suggested += 1
    db.commit()
    return {
        "classified": len(batch),
        "suggested": suggested,
        "unmapped_remaining": max(total_unmapped - len(batch), 0),
        "capped": total_unmapped > len(batch),
    }


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
