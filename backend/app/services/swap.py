"""Microsoft 365 Business Premium swap — the actionable side of the Business
seat cap (services/limits).

An engagement-level toggle (`Engagement.bp_swap_enabled`) proposes moving eligible
personas onto Business Premium to save. Each eligible persona INHERITS the swap
unless it opts out (`PersonaScenario.bp_swap_optout`). Eligibility is by
CAPABILITY: Business Premium must cover every outcome the persona requires today
(their current Microsoft licenses' outcomes + declared PersonaRequirements), so the
swap never drops a capability. The 300-seat cap (LicenseLimit) bounds the total.

This module is the single source of truth for "does the swap apply to this
scenario"; the engine hydrator uses it to substitute the effective target, and the
limit evaluator uses it so a swapped scenario counts against the Business cap.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import bundles as bundles_service

BP_BUNDLE_KEY = "m365-business-premium"


def bp_bundle(db: Session) -> models.Bundle | None:
    return db.execute(
        select(models.Bundle).where(models.Bundle.key == BP_BUNDLE_KEY)
    ).scalar_one_or_none()


def _sku_outcomes(db: Session, engagement_id: str) -> dict[str, set[str]]:
    """coverage key (bundle_id or ref) -> ratified outcome ids, Microsoft side."""
    out: dict[str, set[str]] = {}
    for r in db.execute(
        select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == engagement_id,
            models.CoverageMapEntry.product_kind == "MicrosoftSku",
            models.CoverageMapEntry.ratified.is_(True),
        )
    ).scalars():
        out.setdefault(r.bundle_id or r.microsoft_sku_reference or "", set()).add(r.outcome_id)
    return out


def required_by_persona(db: Session, eng: models.Engagement,
                        sku_outcomes: dict[str, set[str]]) -> dict[str, set[str]]:
    """Outcomes each persona must not lose by swapping: everything their current
    Microsoft licenses deliver + their declared required capabilities."""
    req: dict[str, set[str]] = {}
    for lic in eng.current_licenses:
        key = bundles_service.resolve_bundle(db, lic.sku_reference) or (lic.sku_reference or "")
        outs = sku_outcomes.get(key, set())
        for pid in lic.persona_ids:
            req.setdefault(pid, set()).update(outs)
    for p in eng.personas:
        if p.required_outcome_ids:
            req.setdefault(p.id, set()).update(p.required_outcome_ids)
    return req


def compute_context(db: Session, eng: models.Engagement) -> dict:
    """Everything the swap decision needs, computed once per engagement:
    the BP bundle, its covered outcomes, and required-outcomes per persona."""
    sku_outcomes = _sku_outcomes(db, eng.id)
    bp = bp_bundle(db)
    bp_covered = sku_outcomes.get(bp.id, set()) if bp is not None else set()
    return {
        "bp": bp,
        "bp_covered": bp_covered,
        "required": required_by_persona(db, eng, sku_outcomes),
    }


def eligible(ctx: dict, persona_id: str) -> bool:
    """Business Premium covers every outcome this persona requires (no capability
    loss) — the capability-match eligibility test."""
    if ctx["bp"] is None:
        return False
    return ctx["required"].get(persona_id, set()) <= ctx["bp_covered"]


def applies(eng: models.Engagement, ctx: dict, scenario: models.PersonaScenario) -> bool:
    """Whether the BP swap is active for this scenario: engagement toggle on, the
    persona hasn't opted out, and it is capability-eligible."""
    return bool(
        eng.bp_swap_enabled
        and not scenario.bp_swap_optout
        and eligible(ctx, scenario.persona_id)
    )


def summarize(db: Session, engagement_id: str, result: dict) -> dict:
    """The Business Premium swap view for the readout: per-scenario eligibility /
    opt-out / applied, plus the aggregate swapped-user count and combined annual
    delta (the swap's savings story). `result` is the serialized compute output —
    swapped in-scope scenarios' deltas already reflect the BP substitution."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return {"enabled": False, "bp_available": False, "scenarios": []}
    ctx = compute_context(db, eng)
    persona_name = {p.id: p.name for p in eng.personas}
    headcount = {p.id: p.headcount for p in eng.personas}
    delta_by_scenario = {s["scenario_id"]: s for s in result.get("scenarios", [])}

    rows, swapped_users, swap_delta = [], 0, 0.0
    for s in eng.scenarios:
        is_eligible = eligible(ctx, s.persona_id)
        is_applied = applies(eng, ctx, s)
        rows.append({
            "scenario_id": s.id, "persona_id": s.persona_id,
            "persona_name": persona_name.get(s.persona_id, ""),
            "eligible": is_eligible, "opted_out": s.bp_swap_optout, "applied": is_applied,
        })
        if is_applied and s.in_scope:
            swapped_users += headcount.get(s.persona_id, 0)
            swap_delta += float(delta_by_scenario.get(s.id, {}).get("delta_annual", 0.0))

    return {
        "enabled": eng.bp_swap_enabled,
        "bp_available": ctx["bp"] is not None,
        "bp_name": ctx["bp"].name if ctx["bp"] is not None else "Microsoft 365 Business Premium",
        "eligible_count": sum(1 for r in rows if r["eligible"]),
        "swapped_count": sum(1 for r in rows if r["applied"]),
        "swapped_users": swapped_users,
        "swap_delta_annual": swap_delta,
        "scenarios": rows,
    }
