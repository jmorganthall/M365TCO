"""Operator/admin: secret store, global defaults/outcomes, OpenRouter AI assist.

Secrets are write-only over the API — values are never read back, only their
presence is reported (PRD 4.4 / 12: no secrets in config, encrypted at rest).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from sqlalchemy import func

from ..services import ai, ai_prompts, bundles as bundles_service, defaults, secrets, seeds

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Keys the operator may set, with friendly labels.
_ALLOWED_SECRETS = {
    secrets.OPENROUTER_API_KEY: "OpenRouter API key",
}


@router.get("/secrets")
def list_secret_status():
    store = secrets.get_store()
    present = set(store.keys()) if store.enabled else set()
    return {
        "store_enabled": store.enabled,
        "secrets": [
            {"key": k, "label": label, "set": k in present}
            for k, label in _ALLOWED_SECRETS.items()
        ],
    }


@router.put("/secrets")
def set_secret(payload: schemas.SecretIn):
    store = secrets.get_store()
    if not store.enabled:
        raise HTTPException(
            400, "Secret store disabled: configure TCO_MASTER_SECRET to enable it."
        )
    if payload.key not in _ALLOWED_SECRETS:
        raise HTTPException(422, f"Unknown secret key: {payload.key}")
    store.set(payload.key, payload.value)
    return {"ok": True, "key": payload.key, "set": True}


@router.delete("/secrets/{key}")
def delete_secret(key: str):
    store = secrets.get_store()
    if not store.enabled:
        raise HTTPException(400, "Secret store disabled.")
    if key not in _ALLOWED_SECRETS:
        raise HTTPException(422, f"Unknown secret key: {key}")
    store.delete(key)
    return {"ok": True, "key": key, "set": False}


@router.get("/defaults", response_model=schemas.GlobalDefaultsOut)
def get_global_defaults(db: Session = Depends(get_db)):
    return defaults.get_defaults(db)


@router.put("/defaults", response_model=schemas.GlobalDefaultsOut)
def update_global_defaults(
    payload: schemas.GlobalDefaultsUpdate, db: Session = Depends(get_db)
):
    row = defaults.get_defaults(db)
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


def _resolved_model(db: Session) -> str:
    from ..config import settings
    return defaults.get_defaults(db).openrouter_model or settings.openrouter_model


# ---- Global default outcome library (template for new engagements) ----
@router.get("/default-outcomes", response_model=list[schemas.DefaultOutcomeOut])
def list_default_outcomes(db: Session = Depends(get_db)):
    seeds.seed_default_outcomes(db)
    return db.execute(
        select(models.DefaultOutcome).order_by(models.DefaultOutcome.sort_order)
    ).scalars().all()


@router.post("/default-outcomes", response_model=schemas.DefaultOutcomeOut, status_code=201)
def create_default_outcome(payload: schemas.DefaultOutcomeIn, db: Session = Depends(get_db)):
    seeds.seed_default_outcomes(db)
    # Generate a stable, unique key from the name.
    base = seeds.slugify(payload.name)
    key, n = base, 2
    while db.execute(
        select(models.DefaultOutcome.id).where(models.DefaultOutcome.key == key)
    ).first():
        key = f"{base}-{n}"
        n += 1
    if payload.sort_order is None:
        max_order = db.execute(select(func.max(models.DefaultOutcome.sort_order))).scalar()
        sort_order = (max_order or 0) + 1
    else:
        sort_order = payload.sort_order
    row = models.DefaultOutcome(
        key=key, name=payload.name, description=payload.description, sort_order=sort_order
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/default-outcomes/{outcome_id}", response_model=schemas.DefaultOutcomeOut)
def update_default_outcome(
    outcome_id: str, payload: schemas.DefaultOutcomeIn, db: Session = Depends(get_db)
):
    row = db.get(models.DefaultOutcome, outcome_id)
    if row is None:
        raise HTTPException(404, "Default outcome not found")
    # key is immutable (coverage references it); only name/description/order edit.
    row.name = payload.name
    row.description = payload.description
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    db.commit()
    db.refresh(row)
    return row


@router.delete("/default-outcomes/{outcome_id}", status_code=204)
def delete_default_outcome(outcome_id: str, db: Session = Depends(get_db)):
    row = db.get(models.DefaultOutcome, outcome_id)
    if row is None:
        raise HTTPException(404, "Default outcome not found")
    db.delete(row)
    db.commit()


# ---- Global default Microsoft bundle coverage library (seeds new engagements) ----
@router.get("/default-coverage", response_model=list[schemas.DefaultCoverageOut])
def list_default_coverage(db: Session = Depends(get_db)):
    seeds.seed_default_coverage(db)
    return db.execute(
        select(models.DefaultBundleCoverage).order_by(
            models.DefaultBundleCoverage.bundle_key, models.DefaultBundleCoverage.outcome_key
        )
    ).scalars().all()


def _validate_default_coverage_keys(db: Session, bundle_key: str, outcome_key: str, coverage: str):
    if coverage not in models.COVERAGE:
        raise HTTPException(422, "coverage must be 'Full' or 'Partial'.")
    if not any(b.key == bundle_key for b in bundles_service.list_bundles(db)):
        raise HTTPException(422, f"Unknown bundle key '{bundle_key}'.")
    if db.execute(select(models.DefaultOutcome.id).where(
            models.DefaultOutcome.key == outcome_key)).first() is None:
        raise HTTPException(422, f"Unknown outcome key '{outcome_key}'.")


@router.post("/default-coverage", response_model=schemas.DefaultCoverageOut, status_code=201)
def create_default_coverage(payload: schemas.DefaultCoverageIn, db: Session = Depends(get_db)):
    """Add a bundle → outcome entry to the global default coverage library. Affects
    NEW engagements only; existing engagements keep their own copy."""
    seeds.seed_default_coverage(db)
    _validate_default_coverage_keys(db, payload.bundle_key, payload.outcome_key, payload.coverage)
    if db.execute(select(models.DefaultBundleCoverage.id).where(
            models.DefaultBundleCoverage.bundle_key == payload.bundle_key,
            models.DefaultBundleCoverage.outcome_key == payload.outcome_key)).first():
        raise HTTPException(409, "That bundle already covers that outcome in the default library.")
    row = models.DefaultBundleCoverage(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/default-coverage/{entry_id}", response_model=schemas.DefaultCoverageOut)
def update_default_coverage(entry_id: str, payload: schemas.DefaultCoverageUpdate,
                            db: Session = Depends(get_db)):
    row = db.get(models.DefaultBundleCoverage, entry_id)
    if row is None:
        raise HTTPException(404, "Default coverage entry not found")
    if payload.coverage not in models.COVERAGE:
        raise HTTPException(422, "coverage must be 'Full' or 'Partial'.")
    row.coverage = payload.coverage
    db.commit()
    db.refresh(row)
    return row


@router.delete("/default-coverage/{entry_id}", status_code=204)
def delete_default_coverage(entry_id: str, db: Session = Depends(get_db)):
    row = db.get(models.DefaultBundleCoverage, entry_id)
    if row is None:
        raise HTTPException(404, "Default coverage entry not found")
    db.delete(row)
    db.commit()


@router.get("/ai/status")
def ai_status(db: Session = Depends(get_db)):
    return {"enabled": ai.is_enabled(), "model": _resolved_model(db)}


@router.get("/ai/models")
def ai_models():
    """Live OpenRouter model list for the Settings picker."""
    if not ai.is_enabled():
        raise HTTPException(400, "AI assist disabled: set the OpenRouter API key.")
    try:
        return {"models": ai.list_models()}
    except Exception as exc:  # network/model errors surface cleanly
        raise HTTPException(502, f"Could not list OpenRouter models: {exc}")


def _outcome_dicts(db: Session, engagement_id: str) -> list[dict]:
    outcomes = db.execute(
        select(models.Outcome).where(models.Outcome.engagement_id == engagement_id)
    ).scalars().all()
    return [{"id": o.id, "name": o.name, "description": o.description} for o in outcomes]


def _suggest_and_persist(db, engagement_id, tp, outcome_dicts, instructions, model) -> list[dict]:
    """Ask the model for tp's coverage and write unratified rows, skipping any
    product+outcome that already has an entry. Flushes but does not commit — the
    caller commits so a bulk run is one transaction. Returns the created rows."""
    suggestions = ai.suggest_coverage(
        tp.name, outcome_dicts, instructions=instructions, model=model
    )
    created = []
    for s in suggestions:
        existing = db.execute(
            select(models.CoverageMapEntry).where(
                models.CoverageMapEntry.engagement_id == engagement_id,
                models.CoverageMapEntry.product_kind == "ThirdParty",
                models.CoverageMapEntry.third_party_product_id == tp.id,
                models.CoverageMapEntry.outcome_id == s["outcome_id"],
            )
        ).scalar_one_or_none()
        if existing:
            continue
        row = models.CoverageMapEntry(
            engagement_id=engagement_id, outcome_id=s["outcome_id"],
            product_kind="ThirdParty", third_party_product_id=tp.id,
            coverage=s["coverage"], ai_suggested=True, ratified=False,
        )
        db.add(row)
        db.flush()
        created.append({"id": row.id, "outcome_id": row.outcome_id,
                        "coverage": row.coverage, "rationale": s.get("rationale", "")})
    return created


@router.post("/engagements/{engagement_id}/ai/suggest-coverage")
def suggest_coverage(
    engagement_id: str, payload: schemas.CoverageSuggestRequest, db: Session = Depends(get_db)
):
    """AI coverage suggestion (PRD Section 9). Writes unratified ai_suggested
    CoverageMapEntry rows; they do NOT feed the math until a human ratifies."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise HTTPException(404, "Engagement not found")
    tp = db.get(models.ThirdPartyProduct, payload.third_party_product_id)
    if tp is None or tp.engagement_id != engagement_id:
        raise HTTPException(404, "Third-party product not found")
    if not ai.is_enabled():
        raise HTTPException(400, "AI assist disabled: set the OpenRouter API key.")
    try:
        created = _suggest_and_persist(
            db, engagement_id, tp, _outcome_dicts(db, engagement_id),
            ai_prompts.get_instructions(db, "coverage_suggest"), _resolved_model(db),
        )
    except Exception as exc:  # network/model errors surface cleanly
        raise HTTPException(502, f"AI suggestion failed: {exc}")
    db.commit()
    return {"suggestions": created}


