"""Permutation sweep over the two decision surfaces above the engine: the
Business Premium swap and the recommend-a-path optimizer.

`tests/sweep.py` does this for the pure engine. These two surfaces read the same
first-class data, then decide something — swap this persona, recommend this
bundle — and report money for that decision. So the invariants here are about
**agreement and follow-through**: a number a user acts on must be the number they
get once they act, and a feature that claims to be doing something must actually
do it.

    optimizer   "this bundle saves $X"  →  applying it must save $X. Otherwise
                recommend-a-path recommends a path to a number that evaporates.
    carve-out   "move 300 of these 2,518 people onto another plan"  →  carving
                must MOVE seats, never mint or lose them. It changes the future
                state only: the population, what the customer pays today, and what
                their tools cover are all identical either side of a carve, and
                deleting the carve-out puts the seats back. (This replaced the
                automatic Business Premium swap, which moved whole personas under
                a 300-seat cap and so could never fire on a persona over 300.)

Cases are built through the HTTP API — the same surface the GUI uses — so a
violation here is a violation a user can hit. Run the full space directly:

    cd backend && python -m tests.sweep_services
    cd backend && python -m tests.sweep_services --level ci
"""

from __future__ import annotations

import sys
from decimal import Decimal

D = Decimal

# A minimal priced catalog: Business Premium is the cheap plan a carve-out moves
# people onto, the E-plans progressively dearer, as in the real sheet.
#
# The sheet's listed price is the price for its TermDuration, so a P1Y row lists
# the ANNUAL per-seat figure (services/pricesheet._annualize) — not the monthly
# one. Listing monthly numbers here would price Business Premium at $22/year and
# make every comparison in this sweep meaningless.
CATALOG_CSV = """ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,Currency,UnitPrice,ERP Price,Segment,EffectiveStartDate,EffectiveEndDate,LastUpdatedDate
Microsoft 365 Business Premium,P1,S1,Microsoft 365 Business Premium,P1Y,Monthly,US,USD,211.20,264.00,Commercial,2026-01-01,,2026-01-01
Microsoft 365 E3,P2,S1,Microsoft 365 E3,P1Y,Monthly,US,USD,393.12,491.40,Commercial,2026-01-01,,2026-01-01
Microsoft 365 E5,P3,S1,Microsoft 365 E5,P1Y,Monthly,US,USD,547.20,684.00,Commercial,2026-01-01,,2026-01-01
Office 365 E3,P4,S1,Office 365 E3,P1Y,Monthly,US,USD,262.08,327.60,Commercial,2026-01-01,,2026-01-01
Office 365 E1,P5,S1,Office 365 E1,P1Y,Monthly,US,USD,100.80,126.00,Commercial,2026-01-01,,2026-01-01
"""


# Business Premium's annual ERP in the catalog above (22.00/mo x 12) — what a
# carve-out onto Business Premium must be priced at.
BP_ANNUAL = D("264")


def load_catalog(client) -> None:
    client.post("/api/catalog/import-csv",
                files={"file": ("catalog.csv", CATALOG_CSV, "text/csv")})


# --------------------------------------------------------------------------
# Case generation
# --------------------------------------------------------------------------

def build_case(client, *, headcounts, tool_covered, tool_personas, tool_outcome,
               target_sku, cap_on):
    """One engagement through the public API. Returns its dict shape."""
    eid = client.post("/api/engagements", json={"customer_name": "Sweep"}).json()["id"]
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    # Give the first persona a declared requirement, so a carve-out that failed to
    # inherit requirements (and would then be recommended a plan that drops a
    # needed capability) is actually reachable by the sweep.
    desktop = next((o["id"] for o in outcomes if o["seed_key"] == "desktop-software"), None)
    people = [
        client.post(f"/api/engagements/{eid}/personas", json={
            "name": f"P{i + 1}", "headcount": hc,
            "required_outcome_ids": [desktop] if (i == 0 and desktop) else [],
        }).json()
        for i, hc in enumerate(headcounts)
    ]
    for p in people:
        client.post(f"/api/engagements/{eid}/current-licenses", json={
            "sku_reference": "Office 365 E3", "quantity_purchased": p["headcount"],
            "quantity_assigned": p["headcount"], "unit_price_paid_annual": 327.60,
            "persona_ids": [p["id"]]})

    tool = None
    if tool_covered:
        tool = client.post(f"/api/engagements/{eid}/third-party", json={
            "name": "Tool", "raw_cost": 30000, "cost_period": "Annual",
            "covered_count_override": tool_covered,
            "persona_ids": [people[i]["id"] for i in tool_personas]}).json()
        out = next((o for o in outcomes if o["seed_key"] == tool_outcome), None)
        if out:
            client.post(f"/api/engagements/{eid}/coverage", json={
                "outcome_id": out["id"], "product_kind": "ThirdParty",
                "third_party_product_id": tool["id"], "coverage": "Full",
                "ratified": True})

    scenarios = [
        client.post(f"/api/engagements/{eid}/scenarios", json={
            "persona_id": p["id"], "target_sku_reference": target_sku,
            "target_unit_price_annual": 491.40, "in_scope": True}).json()
        for p in people
    ]
    client.patch(f"/api/engagements/{eid}", json={"business_cap_enabled": cap_on})
    return {"eid": eid, "personas": people, "tool": tool, "scenarios": scenarios}


