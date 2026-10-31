"""The interpreter's rules (tco_engine.interpret, ENGINE_SPEC "Interpretation").
Parity with the code it replaced is checked in test_zz_interpret_parity.py."""

from decimal import Decimal as D

from tco_engine.interpret import (
    RawAddon, RawEngagement, RawLicenseLine, RawPersona, RawScenario, RawTool,
    interpret, to_engine,
)


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def _raw(**kw):
    base = dict(
        id="e",
        personas=(RawPersona("a", "A", 100), RawPersona("b", "B", 300)),
        licence_lines=(), tools=(), scenarios=(),
        microsoft_coverage={"m365-e3": frozenset({"o1", "o2"}), "Legacy Name": frozenset({"o9"})},
        tool_coverage={"t": frozenset({"o1"})},
    )
    base.update(kw)
    return RawEngagement(**base)


def _line(**kw):
    base = dict(id="l", sku_reference="Microsoft 365 E3", quantity_assigned=400,
                quantity_purchased=400, list_price_annual=D("432"), price_override=False,
                overridden_price_annual=None, persona_ids=())
    base.update(kw)
    return RawLicenseLine(**base)


def _tool(**kw):
    base = dict(id="t", name="Tool", raw_cost=D("12000"), cost_period="Annual",
                is_managed=False, tooling_pct=None, covered_count_override=None,
                persona_ids=("a",))
    base.update(kw)
    return RawTool(**base)


def test_an_untagged_line_is_org_wide_and_its_shares_add_up():
    i = interpret(_raw(licence_lines=(_line(),)))
    line = i.licence("l")
    assert line.assumed and line.applies_to == ("a", "b")
    assert line.annual_cost == D("172800")                       # 400 × 432
    assert i.licence_shares[("l", "a")] == D("43200")            # 100/400
    assert i.licence_shares[("l", "b")] == D("129600")           # 300/400
    assert sum(v for (lid, _), v in i.licence_shares.items() if lid == "l") == line.annual_cost


def test_a_tagged_line_applies_only_to_its_personas():
    i = interpret(_raw(licence_lines=(_line(persona_ids=("b",), quantity_assigned=300),)))
    line = i.licence("l")
    assert not line.assumed and line.applies_to == ("b",)
    assert set(i.licence_shares) == {("l", "b")}


def test_a_price_override_is_the_effective_price_and_says_so():
    i = interpret(_raw(licence_lines=(_line(price_override=True, overridden_price_annual=D("300")),)))
    line = i.licence("l")
    assert line.effective_price_annual == D("300") and line.price_source == "override"
    assert line.list_price_annual == D("432")
    # An override switched on with no price falls back to list.
    j = interpret(_raw(licence_lines=(_line(price_override=True),)))
    assert j.licence("l").price_source == "list"


def test_how_a_line_is_read():
    cases = {
        "out_of_scope": _line(out_of_scope=True, linked_bundle_id="m365-e3"),
        "linked": _line(linked_bundle_id="m365-e3"),
        "mapped": _line(mapped_outcome_ids=frozenset({"o5"})),
        "named": _line(sku_reference="Legacy Name"),
        "resolved": _line(resolved_bundle_id="m365-e3"),
        "unread": _line(sku_reference="Mystery"),
    }
    for state, line in cases.items():
        i = interpret(_raw(licence_lines=(line,)))
        assert i.licence("l").read_state == state, state
    # An explicit answer wins over resolving the name.
    i = interpret(_raw(licence_lines=(_line(mapped_outcome_ids=frozenset({"o5"}),
                                            resolved_bundle_id="m365-e3"),)))
    assert i.licence("l").outcomes == frozenset({"o5"})
    # Out of scope: no share of anything.
    i = interpret(_raw(licence_lines=(cases["out_of_scope"],)))
    assert i.licence_shares == {} and i.lines_for("a") == []


