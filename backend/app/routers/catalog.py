"""Microsoft SKU catalog: listing + price-sheet import (PRD Section 8)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
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
    best-effort catalog list price so the UI can auto-fill target/add-on prices.
    Each add-on also carries its eligibility set — the base bundles it may layer
    onto (empty `eligible_base_ids` + `alacarte=true` means any base)."""
    rows = bundles_service.list_bundles(db)
    by_id = {b.id: b for b in rows}
    elig_map = bundles_service.eligibility_map(db)
    out = []
    for b in rows:
        eligible = sorted(elig_map.get(b.id, set()))
        out.append({
            "id": b.id, "key": b.key, "name": b.name, "kind": b.kind,
            "base_bundle_id": b.base_bundle_id,
            "base_name": by_id[b.base_bundle_id].name if b.base_bundle_id in by_id else None,
            "list_price_annual": _bundle_list_price(db, b.name),
            "sort_order": b.sort_order,
            # Eligibility (only meaningful for add-ons).
            "eligible_base_ids": eligible,
            "eligible_base_names": [by_id[i].name for i in eligible if i in by_id],
            "alacarte": b.kind == "addon" and not eligible,
        })
    return out


def _validate_bundle_shape(db: Session, kind: str, base_bundle_id: str | None, self_id: str | None = None):
    """A base bundle has no parent; an add-on must name an existing base bundle
    (not itself, not another add-on)."""
    if kind not in ("bundle", "addon"):
        raise HTTPException(422, "kind must be 'bundle' or 'addon'.")
    if kind == "bundle":
        if base_bundle_id is not None:
            raise HTTPException(422, "A base bundle cannot have a base_bundle_id.")
        return
    # add-on
    if not base_bundle_id:
        raise HTTPException(422, "An add-on must have a base_bundle_id.")
    if base_bundle_id == self_id:
        raise HTTPException(422, "An add-on cannot base onto itself.")
    base = db.get(models.Bundle, base_bundle_id)
    if base is None:
        raise HTTPException(422, "Unknown base bundle.")
    if base.kind != "bundle":
        raise HTTPException(422, "base_bundle_id must point at a base bundle, not an add-on.")


@router.post("/bundles", status_code=201)
def create_bundle(payload: schemas.BundleIn, db: Session = Depends(get_db)):
    """Add an operator-defined staple/add-on to the bundle library."""
    if not payload.key.strip() or not payload.name.strip():
        raise HTTPException(422, "key and name are required.")
    if db.execute(select(models.Bundle).where(models.Bundle.key == payload.key)).scalar():
        raise HTTPException(409, f"A bundle with key '{payload.key}' already exists.")
    _validate_bundle_shape(db, payload.kind, payload.base_bundle_id)
    row = models.Bundle(**payload.model_dump())
    db.add(row)
    db.flush()
    # An add-on's primary base is always a member of its eligibility set, so a
    # newly-created add-on is enforced (restricted to its base) from the start.
    if row.kind == "addon" and row.base_bundle_id:
        db.add(models.AddonEligibility(addon_bundle_id=row.id, base_bundle_id=row.base_bundle_id))
    db.commit()
    return {"id": row.id, "key": row.key, "name": row.name, "kind": row.kind,
            "base_bundle_id": row.base_bundle_id, "sort_order": row.sort_order}


@router.patch("/bundles/{bundle_id}")
def update_bundle(bundle_id: str, payload: schemas.BundleUpdate, db: Session = Depends(get_db)):
    """Edit a bundle's name/kind/base/sort (the immutable `key` is the stable id)."""
    row = db.get(models.Bundle, bundle_id)
    if row is None:
        raise HTTPException(404, "Bundle not found")
    data = payload.model_dump(exclude_unset=True)
    kind = data.get("kind", row.kind)
    base = data.get("base_bundle_id", row.base_bundle_id)
    _validate_bundle_shape(db, kind, base, self_id=row.id)
    for k, v in data.items():
        setattr(row, k, v)
    db.flush()
    # Keep the primary base in the eligibility set; if a bundle was switched to an
    # add-on (or its base changed), ensure the new base is eligible. Eligibility
    # rows for a bundle-kind are meaningless, so clear them when demoted to base.
    if row.kind == "addon" and row.base_bundle_id:
        if row.base_bundle_id not in bundles_service.eligible_base_ids(db, row.id):
            db.add(models.AddonEligibility(
                addon_bundle_id=row.id, base_bundle_id=row.base_bundle_id))
    elif row.kind == "bundle":
        bundles_service.set_addon_eligibility(db, row.id, [])
    db.commit()
    return {"id": row.id, "key": row.key, "name": row.name, "kind": row.kind,
            "base_bundle_id": row.base_bundle_id, "sort_order": row.sort_order}