def iter_cases(client, level: str = "full"):
    headcount_sets = (
        # The single-persona case matters in CI too: it is the one where the
        # optimizer's claim can be checked against the engine directly.
        [(250,), (200, 60), (2518, 632)] if level == "ci"
        else [(200,), (250,), (400,), (200, 60), (400, 250), (2518, 632), (50, 30, 20)]
    )
    # (covered_count, personas the tool is tagged to) — including a tool tagged to
    # a persona OTHER than the one being analyzed, and a tool covering far fewer
    # seats than the persona has.
    tool_shapes = (
        [(0, ()), (100, (0,)), (100, (1,))] if level == "ci"
        else [(0, ()), (50, (0,)), (100, (0,)), (100, (1,)), (500, (0,)), (100, (0, 1))]
    )
    outcomes = ("identity-sso",) if level == "ci" else ("identity-sso", "endpoint-epp")
    targets = ("Microsoft 365 E5",) if level == "ci" else ("Microsoft 365 E3", "Microsoft 365 E5")

    for hcs in headcount_sets:
        for covered, tool_pids in tool_shapes:
            if tool_pids and max(tool_pids) >= len(hcs):
                continue
            for outcome in outcomes:
                for target in targets:
                    for cap_on in (False, True):
                        label = (
                            f"hc={hcs} tool=({covered},{tool_pids}) out={outcome} "
                            f"target={target} cap={cap_on}"
                        )
                        yield label, build_case(
                            client, headcounts=hcs, tool_covered=covered,
                            tool_personas=tool_pids, tool_outcome=outcome,
                            target_sku=target, cap_on=cap_on)


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------

def check_optimizer(client, case) -> list[str]:
    """Recommend-a-path must report money the engine will reproduce."""
    eid = case["eid"]
    persona = case["personas"][0]
    tool = case["tool"]
    bad: list[str] = []

    analysis = client.post(
        f"/api/engagements/{eid}/personas/{persona['id']}/bundle-analysis").json()

    for b in analysis["bundles"]:
        offset = D(str(b["third_party_offset_annual"]))
        if not b["displaced_products"]:
            if offset != 0:
                bad.append(f"opt-offset-phantom: {b['sku_reference']} credits {offset} "
                           f"with nothing displaced")
            continue
        if tool is None:
            continue

        # 1. You cannot save more on a tool than the tool costs.
        cost = D(str(tool["effective_annual_cost"]))
        if offset > cost:
            bad.append(
                f"opt-offset-cap: {b['sku_reference']} credits {offset} against a tool "
                f"costing {cost}"
            )

        # 2. A tool tagged to OTHER personas is not this persona's to retire.
        if tool["persona_ids"] and persona["id"] not in tool["persona_ids"]:
            bad.append(
                f"opt-persona-attribution: {b['sku_reference']} credits a tool tagged "
                f"to other personas"
            )

    # 3. The recommendation must be the best eligible option, and eligible.
    rec = next((b for b in analysis["bundles"] if b["recommended"]), None)
    if rec is not None:
        if not rec["covers_all_required"] or not rec["price_known"] or rec["cap_limited"]:
            bad.append(f"opt-recommend-eligible: recommended {rec['sku_reference']} "
                       f"despite gap/unpriced/cap-limited")
        better = [
            b for b in analysis["bundles"]
            if b["covers_all_required"] and b["price_known"] and not b["cap_limited"]
            and D(str(b["delta_annual"])) < D(str(rec["delta_annual"]))
        ]
        if better:
            bad.append(f"opt-recommend-best: recommended {rec['sku_reference']} but "
                       f"{better[0]['sku_reference']} has a better delta")
    return bad