def test_tool_cost_covers_and_shares():
    i = interpret(_raw(tools=(_tool(),)))
    t = i.tool("t")
    assert t.people_covered == 100 and t.covered_source == "personas"
    assert t.cost_per_person == D("120") and i.tool_shares[("t", "a")] == D("12000")
    assert t.delivered_outcome_ids == frozenset({"o1"})

    # Monthly cost, managed at the engagement default, covering more than its personas.
    i = interpret(_raw(tools=(_tool(cost_period="Monthly", raw_cost=D("1000"), is_managed=True,
                                    covered_count_override=150),)))
    t = i.tool("t")
    assert t.annual_cost == D("12000") and t.tooling_source == "engagement"
    assert t.effective_annual_cost == D("3600.00")               # 12,000 × 0.30
    assert t.people_covered == 150 and t.covered_source == "override"
    assert t.cost_per_person == D("24")
    # The 50 people beyond persona A are nobody's share.
    assert i.tool_shares == {("t", "a"): D("2400")}


def test_an_untagged_tool_without_covers_costs_nobody():
    i = interpret(_raw(tools=(_tool(persona_ids=()),)))
    t = i.tool("t")
    assert t.assumed and t.applies_to == ("a", "b") and t.people_covered == 0
    assert all(v == 0 for v in i.tool_shares.values())


def test_scenario_composition_and_price():
    s = RawScenario(id="s", persona_id="a", target_sku_reference="Microsoft 365 E3",
                    list_price_annual=D("432"), discount_pct=D("0.10"), price_override=False,
                    overridden_price_annual=None, in_scope=True,
                    addons=(RawAddon("legacy-addon", D("68")),), resolved_bundle_id="m365-e3")
    i = interpret(_raw(scenarios=(s,), microsoft_coverage={
        "m365-e3": frozenset({"o1"}), "legacy-addon": frozenset({"o7"})}))
    sc = i.scenarios[0]
    assert sc.composed_list_annual == D("500") and sc.net_annual == D("450.00")
    assert sc.covered_outcome_ids == frozenset({"o1", "o7"}) and sc.complete
    from dataclasses import replace
    o = interpret(_raw(scenarios=(replace(s, price_override=True, overridden_price_annual=D("400")),)))
    assert o.scenarios[0].net_annual == D("400") and o.scenarios[0].price_source == "override"
    empty = interpret(_raw(scenarios=(replace(s, target_sku_reference="", list_price_annual=D("0"),
                                              addons=()),)))
    assert not empty.scenarios[0].complete


def test_the_engine_reads_the_interpreted_values():
    i = interpret(_raw(licence_lines=(_line(price_override=True, overridden_price_annual=D("300"),
                                            out_of_scope=False),
                                      _line(id="x", out_of_scope=True)),
                       tools=(_tool(is_managed=True, tooling_pct=D("0.5")),)))
    eng = to_engine(i)
    assert [l.id for l in eng.current_licenses] == ["l"]          # out of scope is in no number
    assert eng.current_licenses[0].unit_price_paid_annual == D("300")
    tp = eng.third_party_products[0]
    assert tp.effective_annual_cost == i.tool("t").effective_annual_cost == D("6000.00")
    assert tp.per_unit_annual_cost == i.tool("t").cost_per_person


def test_the_interpreted_layer_is_readable_over_the_api(client):
    eid = client.post("/api/engagements", json={"customer_name": "Layer Co"}).json()["id"]
    a = client.post(f"/api/engagements/{eid}/personas", json={"name": "A", "headcount": 100}).json()
    client.post(f"/api/engagements/{eid}/personas", json={"name": "B", "headcount": 300})
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_assigned": 400,
        "unit_price_paid_annual": 300, "persona_ids": []})
    client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 1000, "cost_period": "Monthly", "persona_ids": [a["id"]]})
    v = client.get(f"/api/engagements/{eid}/interpreted").json()
    assert v["derived"] is True
    line = v["licence_lines"][0]
    assert line["assumed"] is True and line["price_source"] == "list"
    assert line["annual_cost"] == 120000.0
    assert sorted(s["annual"] for s in v["licence_shares"]) == [30000.0, 90000.0]
    tool = v["tools"][0]
    assert tool["annual_cost"] == 12000.0 and tool["people_covered"] == 100
    assert tool["covered_source"] == "personas"