@router.put("/bundles/{addon_id}/eligibility")
def set_bundle_eligibility(
    addon_id: str, payload: schemas.AddonEligibilityIn, db: Session = Depends(get_db)
):
    """Replace an add-on's eligible-base set — the composition "logic layer" (which
    bases it may layer onto). Empty list = à-la-carte (any base). Only valid on an
    add-on; every id must name an existing base bundle."""
    row = db.get(models.Bundle, addon_id)
    if row is None:
        raise HTTPException(404, "Bundle not found")
    if row.kind != "addon":
        raise HTTPException(422, "Eligibility applies to add-ons only.")
    for bid in payload.base_bundle_ids:
        base = db.get(models.Bundle, bid)
        if base is None:
            raise HTTPException(422, f"Unknown base bundle '{bid}'.")
        if base.kind != "bundle":
            raise HTTPException(422, "An eligible base must be a base bundle, not an add-on.")
        if bid == addon_id:
            raise HTTPException(422, "An add-on cannot be eligible for itself.")
    ids = bundles_service.set_addon_eligibility(db, addon_id, payload.base_bundle_ids)
    return {"addon_bundle_id": addon_id, "eligible_base_ids": ids, "alacarte": not ids}


@router.delete("/bundles/{bundle_id}")
def delete_bundle(bundle_id: str, db: Session = Depends(get_db)):
    """Delete a bundle — blocked (409) while anything still references it, so a
    delete can never orphan the SKU → Bundle → Outcomes spine. Note: a deleted
    *seed* staple is re-created on next startup; delete is for operator-added ones."""
    row = db.get(models.Bundle, bundle_id)
    if row is None:
        raise HTTPException(404, "Bundle not found")

    def _count(model, *conds):
        return len(db.execute(select(model.id).where(*conds)).scalars().all())

    refs = []
    skus = _count(models.MicrosoftSku,
                  (models.MicrosoftSku.bundle_id == bundle_id)
                  | (models.MicrosoftSku.suggested_bundle_id == bundle_id))
    if skus:
        refs.append(f"{skus} catalog SKU mapping(s)")
    cov = _count(models.CoverageMapEntry, models.CoverageMapEntry.bundle_id == bundle_id)
    if cov:
        refs.append(f"{cov} coverage entr(y/ies)")
    addons = _count(models.ScenarioAddon, models.ScenarioAddon.bundle_id == bundle_id)
    if addons:
        refs.append(f"{addons} scenario add-on(s)")
    children = _count(models.Bundle, models.Bundle.base_bundle_id == bundle_id)
    if children:
        refs.append(f"{children} add-on(s) based on it")
    # A base that's in some add-on's eligibility set (beyond the primary base link).
    elig = _count(models.AddonEligibility, models.AddonEligibility.base_bundle_id == bundle_id)
    if elig:
        refs.append(f"{elig} add-on eligibility link(s)")
    if refs:
        raise HTTPException(409, "Cannot delete: still referenced by " + ", ".join(refs)
                            + ". Clear those references first.")
    # An add-on owns its eligibility rows (where it is the addon) — remove them so
    # the delete doesn't leave orphans.
    for e in db.execute(
        select(models.AddonEligibility).where(
            models.AddonEligibility.addon_bundle_id == bundle_id
        )
    ).scalars().all():
        db.delete(e)
    db.delete(row)
    db.commit()
    return {"deleted": bundle_id}


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