def check_optimizer_agrees_with_engine(client, case) -> list[str]:
    """The recommendation's delta must be what the engine computes once applied —
    checked on single-persona engagements, where the two are directly comparable."""
    eid = case["eid"]
    persona = case["personas"][0]
    scenario = case["scenarios"][0]
    bad: list[str] = []

    analysis = client.post(
        f"/api/engagements/{eid}/personas/{persona['id']}/bundle-analysis").json()
    rec = next((b for b in analysis["bundles"] if b["recommended"]), None)
    if rec is None:
        return bad

    before = {"target_sku_reference": scenario["target_sku_reference"],
              "target_unit_price_annual": scenario["target_unit_price_annual"]}
    client.patch(f"/api/engagements/{eid}/scenarios/{scenario['id']}", json={
        "target_sku_reference": rec["sku_reference"],
        "target_unit_price_annual": rec["target_unit_price_annual"]})
    try:
        result = client.post(f"/api/engagements/{eid}/compute").json()
        engine_delta = D(str(result["scenarios"][0]["delta_annual"]))
        claimed = D(str(rec["delta_annual"]))
        if abs(engine_delta - claimed) > D("0.02"):
            bad.append(
                f"opt-engine-agreement: {rec['sku_reference']} claims {claimed} but the "
                f"engine computes {engine_delta} once applied"
            )
    finally:
        client.patch(f"/api/engagements/{eid}/scenarios/{scenario['id']}", json=before)
    return bad


def check_carve_out(client, case) -> list[str]:
    """Carving seats out of a persona must MOVE them, not mint or lose them.

    A carve-out changes the FUTURE state only — which plan some of these people
    end up on. Everything about today is untouched: the same people exist, they
    hold the same licences, and their tools cover the same seats. If any of that
    drifts, the split has quietly rewritten the customer's baseline, and every
    saving measured against it is wrong.
    """
    eid = case["eid"]
    parent = case["personas"][0]
    bad: list[str] = []
    if parent["headcount"] < 2:
        return bad

    def totals():
        personas = client.get(f"/api/engagements/{eid}/personas").json()
        result = client.post(f"/api/engagements/{eid}/compute").json()
        tools = client.get(f"/api/engagements/{eid}/third-party").json()
        licences = client.get(f"/api/engagements/{eid}/current-licenses").json()
        return {
            "headcount": sum(p["headcount"] for p in personas),
            "covers": {t["id"]: t["covered_count"] for t in tools},
            "current_ms": D(str(result["rollup"]["existing_microsoft_annual"])),
            "personas": personas,
            "licences": licences,
            "tools": tools,
            # Per-persona current spend: the total being right is not enough — the
            # carved people must still be shown holding what they hold.
            "by_persona": {
                sc["persona_id"]: D(str(sc["current_microsoft_annual"]))
                for sc in result["scenarios"]
            },
        }

    def tags_for(state, persona_id):
        """Everything this persona is associated with today."""
        return (
            {l["id"] for l in state["licences"] if persona_id in (l["persona_ids"] or [])},
            {t["id"] for t in state["tools"] if persona_id in (t["persona_ids"] or [])},
            set(next((p["required_outcome_ids"] or [])
                     for p in state["personas"] if p["id"] == persona_id)),
        )

    before = totals()
    seats = max(1, min(300, parent["headcount"] // 3))
    resp = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                       json={"seats": seats, "target_sku_reference": "Microsoft 365 Business Premium"})
    if resp.status_code != 201:
        return [f"carve-accepted: carving {seats} of {parent['headcount']} was rejected "
                f"({resp.status_code}: {resp.text[:120]})"]
    child = resp.json()
    after = totals()

    # 1. The seats moved: same people, split differently.
    if after["headcount"] != before["headcount"]:
        bad.append(f"carve-population-conserved: headcount {before['headcount']} → "
                   f"{after['headcount']}")
    if child["headcount"] != seats:
        bad.append(f"carve-seats-moved: asked for {seats}, child has {child['headcount']}")
    new_parent = next(p for p in after["personas"] if p["id"] == parent["id"])
    if new_parent["headcount"] != parent["headcount"] - seats:
        bad.append(f"carve-parent-reduced: parent {parent['headcount']} → "
                   f"{new_parent['headcount']}, expected {parent['headcount'] - seats}")

    # 2. Lineage is recorded, so the split stays attributable.
    if child.get("parent_persona_id") != parent["id"]:
        bad.append("carve-lineage: child does not point at the persona it came from")

    # 3. Today is unchanged — the carve-out inherits what these people hold, so
    #    current spend and tool coverage cannot move.
    if after["current_ms"] != before["current_ms"]:
        bad.append(f"carve-current-spend-conserved: current Microsoft spend "
                   f"{before['current_ms']} → {after['current_ms']}")
    if after["covers"] != before["covers"]:
        bad.append(f"carve-covers-conserved: third-party covers {before['covers']} → "
                   f"{after['covers']}")

    # 4. The carve-out inherits what these people hold. Without this they look
    #    like a population with no licensing: their delta reads as pure new cost
    #    and the coverage check reports capability they actually have today.
    parent_tags = tags_for(after, parent["id"])
    child_tags = tags_for(after, child["id"])
    if child_tags != parent_tags:
        bad.append(
            f"carve-inherits-associations: child holds licences/tools/requirements "
            f"{child_tags} but the persona it came from holds {parent_tags}"
        )

    # 5. ...so current spend SPLITS between them by headcount, rather than the
    #    carved seats dropping to zero and the parent keeping the whole bill.
    old_hc = D(parent["headcount"])
    before_parent = before["by_persona"].get(parent["id"])
    if before_parent and old_hc:
        expect_child = (before_parent * D(seats) / old_hc).quantize(D("0.01"))
        got_child = after["by_persona"].get(child["id"], D("0")).quantize(D("0.01"))
        if abs(got_child - expect_child) > D("0.02"):
            bad.append(
                f"carve-spend-splits: carved {seats} of {old_hc} seats should carry "
                f"{expect_child} of current spend, carries {got_child}"
            )

    # 6. The carve-out is a real, costed persona — not a label.
    scenarios = client.get(f"/api/engagements/{eid}/scenarios").json()
    child_scenario = next((x for x in scenarios if x["persona_id"] == child["id"]), None)
    if child_scenario is None:
        bad.append("carve-has-scenario: carve-out has no scenario, so it has no future state")
    else:
        if child_scenario["target_sku_reference"] != "Microsoft 365 Business Premium":
            bad.append(f"carve-target-applied: child targets "
                       f"{child_scenario['target_sku_reference']!r}, not what was asked for")
        # Priced AS the plan it moved to. Inheriting the parent's $/seat would quote
        # Business Premium at the E3 rate — a number on the readout nobody could buy.
        got = D(str(child_scenario["target_unit_price_annual"]))
        if got != BP_ANNUAL:
            bad.append(f"carve-target-priced: child targets Business Premium at {got}/seat/yr, "
                       f"but the catalog price is {BP_ANNUAL}")

    # 7. Undo puts the seats back. A split you cannot reverse is a trap.
    client.delete(f"/api/engagements/{eid}/personas/{child['id']}")
    restored = totals()
    if restored["headcount"] != before["headcount"]:
        bad.append(f"carve-undo-restores: headcount after undo {restored['headcount']} "
                   f"!= {before['headcount']}")
    if restored["covers"] != before["covers"]:
        bad.append(f"carve-undo-restores: covers after undo {restored['covers']} "
                   f"!= {before['covers']}")
    if restored["current_ms"] != before["current_ms"]:
        bad.append(f"carve-undo-restores: current spend after undo "
                   f"{restored['current_ms']} != {before['current_ms']}")

    # 8. Carving everyone is refused — it would leave an empty persona behind, and
    #    "move the whole group" is just editing this persona's target.
    whole = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                        json={"seats": new_parent["headcount"] + seats})
    if whole.status_code == 201:
        bad.append("carve-bounded: carving the entire persona was allowed")

    return bad


