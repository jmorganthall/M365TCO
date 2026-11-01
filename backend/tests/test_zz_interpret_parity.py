"""Parity: the engine's input built through the interpreter equals what the
hydrator built before it (docs/LAYER_AUDIT.md, Phase 1 gate).

Named to run LAST, so it checks every engagement the whole suite created, plus the
service sweep's CI cases built here. `_legacy_hydrate` is a frozen copy of the
hydrator as it was; this file goes when Phase 2 retires what that copy leans on.
"""

from sqlalchemy import select

from sqlalchemy.orm import Session  # noqa: F401  (frozen copies' annotations)

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
            for g in _legacy_persona_coverage_gaps(db, eid):
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



# ---------------------------------------------------------------------------
# Frozen reference: Review and the seat-cap count before the interpreter (1c)
# ---------------------------------------------------------------------------

def _legacy_review(db, engagement_id):
    from app import models
    from app.services import bundles, compute, library_updates, results, seat_cap
    STEP_ORDER = ['customer', 'groups', 'tools', 'future', 'gaps', 'review', 'summary']
    eng = db.get(models.Engagement, engagement_id)
    checks: list[dict] = []
    left_out: list[dict] = []

    def check(code, step, message, severity="warn"):
        checks.append({"code": code, "step": step, "severity": severity, "message": message})

    def leave_out(what, why, step):
        left_out.append({"what": what, "why": why, "step": step})

    personas = {p.id: p for p in eng.personas}
    total_hc = sum(p.headcount or 0 for p in eng.personas)

    # ---- Personas & licences -----------------------------------------------
    if not eng.personas:
        check("no_groups", "groups", "No personas yet. Add the personas who get different licences.")
    if eng.employee_count and total_hc and total_hc != eng.employee_count:
        check("headcount_vs_employees", "groups",
              f"The personas add up to {total_hc:,} people; the customer has {eng.employee_count:,} "
              f"employees. Is a persona missing, or is the employee count out of date?", "info")

    for lic in eng.licenses_in_scope:
        name = lic.sku_reference or "A licence line"
        tagged = [personas[pid] for pid in lic.persona_ids if pid in personas]
        if not lic.persona_ids:
            check("licence_untagged", "groups",
                  f"{name} isn't assigned to any persona, so it is counted across every persona. "
                  f"Say which personas get it.")
        elif lic.coverage_scope != "TenantWide":
            hc = sum(p.headcount or 0 for p in tagged)
            if lic.quantity_assigned != hc:
                check("seats_vs_headcount", "groups",
                      f"{name}: {lic.quantity_assigned:,} seats are assigned, but the personas that get "
                      f"it have {hc:,} people. Adjust the seats, or split the persona if only some of "
                      f"them hold it.", "info")
        unused = max((lic.quantity_purchased or 0) - (lic.quantity_assigned or 0), 0)
        if unused and not lic.unused_seats_answer:
            check("unused_seats_unanswered", "groups",
                  f"{name}: {unused:,} seats are paid for but not assigned. Kept on purpose, or not needed?")
            leave_out(f"{unused:,} unused {name} seats",
                      "not yet answered whether they're needed", "groups")
    in_scope = eng.licenses_in_scope
    if in_scope and not eng.microsoft_renewal_date and any(
            not lic.renewal_date for lic in in_scope):
        check("renewal_date_missing", "groups",
              "The Microsoft agreement has no renewal date, so it is treated as month-to-month: "
              "Microsoft reductions count from today.", "info")
    out = [lic.sku_reference or "A licence line" for lic in eng.current_licenses if lic.out_of_scope]
    if out:
        check("license_out_of_scope", "groups",
              f"Set aside as out of scope for this workshop: {', '.join(out)}. "
              f"They are in no number; the PDF lists them.", "info")

    # ---- Other tools --------------------------------------------------------
    tp_outcomes = compute._ratified_thirdparty_outcomes(db, engagement_id)
    for t in eng.third_party_products:
        missing = []
        if not t.raw_cost or float(t.raw_cost) <= 0:
            missing.append("no cost")
        if not t.covered_count:
            missing.append("no users")
        if not tp_outcomes.get(t.id):
            missing.append("no uses ticked")
        if missing:
            check("tool_incomplete", "tools",
                  f"{t.name or 'A tool'} has {', '.join(missing)}, so it is left out of the numbers.")
            leave_out(t.name or "A tool", ", ".join(missing), "tools")
        elif not t.renewal_date:
            check("renewal_date_missing", "tools",
                  f"{t.name}: no renewal date, so it is treated as month-to-month and its saving "
                  f"counts from today.", "info")

    # ---- Future state (engine, not persisted) ------------------------------
    result = results.engine_result(db, engagement_id)
    with_scenario = {s.persona_id for s in eng.scenarios}
    for p in eng.personas:
        if p.id not in with_scenario:
            check("group_without_plan", "future",
                  f"{p.name} has no future plan yet, so it isn't in the totals.")
    # The Business seat cap, in the future state (TARGET_SCHEMA D27): a plan that
    # needs more Business seats than are left is not something Microsoft will sell.
    for o in seat_cap.split_offers(db, engagement_id):
        if o["kind"] == "no_seats":
            msg = (f"{o['persona_name']}: {o['headcount']:,} people on {o['plan']}, but no Business "
                   f"seats are left ({o['used_elsewhere']:,} of {o['cap']:,} are used by other personas). "
                   f"Choose another plan on Future state.")
        elif o["kind"] == "nested":
            msg = (f"{o['persona_name']}: {o['headcount']:,} people on {o['plan']}, but only "
                   f"{o['seats_left']:,} Business seats are left. Reduce this carve-out on Future state.")
        else:
            msg = (f"{o['persona_name']}: {o['headcount']:,} people on {o['plan']}, but only "
                   f"{o['seats_left']:,} Business seats are left. Split it on Future state.")
        check("seat_cap_exceeded", "future", msg)
    for d in result.dispositions:
        if d.requires_residual_classification:
            check("residual_unanswered", "future",
                  f"{d.third_party_product_name} is only partly replaced. Keep it for the remaining "
                  f"users, or retire it?")
            leave_out(f"Retiring the rest of {d.third_party_product_name}",
                      "not yet decided — assumed kept", "future")

    # ---- Capability comparison (shared with the Coverage Check) -------------
    gaps = compute.persona_coverage_gaps(db, engagement_id)
    # A licence nobody can read (D23): one question per licence name, answered on
    # Other tools; until then each group holding it has its capability changes
    # left out (its money still counts).
    unread: dict[str, tuple[str, list[str]]] = {}
    for g in gaps:
        for u in g["unmapped_current_licenses"]:
            key = bundles.normalize_alias(u["sku_reference"])
            unread.setdefault(key, (u["sku_reference"], []))[1].append(g["persona_name"])
    for ref, groups in unread.values():
        check("license_unread", "tools",
              f"What {ref} includes isn't known yet ({', '.join(groups)}). Say it's the same as a "
              f"library plan, tick what it delivers, or set it aside as out of scope.")
    for g in gaps:
        name = g["persona_name"]
        if g["unmapped_current_licenses"]:
            if g["has_scenario"]:
                refs = ", ".join(u["sku_reference"] for u in g["unmapped_current_licenses"])
                leave_out(f"Capability changes for {name}", f"what {refs} includes isn't answered",
                          "tools")
            continue
        if g["has_scenario"] and g["target_unmapped"]:
            check("target_unmapped", "future",
                  f"{name}: the future plan isn't recognised, so its capabilities can't be compared.")
        if g["dropped_outcomes"]:
            names = ", ".join(o["name"] for o in g["dropped_outcomes"])
            check("capability_dropped", "future",
                  f"{name}'s future plan drops something it has today: {names}. Confirm that's intended.",
                  "info")
        n = len(g["unconfirmed_outcomes"])
        if g["has_scenario"] and n:
            check("gap_unanswered", "gaps",
                  f"{name}: {n} capabilit{'y' if n == 1 else 'ies'} the plan adds "
                  f"{'is' if n == 1 else 'are'} not yet confirmed as missing today.")
            leave_out(f"{n} new capabilit{'y' if n == 1 else 'ies'} for {name}",
                      "not yet confirmed as missing today", "gaps")

    # ---- Library updates (TARGET_SCHEMA §4.7, §6.4) ---------------------------
    waiting = library_updates.pending(db, eng)
    if waiting:
        n = len(waiting)
        check("library_update_pending", "gaps",
              f"{n} library update{'s are' if n > 1 else ' is'} waiting for this engagement "
              f"(the shared library changed after it was created). Apply or decline "
              f"{'them' if n > 1 else 'it'} on the Coverage check.", "info")

    rank = {s: i for i, s in enumerate(STEP_ORDER)}
    # A licence nobody can read blanks whole groups' capability story: it leads.
    checks.sort(key=lambda c: (c["code"] != "license_unread", rank.get(c["step"], 99),
                               c["severity"] != "warn"))
    left_out.sort(key=lambda x: rank.get(x["step"], 99))
    return {"checks": checks, "left_out": left_out}


