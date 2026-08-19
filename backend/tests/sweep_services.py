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
    swap        "the swap saves money"  →  turning it on must never raise the net
                and must respect the seat cap it exists to honor. Where the cap
                turns personas away, the swap must SAY so — an enabled feature
                that moves no number and explains nothing is indistinguishable
                from a broken one, which is how its defects went unnoticed.

Cases are built through the HTTP API — the same surface the GUI uses — so a
violation here is a violation a user can hit. Run the full space directly:

    cd backend && python -m tests.sweep_services
    cd backend && python -m tests.sweep_services --level ci
"""

from __future__ import annotations

import sys
from decimal import Decimal

D = Decimal

# A minimal priced catalog. Business Premium is the cheap plan the swap wants to
# move people onto; the E-plans are progressively dearer, as in the real sheet.
CATALOG_CSV = """ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,Currency,UnitPrice,ERP Price,Segment,EffectiveStartDate,EffectiveEndDate,LastUpdatedDate
Microsoft 365 Business Premium,P1,S1,Microsoft 365 Business Premium,P1Y,Monthly,US,USD,17.60,22.00,Commercial,2026-01-01,,2026-01-01
Microsoft 365 E3,P2,S1,Microsoft 365 E3,P1Y,Monthly,US,USD,32.76,40.95,Commercial,2026-01-01,,2026-01-01
Microsoft 365 E5,P3,S1,Microsoft 365 E5,P1Y,Monthly,US,USD,45.60,57.00,Commercial,2026-01-01,,2026-01-01
Office 365 E3,P4,S1,Office 365 E3,P1Y,Monthly,US,USD,21.84,27.30,Commercial,2026-01-01,,2026-01-01
Office 365 E1,P5,S1,Office 365 E1,P1Y,Monthly,US,USD,8.40,10.50,Commercial,2026-01-01,,2026-01-01
"""


def load_catalog(client) -> None:
    client.post("/api/catalog/import-csv",
                files={"file": ("catalog.csv", CATALOG_CSV, "text/csv")})


# --------------------------------------------------------------------------
# Case generation
# --------------------------------------------------------------------------

def build_case(client, *, headcounts, tool_covered, tool_personas, tool_outcome,
               target_sku, swap_on, cap_on):
    """One engagement through the public API. Returns its dict shape."""
    eid = client.post("/api/engagements", json={"customer_name": "Sweep"}).json()["id"]
    people = [
        client.post(f"/api/engagements/{eid}/personas",
                    json={"name": f"P{i + 1}", "headcount": hc}).json()
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
        outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
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
    client.patch(f"/api/engagements/{eid}",
                 json={"bp_swap_enabled": swap_on, "business_cap_enabled": cap_on})
    return {"eid": eid, "personas": people, "tool": tool, "scenarios": scenarios}


def iter_cases(client, level: str = "full"):
    headcount_sets = (
        [(200, 60), (2518, 632)] if level == "ci"
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
                    for swap_on in (False, True):
                        for cap_on in (False, True):
                            label = (
                                f"hc={hcs} tool=({covered},{tool_pids}) out={outcome} "
                                f"target={target} swap={swap_on} cap={cap_on}"
                            )
                            yield label, build_case(
                                client, headcounts=hcs, tool_covered=covered,
                                tool_personas=tool_pids, tool_outcome=outcome,
                                target_sku=target, swap_on=swap_on, cap_on=cap_on)


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


def check_swap(client, case) -> list[str]:
    """The swap must save money, respect the cap, and use the headroom it has."""
    eid = case["eid"]
    bad: list[str] = []

    result = client.post(f"/api/engagements/{eid}/compute").json()
    summary = result.get("bp_swap") or {}
    if not summary.get("enabled"):
        return bad

    net_on = D(str(result["rollup"]["net_tco_delta_annual"]))
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": False})
    try:
        net_off = D(str(client.post(f"/api/engagements/{eid}/compute")
                        .json()["rollup"]["net_tco_delta_annual"]))
    finally:
        client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})

    rows = summary.get("scenarios", [])
    applied = [r for r in rows if r["applied"]]

    # 1. A swap is applied only because it saves — so it must never cost more.
    #    (delta = new − old: a larger number is worse.)
    if applied and net_on > net_off + D("0.02"):
        bad.append(f"swap-never-worse: swapping raised the net from {net_off} to {net_on}")

    # 2. Applied ⇒ eligible and not opted out.
    for r in applied:
        if not r["eligible"]:
            bad.append(f"swap-eligibility: applied to ineligible {r['persona_name']}")
        if r["opted_out"]:
            bad.append(f"swap-optout: applied to opted-out {r['persona_name']}")

    # 3. The swap exists to honor the seat cap — it must not exceed it.
    cap = summary.get("cap")
    if cap and cap["committed_seats"] > cap["max"]:
        bad.append(f"swap-cap-respected: committed {cap['committed_seats']} > cap {cap['max']}")

    # 4. Cap headroom the swap cannot use must be DISCLOSED, not silently
    #    abandoned. The swap moves whole personas (a persona is the unit of
    #    licensing in this model), so a persona larger than the cap can never fit
    #    — filling the remaining seats would mean splitting a persona, which is an
    #    operator decision about their own population, not something the math may
    #    invent. What the math owes the operator is the number and the next step.
    stranded = [r for r in rows if r["reason"] == "capped"]
    if stranded:
        if not summary.get("stranded_seats"):
            bad.append("swap-strand-disclosed: personas turned away by the cap but "
                       "stranded_seats not reported")
        if cap is None:
            bad.append("swap-strand-disclosed: personas capped with no cap reported")

    # 5. Every non-applied row carries a reason a user can act on.
    for r in rows:
        if not r["applied"] and not r["reason"]:
            bad.append(f"swap-reason-given: {r['persona_name']} not swapped with no reason")

    # 6. An ENABLED swap that applies to nobody must say why. A feature that is on,
    #    moves no number, and explains nothing is indistinguishable from a broken
    #    one — which is exactly how this defect was found.
    if not applied and not summary.get("inert_reason"):
        bad.append("swap-inert-explained: swap enabled, nothing applied, no reason given")

    # 7. A swap that applies to nobody must change nothing.
    if not applied and net_on != net_off:
        bad.append(f"swap-no-op: no scenario swapped but the net moved {net_off} → {net_on}")

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
        problems = check_swap(client, case)
        problems += check_optimizer(client, case)
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
