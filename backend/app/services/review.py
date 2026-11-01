"""The walkthrough's Review step (docs/WALKTHROUGH.md §5–§6): every check that
works without AI, plus the list of what is being LEFT OUT because it wasn't
answered — so the AE can ask before producing the PDF.

The checks are the interpreter's findings (tco_engine.interpret `findings`,
TARGET_SCHEMA §6.4); this module only words them. A pure read (viewing never
writes). Each check names the step where it is fixed.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


from .. import models
from . import library_updates, raw, results

STEP_ORDER = ["customer", "groups", "tools", "future", "gaps", "review", "summary"]


def _plural_cap(n: int) -> tuple[str, str]:
    return ("y" if n == 1 else "ies"), ("is" if n == 1 else "are")


def review(db: Session, engagement_id: str) -> dict:
    """The interpreter's findings (tco_engine.interpret, TARGET_SCHEMA §6.4), worded,
    plus the two that come from outside it: a partly replaced tool with no
    keep-or-retire answer (an engine result) and pending library updates."""
    eng = db.get(models.Engagement, engagement_id)
    i = raw.interpreted(db, engagement_id)
    outcome_name = {o.id: o.name for o in eng.outcomes}
    checks: list[dict] = []
    left_out: list[dict] = []

    subject: dict = {}   # what the finding being worded is about (persona, licence, tool)

    def check(code, step, message, severity="warn"):
        checks.append({"code": code, "step": step, "severity": severity, "message": message,
                       **subject})

    def leave_out(what, why, step):
        left_out.append({"what": what, "why": why, "step": step})

    def residual():
        result = results.engine_result(db, engagement_id)
        for d in result.dispositions:
            if d.requires_residual_classification:
                check("residual_unanswered", "future",
                      f"{d.third_party_product_name} is only partly replaced. Keep it for the remaining "
                      f"users, or retire it?")
                leave_out(f"Retiring the rest of {d.third_party_product_name}",
                          "not yet decided — assumed kept", "future")

    after_future = {"license_unread", "capability_changes_unknown", "target_unmapped",
                    "capability_dropped", "gap_unanswered"}
    residual_done = False
    for f in i.findings:
        x = f.facts
        if f.code in after_future and not residual_done:
            subject = {}
            residual(); residual_done = True
        subject = {"persona_id": f.persona_id, "licence_id": f.licence_id, "tool_id": f.tool_id,
                   "persona_ids": list(x.get("persona_ids") or ())}
        if f.code == "no_groups":
            check(f.code, f.step, "No personas yet. Add the personas who get different licences.")
        elif f.code == "headcount_vs_employees":
            check(f.code, f.step,
                  f"The personas add up to {x['total']:,} people; the customer has {x['employees']:,} "
                  f"employees. Is a persona missing, or is the employee count out of date?", "info")
        elif f.code == "licence_untagged":
            check(f.code, f.step,
                  f"{x['name']} isn't assigned to any persona, so it is counted across every persona. "
                  f"Say which personas get it.")
        elif f.code == "seats_vs_headcount":
            check(f.code, f.step,
                  f"{x['name']}: {x['assigned']:,} seats are assigned, but the personas that get "
                  f"it have {x['headcount']:,} people. Adjust the seats, or split the persona if only some of "
                  f"them hold it.", "info")
        elif f.code == "unused_seats_unanswered":
            check(f.code, f.step,
                  f"{x['name']}: {x['unused']:,} seats are paid for but not assigned. Kept on purpose, or not needed?")
            leave_out(f"{x['unused']:,} unused {x['name']} seats",
                      "not yet answered whether they're needed", "groups")
        elif f.code == "renewal_date_missing" and x["subject"] == "agreement":
            check(f.code, f.step,
                  "The Microsoft agreement has no renewal date, so it is treated as month-to-month: "
                  "Microsoft reductions count from today.", "info")
        elif f.code == "license_out_of_scope":
            check(f.code, f.step,
                  f"Set aside as out of scope for this workshop: {', '.join(x['names'])}. "
                  f"They are in no number; the PDF lists them.", "info")
        elif f.code == "tool_incomplete":
            check(f.code, f.step,
                  f"{x['name']} has {', '.join(x['missing'])}, so it is left out of the numbers.")
            leave_out(x["name"], ", ".join(x["missing"]), "tools")
        elif f.code == "renewal_date_missing":
            check(f.code, f.step,
                  f"{x['name']}: no renewal date, so it is treated as month-to-month and its saving "
                  f"counts from today.", "info")
        elif f.code == "plan_duplicated":
            check(f.code, f.step,
                  f"{x['name']} has {x['count']} future plans; each is costed, so the totals count "
                  f"these people more than once. Remove the extra plans on Future state.")
        elif f.code == "group_without_plan":
            check(f.code, f.step, f"{x['name']} has no future plan yet, so it isn't in the totals.")
        elif f.code == "seat_cap_exceeded":
            if x["kind"] == "no_seats":
                msg = (f"{x['persona_name']}: {x['headcount']:,} people on {x['plan']}, but {x['cap_name']} has no "
                       f"seats left ({x['used_elsewhere']:,} of {x['cap']:,} are used by other personas). "
                       f"Choose another plan on Future state.")
            elif x["kind"] == "nested":
                msg = (f"{x['persona_name']}: {x['headcount']:,} people on {x['plan']}, but {x['cap_name']} "
                       f"has only {x['seats_left']:,} seats left. Reduce this carve-out on Future state.")
            else:
                msg = (f"{x['persona_name']}: {x['headcount']:,} people on {x['plan']}, but {x['cap_name']} "
                       f"has only {x['seats_left']:,} seats left. Split it on Future state.")
            check(f.code, f.step, msg)
        elif f.code == "license_unread":
            check(f.code, f.step,
                  f"What {x['name']} includes isn't known yet ({', '.join(x['personas'])}). Say it's the same as a "
                  f"library plan, tick what it delivers, or set it aside as out of scope.")
        elif f.code == "capability_changes_unknown":
            leave_out(f"Capability changes for {x['persona_name']}",
                      f"what {', '.join(x['names'])} includes isn't answered", "tools")
        elif f.code == "target_unmapped":
            check(f.code, f.step,
                  f"{x['persona_name']}: the future plan isn't recognised, so its capabilities can't be compared.")
        elif f.code == "capability_dropped":
            names = ", ".join(outcome_name.get(o, o) for o in f.outcome_ids)
            check(f.code, f.step,
                  f"{x['persona_name']}'s future plan drops something it has today: {names}. Confirm that's intended.",
                  "info")
        elif f.code == "gap_unanswered":
            n = x["count"]
            y, verb = _plural_cap(n)
            check(f.code, f.step,
                  f"{x['persona_name']}: {n} capabilit{y} the plan adds {verb} not yet confirmed as missing today.")
            leave_out(f"{n} new capabilit{y} for {x['persona_name']}",
                      "not yet confirmed as missing today", "gaps")
    subject = {}
    if not residual_done:
        residual()

    # ---- Library updates (TARGET_SCHEMA §4.7, §6.4) ---------------------------
    waiting = library_updates.pending(db, eng)
    if waiting:
        n = len(waiting)
        check("library_update_pending", "gaps",
              f"{n} library update{'s are' if n > 1 else ' is'} waiting for this engagement "
              f"(the shared library changed after it was created). Apply or decline "
              f"{'them' if n > 1 else 'it'} on the Coverage check.", "info")

    rank = {s: k for k, s in enumerate(STEP_ORDER)}
    # A licence nobody can read blanks whole groups' capability story: it leads.
    checks.sort(key=lambda c: (c["code"] != "license_unread", rank.get(c["step"], 99),
                               c["severity"] != "warn"))
    left_out.sort(key=lambda x: rank.get(x["step"], 99))
    return {"checks": checks, "left_out": left_out}
