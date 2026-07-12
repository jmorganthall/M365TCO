"""Microsoft 365 Business Premium swap — the actionable side of the Business
seat cap (services/limits).

An engagement-level toggle (`Engagement.bp_swap_enabled`) proposes moving eligible
personas onto Business Premium to save. Each eligible persona INHERITS the swap
unless it opts out (`PersonaScenario.bp_swap_optout`). Eligibility is by
CAPABILITY: Business Premium must cover every outcome the persona requires today
(their current Microsoft licenses' outcomes + declared PersonaRequirements), so the
swap never drops a capability.

The swap then fills only UP TO THE LIMIT. Business Premium is capped at 300 seats
per tenant (the `m365-business-seat-cap` LicenseLimit), so the swap greedily moves
the most-saving eligible personas onto Business Premium until the cap's future-state
headroom runs out — whole personas, biggest per-seat saving first. Personas that
don't fit keep their own target (reported as `capped`); a swap that wouldn't actually
save is skipped (`no_savings`). The result is always a plan you can buy: the swap
never proposes more than 300 Business Premium seats, and never renders the impossible
"everyone on Business Premium over the cap" state.

Nothing is persisted — the swap set is a pure derived computation over existing
first-class data (Engagement/PersonaScenario toggles, the coverage map, the priced
catalog, and the LicenseLimit spine), recomputed every compute. This module is the
single source of truth for "does the swap apply to this scenario": the engine
hydrator uses it to substitute the effective target, and the limit evaluator uses it
so a swapped scenario counts against the Business cap — action and guardrail agree.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import bundles as bundles_service

BP_BUNDLE_KEY = "m365-business-premium"


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


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


def _business_cap(db: Session, bp_id: str) -> tuple[models.LicenseLimit | None, set[str]]:
    """The `max_total_seats` LicenseLimit that governs Business Premium (BP is one of
    its member bundles), with its member bundle-id set. `(None, set())` when no such
    cap is defined — the swap is then unbounded (every saving candidate swaps)."""
    for lim in db.execute(
        select(models.LicenseLimit)
        .where(models.LicenseLimit.limit_type == "max_total_seats")
        .order_by(models.LicenseLimit.sort_order)
    ).scalars():
        member_ids = {
            m.bundle_id
            for m in db.execute(
                select(models.LicenseLimitMember).where(
                    models.LicenseLimitMember.license_limit_id == lim.id
                )
            ).scalars()
        }
        if bp_id in member_ids:
            return lim, member_ids
    return None, set()


def _own_target_net(s: models.PersonaScenario) -> Decimal:
    """The scenario's own composed target price per seat (base + add-ons, less the
    scenario discount) — the future-state cost it would carry WITHOUT the swap. This
    is what swapping to Business Premium is measured against for the saving test."""
    list_price = _dec(s.target_unit_price_annual)
    for a in s.addons:
        list_price += _dec(a.unit_price_annual)
    return list_price * (Decimal("1") - _dec(s.target_discount_pct))


def compute_context(db: Session, eng: models.Engagement) -> dict:
    """Everything the swap decision needs, computed once per engagement: the BP
    bundle, its covered outcomes and catalog price, required-outcomes per persona,
    and the cap-filled swap set (which in-scope scenarios actually redirect to BP)."""
    sku_outcomes = _sku_outcomes(db, eng.id)
    bp = bp_bundle(db)
    bp_covered = sku_outcomes.get(bp.id, set()) if bp is not None else set()
    ctx = {
        "bp": bp,
        "bp_covered": bp_covered,
        "bp_price": bundles_service.catalog_annual_erp(db, bp.name) if bp is not None else Decimal("0"),
        "required": required_by_persona(db, eng, sku_outcomes),
    }
    ctx["swapped_ids"], ctx["reasons"], ctx["cap"] = _fill_to_cap(db, eng, ctx)
    return ctx


def eligible(ctx: dict, persona_id: str) -> bool:
    """Business Premium covers every outcome this persona requires (no capability
    loss) — the capability-match eligibility test."""
    if ctx["bp"] is None:
        return False
    return ctx["required"].get(persona_id, set()) <= ctx["bp_covered"]


def _fill_to_cap(
    db: Session, eng: models.Engagement, ctx: dict
) -> tuple[set[str], dict[str, str], dict | None]:
    """Decide which in-scope scenarios the swap redirects onto Business Premium,
    filling the Business seat cap's future-state headroom with the most-saving
    eligible personas first (whole personas). Returns `(swapped_ids, reasons,
    cap_info)`, where `reasons[scenario_id]` is one of applied / capped / no_savings /
    opted_out / ineligible / price_unknown, and `cap_info` describes the ceiling."""
    bp = ctx["bp"]
    reasons: dict[str, str] = {}
    if not eng.bp_swap_enabled or bp is None or ctx["bp_price"] <= 0:
        # Off, no BP bundle, or BP price unknown (can't prove a saving) → no swaps.
        if eng.bp_swap_enabled and bp is not None and ctx["bp_price"] <= 0:
            for s in eng.scenarios:
                if s.in_scope and not s.bp_swap_optout and eligible(ctx, s.persona_id):
                    reasons[s.id] = "price_unknown"
        return set(), reasons, None

    hc = {p.id: p.headcount for p in eng.personas}
    lim, member_ids = _business_cap(db, bp.id)

    ref_cache: dict[str, str | None] = {}

    def _resolve(ref: str) -> str | None:
        if ref not in ref_cache:
            ref_cache[ref] = bundles_service.resolve_bundle(db, ref or "")
        return ref_cache[ref]

    def _natural_member(s: models.PersonaScenario) -> bool:
        """Does the scenario's own (non-swap) target already sit on a Business-family
        member bundle? Such a scenario consumes a cap seat regardless of the swap, so
        moving it to Business Premium is cap-neutral."""
        ids = {_resolve(s.target_sku_reference)} | {a.bundle_id for a in s.addons}
        return bool(ids & member_ids)

    def _bp_net(s: models.PersonaScenario) -> Decimal:
        return ctx["bp_price"] * (Decimal("1") - _dec(s.target_discount_pct))

    # Classify every in-scope scenario. `swapped` collects cap-neutral swaps (already
    # on a member plan) plus, later, the contested swaps that fit; `fixed_seats` is the
    # future-state Business seats committed no matter what the swap decides.
    swapped: set[str] = set()
    contested: list[tuple[models.PersonaScenario, Decimal]] = []
    fixed_seats = 0
    for s in eng.scenarios:
        if not s.in_scope:
            continue
        nat_member = bool(member_ids) and _natural_member(s)
        if s.bp_swap_optout:
            reasons[s.id] = "opted_out"
        elif not eligible(ctx, s.persona_id):
            reasons[s.id] = "ineligible"
        elif _own_target_net(s) - _bp_net(s) <= 0:
            reasons[s.id] = "no_savings"
        elif nat_member:
            swapped.add(s.id)               # cap-neutral: already a member seat
            reasons[s.id] = "applied"
        else:
            contested.append((s, _own_target_net(s) - _bp_net(s)))
            continue                        # reason assigned during the fill below
        if nat_member:
            fixed_seats += hc.get(s.persona_id, 0)

    if lim is None:                          # unbounded: every saving candidate swaps
        for s, _ in contested:
            swapped.add(s.id)
            reasons[s.id] = "applied"
        return swapped, reasons, None

    headroom = max(0, lim.max_quantity - fixed_seats)
    # Most per-seat saving first; ties: larger group first, then stable id.
    contested.sort(key=lambda t: (-t[1], -hc.get(t[0].persona_id, 0), t[0].id))
    swapped_seats = 0
    for s, _ in contested:
        need = hc.get(s.persona_id, 0)
        if need <= headroom:
            swapped.add(s.id)
            reasons[s.id] = "applied"
            headroom -= need
            swapped_seats += need
        else:
            reasons[s.id] = "capped"        # eligible + saving, but no headroom left

    cap = {
        "name": lim.name,
        "max": lim.max_quantity,
        "committed_seats": fixed_seats + swapped_seats,   # future-state Business seats
        "headroom_remaining": headroom,
    }
    return swapped, reasons, cap


def applies(eng: models.Engagement, ctx: dict, scenario: models.PersonaScenario) -> bool:
    """Whether the BP swap redirects this scenario onto Business Premium — i.e. it is
    in the cap-filled swap set (engagement toggle on, not opted out, capability-
    eligible, a genuine saving, and it fit under the 300-seat Business cap)."""
    return scenario.id in ctx.get("swapped_ids", set())


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
        reason = ctx["reasons"].get(s.id)
        if reason is None:  # not in scope, or the swap is off
            reason = (
                "opted_out" if s.bp_swap_optout
                else "ineligible" if not is_eligible
                else "out_of_scope" if not s.in_scope
                else ""
            )
        rows.append({
            "scenario_id": s.id, "persona_id": s.persona_id,
            "persona_name": persona_name.get(s.persona_id, ""),
            "eligible": is_eligible, "opted_out": s.bp_swap_optout,
            "applied": is_applied, "reason": reason,
        })
        if is_applied and s.in_scope:
            swapped_users += headcount.get(s.persona_id, 0)
            swap_delta += float(delta_by_scenario.get(s.id, {}).get("delta_annual", 0.0))

    return {
        "enabled": eng.bp_swap_enabled,
        "bp_available": ctx["bp"] is not None,
        "bp_price_known": ctx["bp_price"] > 0,
        "bp_name": ctx["bp"].name if ctx["bp"] is not None else "Microsoft 365 Business Premium",
        "eligible_count": sum(1 for r in rows if r["eligible"]),
        "swapped_count": sum(1 for r in rows if r["applied"]),
        # Eligible personas the swap wanted but the 300-seat cap left no room for.
        "capped_count": sum(1 for r in rows if r["reason"] == "capped"),
        "swapped_users": swapped_users,
        "swap_delta_annual": swap_delta,
        "cap": ctx.get("cap"),
        "scenarios": rows,
    }