def _future_seat_usage(db, eng, mset, resolve):
    """THE count of a seat cap's seats in the future state — the one definition the
    Readout's licence-limit card, the recommender, the split offer and Review all
    use (TARGET_SCHEMA D27, §6.4 `seat_cap_exceeded`).

    Per persona:
    - a persona with an in-scope scenario counts its headcount when the scenario's
      base or any add-on is a member bundle (its current lines don't count: the
      move replaces them);
    - every other persona keeps its current licences, so it counts its share of the
      assigned seats on member lines (a line shared by several personas splits by
      headcount; an untagged line applies to every persona).

    Returns (seats per persona id, unattributed seats). Unattributed = member lines
    whose personas have no headcount to share them by; they count in full."""
    headcount = {p.id: p.headcount or 0 for p in eng.personas}
    moving = {s.persona_id: s for s in eng.scenarios if s.in_scope}
    usage: dict[str, float] = {}
    unattributed = 0.0
    for pid, s in moving.items():
        plan = {resolve(s.target_sku_reference)} | {a.bundle_id for a in s.addons}
        if plan & mset:
            usage[pid] = usage.get(pid, 0.0) + headcount.get(pid, 0)
    for lic in eng.licenses_in_scope:
        if resolve(lic.sku_reference) not in mset:
            continue
        applies = list(lic.persona_ids) or list(headcount)
        total_hc = sum(headcount.get(pid, 0) for pid in applies)
        if not total_hc:
            unattributed += lic.quantity_assigned or 0
            continue
        for pid in applies:
            if pid in moving or not headcount.get(pid, 0):
                continue
            usage[pid] = usage.get(pid, 0.0) + (lic.quantity_assigned or 0) * headcount[pid] / total_hc
    return usage, unattributed


def _resolver(db):
    from app.services import bundles as bundles_service
    cache: dict[str, str | None] = {}

    def resolve(ref: str) -> str | None:
        if ref not in cache:
            cache[ref] = bundles_service.resolve_bundle(db, ref or "")
        return cache[ref]
    return resolve


