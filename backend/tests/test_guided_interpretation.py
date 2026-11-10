"""ENGINE_SPEC 6.0a / GUIDED_PLAN G2–G6, G8: the one splitting rule for a tool's
cost, the seat-mismatch answer, the persona baseline and the headcount check."""

from decimal import Decimal as D

from tco_engine import compute
from tco_engine.interpret import (
    RawEngagement, RawLicenseLine, RawPersona, RawScenario, RawTool, interpret, people_covered, to_engine,
)


def _raw(tools=(), scenarios=(), lines=(), **kw):
    return RawEngagement(
        id="e", personas=(RawPersona("kw", "Knowledge", 1000), RawPersona("fl", "Frontline", 200)),
        licence_lines=lines, tools=tools, scenarios=scenarios,
        microsoft_coverage={"e5": frozenset({"edr", "mail"}), "f3": frozenset({"mail"})},
        tool_coverage={"t": frozenset({"edr"})}, **kw)


def _tool(**kw):
    base = dict(id="t", name="EDR Co", raw_cost=D("150000"), cost_period="Annual", is_managed=False,
                tooling_pct=None, covered_count_override=1500, persona_ids=("kw", "fl"))
    base.update(kw)
    return RawTool(**base)


def _plan(pid, bundle):
    return RawScenario(id=f"s-{pid}", persona_id=pid, target_sku_reference=bundle,
                       list_price_annual=D("100"), discount_pct=None, price_override=False,
                       overridden_price_annual=None, in_scope=True, resolved_bundle_id=bundle)


def test_rule_table():
    assert people_covered(1200, None, None) == (1200, "personas")
    assert people_covered(1200, 1000, None) == (1200, "personas")
    assert people_covered(1200, 1500, None) == (1500, "override")
    assert people_covered(1200, 1500, "PeopleWhoStay") == (1500, "override")
    assert people_covered(1200, 1500, "ExtraSeats") == (1200, "extra_seats")


def test_cost_is_never_lost():
    for answer in (None, "ExtraSeats", "PeopleWhoStay"):
        i = interpret(_raw(tools=(_tool(extra_seats_answer=answer),)))
        t = i.tool("t")
        total = sum(i.tool_shares.values()) + t.standing_annual
        assert abs(total - t.effective_annual_cost) < D("0.01")


def test_extra_seats_spreads_the_whole_cost_and_a_full_move_retires_it():
    i = interpret(_raw(tools=(_tool(extra_seats_answer="ExtraSeats"),),
                       scenarios=(_plan("kw", "e5"), _plan("fl", "e5"))))
    assert i.tool("t").standing_seats == 0
    r = compute(to_engine(i))
    d = next(d for d in r.dispositions if d.third_party_product_id == "t")
    assert d.residual_count == 0
    offset = sum(o.credited_offset_annual for s in r.scenarios for o in s.offsets)
    assert offset == D("150000.00")


def test_people_who_stay_keep_their_seats_cost():
    i = interpret(_raw(tools=(_tool(extra_seats_answer="PeopleWhoStay"),),
                       scenarios=(_plan("kw", "e5"), _plan("fl", "e5"))))
    t = i.tool("t")
    assert t.standing_seats == 300 and t.standing_annual == D("30000")
    r = compute(to_engine(i))
    offset = sum(o.credited_offset_annual for s in r.scenarios for o in s.offsets)
    assert offset == D("120000.00")


def test_partial_move_keeps_the_unmoved_personas_share():
    # Frontline's F3 does not deliver EDR, so its share stays (no question asked).
    i = interpret(_raw(tools=(_tool(extra_seats_answer="ExtraSeats"),),
                       scenarios=(_plan("kw", "e5"), _plan("fl", "f3"))))
    r = compute(to_engine(i))
    offset = sum(o.credited_offset_annual for s in r.scenarios for o in s.offsets)
    assert offset == D("125000.00")     # 1000 / 1200 of 150,000