@router.post("/engagements/{engagement_id}/ai/suggest-coverage-all")
def suggest_coverage_all(engagement_id: str, db: Session = Depends(get_db)):
    """Bulk coverage suggestion: run it for every third-party product that has NO
    coverage mapped yet. Products that already have any coverage entry are left
    untouched. All output is unratified, same as the per-product button."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise HTTPException(404, "Engagement not found")
    if not ai.is_enabled():
        raise HTTPException(400, "AI assist disabled: set the OpenRouter API key.")

    products = db.execute(
        select(models.ThirdPartyProduct).where(
            models.ThirdPartyProduct.engagement_id == engagement_id
        )
    ).scalars().all()
    mapped_ids = set(db.execute(
        select(models.CoverageMapEntry.third_party_product_id).where(
            models.CoverageMapEntry.engagement_id == engagement_id,
            models.CoverageMapEntry.product_kind == "ThirdParty",
        )
    ).scalars().all())
    unmapped = [tp for tp in products if tp.id not in mapped_ids]

    outcome_dicts = _outcome_dicts(db, engagement_id)
    instructions = ai_prompts.get_instructions(db, "coverage_suggest")
    model = _resolved_model(db)
    created_total, results, errors = 0, [], []
    for tp in unmapped:
        try:
            created = _suggest_and_persist(db, engagement_id, tp, outcome_dicts, instructions, model)
            created_total += len(created)
            # created == 0 means the model found no correlation for this product.
            results.append({"name": tp.name, "created": len(created)})
        except Exception as exc:  # one bad product must not abort the rest
            errors.append(f"{tp.name}: {exc}")
    db.commit()
    return {
        "products_processed": len(results),
        "suggestions_created": created_total,
        "skipped_mapped": len(products) - len(unmapped),
        "results": results,
        "errors": errors,
    }


@router.post("/engagements/{engagement_id}/ai/parse-third-party")
def parse_third_party(
    engagement_id: str, payload: schemas.TextParseRequest, db: Session = Depends(get_db)
):
    """Parse a block of customer-provided text into third-party product rows.
    Advisory only — returns rows for the operator to review and edit; nothing is
    persisted here (the UI creates products via the normal endpoint)."""
    if db.get(models.Engagement, engagement_id) is None:
        raise HTTPException(404, "Engagement not found")
    if not ai.is_enabled():
        raise HTTPException(400, "AI assist disabled: set the OpenRouter API key.")
    if not payload.raw_text.strip():
        raise HTTPException(422, "No text to parse.")
    try:
        rows = ai.parse_third_party(
            payload.raw_text,
            instructions=ai_prompts.get_instructions(db, "third_party_parse"),
            model=_resolved_model(db),
        )
    except Exception as exc:  # network/model errors surface cleanly
        raise HTTPException(502, f"AI parse failed: {exc}")
    return {"rows": rows}


@router.post("/engagements/{engagement_id}/ai/parse-current-licenses")
def parse_current_licenses(
    engagement_id: str, payload: schemas.TextParseRequest, db: Session = Depends(get_db)
):
    """Parse a block of customer-provided text into existing-license rows.
    Advisory only — returns rows (with a normalized annual per-seat price) for the
    operator to review and edit; nothing is persisted here."""
    if db.get(models.Engagement, engagement_id) is None:
        raise HTTPException(404, "Engagement not found")
    if not ai.is_enabled():
        raise HTTPException(400, "AI assist disabled: set the OpenRouter API key.")
    if not payload.raw_text.strip():
        raise HTTPException(422, "No text to parse.")
    try:
        rows = ai.parse_current_licenses(
            payload.raw_text,
            instructions=ai_prompts.get_instructions(db, "current_license_parse"),
            model=_resolved_model(db),
        )
    except Exception as exc:  # network/model errors surface cleanly
        raise HTTPException(502, f"AI parse failed: {exc}")
    return {"rows": rows}


# ---- Editable AI instruction templates (AiPrompt) ----
def _prompt_payload(row: models.AiPrompt) -> dict:
    return {
        "key": row.key, "label": row.label, "description": row.description,
        "instructions": row.instructions,
        "is_default": row.instructions == ai_prompts.default_instructions(row.key),
    }


@router.get("/ai/prompts")
def list_ai_prompts(db: Session = Depends(get_db)):
    """Every AI function's editable system instructions, so the operator can see
    exactly what is consistently being sent and tune it."""
    return {"prompts": [_prompt_payload(p) for p in ai_prompts.list_prompts(db)]}


@router.patch("/ai/prompts/{key}")
def update_ai_prompt(key: str, payload: schemas.AiPromptUpdate, db: Session = Depends(get_db)):
    ai_prompts.seed_defaults(db)
    row = ai_prompts.update_instructions(db, key, payload.instructions)
    if row is None:
        raise HTTPException(404, "AI prompt not found")
    return _prompt_payload(row)


@router.post("/ai/prompts/{key}/reset")
def reset_ai_prompt(key: str, db: Session = Depends(get_db)):
    ai_prompts.seed_defaults(db)
    row = ai_prompts.reset_instructions(db, key)
    if row is None:
        raise HTTPException(404, "AI prompt not found")
    return _prompt_payload(row)