def _legacy_evaluate(db, engagement_id):
    from app import models
    from app.services.limits import _members_by_limit
    """Every license limit against this engagement, tenant-wide: seats today (the
    assigned seats on member licence lines) and seats in the future state — counted
    by `_future_seat_usage`, the same definition the split offer and Review use, so
    the Readout card can never disagree with them. Reads only. Empty when no limits
    are defined."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return []
    limits = db.execute(
        select(models.LicenseLimit).order_by(models.LicenseLimit.sort_order)
    ).scalars().all()
    if not limits:
        return []

    members = _members_by_limit(db)
    bundle_name = {b.id: b.name for b in db.execute(select(models.Bundle)).scalars().all()}
    resolve = _resolver(db)

    out: list[dict] = []
    for lim in limits:
        mset = members.get(lim.id, set())
        current_seats = sum(
            lic.quantity_assigned
            for lic in eng.licenses_in_scope
            if resolve(lic.sku_reference) in mset
        )
        usage, unattributed = _future_seat_usage(db, eng, mset, resolve)
        target_seats = int(round(sum(usage.values()) + unattributed))
        cap = lim.max_quantity
        out.append({
            "id": lim.id, "key": lim.key, "name": lim.name,
            "limit_type": lim.limit_type, "max_quantity": cap,
            "unit_basis": lim.unit_basis, "scope": lim.scope,
            "member_bundle_names": sorted(
                bundle_name[i] for i in mset if i in bundle_name
            ),
            "current_seats": current_seats,
            "target_seats": target_seats,
            "current_over_by": max(0, current_seats - cap),
            "target_over_by": max(0, target_seats - cap),
            # The actionable signal: the plan (future state) or the existing tenant
            # already exceeds the ceiling.
            "violated": current_seats > cap or target_seats > cap,
        })
    return out


def _legacy_seat_cap_context(db, engagement_id, exclude_persona_id=None):
    from app import models
    from app.services.limits import _members_by_limit
    """Seats left under each tenant seat cap (max_total_seats) in the FUTURE state,
    for one persona's decision (TARGET_SCHEMA §3.1, D27): the future-state count
    (`_future_seat_usage`, shared with the Readout card) less the excluded
    persona's own seats — they are what is being decided.

    Returns one dict per cap: {key, limit_id, name, cap, consumed, headroom,
    member_bundle_ids, member_bundle_names, member_references}; member_references
    are the member bundle NAMES (an optimizer candidate's sku_reference). Empty when
    no such limit is defined."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return []
    limits = [
        lim for lim in db.execute(
            select(models.LicenseLimit).order_by(models.LicenseLimit.sort_order)
        ).scalars().all()
        if lim.limit_type == "max_total_seats"
    ]
    if not limits:
        return []

    members = _members_by_limit(db)
    bundle_name = {b.id: b.name for b in db.execute(select(models.Bundle)).scalars().all()}
    resolve = _resolver(db)

    out: list[dict] = []
    for lim in limits:
        mset = members.get(lim.id, set())
        usage, unattributed = _future_seat_usage(db, eng, mset, resolve)
        consumed = int(round(
            sum(v for pid, v in usage.items() if pid != exclude_persona_id) + unattributed))
        member_names = sorted(bundle_name[i] for i in mset if i in bundle_name)
        out.append({
            "key": lim.key,
            "limit_id": lim.id,
            "name": lim.name,
            "cap": lim.max_quantity,
            "consumed": consumed,
            "headroom": max(0, lim.max_quantity - consumed),
            "member_bundle_ids": sorted(mset),
            "member_bundle_names": member_names,
            "member_references": member_names,
        })
    return out


def test_review_and_seat_caps_are_identical_through_the_interpreter(client):
    """Review's checks and the seat-cap count, now built from the interpreter's
    findings and seat caps, equal what the code before it produced, for every
    engagement the suite built."""
    from app import models
    from app.db import SessionLocal
    from app.services import limits, raw, review

    checked = 0
    with SessionLocal() as db:
        for eid in _all_engagement_ids():
            assert review.review(db, eid) == _legacy_review(db, eid), eid
            assert limits.evaluate(db, eid) == _legacy_evaluate(db, eid), eid
            eng = db.get(models.Engagement, eid)
            i = raw.interpreted(db, eid)
            for pid in [None] + [p.id for p in eng.personas]:
                assert limits.seat_cap_context(db, eid, pid, interpreted=i) == \
                    _legacy_seat_cap_context(db, eid, pid), (eid, pid)
            checked += 1
    assert checked > 0



# ---------------------------------------------------------------------------
# Frozen reference: the Coverage check before it read the interpreter (Phase 2)
# ---------------------------------------------------------------------------

def _legacy_persona_coverage_gaps(db, engagement_id):
    from app import models
    from app.services import bundles, compute
    from app.services import personas as persona_order
    _ratified_sku_outcomes = compute._ratified_sku_outcomes
    _ratified_thirdparty_outcomes = compute._ratified_thirdparty_outcomes
    licence_readings = compute.licence_readings
    _cover_key = compute._cover_key
    """Per persona: the outcomes the PROPOSED target scenario (base bundle +
    add-ons) would deliver that nothing delivers today. "Delivered today" reads
    the existing coverage map: the persona's current Microsoft licensing (its
    bundles' ratified coverage, tagged-or-org-wide lines) plus third parties
    whose ratified coverage applies to the persona. Derived, persists nothing.
    Serves both the Coverage Check step (as gaps to resolve) and the readout's
    New-outcomes section (whatever remains unresolved is genuinely new).

    Alongside the gaps it returns the guards that say when the comparison itself
    cannot be trusted: `unmapped_current_licenses` (cost counted, capability
    invisible), `unmapped_target`/`target_unmapped` (the target contributes no
    ratified coverage, so it can neither add nor drop anything) and
    `org_wide_current_licenses` (untagged lines counted for every persona are
    what make the target look redundant)."""
    eng = db.get(models.Engagement, engagement_id)
    sku_outcomes = _ratified_sku_outcomes(db, engagement_id)
    tp_outcomes = _ratified_thirdparty_outcomes(db, engagement_id)
    readings = licence_readings(db, eng, sku_outcomes)
    name_by_id = {o.id: o.name for o in eng.outcomes}
    desc_by_id = {o.id: o.description or "" for o in eng.outcomes}
    # The library key travels with each outcome so a frozen snapshot can still be
    # matched across engagements (the Workshop value ranking, TARGET_SCHEMA §7.2).
    seed_key_by_id = {o.id: o.seed_key for o in eng.outcomes}

    # Outcomes each persona's proposed scenario (base target + add-ons) delivers,
    # plus the references it draws that coverage from. Keeping the references is
    # what lets an UNMAPPED target (a SKU with no ratified coverage here) be
    # reported as a data gap instead of reading as "delivers nothing" — which
    # would silently empty the New-outcomes story and invent a full-capability drop.
    bundle_name = {b.id: b.name for b in bundles.list_bundles(db)}
    target_by_persona: dict[str, set[str]] = {}
    target_refs_by_persona: dict[str, list[dict]] = {}
    for s in eng.scenarios:
        refs = [{
            "reference": s.target_sku_reference or "",
            "key": _cover_key(db, s.target_sku_reference),
            # True: a known bundle whose coverage is missing here (fix in the
            # Coverage map). False: the SKU matches no bundle at all (map it in
            # Settings -> Staple bundles). Add-ons are bundle-keyed by construction.
            "resolves_to_bundle": bundles.resolve_bundle(db, s.target_sku_reference) is not None,
        }]
        refs += [
            {"reference": bundle_name.get(a.bundle_id, a.bundle_id),
             "key": a.bundle_id, "resolves_to_bundle": True}
            for a in s.addons
        ]
        covered: set[str] = set()
        for r in refs:
            covered |= sku_outcomes.get(r["key"], set())
        target_by_persona[s.persona_id] = covered
        target_refs_by_persona[s.persona_id] = refs

    # The customer's answers about gaps (TARGET_SCHEMA §4.8): CoveredOutsideInventory
    # counts as delivered today; NotDeliveredToday confirms a gap; no answer leaves
    # it unconfirmed (never claimed as new, D21).
    answers = {
        (a.persona_id, a.outcome_id): a for a in eng.gap_answers
    }

    def _outcome_dicts(ids, pid=None):
        out = []
        for oid in ids:
            d = {"id": oid, "name": name_by_id.get(oid, oid),
                 "description": desc_by_id.get(oid, ""),
                 "seed_key": seed_key_by_id.get(oid)}
            if pid is not None:
                a = answers.get((pid, oid))
                d["answer"] = a.answer if a else None
                d["answer_id"] = a.id if a else None
            out.append(d)
        return out

    personas = []
    for p in persona_order.ordered(eng.personas):
        target_outcomes = target_by_persona.get(p.id, set())
        # Current Microsoft licensing that applies to this persona (tagged to it, or
        # untagged = org-wide). A persona can hold SEVERAL current licenses (a
        # many-to-one relationship); its delivered capability is the UNION of all of
        # them. We also record any line whose SKU resolves to NO mapped capability —
        # its outcomes are invisible to the comparison, so a smaller target can
        # silently drop it. That is the multi-license trap: e.g. a persona on
        # "Office 365 E3" + "Enterprise Mobility + Security E3" where EMS maps to no
        # bundle looks fully covered by "Office 365 E3" alone, hiding the EMS loss.
        # Tagged and untagged (org-wide) coverage is tracked separately: an untagged
        # line counts for EVERY persona, which is deliberately conservative but can
        # make a persona look like it already holds what the target adds. Knowing
        # which coverage came from an untagged line is what lets the readout say so.
        ms_tagged: set[str] = set()
        ms_org_wide: set[str] = set()
        unmapped: list[dict] = []
        org_wide_by_ref: dict[str, set[str]] = {}
        seen_refs: set[str] = set()
        for lic in eng.licenses_in_scope:
            if lic.persona_ids and p.id not in lic.persona_ids:
                continue
            reading = readings[lic.id]
            outs = set(reading.outcomes)
            ref = lic.sku_reference or ""
            if lic.persona_ids:
                ms_tagged |= outs
            else:
                ms_org_wide |= outs
                if outs and ref:
                    org_wide_by_ref.setdefault(ref, set()).update(outs)
            # Once per licence name, compared as name resolution compares it.
            if reading.state == "unread" and bundles.normalize_alias(ref) not in seen_refs:
                seen_refs.add(bundles.normalize_alias(ref))
                unmapped.append({
                    "sku_reference": ref or "A licence line",
                    # True: the line names (or is linked to) a library plan that
                    # this engagement has no coverage for — "use the library's
                    # list". False: the library doesn't know it. Either way it is
                    # answered on the unknown-licence card (Other tools).
                    "resolves_to_bundle": reading.bundle_id is not None,
                })
        ms_today = ms_tagged | ms_org_wide
        # Third parties per the ratified coverage map: tagged to this persona,
        # or untagged (org-wide).
        tp_today: set[str] = set()
        for t in eng.third_party_products:
            if t.persona_ids and p.id not in t.persona_ids:
                continue
            tp_today |= tp_outcomes.get(t.id, set())
        outside = {
            oid for (pid, oid), a in answers.items()
            if pid == p.id and a.answer == "CoveredOutsideInventory"
        }
        covered_today = ms_today | tp_today | outside
        uncovered = sorted(target_outcomes - covered_today)
        confirmed = [o for o in uncovered
                     if (a := answers.get((p.id, o))) and a.answer == "NotDeliveredToday"]
        unconfirmed = [o for o in uncovered if (p.id, o) not in answers]
        # The target references that contribute NO ratified coverage. With all of
        # them unmapped there is nothing to compare against, so the capability
        # story is a data gap, not a finding.
        target_refs = target_refs_by_persona.get(p.id, [])
        unmapped_target = [
            {"reference": r["reference"], "resolves_to_bundle": r["resolves_to_bundle"]}
            for r in target_refs if not sku_outcomes.get(r["key"]) and r["reference"]
        ]
        target_unmapped = bool(target_refs) and not target_outcomes
        # Capability the move would DROP: outcomes the persona's current Microsoft
        # licensing delivers today that the proposed target won't — the reverse of
        # `uncovered`. Surfaced so a downgrade is a confirmed choice, never a silent
        # loss. Only meaningful once a target scenario exists AND that target has
        # ratified coverage — an unmapped target would otherwise "drop" everything.
        dropped = (
            sorted(ms_today - target_outcomes)
            if p.id in target_by_persona and not target_unmapped else []
        )
        # Which untagged (org-wide) lines actually decide the "nothing new" verdict:
        # the lines covering target outcomes that NOTHING tagged to this persona
        # covers. These are the lines to persona-tag before the value story can be
        # trusted — an untagged line that changes no verdict is not named.
        decisive = (target_outcomes & ms_org_wide) - ms_tagged - tp_today
        org_wide_decisive = sorted(
            ref for ref, outs in org_wide_by_ref.items() if outs & decisive
        )
        personas.append({
            "persona_id": p.id,
            "persona_name": p.name,
            "headcount": p.headcount,
            "has_scenario": p.id in target_by_persona,
            "target_outcome_count": len(target_outcomes),
            "covered_of_target": len(target_outcomes & covered_today),
            # Every gap the move would fill, each with the customer's answer.
            "uncovered_outcomes": _outcome_dicts(uncovered, p.id),
            # ...split: confirmed gaps are new outcomes; unanswered ones are not
            # claimed until the customer confirms nothing delivers them today.
            "confirmed_new_outcomes": _outcome_dicts(confirmed),
            "unconfirmed_outcomes": _outcome_dicts(unconfirmed),
            "covered_outside_outcomes": _outcome_dicts(sorted(outside & target_outcomes), p.id),
            # Honesty guards for a target that delivers LESS than today (below).
            "dropped_outcomes": _outcome_dicts(dropped),
            "unmapped_current_licenses": unmapped,
            # Honesty guards for a comparison that cannot be trusted: a target with
            # no ratified coverage, and current coverage attributed org-wide.
            "unmapped_target": unmapped_target,
            "target_unmapped": target_unmapped,
            "org_wide_current_licenses": org_wide_decisive,
        })
    return personas


def test_coverage_check_is_identical_through_the_interpreter(client):
    from app.db import SessionLocal
    from app.services import compute

    with SessionLocal() as db:
        for eid in _all_engagement_ids():
            assert compute.persona_coverage_gaps(db, eid) == _legacy_persona_coverage_gaps(db, eid), eid



# ---------------------------------------------------------------------------
# Frozen reference: the recommender before it read the interpreter (Phase 2)
# ---------------------------------------------------------------------------

def _legacy_analyze_persona_bundles(
    db, engagement_id, persona_id, prices=None,
    *, headcount=None, exclude_capped=False, follow_own_plan=False,
):
    from decimal import Decimal
    from app import models
    from app.services import bundles, limits, raw
    from app.services.compute import (
        _catalog_annual_erp, _cover_key, _dec, _min_cost_cover, _ratified_sku_outcomes,
        _ratified_thirdparty_outcomes, licence_readings,
    )
    from tco_engine import CandidateBundle, ThirdPartyProduct as EngThirdParty, analyze_bundles
    """Recommend a path for this persona: compose each staple base bundle with the
    cheapest add-ons that close its capability gaps, then rank the composed
    options by TCO. Each candidate is a base + gap-closing add-ons (outcomes
    unioned, prices summed) — the same composition the scenario editor applies.

    The seat cap always applies, measured in the future state (TARGET_SCHEMA D27).
    `headcount` analyses part of the persona (the remainder of a split) and
    `exclude_capped` drops every seat-capped plan — together, "the best plan for
    the people over the cap". `follow_own_plan` makes that plan decide each tool
    the way the persona's own chosen plan does (the split's other side, D29).

    A persona in a carve-out family (a parent and its carve-outs) is recommended
    the plan the family's shared tool decision gives it (TARGET_SCHEMA D29); its
    own cheapest plan stays listed, tagged, when it is cheaper and decides
    differently."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise ValueError("Engagement not found")
    persona = db.get(models.Persona, persona_id)
    if persona is None or persona.engagement_id != engagement_id:
        raise ValueError("Persona not found")

    sku_outcomes = _ratified_sku_outcomes(db, engagement_id)  # ref -> {outcome_id}
    tp_outcomes = _ratified_thirdparty_outcomes(db, engagement_id)
    readings = licence_readings(db, eng, sku_outcomes)
    outcome_names = {o.id: o.name for o in eng.outcomes}

    # Base bundles = full bundles (kind='bundle') with coverage; add-ons layer on.
    all_bundles = bundles.list_bundles(db)
    base_bundles = [b for b in all_bundles if b.kind == "bundle" and b.id in sku_outcomes]
    addon_bundles = [b for b in all_bundles if b.kind == "addon" and b.id in sku_outcomes]

    # Required outcomes = what the persona's current Microsoft licenses deliver
    # (the "don't lose capability" baseline used for gap detection). A line may be
    # tagged to several personas; its cost is split across their combined headcount
    # (mirrors the engine's §6.2 allocation).
    hc = {p.id: p.headcount for p in eng.personas}
    # The lines that apply to this persona: tagged to it, or untagged (Org-wide,
    # every persona — TARGET_SCHEMA D12, the engine's reading).
    persona_lines = [l for l in eng.licenses_in_scope
                     if not l.persona_ids or persona_id in l.persona_ids]
    required: set[str] = set()
    sources: dict[str, list[str]] = {}  # outcome -> why it is required
    current_ms = Decimal("0")
    for line in persona_lines:
        required |= readings[line.id].outcomes
        for oid in readings[line.id].outcomes:
            sources.setdefault(oid, ["licence"])
        line_total = Decimal(line.quantity_assigned) * line.effective_unit_price_annual
        tagged = [pid for pid in (line.persona_ids or hc) if pid in hc]
        tagged_hc = sum(hc[pid] for pid in tagged)
        share = (Decimal(hc.get(persona_id, 0)) / Decimal(tagged_hc)) if tagged_hc > 0 \
            else Decimal(1) / Decimal(len(tagged) or 1)
        current_ms += line_total * share

    # The tools that apply to this persona: tagged to it, or to no persona (the
    # engine's rule, ENGINE_SPEC 6.3a).
    held_tools = [tp for tp in eng.third_party_products
                  if not tp.persona_ids or persona_id in tp.persona_ids]

    # Everything they have today (MS + this persona's tools) — used to compute the
    # ADDED outcomes a bundle brings that they don't have at all.
    current_capability = set(required)
    for tp in held_tools:
        current_capability |= tp_outcomes.get(tp.id, set())

    # Persona-declared required capabilities (Personas tab) are ALSO required for
    # gap detection, even when no current license delivers them — this is what
    # keeps a persona that needs Desktop Software off a Frontline bundle. Added to
    # `required` only (not `current_capability`), so such a bundle still surfaces
    # it as a newly-added capability.
    for oid in persona.required_outcome_ids:
        if oid in outcome_names:
            required.add(oid)
            sources.setdefault(oid, []).append("requirement")

    third_party = [
        EngThirdParty(
            id=tp.id, name=tp.name, annual_cost=_dec(tp.annual_cost),
            covered_count=tp.covered_count, is_managed=tp.is_managed,
            tooling_pct=_dec(tp.tooling_pct),
            delivered_outcome_ids=frozenset(tp_outcomes.get(tp.id, set())),
            # The tags matter here exactly as they do in the engine: without them
            # the optimizer would credit this persona for a tool a DIFFERENT
            # persona holds (ENGINE_SPEC 6.3a).
            persona_ids=frozenset(tp.persona_ids),
        )
        for tp in eng.third_party_products
    ]
    tp_names = {tp.id: tp.name for tp in eng.third_party_products}

    prices = prices or {}
    basis = bundles.engagement_price_basis(eng)

    price_cache: dict[str, Decimal] = {}
    catalog = None  # bundles.PriceCatalog, read once on the first catalog lookup

    def _price(bundle) -> Decimal:
        nonlocal catalog
        if bundle.id not in price_cache:
            override = prices.get(bundle.name)
            if override is not None:
                price_cache[bundle.id] = Decimal(str(override))
            else:
                if catalog is None:
                    catalog = bundles.PriceCatalog(db)
                price_cache[bundle.id] = _catalog_annual_erp(db, bundle, basis, catalog)
        return price_cache[bundle.id]

    # A tool's outcomes are kept by the tool or by Microsoft (TARGET_SCHEMA D28):
    # it retires only when a plan covers everything it delivers. An add-on joins to
    # retire tools only when its cost is less than their credit — the engine's credit
    # (ENGINE_SPEC 6.3a: this persona's tools, at most the seats they cover).
    n = persona.headcount if headcount is None else headcount
    held_eng = [t for t in third_party
                if (not t.persona_ids or persona_id in t.persona_ids) and t.delivered_outcome_ids]

    def _retired(cover: set[str]) -> list:
        return [t for t in held_eng if t.delivered_outcome_ids <= cover]

    def _credit(tools) -> Decimal:
        return sum((Decimal(min(n, t.covered_count)) * t.per_unit_annual_cost for t in tools),
                   Decimal("0"))

    # Compose each base bundle with the cheapest add-ons that close its gaps. An
    # add-on is applicable to a base when it is eligible for it — à-la-carte add-ons
    # (no eligibility rows) apply to any base; otherwise the base must be in the
    # add-on's AddonEligibility set (e.g. E5 Security → E3). `composition[name]`
    # carries the chosen add-ons back to the UI (and the "Use" apply).
    elig_map = bundles.eligibility_map(db)
    candidates = []
    composition: dict[str, dict] = {}
    kept_tools: dict[str, list[str]] = {}

    def _retiring_addons(cover: set[str], taken: list[str], base_id: str) -> list[dict]:
        """The priced add-ons worth adding to retire tools this composition keeps:
        the set whose tool credit most exceeds its cost (none when nothing pays)."""
        missing = set().union(*[t.delivered_outcome_ids for t in held_eng
                                if not t.delivered_outcome_ids <= cover]) - cover
        opts = []
        for a in addon_bundles:
            if a.id in taken or not bundles.addon_applies(a.id, base_id, elig_map):
                continue
            helps = frozenset(sku_outcomes.get(a.id, set())) & missing
            if helps and _price(a) > 0:
                opts.append({"bundle": a, "cover": helps, "price": _price(a)})
        opts = sorted(opts, key=lambda o: o["price"])[:10]  # exhaustive, kept small
        base_credit = _credit(_retired(cover))
        best: tuple[Decimal, Decimal, list[dict]] = (Decimal("0"), Decimal("0"), [])
        for mask in range(1, 1 << len(opts)):
            pick = [opts[i] for i in range(len(opts)) if mask & (1 << i)]
            cost = sum((o["price"] for o in pick), Decimal("0"))
            c = set(cover).union(*[sku_outcomes.get(o["bundle"].id, set()) for o in pick])
            gain = _credit(_retired(c)) - base_credit - Decimal(n) * cost
            if gain > best[0] or (gain == best[0] and gain > 0 and cost < best[1]):
                best = (gain, cost, pick)
        return best[2]
    for base in base_bundles:
        base_cover = set(sku_outcomes.get(base.id, set()))
        base_price = _price(base)
        gaps = frozenset(required - base_cover)
        options = []
        for a in addon_bundles:
            if not bundles.addon_applies(a.id, base.id, elig_map):
                continue
            cover = frozenset(sku_outcomes.get(a.id, set())) & gaps
            # Only add-ons that close a real gap are worth composing, and only priced
            # ones: an unpriced add-on would join at $0 and make the plan read as
            # cheaper than anything the customer can buy.
            if cover and _price(a) > 0:
                options.append({"bundle": a, "cover": cover, "price": _price(a)})
        closeable = frozenset().union(*[o["cover"] for o in options]) if options else frozenset()
        # An unpriced base stays unpriced: composing priced add-ons onto it would
        # quote the add-ons alone as the plan's price (and could recommend it).
        chosen = _min_cost_cover(closeable, options) if base_price > 0 else []
        cover_now = set(base_cover).union(*[sku_outcomes.get(o["bundle"].id, set()) for o in chosen])
        if base_price > 0:
            chosen = chosen + _retiring_addons(
                cover_now, [o["bundle"].id for o in chosen], base.id)

        composed_cover = set(base_cover)
        addon_total = Decimal("0")
        for o in chosen:
            composed_cover |= sku_outcomes.get(o["bundle"].id, set())
            addon_total += o["price"]
        retired_ids = {t.id for t in _retired(composed_cover)}
        chosen_meta = []
        for o in chosen:
            # The tools this add-on is what retires: retired with it, not without it.
            without = set(base_cover).union(*[sku_outcomes.get(x["bundle"].id, set())
                                              for x in chosen if x is not o])
            retires = sorted(t.name for t in _retired(composed_cover)
                             if t.id not in {r.id for r in _retired(without)})
            chosen_meta.append({
                "bundle_id": o["bundle"].id,
                "name": o["bundle"].name,
                "unit_price_annual": float(o["price"]),
                "closes": [outcome_names.get(x, x) for x in sorted(o["cover"])],
                "retires": retires,
            })
        kept_tools[base.name] = sorted(t.name for t in held_eng if t.id not in retired_ids)
        candidates.append(
            CandidateBundle(
                sku_reference=base.name,  # the bundle name is what a scenario targets
                covered_outcome_ids=frozenset(composed_cover),
                target_unit_price_annual=base_price + addon_total,
            )
        )
        composition[base.name] = {
            "base_bundle_id": base.id,
            "base_price_annual": float(base_price),
            "addons": chosen_meta,
            "addon_total_annual": float(addon_total),
        }

    # Seat cap (D27): tell the optimizer how many seats each capped family (e.g.
    # M365 Business ≤ 300) has left in the future state of everyone else. With none
    # left the family isn't recommended; with too few, the recommendation stands and
    # the persona is offered a split (seat_cap.split_offer).
    cap_headroom_by_reference: dict[str, int] = {}
    interp = raw.interpreted(db, engagement_id)
    seat_caps = limits.seat_cap_context(db, engagement_id, exclude_persona_id=persona_id,
                                        interpreted=interp)
    capped_refs: set[str] = set()
    if exclude_capped:
        capped_refs = {ref for cap in seat_caps for ref in cap["member_references"]}
        candidates = [c for c in candidates if c.sku_reference not in capped_refs]
    else:
        for cap in seat_caps:
            for ref in cap["member_references"]:
                # If a reference is capped by more than one limit, the tightest wins.
                cap_headroom_by_reference[ref] = min(
                    cap["headroom"], cap_headroom_by_reference.get(ref, cap["headroom"])
                )

    if headcount is not None and persona.headcount:
        current_ms = current_ms * Decimal(n) / Decimal(persona.headcount)
    analyses = analyze_bundles(
        n, current_ms, frozenset(required),
        frozenset(current_capability), candidates, third_party,
        cap_headroom_by_reference=cap_headroom_by_reference,
        persona_id=persona.id,
    )

    # ---- Carve-out family (TARGET_SCHEMA D29) ---------------------------------
    def _tool_credit(tools, seats: int) -> Decimal:
        return sum((Decimal(min(seats, t.covered_count)) * t.per_unit_annual_cost for t in tools),
                   Decimal("0"))

    def _plan_fitting(pid: str, seats: int, req: set[str], held: list, want: set[str],
                      caps: dict[str, int], excluded: set[str]) -> dict | None:
        """The cheapest priced, gap-free, not-cap-limited plan for this member that
        retires exactly the tools in `want` (of those it holds): each base plus the
        cheapest add-ons covering its required gaps and every outcome of `want`."""
        need = set(req).union(*[t.delivered_outcome_ids for t in held if t.id in want])
        best = None
        for base in base_bundles:
            base_price = _price(base)
            headroom = caps.get(base.name)
            if base_price <= 0 or base.name in excluded or (headroom is not None and headroom <= 0):
                continue
            base_cover = set(sku_outcomes.get(base.id, set()))
            missing = frozenset(need - base_cover)
            opts = []
            for a in addon_bundles:
                if not bundles.addon_applies(a.id, base.id, elig_map):
                    continue
                helps = frozenset(sku_outcomes.get(a.id, set())) & missing
                if helps and _price(a) > 0:
                    opts.append({"bundle": a, "cover": helps, "price": _price(a)})
            if not missing <= frozenset().union(*[o["cover"] for o in opts]):
                continue
            chosen = _min_cost_cover(missing, opts)
            cover = base_cover.union(*[sku_outcomes.get(o["bundle"].id, set()) for o in chosen])
            retired = {t.id for t in held if t.delivered_outcome_ids <= cover}
            if retired != want:
                continue  # covers another tool incidentally: a different decision
            price = base_price + sum((o["price"] for o in chosen), Decimal("0"))
            cost = Decimal(seats) * price - _tool_credit([t for t in held if t.id in retired], seats)
            if best is None or (cost, price) < (best["cost"], best["price"]):
                best = {"base": base, "base_price": base_price, "addons": chosen, "price": price,
                        "cover": cover, "base_cover": base_cover, "retired": retired, "cost": cost}
        return best

    def _scenario_cover(pid: str) -> set[str] | None:
        """What a persona's chosen plan (base + add-ons) covers; None with no plan."""
        sc = db.execute(select(models.PersonaScenario).where(
            models.PersonaScenario.persona_id == pid)).scalars().first()
        if sc is None:
            return None
        cover = set(sku_outcomes.get(_cover_key(db, sc.target_sku_reference), set()))
        for addon in sc.addons:
            cover |= sku_outcomes.get(addon.bundle_id, set())
        return cover

    def _family_summary(members, retires: set[str], fallback: bool, basis: str) -> dict:
        held_ids = {t.id for m in members for t in third_party
                    if (not t.persona_ids or m.id in t.persona_ids) and t.delivered_outcome_ids}
        return {
            "basis": basis,  # "family": decided with the carve-outs; "split": follows its own plan
            "members": [{"id": m.id, "name": m.name, "headcount": m.headcount} for m in members],
            "retires": sorted(tp_names.get(i, i) for i in retires),
            "keeps": sorted(tp_names.get(i, i) for i in held_ids - retires),
            # No plan fits the shared decision for every member: each is on its own (D28).
            "fallback": fallback,
        }

    def _apply_family_plan(rows: list[dict], plan: dict) -> None:
        """Make `plan` this persona's recommendation, tagged as the family's choice;
        tag the persona's own cheaper plan when it decides a tool differently."""
        cand = CandidateBundle(sku_reference=plan["base"].name,
                               covered_outcome_ids=frozenset(plan["cover"]),
                               target_unit_price_annual=plan["price"])
        [b] = analyze_bundles(n, current_ms, frozenset(required), frozenset(current_capability),
                              [cand], third_party,
                              cap_headroom_by_reference=cap_headroom_by_reference,
                              persona_id=persona.id)
        addons = []
        for o in plan["addons"]:
            without = plan["base_cover"].union(*[sku_outcomes.get(x["bundle"].id, set())
                                                 for x in plan["addons"] if x is not o])
            addons.append({
                "bundle_id": o["bundle"].id, "name": o["bundle"].name,
                "unit_price_annual": float(o["price"]),
                "closes": [outcome_names.get(x, x) for x in sorted(o["cover"] & required)],
                "retires": sorted(t.name for t in _retired(plan["cover"])
                                  if t.id not in {r.id for r in _retired(without)}),
            })
        comp = {"base_bundle_id": plan["base"].id, "base_price_annual": float(plan["base_price"]),
                "addons": addons,
                "addon_total_annual": float(sum((o["price"] for o in plan["addons"]), Decimal("0")))}
        kept = sorted(t.name for t in held_eng if t.id not in plan["retired"])
        alone = next((r for r in rows if r["recommended"]), None)
        ids = sorted(a["bundle_id"] for a in addons)
        fam = next((r for r in rows if r["sku_reference"] == plan["base"].name
                    and sorted(a["bundle_id"] for a in r["addons"]) == ids), None)
        if fam is None:
            fam = _row(b, comp, kept)
            rows.append(fam)
        for r in rows:
            r["recommended"] = False
        fam["recommended"] = fam["family_choice"] = True
        if alone is not None and alone is not fam and alone["delta_annual"] < fam["delta_annual"]:
            alone["cheaper_alone"] = True
            alone["alone_saves_annual"] = round(fam["delta_annual"] - alone["delta_annual"], 2)
        rows.sort(key=lambda r: (not r["family_choice"], not r["cheaper_alone"]))

    def _family_decision(rows: list[dict]) -> dict | None:
        """D29: the family's tools retire for every member or none, whichever costs
        the family less (ties retire more); this persona gets its plan under it."""
        root = persona if persona.parent_persona_id is None else db.get(models.Persona, persona.parent_persona_id)
        if root is None:
            return None
        members = [root] + [p for p in eng.personas if p.parent_persona_id == root.id]
        if len(members) < 2:
            return None
        ctx = {}
        for m in members:
            if m.id == persona.id:
                ctx[m.id] = (n, required, held_eng, cap_headroom_by_reference)
                continue
            lines = [l for l in eng.licenses_in_scope if not l.persona_ids or m.id in l.persona_ids]
            req = set().union(*[readings[l.id].outcomes for l in lines])
            req |= {oid for oid in m.required_outcome_ids if oid in outcome_names}
            held = [t for t in third_party
                    if (not t.persona_ids or m.id in t.persona_ids) and t.delivered_outcome_ids]
            caps: dict[str, int] = {}
            for cap in limits.seat_cap_context(db, engagement_id, exclude_persona_id=m.id,
                                               interpreted=interp):
                for ref in cap["member_references"]:
                    caps[ref] = min(cap["headroom"], caps.get(ref, cap["headroom"]))
            ctx[m.id] = (m.headcount, req, held, caps)
        # Only a tool some priced plan could cover can retire; the rest are kept.
        sellable = set().union(*[sku_outcomes.get(b.id, set()) for b in base_bundles + addon_bundles
                                 if _price(b) > 0])
        tools = sorted({t.id for c in ctx.values() for t in c[2] if t.delivered_outcome_ids <= sellable})
        best = None
        if len(tools) <= 12:  # exhaustive; a family rarely holds more than a few tools
            for mask in range(1 << len(tools)):
                retire = {tools[i] for i in range(len(tools)) if mask & (1 << i)}
                plans, total = {}, Decimal("0")
                for m in members:
                    seats, req, held, caps = ctx[m.id]
                    plan = _plan_fitting(m.id, seats, req, held, retire & {t.id for t in held},
                                         caps, capped_refs)
                    if plan is None:
                        break
                    plans[m.id], total = plan, total + plan["cost"]
                else:
                    if best is None or (total, -len(retire)) < (best[0], -len(best[1])):
                        best = (total, retire, plans)
        if best is None:
            return _family_summary(members, set(), True, "family")
        _apply_family_plan(rows, best[2][persona.id])
        return _family_summary(members, best[1], False, "family")

    def names(ids):
        return [outcome_names.get(i, i) for i in ids]

    def positioning(b) -> str:
        """The value story to lead with for this bundle. Cost-change convention:
        delta < 0 saves money, delta > 0 costs more."""
        saves = b.delta_annual < 0
        higher = b.delta_annual > 0
        added = bool(b.added_outcome_ids)
        if saves and added:
            return "Lower TCO + new capabilities"
        if saves:
            return "Lower TCO"
        if added:
            return "New capabilities + integrated ecosystem"
        if higher:
            return "Higher cost — consider reimagining required outcomes"
        return "Cost-neutral"

    def _row(b, comp: dict | None = None, kept: list[str] | None = None) -> dict:
        comp = composition.get(b.sku_reference, {}) if comp is None else comp
        return {
            "sku_reference": b.sku_reference,
            "base_price_annual": comp.get("base_price_annual", 0.0),
            "addons": comp.get("addons", []),
            "addon_total_annual": comp.get("addon_total_annual", 0.0),
            "target_unit_price_annual": float(b.target_unit_price_annual),
            "target_spend_annual": float(b.target_spend_annual),
            "current_spend_annual": float(b.current_spend_annual),
            "delta_annual": float(b.delta_annual),
            "third_party_offset_annual": float(b.third_party_offset_annual),
            "covered_required_outcomes": names(b.covered_required_outcome_ids),
            "gap_outcomes": names(b.gap_outcome_ids),
            "added_outcomes": names(b.added_outcome_ids),
            "displaced_products": [tp_names.get(i, i) for i in b.displaced_product_ids],
            # This persona's tools the option leaves in place (they keep costing).
            "kept_products": kept_tools.get(b.sku_reference, []) if kept is None else kept,
            "covers_all_required": b.covers_all_required,
            "price_known": b.price_known,
            "recommended": b.recommended,
            "cap_limited": b.cap_limited,
            "cap_headroom": b.cap_headroom,
            "over_cap_by": b.over_cap_by,
            "positioning": positioning(b),
            # D29: the family's shared tool decision gives this plan; and this
            # persona's own cheaper plan, which decides a tool differently.
            "family_choice": False,
            "cheaper_alone": False,
            "alone_saves_annual": 0.0,
        }

    rows = [_row(b) for b in analyses]
    family = None
    if follow_own_plan:
        own = _scenario_cover(persona.id)
        if own is not None:
            want = {t.id for t in held_eng if t.delivered_outcome_ids <= own}
            plan = _plan_fitting(persona.id, n, required, held_eng, want,
                                 cap_headroom_by_reference, capped_refs)
            family = _family_summary([persona], want, plan is None, "split")
            if plan:
                _apply_family_plan(rows, plan)
    else:
        family = _family_decision(rows)

    return {
        "persona_id": persona.id,
        "persona_name": persona.name,
        "headcount": n,
        "current_microsoft_annual": float(current_ms),
        # Each with why it is required: "licence" and/or "requirement".
        "required_outcomes": [
            {"id": i, "name": outcome_names.get(i, i), "sources": sources.get(i, [])}
            for i in sorted(required, key=lambda x: outcome_names.get(x, x))
        ],
        "bundles": rows,
        # The carve-out family's shared tool decision (D29); None outside a family.
        "family": family,
        # Seat-cap context: how many capped-family seats the rest of the future state
        # uses and how many are left for this persona.
        "seat_caps": [
            {
                "key": c["key"], "name": c["name"], "cap": c["cap"], "consumed": c["consumed"],
                "headroom": c["headroom"], "member_bundle_names": c["member_bundle_names"],
                # The people beyond the seats left, if this persona took a plan under
                # the cap — what a split would move (0 when it fits or none are left).
                "over_by": max(0, n - c["headroom"]) if c["headroom"] > 0 else 0,
            }
            for c in seat_caps
        ],
    }


def test_recommender_is_identical_through_the_interpreter(client):
    """Every persona's best-bundle analysis — and the split's "people over the cap"
    variant — equals the recommender as it was, for every engagement the suite built."""
    from app import models
    from app.db import SessionLocal
    from app.services import compute

    checked = 0
    with SessionLocal() as db:
        for eid in _all_engagement_ids():
            for p in db.get(models.Engagement, eid).personas:
                assert compute.analyze_persona_bundles(db, eid, p.id) == \
                    _legacy_analyze_persona_bundles(db, eid, p.id), (eid, p.id)
                half = max(1, (p.headcount or 0) // 2)
                assert compute.analyze_persona_bundles(
                    db, eid, p.id, headcount=half, exclude_capped=True, follow_own_plan=True) == \
                    _legacy_analyze_persona_bundles(
                        db, eid, p.id, headcount=half, exclude_capped=True, follow_own_plan=True), (eid, p.id)
                checked += 1
    assert checked > 0


def test_licence_readings_are_identical_through_the_interpreter(client):
    """How each licence line is read (state, outcomes, the plan it reads as) — out of
    scope lines included — equals compute.licence_readings, which the unknown-licence
    card, library updates and carve-out drift read until they move to the interpreter."""
    from app import models
    from app.db import SessionLocal
    from app.services import compute, raw

    with SessionLocal() as db:
        for eid in _all_engagement_ids():
            eng = db.get(models.Engagement, eid)
            old = compute.licence_readings(db, eng)
            i = raw.interpreted(db, eid)
            for line in i.licence_lines:
                o = old[line.id]
                assert (line.read_state, line.outcomes, line.bundle_id) == \
                    (o.state, o.outcomes, o.bundle_id), (eid, line.id)



# Frozen reference: the split offer before it read the interpreter's finding.

def _legacy_split_offer(db, engagement_id, persona_id):
    from sqlalchemy import select as _select
    from app import models
    from app.services import bundles, compute, limits
    from app.services.seat_cap import _scenario, capped_name, over_name
    select = _select
    """The split offer for one persona, or None when its plan fits (or isn't capped).

    `kind` is "split" (ready to confirm), "no_seats" (nothing left to split: choose
    an uncapped plan), "nested" (a carve-out can't be split again) or
    "no_alternative" (no priced uncapped plan covers what these people need)."""
    persona = db.get(models.Persona, persona_id)
    if persona is None or persona.engagement_id != engagement_id:
        return None
    s = _scenario(db, persona_id)
    if s is None or not s.in_scope or not s.target_sku_reference:
        return None
    plan_ids = {bundles.resolve_bundle(db, s.target_sku_reference)} | {a.bundle_id for a in s.addons}
    # The tightest cap the plan (base or any add-on) falls under decides. Caps are
    # matched by their member bundle ids, never by a display name.
    hit = None
    for ctx in limits.seat_cap_context(db, engagement_id, exclude_persona_id=persona_id):
        if set(ctx["member_bundle_ids"]) & plan_ids:
            if hit is None or ctx["headroom"] < hit["headroom"]:
                hit = ctx
    headcount = persona.headcount or 0
    if hit is None or headcount <= hit["headroom"]:
        return None

    seats_left = hit["headroom"]
    other_seats = headcount - seats_left
    offer = {
        "persona_id": persona.id, "persona_name": persona.name, "headcount": headcount,
        "plan": s.target_sku_reference, "cap_name": hit["name"], "cap": hit["cap"],
        "used_elsewhere": hit["consumed"], "seats_left": seats_left,
        "capped_seats": seats_left, "other_seats": other_seats,
        "other_plan": None, "other_addons": [], "other_price_annual": None,
        "parent_side": "capped" if seats_left >= other_seats else "other",
        "capped_name": capped_name(persona.name, hit["cap"]),
        "over_name": over_name(persona.name),
    }
    if seats_left <= 0:
        return {**offer, "kind": "no_seats"}
    if persona.parent_persona_id is not None:
        return {**offer, "kind": "nested"}
    # The best plan for the people over the cap, with every capped plan removed,
    # deciding each tool the way the capped side's plan does (D29).
    rest = compute.analyze_persona_bundles(
        db, engagement_id, persona_id, headcount=other_seats, exclude_capped=True,
        follow_own_plan=True)
    best = next((b for b in rest["bundles"] if b["recommended"]), None)
    if best is None:
        return {**offer, "kind": "no_alternative"}
    offer.update(
        other_plan=best["sku_reference"],
        other_addons=[{"bundle_id": a["bundle_id"], "name": a.get("name", ""),
                       "unit_price_annual": a["unit_price_annual"]} for a in best["addons"]],
        other_price_annual=best["base_price_annual"],
    )
    # The carve-out is the smaller side; the parent keeps its own name (code never
    # renames what a person typed).
    offer["carve_out_name"] = offer["over_name"] if offer["parent_side"] == "capped" else offer["capped_name"]
    return {**offer, "kind": "split"}


def test_split_offers_are_identical_through_the_interpreter(client):
    from app import models
    from app.db import SessionLocal
    from app.services import seat_cap

    with SessionLocal() as db:
        for eid in _all_engagement_ids():
            for p in db.get(models.Engagement, eid).personas:
                assert seat_cap.split_offer(db, eid, p.id) == _legacy_split_offer(db, eid, p.id), (eid, p.id)



def test_readout_reads_the_interpreter_as_it_read_raw_rows(client):
    """The Readout's data choices — each tool's uses, what each persona holds today
    (tools with no cost, users or uses left out), each licence's prices — equal the
    expressions it used on raw rows before, for every engagement the suite built."""
    from datetime import date

    from app import models
    from app.db import SessionLocal
    from app.services import branding, raw, readout

    with SessionLocal() as db:
        for eid in _all_engagement_ids():
            eng = db.get(models.Engagement, eid)
            ctx = readout._Context(eng, {}, [], branding.resolved(None), date.today(),
                                   raw.interpreted(db, eid))
            outcome_name = {o.id: o.name for o in eng.outcomes}
            uses: dict[str, list[str]] = {}
            for ce in eng.coverage_entries:
                if ce.product_kind == "ThirdParty" and ce.ratified and ce.third_party_product_id:
                    name = outcome_name.get(ce.outcome_id)
                    if name:
                        uses.setdefault(ce.third_party_product_id, []).append(name)
            assert ctx.tool_uses == {k: sorted(v) for k, v in uses.items()}, eid
            for p in eng.personas:
                lines = [l for l in eng.licenses_in_scope
                         if (p.id in l.persona_ids if l.persona_ids else True)]
                tools = [t for t in eng.third_party_products
                         if float(t.raw_cost or 0) > 0 and (t.covered_count or 0) > 0 and uses.get(t.id)
                         and (not t.persona_ids or p.id in t.persona_ids)]
                refs, got = ctx.today_of(p.id)
                assert refs == {l.sku_reference for l in lines if l.sku_reference}, (eid, p.id)
                assert [t.id for t in got] == [t.id for t in tools], (eid, p.id)
            for lic in eng.licenses_in_scope:
                assert ctx.licence_prices(lic.id) == (float(lic.effective_unit_price_annual),
                                                      float(lic.list_unit_price_annual)), (eid, lic.id)


def test_readout_views_equal_what_each_renderer_added_up(client):
    """The totals and groupings now computed once (services/results.views) equal
    the sums the Summary screen and the HTML readout each did themselves."""
    from app.db import SessionLocal
    from app.services import results

    with SessionLocal() as db:
        for eid in _all_engagement_ids():
            r = results.current_result(db, eid)
            v = r["views"]
            assert v["new_outcome_count"] == len(
                {o["id"] for n in r["new_outcomes"] for o in n.get("outcomes") or []}), eid
            annual, timed = {}, {}
            for i in r["rollup"]["headline"]["items"]:
                if i.get("sub_line") == "consolidation" and i.get("persona_id"):
                    annual[i["persona_id"]] = annual.get(i["persona_id"], 0.0) + float(i["annual_amount"])
                    timed[i["persona_id"]] = timed.get(i["persona_id"], 0.0) + float(i["amount"])
            assert {k: c["annual"] for k, c in v["consolidation_by_persona"].items()} == annual, eid
            assert {k: c["timed"] for k, c in v["consolidation_by_persona"].items()} == timed, eid
            for q in r["rollup"]["quick_wins"]:
                by = {}
                for c in q.get("covered_by") or []:
                    by[c["persona_name"]] = by.get(c["persona_name"], 0) + c["seats"]
                got = v["quick_win_who"][q["third_party_product_id"]]
                assert {w["persona_name"]: w["seats"] for w in got} == by, eid
            in_scope = [s for s in r["scenarios"] if s["in_scope"]]
            freed = r["rollup"]["freed_third_party"]
            key = {"redundant_today_annual": "redundant_today_annual",
                   "move_unlocked_annual": "move_unlocked_annual",
                   "credited_annual": "credited_offset_annual"}
            for g in v["bridge"]:
                already = g["key"] != "replaced"
                items = [f for f in freed if bool(f["already_covered"]) == already and float(f[g["field"]] or 0)]
                assert abs(g["total"] - sum(float(f[g["field"]]) for f in items)) < 1e-6, eid
                for s in in_scope:
                    old = sum(next((float(o[key[g["field"]]] or 0) for o in s["offsets"]
                                    if o["third_party_product_id"] == f["third_party_product_id"]), 0.0)
                              for f in items)
                    assert abs(g["by_scenario"][s["scenario_id"]] - old) < 1e-6, eid
