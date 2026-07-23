"""Engagement lifecycle + computed readout/export/snapshots (PRD 12, 11.5)."""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..db import get_db
from ..services import (
    ai, ai_prompts, compute, defaults, exporter, limits, narrative, sanity, seeds, swap,
)
from ..services.serialize import result_to_dict


def _computed_dict(db, engagement_id: str) -> dict:
    """The serialized engine result plus the tenant-wide license-limit evaluation
    (§ services/limits), the Business Premium swap summary (§ services/swap),
    and the per-persona new-outcomes story (§ services/compute) — so every
    readout consumer (compute, HTML/xlsx export, snapshot) carries the guardrail
    check, the swap story, and the value story, and none is hidden."""
    result = result_to_dict(compute.compute_and_persist(db, engagement_id))
    result["license_limits"] = limits.evaluate(db, engagement_id)
    result["bp_swap"] = swap.summarize(db, engagement_id, result)
    result["new_outcomes"] = compute.new_outcomes(db, engagement_id, result)
    return result

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


def _catalog_market_currency(db: Session) -> tuple[str, str]:
    """The market/currency the numbers actually are: the loaded price catalog's
    (single-market by design), or the configured defaults when none is loaded."""
    row = db.execute(select(models.MicrosoftSku).limit(1)).scalars().first()
    if row:
        return row.market, row.currency
    return settings.default_market, settings.default_currency


def _validate_market_currency(db: Session, market: str | None, currency: str | None) -> None:
    """The engine does no currency conversion — every price is used exactly as
    entered and quoted from the loaded catalog. Market/currency are therefore
    validated soft references (pass None for a value that wasn't explicitly
    provided); accepting a mismatch would produce a readout whose header
    contradicts its own numbers."""
    if market is None and currency is None:
        return
    want_market, want_currency = _catalog_market_currency(db)
    if market is not None and market != want_market:
        raise HTTPException(
            422,
            f"Market must be '{want_market}': the loaded price catalog is "
            f"{want_market}/{want_currency} and prices are never converted.",
        )
    if currency is not None and currency != want_currency:
        raise HTTPException(
            422,
            f"Currency must be '{want_currency}': the loaded price catalog is "
            f"{want_market}/{want_currency} and prices are never converted.",
        )


