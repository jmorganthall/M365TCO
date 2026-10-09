"""The walkthrough's Review step (docs/WALKTHROUGH.md §5–§6): every check that
works without AI, plus the list of what is being LEFT OUT because it wasn't
answered — so the AE can ask before producing the PDF.

A pure read: it hydrates and runs the engine without persisting anything
(viewing never writes). Each check names the step where it is fixed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tco_engine import compute as engine_compute

from .. import models
from . import compute

STEP_ORDER = ["customer", "groups", "tools", "future", "gaps", "review", "summary"]


def review(db: Session, engagement_id: str) -> dict:
    eng = db.get(models.Engagement, engagement_id)
    checks: list[dict] = []
    left_out: list[dict] = []

    def check(code, step, message, severity="warn"):
        checks.append({"code": code, "step": step, "severity": severity, "message": message})

    def leave_out(what, why, step):
        left_out.append({"what": what, "why": why, "step": step})

    personas = {p.id: p for p in eng.personas}
    total_hc = sum(p.headcount or 0 for p in eng.personas)

    # ---- Groups & licences -------------------------------------------------
    if not eng.personas:
        check("no_groups", "groups", "No groups yet. Add the groups of people who get different licences.")
    if eng.employee_count and total_hc and total_hc != eng.employee_count:
        check("headcount_vs_employees", "groups",
              f"The groups add up to {total_hc:,} people; the customer has {eng.employee_count:,} "
              f"employees. Is a group missing, or is the employee count out of date?", "info")

    for lic in eng.licenses_in_scope:
        name = lic.sku_reference or "A licence line"
        tagged = [personas[pid] for pid in lic.persona_ids if pid in personas]
        if not lic.persona_ids:
            check("licence_untagged", "groups",
                  f"{name} isn't assigned to any group, so it is counted across every group. "
                  f"Say which groups get it.")
        elif lic.coverage_scope != "TenantWide":
            hc = sum(p.headcount or 0 for p in tagged)
            if lic.quantity_assigned != hc:
                check("seats_vs_headcount", "groups",
                      f"{name}: {lic.quantity_assigned:,} seats are assigned, but the groups that get "
                      f"it have {hc:,} people. Adjust the seats, or split the group if only some of "
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
    result = engine_compute(compute.hydrate(db, engagement_id))
    with_scenario = {s.persona_id for s in eng.scenarios}
    for p in eng.personas:
        if p.id not in with_scenario:
            check("group_without_plan", "future",
                  f"{p.name} has no future plan yet, so it isn't in the totals.")
    for d in result.dispositions:
        if d.requires_residual_classification:
            check("residual_unanswered", "future",
                  f"{d.third_party_product_name} is only partly replaced. Keep it for the remaining "
                  f"users, or retire it?")
            leave_out(f"Retiring the rest of {d.third_party_product_name}",
                      "not yet decided — assumed kept", "future")

    # ---- Capability comparison (shared with the Coverage Check) -------------
    for g in compute.persona_coverage_gaps(db, engagement_id):
        name = g["persona_name"]
        if g["unmapped_current_licenses"]:
            refs = ", ".join(u["sku_reference"] for u in g["unmapped_current_licenses"])
            check("licence_unmapped", "groups",
                  f"{name}: {refs} isn't recognised, so what it delivers can't be compared.")
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

    rank = {s: i for i, s in enumerate(STEP_ORDER)}
    checks.sort(key=lambda c: (rank.get(c["step"], 99), c["severity"] != "warn"))
    left_out.sort(key=lambda x: rank.get(x["step"], 99))
    return {"checks": checks, "left_out": left_out}
