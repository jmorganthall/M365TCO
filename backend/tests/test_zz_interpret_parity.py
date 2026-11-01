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