@router.post("", response_model=schemas.EngagementOut, status_code=201)
def create_engagement(payload: schemas.EngagementCreate, db: Session = Depends(get_db)):
    data = payload.model_dump()
    # Market/currency: inherit the catalog's values when not explicitly given;
    # an explicit value must agree with the catalog (no conversion exists).
    provided = payload.model_fields_set
    want_market, want_currency = _catalog_market_currency(db)
    if "market" not in provided:
        data["market"] = want_market
    if "currency" not in provided:
        data["currency"] = want_currency
    _validate_market_currency(
        db,
        data["market"] if "market" in provided else None,
        data["currency"] if "currency" in provided else None,
    )
    # Inherit engagement-level defaults from the global defaults when omitted
    # (the New-engagement form no longer asks for the tooling split).
    gd = defaults.get_defaults(db)
    if data.get("global_tooling_pct") is None:
        data["global_tooling_pct"] = gd.default_tooling_pct
    if data.get("default_segment") is None:
        data["default_segment"] = gd.default_segment
    if data.get("default_term_duration") is None:
        data["default_term_duration"] = gd.default_term_duration
    if data.get("default_billing_plan") is None:
        data["default_billing_plan"] = gd.default_billing_plan
    if data.get("workshop_date") is None:
        data["workshop_date"] = date.today()  # default the Customer Info workshop date to today
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
    data = payload.model_dump(exclude_unset=True)
    _validate_market_currency(db, data.get("market"), data.get("currency"))
    for k, v in data.items():
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
        default_segment=src.default_segment,
        default_term_duration=src.default_term_duration,
        default_billing_plan=src.default_billing_plan,
        brand_logo_data_url=src.brand_logo_data_url,
        brand_primary_color=src.brand_primary_color,
        brand_accent_color=src.brand_accent_color,
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

    # Carry each persona's required-outcome links across (needs both maps).
    for p in src.personas:
        np_id = persona_map.get(p.id)
        for link in p.requirement_links:
            mapped = outcome_map.get(link.outcome_id)
            if np_id and mapped:
                db.add(models.PersonaRequirement(persona_id=np_id, outcome_id=mapped))

    tp_map: dict[str, str] = {}
    for tp in src.third_party_products:
        ntp = models.ThirdPartyProduct(
            engagement_id=dst.id, name=tp.name, vendor=tp.vendor, raw_cost=tp.raw_cost,
            cost_period=tp.cost_period, annual_cost=tp.annual_cost, unit_basis=tp.unit_basis,
            covered_count=tp.covered_count, covered_count_override=tp.covered_count_override,
            per_unit_annual_cost=tp.per_unit_annual_cost,
            renewal_date=tp.renewal_date,
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
            unit_price_paid_annual=lic.unit_price_paid_annual,
            discount_pct=lic.discount_pct, source_tag=lic.source_tag,
            segment=lic.segment, term_duration=lic.term_duration,
            billing_plan=lic.billing_plan,
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
            bp_swap_optout=s.bp_swap_optout,
            term_duration=s.term_duration, billing_plan=s.billing_plan,
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

    for n in src.narratives:
        db.add(models.ScenarioNarrative(
            engagement_id=dst.id,
            persona_id=persona_map.get(n.persona_id) if n.persona_id else None,
            persona_name=n.persona_name, today=n.today, whats_new=n.whats_new,
            value=n.value, generated_at=n.generated_at,
        ))

    db.commit()
    db.refresh(dst)
    return dst


@router.post("/{engagement_id}/compute")
def compute_engagement(engagement_id: str, db: Session = Depends(get_db)):
    _get_engagement(db, engagement_id)
    return _computed_dict(db, engagement_id)


@router.post("/{engagement_id}/sanity-check")
def sanity_check(engagement_id: str, db: Session = Depends(get_db)):
    """Advisory pre-readout AI check: "does this data make sense?" Computes the
    engagement, summarizes it, and asks an inexpensive model to flag likely
    mistakes. Never edits data or the math (PRD 9). Returns findings for the
    operator to eyeball before showing a readout on a live call."""
    eng = _get_engagement(db, engagement_id)
    if not ai.is_enabled():
        raise HTTPException(400, "AI assist disabled: set the OpenRouter API key.")
    result = result_to_dict(compute.compute_and_persist(db, engagement_id))
    summary = sanity.build_sanity_payload(eng, result)
    row = defaults.get_defaults(db)
    model = row.sanity_check_model or settings.sanity_check_model
    try:
        findings = ai.sanity_check(
            summary,
            instructions=ai_prompts.get_instructions(db, "readout_sanity_check"),
            model=model,
            web_search=row.sanity_check_web_search,
        )
    except Exception as exc:  # network/model errors surface cleanly
        raise HTTPException(502, f"Sanity check failed: {exc}")
    return {"model": model, "findings": findings}


def _narratives_response(eng: models.Engagement) -> dict:
    rows = sorted(eng.narratives, key=lambda n: n.persona_name)
    return {
        "narratives": [
            {"persona": n.persona_name, "today": n.today,
             "whats_new": n.whats_new, "value": n.value}
            for n in rows
        ],
        "generated_at": rows[0].generated_at.isoformat() if rows else None,
    }


@router.get("/{engagement_id}/narrative")
def get_scenario_narrative(engagement_id: str, db: Session = Depends(get_db)):
    """The STORED per-persona business narratives (engagement-level data — they
    survive navigation; empty until first generated)."""
    return _narratives_response(_get_engagement(db, engagement_id))


@router.post("/{engagement_id}/narrative")
def scenario_narrative(engagement_id: str, db: Session = Depends(get_db)):
    """(Re)generate the per-scenario business narrative (today / what's new /
    value) for the in-scope personas, grounded in the computed scenarios AND the
    Customer Info context (who the customer is — the prompt weaves in market
    direction / recent headlines when web search is enabled). The result is
    STORED on the engagement (replacing the previous set) so it survives
    navigation; it remains advisory and never feeds the math (PRD 9)."""
    eng = _get_engagement(db, engagement_id)
    if not ai.is_enabled():
        raise HTTPException(400, "AI assist disabled: set the OpenRouter API key.")
    result = result_to_dict(compute.compute_and_persist(db, engagement_id))
    result["new_outcomes"] = compute.new_outcomes(db, engagement_id, result)
    scenarios = narrative.build_narrative_payload(eng, result)
    if not scenarios:
        return {"narratives": [], "generated_at": None}  # nothing in scope to narrate
    row = defaults.get_defaults(db)
    model = row.openrouter_model or settings.openrouter_model
    try:
        narratives = ai.scenario_narratives(
            scenarios,
            instructions=ai_prompts.get_instructions(db, "scenario_narrative"),
            model=model,
            web_search=row.openrouter_web_search,
            customer=narrative.build_customer_context(eng),
        )
    except Exception as exc:  # network/model errors surface cleanly
        raise HTTPException(502, f"Narrative generation failed: {exc}")

    # Replace the stored set wholesale — regeneration is the update path.
    persona_by_name = {p.name: p.id for p in eng.personas}
    for old in list(eng.narratives):
        db.delete(old)
    db.flush()
    for n in narratives:
        db.add(models.ScenarioNarrative(
            engagement_id=engagement_id,
            persona_id=persona_by_name.get(n.get("persona")),
            persona_name=n.get("persona") or "",
            today=n.get("today") or "", whats_new=n.get("whats_new") or "",
            value=n.get("value") or "",
        ))
    db.commit()
    db.refresh(eng)
    return _narratives_response(eng)


@router.get("/{engagement_id}/coverage-gaps")
def coverage_gaps(engagement_id: str, db: Session = Depends(get_db)):
    """Per-persona coverage validation (derived, persists nothing). We check ONLY
    the outcomes a persona's PROPOSED target scenario (base bundle + add-ons)
    would deliver — the potential "new outcomes" — and surface the ones NOT
    already delivered today. "Delivered today" reads the existing coverage map:
    the persona's current Microsoft licensing (its bundles' ratified coverage,
    tagged-or-org-wide lines) plus third parties whose ratified coverage applies
    to the persona (tagged to it, or untagged = org-wide). Reads relationships
    only; the operator resolves each gap by mapping an existing third party (or
    adding one), so the future new-outcomes story is trustworthy."""
    eng = _get_engagement(db, engagement_id)
    third_parties = [
        {"id": t.id, "name": t.name, "persona_ids": list(t.persona_ids)}
        for t in eng.third_party_products
    ]
    return {
        "personas": compute.persona_coverage_gaps(db, engagement_id),
        "third_parties": third_parties,
    }


@router.get("/{engagement_id}/readout.html", response_class=HTMLResponse)
def readout_html(engagement_id: str, db: Session = Depends(get_db)):
    eng = _get_engagement(db, engagement_id)
    result = _computed_dict(db, engagement_id)
    return HTMLResponse(exporter.build_html(eng, result))


@router.get("/{engagement_id}/readout.xlsx")
def readout_xlsx(engagement_id: str, db: Session = Depends(get_db)):
    eng = _get_engagement(db, engagement_id)
    result = _computed_dict(db, engagement_id)
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
    result = _computed_dict(db, engagement_id)
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