def run(client=None, level: str = "full") -> dict:
    if client is None:
        from fastapi.testclient import TestClient

        from app.db import init_db
        from app.main import app

        init_db()
        client = TestClient(app)
    load_catalog(client)

    failures: dict[str, list[tuple[str, str]]] = {}
    total = 0
    for label, case in iter_cases(client, level):
        total += 1
        problems = check_optimizer(client, case)
        problems += check_carve_out(client, case)
        if len(case["personas"]) == 1:
            problems += check_optimizer_agrees_with_engine(client, case)
        for p in problems:
            name, _, detail = p.partition(": ")
            failures.setdefault(name, []).append((label, detail))
    return {"total": total, "failures": failures}


def main(argv: list[str]) -> int:
    level = "full"
    if "--level" in argv:
        level = argv[argv.index("--level") + 1]
    report = run(level=level)
    if "--json" in argv:
        # Machine-readable, for the pytest wrapper: this sweep loads a pricing
        # catalog (global, not engagement-scoped), so it must run in its own
        # process against its own DB rather than the shared test session's.
        import json

        print(json.dumps(report))
        return 0
    print(f"swept {report['total']:,} engagements ({level})")
    failures = report["failures"]
    if not failures:
        print("no invariant violations")
        return 0
    print(f"\n{sum(len(v) for v in failures.values()):,} violations across "
          f"{len(failures)} invariants:\n")
    for name, items in sorted(failures.items(), key=lambda kv: -len(kv[1])):
        print(f"  {name}: {len(items):,} cases")
        for label, detail in items[:2]:
            print(f"      {detail}")
            print(f"        ↳ {label}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
