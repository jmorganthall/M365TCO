"""Operator/admin: secret store, OpenRouter AI assist, Partner Center consent.

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

from ..services import ai, defaults, secrets, seeds

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Keys the operator may set, with friendly labels.
_ALLOWED_SECRETS = {
    secrets.OPENROUTER_API_KEY: "OpenRouter API key",
    secrets.PARTNER_CENTER_REFRESH_TOKEN: "Partner Center refresh token",
    secrets.PARTNER_CENTER_APP_ID: "Partner Center application id",
    secrets.PARTNER_CENTER_APP_SECRET: "Partner Center client secret",
    secrets.PARTNER_CENTER_TENANT_ID: "Partner Center tenant id",
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

    outcomes = db.execute(
        select(models.Outcome).where(models.Outcome.engagement_id == engagement_id)
    ).scalars().all()
    outcome_dicts = [{"id": o.id, "name": o.name, "description": o.description} for o in outcomes]

    try:
        suggestions = ai.suggest_coverage(
            tp.name, outcome_dicts, model=_resolved_model(db)
        )
    except Exception as exc:  # network/model errors surface cleanly
        raise HTTPException(502, f"AI suggestion failed: {exc}")

    created = []
    for s in suggestions:
        # Skip if an entry already exists for this product+outcome.
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
    db.commit()
    return {"suggestions": created}