def test_extra_seats_question_only_when_a_move_retires_it():
    no_move = interpret(_raw(tools=(_tool(),), scenarios=(_plan("kw", "f3"), _plan("fl", "f3"))))
    assert not [f for f in no_move.findings if f.code == "extra_seats_unanswered"]
    moved = interpret(_raw(tools=(_tool(),), scenarios=(_plan("kw", "e5"),)))
    f = [f for f in moved.findings if f.code == "extra_seats_unanswered"]
    assert len(f) == 1 and f[0].facts["extra"] == 300
    answered = interpret(_raw(tools=(_tool(extra_seats_answer="ExtraSeats"),),
                              scenarios=(_plan("kw", "e5"),)))
    assert not [f for f in answered.findings if f.code == "extra_seats_unanswered"]
    matched = interpret(_raw(tools=(_tool(covered_count_override=1200),), scenarios=(_plan("kw", "e5"),)))
    assert not [f for f in matched.findings if f.code == "extra_seats_unanswered"]


def test_fewer_seats_than_people_is_noted_not_asked():
    i = interpret(_raw(tools=(_tool(covered_count_override=1000),)))
    assert i.tool("t").people_covered == 1200
    assert [f.code for f in i.findings if f.tool_id == "t" and f.code == "tool_seats_short"]


def test_baseline_per_persona():
    line = RawLicenseLine(id="l", sku_reference="E3", quantity_assigned=1200, quantity_purchased=1200,
                          list_price_annual=D("432"), price_override=False, overridden_price_annual=None,
                          persona_ids=())
    i = interpret(_raw(tools=(_tool(covered_count_override=None, persona_ids=("kw",)),), lines=(line,)))
    b = {x.persona_id: x for x in i.baselines}
    assert b["kw"].microsoft_annual == D("432000.00") and b["kw"].tools_annual == D("150000.00")
    assert b["kw"].per_user_monthly == D("48.50")              # 582,000 / 1000 / 12
    assert b["fl"].tools_annual == 0 and b["fl"].per_user_monthly == D("36.00")


def test_headcount_mismatch_finding():
    assert [f for f in interpret(_raw(supported_headcount=1500)).findings if f.code == "headcount_mismatch"]
    assert not [f for f in interpret(_raw(supported_headcount=1200)).findings if f.code == "headcount_mismatch"]
    assert not [f for f in interpret(_raw()).findings if f.code == "headcount_mismatch"]


def test_summary_counts_down_and_totals_the_baseline():
    i = interpret(_raw(tools=(_tool(extra_seats_answer="PeopleWhoStay"),), supported_headcount=1500))
    s = i.summary
    assert s["groups_headcount"] == 1200 and s["remaining_headcount"] == 300
    assert s["baseline_tools_annual"] == D("120000.00") and s["standing_annual"] == D("30000.00")
    assert s["baseline_total_annual"] == sum(b.total_annual for b in i.baselines)
    assert interpret(_raw()).summary["remaining_headcount"] is None


def test_persona_view_after_is_today_plus_the_delta(client):
    eid = client.post("/api/engagements", json={"customer_name": "View Co"}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 100}).json()
    q = client.post(f"/api/engagements/{eid}/personas", json={"name": "FL", "headcount": 50}).json()
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_assigned": 100, "quantity_purchased": 100,
        "unit_price_paid_annual": 300, "persona_ids": [p["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 480, "in_scope": True})
    views = {v["persona_id"]: v for v in client.get(f"/api/engagements/{eid}/result").json()["views"]["personas"]}
    kw, fl = views[p["id"]], views[q["id"]]
    assert kw["today_per_user_monthly"] == 25.0           # 300 / 12
    assert kw["after_per_user_monthly"] == 40.0           # 480 / 12
    assert kw["delta_per_user_monthly"] == 15.0 and kw["has_plan"]
    assert not fl["has_plan"] and fl["after_annual"] == fl["today_annual"]
