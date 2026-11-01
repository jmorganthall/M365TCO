"""Parity: the engine's input built through the interpreter equals what the
hydrator built before it (docs/LAYER_AUDIT.md, Phase 1 gate).

Named to run LAST, so it checks every engagement the whole suite created, plus the
service sweep's CI cases built here. `_legacy_hydrate` is a frozen copy of the
hydrator as it was; this file goes when Phase 2 retires what that copy leans on.
"""

from sqlalchemy import select

from tco_engine.interpret import to_engine
from tco_engine.models import (
    CoverageScope, CurrentLicenseLine, Engagement as EngEngagement, Override,
    Persona as EngPersona, PersonaScenario as EngScenario, ResidualIntent,
    ThirdPartyProduct as EngThirdParty, UnusedSeatsAnswer,
)


# ---------------------------------------------------------------------------
# Frozen reference: the hydrator before the interpreter
# ---------------------------------------------------------------------------

def _legacy_hydrate(db, engagement_id):
    from app import models
    from app.services import compute
    _dec = compute._dec
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise ValueError(f"Engagement {engagement_id} not found")

    sku_outcomes = compute._ratified_sku_outcomes(db, engagement_id)
    tp_outcomes = compute._ratified_thirdparty_outcomes(db, engagement_id)
    readings = compute.licence_readings(db, eng, sku_outcomes)

    # Operator choices persisted on disposition rows.
    disp_rows = {
        d.third_party_product_id: d
        for d in db.execute(
            select(models.ProductDisposition).where(
                models.ProductDisposition.engagement_id == engagement_id
            )
        ).scalars()
    }

    personas = [
        EngPersona(id=p.id, name=p.name, headcount=p.headcount)
        for p in compute.persona_order.ordered(eng.personas)
    ]

    current_lines = [
        CurrentLicenseLine(
            quantity_assigned=lic.quantity_assigned,
            # Effective price = the override when the line overrides list, else the
            # catalog list baseline — so an override actually lowers current spend.
            unit_price_paid_annual=lic.effective_unit_price_annual,
            sku_reference=lic.sku_reference,
            persona_ids=tuple(lic.persona_ids),
            # How many seats this line entitles — per-seat purchase vs tenant-wide
            # entitlement. Drives how many seats a duplicate tool can be credited
            # as redundant today (ENGINE_SPEC 6.10).
            coverage_scope=CoverageScope(lic.coverage_scope or "PerUser"),
            # What this existing license already delivers (how the line is read:
            # its linked plan, its own mapped outcomes, or its name's plan), for
            # quick-win duplicate detection. An unread line delivers nothing known.
            covered_outcome_ids=readings[lic.id].outcomes,
            # Over-licensing and timing (ENGINE_SPEC 6.11).
            id=lic.id,
            quantity_purchased=lic.quantity_purchased or 0,
            renewal_date=lic.renewal_date,
            unused_seats_answer=(
                UnusedSeatsAnswer(lic.unused_seats_answer) if lic.unused_seats_answer else None
            ),
        )
        # Out-of-scope lines are not engine inputs (ENGINE_SPEC 6.2): they are in
        # no number, today or after the move.
        for lic in eng.licenses_in_scope
    ]

    third_party = []
    for tp in eng.third_party_products:
        disp = disp_rows.get(tp.id)
        third_party.append(
            EngThirdParty(
                id=tp.id,
                name=tp.name,
                annual_cost=_dec(tp.annual_cost),
                covered_count=tp.covered_count,
                is_managed=tp.is_managed,
                tooling_pct=_dec(tp.tooling_pct),
                renewal_date=tp.renewal_date.isoformat() if tp.renewal_date else None,
                delivered_outcome_ids=frozenset(tp_outcomes.get(tp.id, set())),
                persona_ids=frozenset(tp.persona_ids),
                override=Override(disp.override) if disp else Override.NONE,
                override_reason=disp.override_reason if disp else "",
                residual_intent=(
                    ResidualIntent(disp.residual_intent) if disp else ResidualIntent.NONE
                ),
            )
        )

    # Compose each scenario's future state = base bundle + add-on bundles: union
    # the covered outcomes, sum the list prices, then apply the discount to yield
    # the net per-seat price the engine consumes. There is no target substitution
    # here any more: a persona that should move to a different plan says so on its
    # own scenario, and a persona only PART of which should move is carved into a
    # child persona with its own scenario (Persona.parent_persona_id). What the
    # engine costs is therefore always what the operator can see on the row.
    scenarios = []
    for s in eng.scenarios:
        covered = set(sku_outcomes.get(compute._cover_key(db, s.target_sku_reference), set()))
        for addon in s.addons:
            covered |= sku_outcomes.get(addon.bundle_id, set())
        # Net = the override when the scenario overrides list, else
        # (base + add-ons) × (1 − discount).
        net_price = s.effective_net_annual
        target_ref = s.target_sku_reference
        scenarios.append(EngScenario(
            id=s.id,
            persona_id=s.persona_id,
            target_sku_reference=target_ref,
            target_unit_price_annual=net_price,
            in_scope=s.in_scope,
            target_covered_outcome_ids=frozenset(covered),
        ))

    _legacy = EngEngagement(
        id=eng.id,
        personas=personas,
        third_party_products=third_party,
        scenarios=scenarios,
        current_licenses=current_lines,
        ecif_roi_conservative=_dec(eng.ecif_roi_conservative),
        ecif_roi_generous=_dec(eng.ecif_roi_generous),
        # Month 0 of the timed headline. An engagement created before the workshop
        # date existed falls back to the day it was created — a fixed date, so the
        # numbers never drift with the calendar.
        workshop_date=eng.workshop_date or (eng.created_at.date() if eng.created_at else None),
        microsoft_renewal_date=eng.microsoft_renewal_date,
        horizon_years=int(eng.modeling_horizon_years or 3),
    )
    return _legacy


