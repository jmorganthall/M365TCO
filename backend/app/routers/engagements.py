"""Engagement lifecycle + computed readout/export/snapshots (PRD 12, 11.5)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..services import compute, defaults, exporter, seeds
from ..services.serialize import result_to_dict

router = APIRouter(prefix="/api/engagements", tags=["engagements"])


def _get_engagement(db: Session, engagement_id: str) -> models.Engagement:
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise HTTPException(404, "Engagement not found")
    return eng


@router.get("", response_model=list[schemas.EngagementOut])
def list_engagements(db: Session = Depends(get_db)):
    return db.execute(
        select(models.Engagement).order_by(models.Engagement.updated_at.desc())
    ).scalars().all()


@router.post("", response_model=schemas.EngagementOut, status_code=201)
def create_engagement(payload: schemas.EngagementCreate, db: Session = Depends(get_db)):
    data = payload.model_dump()
    # Inherit engagement-level defaults from the global defaults when omitted
    # (the New-engagement form no longer asks for the tooling split).
    gd = defaults.get_defaults(db)
    if data.get("global_tooling_pct") is None:
        data["global_tooling_pct"] = gd.default_tooling_pct
    if data.get("modeling_horizon_years") is None:
        data["modeling_horizon_years"] = gd.default_modeling_horizon_years
    eng = models.Engagement(**data)
    db.add(eng)
    db.flush()
    seeds.seed_engagement(db, eng)  # copy default outcomes + MS coverage (5.3.1)
    db.commit()
    db.refresh(eng)
    return eng


@router.get("/{engagement_id}", response_model=schemas.EngagementOut)
def get_engagement(engagement_id: str, db: Session = Depends(get_db)):
    return _get_engagement(db, engagement_id)


@router.patch("/{engagement_id}", response_model=schemas.EngagementOut)
def update_engagement(
    engagement_id: str, payload: schemas.EngagementUpdate, db: Session = Depends(get_db)
):
    eng = _get_engagement(db, engagement_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(eng, k, v)
    db.commit()
    db.refresh(eng)
    return eng


@router.delete("/{engagement_id}", status_code=204)
def delete_engagement(engagement_id: str, db: Session = Depends(get_db)):
    eng = _get_engagement(db, engagement_id)
    db.delete(eng)
    db.commit()


@router.post("/{engagement_id}/duplicate", response_model=schemas.EngagementOut, status_code=201)
def duplicate_engagement(engagement_id: str, db: Session = Depends(get_db)):
    """Deep-copy an engagement and all its child rows, remapping ids."""
    src = _get_engagement(db, engagement_id)
    dst = models.Engagement(
        customer_name=f"{src.customer_name} (copy)",
        market=src.market,
        currency=src.currency,
        modeling_horizon_years=src.modeling_horizon_years,
        global_tooling_pct=src.global_tooling_pct,
        notes=src.notes,
    )
    db.add(dst)
    db.flush()

    persona_map: dict[str, str] = {}
    for p in src.personas:
        np = models.Persona(
            engagement_id=dst.id, name=p.name, headcount=p.headcount,
            description=p.description, source_tag=p.source_tag,
        )
        db.add(np)
        db.flush()
        persona_map[p.id] = np.id

    outcome_map: dict[str, str] = {}
    for o in src.outcomes:
        no = models.Outcome(
            engagement_id=dst.id, name=o.name, description=o.description,
            is_custom=o.is_custom, seed_key=o.seed_key,
        )
        db.add(no)
        db.flush()
        outcome_map[o.id] = no.id

    tp_map: dict[str, str] = {}
    for tp in src.third_party_products:
        ntp = models.ThirdPartyProduct(
            engagement_id=dst.id, name=tp.name, vendor=tp.vendor, raw_cost=tp.raw_cost,
            cost_period=tp.cost_period, annual_cost=tp.annual_cost, unit_basis=tp.unit_basis,
            covered_count=tp.covered_count, per_unit_annual_cost=tp.per_unit_annual_cost,
            renewal_date=tp.renewal_date, commitment_term_months=tp.commitment_term_months,
            is_managed=tp.is_managed, tooling_pct=tp.tooling_pct,
            effective_annual_cost=tp.effective_annual_cost, source_tag=tp.source_tag,
        )
        for pid in tp.persona_ids:
            mapped = persona_map.get(pid)
            if mapped:
                ntp.persona_links.append(models.ThirdPartyPersona(persona_id=mapped))
        db.add(ntp)
        db.flush()
        tp_map[tp.id] = ntp.id

    for lic in src.current_licenses:
        nlic = models.CurrentMicrosoftLicense(
            engagement_id=dst.id, sku_reference=lic.sku_reference,
            quantity_purchased=lic.quantity_purchased, quantity_assigned=lic.quantity_assigned,
            unit_price_paid_annual=lic.unit_price_paid_annual, price_basis=lic.price_basis,
            discount_pct=lic.discount_pct, source_tag=lic.source_tag,
        )
        # Carry the persona tags across, remapped to the cloned personas.
        src_pids = lic.persona_ids or ([lic.persona_id] if lic.persona_id else [])
        for pid in src_pids:
            mapped = persona_map.get(pid)
            if mapped:
                nlic.persona_links.append(models.CurrentLicensePersona(persona_id=mapped))
        db.add(nlic)

    for ce in src.coverage_entries:
        db.add(models.CoverageMapEntry(
            engagement_id=dst.id, outcome_id=outcome_map.get(ce.outcome_id, ce.outcome_id),
            product_kind=ce.product_kind, microsoft_sku_reference=ce.microsoft_sku_reference,
            third_party_product_id=tp_map.get(ce.third_party_product_id) if ce.third_party_product_id else None,
            coverage=ce.coverage, ai_suggested=ce.ai_suggested, ratified=ce.ratified,
        ))

    for s in src.scenarios:
        ns = models.PersonaScenario(
            engagement_id=dst.id, persona_id=persona_map.get(s.persona_id, s.persona_id),
            target_sku_reference=s.target_sku_reference,
            target_unit_price_annual=s.target_unit_price_annual,
            target_discount_pct=s.target_discount_pct, in_scope=s.in_scope,
        )
        # Bundle ids are global, so add-ons carry across unchanged.
        for ad in s.addons:
            ns.addons.append(models.ScenarioAddon(
                bundle_id=ad.bundle_id, unit_price_annual=ad.unit_price_annual))
        db.add(ns)

    for d in src.dispositions:
        db.add(models.ProductDisposition(
            engagement_id=dst.id,
            third_party_product_id=tp_map.get(d.third_party_product_id, d.third_party_product_id),
            override=d.override, override_reason=d.override_reason,
            residual_intent=d.residual_intent,
        ))

    db.commit()
    db.refresh(dst)
    return dst


@router.post("/{engagement_id}/compute")
def compute_engagement(engagement_id: str, db: Session = Depends(get_db)):
    _get_engagement(db, engagement_id)
    result = compute.compute_and_persist(db, engagement_id)
    return result_to_dict(result)


@router.get("/{engagement_id}/readout.html", response_class=HTMLResponse)
def readout_html(engagement_id: str, db: Session = Depends(get_db)):
    eng = _get_engagement(db, engagement_id)
    result = result_to_dict(compute.compute_and_persist(db, engagement_id))
    return HTMLResponse(exporter.build_html(eng, result))


@router.get("/{engagement_id}/readout.xlsx")
def readout_xlsx(engagement_id: str, db: Session = Depends(get_db)):
    eng = _get_engagement(db, engagement_id)
    result = result_to_dict(compute.compute_and_persist(db, engagement_id))
    data = exporter.build_xlsx(eng, result)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="tco-{engagement_id[:8]}.xlsx"'
        },
    )


@router.post("/{engagement_id}/snapshots", status_code=201)
def create_snapshot(engagement_id: str, label: str = "", db: Session = Depends(get_db)):
    """Reproducible saved readout (PRD 12) — survives later catalog updates."""
    _get_engagement(db, engagement_id)
    result = result_to_dict(compute.compute_and_persist(db, engagement_id))
    catalog_version = (
        db.execute(select(models.MicrosoftSku.catalog_version).limit(1)).scalar() or ""
    )
    snap = models.EngagementSnapshot(
        engagement_id=engagement_id, label=label,
        catalog_version=catalog_version, payload_json=json.dumps(result),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return {"id": snap.id, "label": snap.label, "created_at": snap.created_at.isoformat(),
            "catalog_version": snap.catalog_version}


@router.get("/{engagement_id}/snapshots")
def list_snapshots(engagement_id: str, db: Session = Depends(get_db)):
    _get_engagement(db, engagement_id)
    rows = db.execute(
        select(models.EngagementSnapshot)
        .where(models.EngagementSnapshot.engagement_id == engagement_id)
        .order_by(models.EngagementSnapshot.created_at.desc())
    ).scalars().all()
    return [
        {"id": s.id, "label": s.label, "created_at": s.created_at.isoformat(),
         "catalog_version": s.catalog_version} for s in rows
    ]


@router.get("/{engagement_id}/snapshots/{snapshot_id}")
def get_snapshot(engagement_id: str, snapshot_id: str, db: Session = Depends(get_db)):
    snap = db.get(models.EngagementSnapshot, snapshot_id)
    if snap is None or snap.engagement_id != engagement_id:
        raise HTTPException(404, "Snapshot not found")
    return {
        "id": snap.id, "label": snap.label, "created_at": snap.created_at.isoformat(),
        "catalog_version": snap.catalog_version, "payload": json.loads(snap.payload_json),
    }
