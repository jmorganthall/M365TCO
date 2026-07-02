"""Engagement-scoped sub-resources: personas, outcomes, current licenses,
third-party products, coverage map, scenarios, and disposition overrides."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..services import compute, inspector

router = APIRouter(prefix="/api/engagements/{engagement_id}", tags=["entities"])


@router.get("/inspect")
def inspect_data(engagement_id: str, db: Session = Depends(get_db)):
    """Live, read-only view of the whole engagement data model for the GUI Data
    inspector: every object, every persisted field (classified), references
    resolved, plus the input → engine → output flow."""
    eng = _require_engagement(db, engagement_id)
    return inspector.inspect_engagement(db, eng)


def _require_engagement(db: Session, engagement_id: str) -> models.Engagement:
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise HTTPException(404, "Engagement not found")
    return eng


def _normalize_third_party(eng: models.Engagement, tp: models.ThirdPartyProduct) -> None:
    """Derive annual_cost, per-unit, effective cost on input (PRD 5.6)."""
    raw = Decimal(str(tp.raw_cost or 0))
    annual = raw * 12 if tp.cost_period == "Monthly" else raw
    tp.annual_cost = annual
    # tooling_pct defaults to the engagement split; only applied when managed.
    tooling = Decimal(str(tp.tooling_pct if tp.tooling_pct is not None else eng.global_tooling_pct))
    tp.tooling_pct = tooling
    tp.effective_annual_cost = (annual * tooling) if tp.is_managed else annual
    if tp.covered_count and tp.covered_count > 0:
        tp.per_unit_annual_cost = Decimal(str(tp.effective_annual_cost)) / Decimal(tp.covered_count)
    else:
        tp.per_unit_annual_cost = Decimal("0")


# ---------- Personas ----------
@router.get("/personas", response_model=list[schemas.PersonaOut])
def list_personas(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.Persona).where(models.Persona.engagement_id == engagement_id)
    ).scalars().all()


@router.post("/personas", response_model=schemas.PersonaOut, status_code=201)
def create_persona(engagement_id: str, payload: schemas.PersonaIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    row = models.Persona(engagement_id=engagement_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/personas/{persona_id}", response_model=schemas.PersonaOut)
def update_persona(engagement_id: str, persona_id: str, payload: schemas.PersonaIn, db: Session = Depends(get_db)):
    row = db.get(models.Persona, persona_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Persona not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/personas/{persona_id}", status_code=204)
def delete_persona(engagement_id: str, persona_id: str, db: Session = Depends(get_db)):
    row = db.get(models.Persona, persona_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Persona not found")
    db.delete(row)
    db.commit()


@router.post("/personas/{persona_id}/bundle-analysis")
def bundle_analysis(
    engagement_id: str, persona_id: str,
    payload: schemas.BundleAnalysisRequest | None = None,
    db: Session = Depends(get_db),
):
    """Evaluate every candidate Microsoft bundle as this persona's target and
    rank by TCO (best-bundle optimizer). Optional per-bundle price overrides."""
    _require_engagement(db, engagement_id)
    try:
        return compute.analyze_persona_bundles(
            db, engagement_id, persona_id, prices=(payload.prices if payload else None)
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))


# ---------- Outcomes ----------
@router.get("/outcomes", response_model=list[schemas.OutcomeOut])
def list_outcomes(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.Outcome).where(models.Outcome.engagement_id == engagement_id)
    ).scalars().all()


@router.post("/outcomes", response_model=schemas.OutcomeOut, status_code=201)
def create_outcome(engagement_id: str, payload: schemas.OutcomeIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    row = models.Outcome(engagement_id=engagement_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/outcomes/{outcome_id}", response_model=schemas.OutcomeOut)
def update_outcome(engagement_id: str, outcome_id: str, payload: schemas.OutcomeIn, db: Session = Depends(get_db)):
    row = db.get(models.Outcome, outcome_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Outcome not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/outcomes/{outcome_id}", status_code=204)
def delete_outcome(engagement_id: str, outcome_id: str, db: Session = Depends(get_db)):
    row = db.get(models.Outcome, outcome_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Outcome not found")
    db.delete(row)
    db.commit()


# ---------- Current Microsoft licenses ----------
@router.get("/current-licenses", response_model=list[schemas.CurrentLicenseOut])
def list_licenses(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.CurrentMicrosoftLicense).where(
            models.CurrentMicrosoftLicense.engagement_id == engagement_id
        )
    ).scalars().all()


def _set_persona_tags(row: models.CurrentMicrosoftLicense, persona_ids: list[str]):
    """Reconcile a license's persona tags to the given set. Diffs (rather than
    clear-and-re-add) so an unchanged tag isn't re-inserted — which would trip the
    unique constraint before the delete of the old row flushes."""
    want = list(dict.fromkeys(persona_ids))
    have = {pl.persona_id: pl for pl in row.persona_links}
    for pid, pl in list(have.items()):
        if pid not in want:
            row.persona_links.remove(pl)
    for pid in want:
        if pid not in have:
            row.persona_links.append(models.CurrentLicensePersona(persona_id=pid))


@router.post("/current-licenses", response_model=schemas.CurrentLicenseOut, status_code=201)
def create_license(engagement_id: str, payload: schemas.CurrentLicenseIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    data = payload.model_dump()
    persona_ids = data.pop("persona_ids", [])
    row = models.CurrentMicrosoftLicense(engagement_id=engagement_id, **data)
    _set_persona_tags(row, persona_ids)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/current-licenses/{license_id}", response_model=schemas.CurrentLicenseOut)
def update_license(engagement_id: str, license_id: str, payload: schemas.CurrentLicenseIn, db: Session = Depends(get_db)):
    row = db.get(models.CurrentMicrosoftLicense, license_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "License not found")
    data = payload.model_dump(exclude_unset=True)
    if "persona_ids" in data:
        _set_persona_tags(row, data.pop("persona_ids"))
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/current-licenses/{license_id}", status_code=204)
def delete_license(engagement_id: str, license_id: str, db: Session = Depends(get_db)):
    row = db.get(models.CurrentMicrosoftLicense, license_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "License not found")
    db.delete(row)
    db.commit()


# ---------- Third-party products ----------
@router.get("/third-party", response_model=list[schemas.ThirdPartyOut])
def list_third_party(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.ThirdPartyProduct).where(
            models.ThirdPartyProduct.engagement_id == engagement_id
        )
    ).scalars().all()


@router.post("/third-party", response_model=schemas.ThirdPartyOut, status_code=201)
def create_third_party(engagement_id: str, payload: schemas.ThirdPartyIn, db: Session = Depends(get_db)):
    eng = _require_engagement(db, engagement_id)
    row = models.ThirdPartyProduct(engagement_id=engagement_id, **payload.model_dump())
    _normalize_third_party(eng, row)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/third-party/{tp_id}", response_model=schemas.ThirdPartyOut)
def update_third_party(engagement_id: str, tp_id: str, payload: schemas.ThirdPartyIn, db: Session = Depends(get_db)):
    eng = _require_engagement(db, engagement_id)
    row = db.get(models.ThirdPartyProduct, tp_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Third-party product not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    _normalize_third_party(eng, row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/third-party/{tp_id}", status_code=204)
def delete_third_party(engagement_id: str, tp_id: str, db: Session = Depends(get_db)):
    row = db.get(models.ThirdPartyProduct, tp_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Third-party product not found")
    db.delete(row)
    db.commit()


# ---------- Coverage map ----------
@router.get("/coverage", response_model=list[schemas.CoverageOut])
def list_coverage(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == engagement_id
        )
    ).scalars().all()


@router.post("/coverage", response_model=schemas.CoverageOut, status_code=201)
def create_coverage(engagement_id: str, payload: schemas.CoverageIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    row = models.CoverageMapEntry(engagement_id=engagement_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/coverage/{entry_id}", response_model=schemas.CoverageOut)
def update_coverage(engagement_id: str, entry_id: str, payload: schemas.CoverageIn, db: Session = Depends(get_db)):
    row = db.get(models.CoverageMapEntry, entry_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Coverage entry not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.post("/coverage/{entry_id}/ratify", response_model=schemas.CoverageOut)
def ratify_coverage(engagement_id: str, entry_id: str, db: Session = Depends(get_db)):
    """Human ratification gate (PRD 5.7): only ratified entries feed the math."""
    row = db.get(models.CoverageMapEntry, entry_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Coverage entry not found")
    row.ratified = True
    db.commit()
    db.refresh(row)
    return row


@router.delete("/coverage/{entry_id}", status_code=204)
def delete_coverage(engagement_id: str, entry_id: str, db: Session = Depends(get_db)):
    row = db.get(models.CoverageMapEntry, entry_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Coverage entry not found")
    db.delete(row)
    db.commit()


# ---------- Persona scenarios ----------
@router.get("/scenarios", response_model=list[schemas.ScenarioOut])
def list_scenarios(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.PersonaScenario).where(
            models.PersonaScenario.engagement_id == engagement_id
        )
    ).scalars().all()


@router.post("/scenarios", response_model=schemas.ScenarioOut, status_code=201)
def create_scenario(engagement_id: str, payload: schemas.ScenarioIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    row = models.PersonaScenario(engagement_id=engagement_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/scenarios/{scenario_id}", response_model=schemas.ScenarioOut)
def update_scenario(engagement_id: str, scenario_id: str, payload: schemas.ScenarioUpdate, db: Session = Depends(get_db)):
    row = db.get(models.PersonaScenario, scenario_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Scenario not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(engagement_id: str, scenario_id: str, db: Session = Depends(get_db)):
    row = db.get(models.PersonaScenario, scenario_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Scenario not found")
    db.delete(row)
    db.commit()


# ---------- Disposition overrides ----------
@router.put("/dispositions/{tp_id}/override")
def set_disposition_override(
    engagement_id: str, tp_id: str, payload: schemas.DispositionOverrideIn,
    db: Session = Depends(get_db),
):
    """Record the operator's override / residual-intent choice (PRD 6.9 rule 2).

    A ForceFullElimination requires a reason. An intended residual is recorded
    separately and is NOT an override.
    """
    _require_engagement(db, engagement_id)
    tp = db.get(models.ThirdPartyProduct, tp_id)
    if tp is None or tp.engagement_id != engagement_id:
        raise HTTPException(404, "Third-party product not found")
    if payload.override == "ForceFullElimination" and not payload.override_reason.strip():
        raise HTTPException(422, "override_reason is required for ForceFullElimination")

    row = db.execute(
        select(models.ProductDisposition).where(
            models.ProductDisposition.engagement_id == engagement_id,
            models.ProductDisposition.third_party_product_id == tp_id,
        )
    ).scalar_one_or_none()
    if row is None:
        row = models.ProductDisposition(engagement_id=engagement_id, third_party_product_id=tp_id)
        db.add(row)
    row.override = payload.override
    row.override_reason = payload.override_reason
    row.residual_intent = payload.residual_intent
    db.commit()
    return {"ok": True}