def _all_engagement_ids():
    from app import models
    from app.db import SessionLocal

    with SessionLocal() as db:
        return [e for (e,) in db.execute(select(models.Engagement.id)).all()]


def test_engine_input_is_identical_through_the_interpreter(client):
    """Every engagement in the test database (the whole suite's, when run after it,
    plus the service sweep's CI cases built here) gets the same engine input from
    interpret() as from the frozen legacy hydrator."""
    from tests import sweep_services

    # No price list is loaded: the catalog is shared by the whole suite, and parity
    # is about the engine's input, which the loader reads as stored.
    for _label, _case in sweep_services.iter_cases(client, level="ci"):
        pass

    from app.db import SessionLocal
    from app.services import raw

    ids = _all_engagement_ids()
    assert len(ids) >= 8
    with SessionLocal() as db:
        for eid in ids:
            assert to_engine(raw.interpreted(db, eid)) == _legacy_hydrate(db, eid), eid


def test_outcome_tables_agree_with_the_coverage_check_and_the_engine(client):
    """The persona × outcome tables (§6.3) answer the same questions the Coverage
    check (compute.persona_coverage_gaps) and the engine's displacement answer
    today, for every engagement the suite built: what each move would add, which
    of that is confirmed new or unconfirmed, what Microsoft capability it drops,
    and which tools each move retires."""
    from tco_engine import compute as engine_compute

    from app.db import SessionLocal
    from app.services import compute, raw

    checked = 0
    with SessionLocal() as db:
        for eid in _all_engagement_ids():
            i = raw.interpreted(db, eid)
            plans = {s.persona_id: s for s in i.scenarios}
            for g in compute.persona_coverage_gaps(db, eid):
                pid = g["persona_id"]
                rows = [r for r in i.proposed if r.persona_id == pid]
                today = {r.outcome_id: r for r in i.today if r.persona_id == pid}
                if not g["has_scenario"]:
                    assert all(not r.has_plan for r in rows)
                    continue
                ids = lambda key: {o["id"] for o in g[key]}  # noqa: E731
                added = {r.outcome_id for r in rows if r.by_target and not r.delivered_today}
                assert ids("uncovered_outcomes") == added, (eid, pid)
                if plans[pid].complete:
                    assert ids("confirmed_new_outcomes") == {
                        r.outcome_id for r in rows if r.status == "Gained"}, (eid, pid)
                    assert ids("unconfirmed_outcomes") == {
                        r.outcome_id for r in rows if r.status == "Unconfirmed"}, (eid, pid)
                if not g["target_unmapped"]:
                    assert ids("dropped_outcomes") == {
                        o for o, t in today.items() if t.by_microsoft
                        and not any(r.outcome_id == o and r.by_target for r in rows)}, (eid, pid)
                checked += 1

            result = engine_compute(to_engine(i))
            for sr in result.scenarios:
                credited = {o.third_party_product_id for o in sr.offsets}
                retired = {r.tool_id for r in i.tool_retirements
                           if r.persona_id == sr.persona_id and r.retired and not r.forced}
                assert credited == retired, (eid, sr.persona_id)
    assert checked > 0
