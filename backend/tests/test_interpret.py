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
    assert {r["outcome_id"] for r in v["today"]} and all("status" in r for r in v["proposed"])
    assert [r["tool_id"] for r in v["tool_retirements"]] == [tool["id"]]


# ---------------------------------------------------------------------------
# Persona × outcome tables (TARGET_SCHEMA §6.3)
# ---------------------------------------------------------------------------

def _plan(cover_key="m365-e3", **kw):
    base = dict(id="s", persona_id="a", target_sku_reference="Microsoft 365 E3",
                list_price_annual=D("432"), discount_pct=None, price_override=False,
                overridden_price_annual=None, in_scope=True, resolved_bundle_id=cover_key)
    base.update(kw)
    return RawScenario(**base)


def _status(i, pid="a"):
    return {r.outcome_id: r.status for r in i.proposed if r.persona_id == pid}


def test_each_status():
    # Today: o1 by Microsoft (line), o3 only by a tool, o4 by Microsoft but not in
    # the plan. The plan (m365-e3 here) delivers o1, o2, o3. o5 is required by
    # nobody... until persona A declares it.
    i = interpret(_raw(
        personas=(RawPersona("a", "A", 100, required_outcome_ids=frozenset({"o1", "o5"})),),
        microsoft_coverage={"today": frozenset({"o1", "o4"}),
                            "m365-e3": frozenset({"o1", "o2", "o3"})},
        licence_lines=(_line(persona_ids=("a",), linked_bundle_id="today"),),
        tools=(_tool(),), tool_coverage={"t": frozenset({"o3"})},
        scenarios=(_plan(),)))
    st = _status(i)
    assert st["o1"] == "Kept"
    assert st["o3"] == "MovedToMicrosoft"       # only a tool today; the plan takes it on
    assert st["o2"] == "Unconfirmed"            # new, but nobody confirmed the gap
    assert st["o4"] == "Lost"
    assert st["o5"] == "Gap"                    # needed, delivered neither today nor after
    needed = {t.outcome_id: t.needed_source for t in i.today if t.needed}
    assert needed == {"o1": "required", "o5": "required"}
    # The move retires the tool; nothing keeps it.
    [r] = i.tool_retirements
    assert r.retired and not r.forced and not r.quick_win

    # Confirm the gap: o2 is Gained.
    g = interpret(_raw(
        personas=(RawPersona("a", "A", 100),),
        microsoft_coverage={"m365-e3": frozenset({"o2"})},
        scenarios=(_plan(),), gap_answers={("a", "o2"): "NotDeliveredToday"}))
    assert _status(g) == {"o2": "Gained"}


def test_covered_outside_the_inventory_is_delivered_today_and_never_new():
    i = interpret(_raw(
        personas=(RawPersona("a", "A", 100),),
        microsoft_coverage={"m365-e3": frozenset({"o2"})},
        scenarios=(_plan(),), gap_answers={("a", "o2"): "CoveredOutsideInventory"}))
    [t] = i.today
    assert t.delivered_today and t.by_outside
    assert _status(i) == {"o2": "Kept"}


def test_a_tool_the_plan_does_not_cover_is_kept_and_keeps_its_outcomes():
    i = interpret(_raw(
        personas=(RawPersona("a", "A", 100),),
        microsoft_coverage={"m365-e3": frozenset({"o1"})},
        tools=(_tool(),), tool_coverage={"t": frozenset({"o1", "o9"})},
        scenarios=(_plan(),)))
    [r] = i.tool_retirements
    assert not r.retired
    rows = {p.outcome_id: p for p in i.proposed}
    assert rows["o9"].by_kept_tool and rows["o9"].status == "Kept"
    assert {s.tool_id for s in i.proposed_sources if s.outcome_id == "o9"} == {"t"}


def test_forced_retirement_and_quick_win():
    i = interpret(_raw(
        personas=(RawPersona("a", "A", 100),),
        microsoft_coverage={"m365-e3": frozenset({"o1"}), "today": frozenset({"o1", "o9"})},
        licence_lines=(_line(linked_bundle_id="today"),),
        tools=(_tool(override="ForceFullElimination"),), tool_coverage={"t": frozenset({"o9"})},
        scenarios=(_plan(),)))
    [r] = i.tool_retirements
    assert r.retired and r.forced          # the plan doesn't cover o9; the operator forced it
    assert r.quick_win                     # today's licence already delivers o9


def test_no_plan_keeps_today_and_an_incomplete_plan_is_pending():
    i = interpret(_raw(personas=(RawPersona("a", "A", 100),),
                       microsoft_coverage={"today": frozenset({"o1"})},
                       licence_lines=(_line(linked_bundle_id="today"),)))
    [row] = i.proposed
    assert not row.has_plan and row.status == "Kept" and row.delivered_after
    p = interpret(_raw(personas=(RawPersona("a", "A", 100),),
                       microsoft_coverage={"today": frozenset({"o1"})},
                       licence_lines=(_line(linked_bundle_id="today"),),
                       scenarios=(_plan(target_sku_reference="", list_price_annual=D("0")),)))
    assert {r.status for r in p.proposed} == {"Pending"}


def test_an_unread_line_marks_its_personas_outcomes_unknown():
    i = interpret(_raw(licence_lines=(_line(sku_reference="Mystery", persona_ids=("b",)),)))
    unknown = {p.id: p.outcomes_unknown for p in i.personas}
    assert unknown == {"a": False, "b": True}


# ---------------------------------------------------------------------------
# Seat caps and findings (TARGET_SCHEMA D27, §6.4)
# ---------------------------------------------------------------------------

from tco_engine.interpret import RawLimit  # noqa: E402

_CAP = RawLimit(key="biz", name="Business", limit_type="max_total_seats", max_quantity=300,
                member_bundle_ids=frozenset({"bp"}))


def test_seat_cap_counts_movers_and_stayers_once():
    # A (100) moves onto Business; B (300) keeps 40 Business seats it holds today.
    i = interpret(_raw(
        licence_limits=(_CAP,),
        licence_lines=(_line(id="bp-line", persona_ids=("b",), quantity_assigned=40,
                             resolved_bundle_id="bp"),),
        scenarios=(_plan(cover_key="bp", resolved_bundle_id="bp"),)))
    [c] = i.seat_caps
    assert c.current_seats == 40 and c.future_seats == 140
    assert c.used_by_others("a") == 40 and c.seats_left_for("a") == 260
    assert not any(f.code == "seat_cap_exceeded" for f in i.findings)


def test_over_the_cap_is_a_finding_with_its_facts():
    i = interpret(_raw(
        personas=(RawPersona("a", "A", 280), RawPersona("b", "B", 40)),
        licence_limits=(_CAP,),
        licence_lines=(_line(id="bp-line", persona_ids=("b",), quantity_assigned=40,
                             resolved_bundle_id="bp"),),
        scenarios=(_plan(cover_key="bp", resolved_bundle_id="bp"),)))
    [f] = [f for f in i.findings if f.code == "seat_cap_exceeded"]
    assert f.persona_id == "a" and f.facts["kind"] == "split"
    assert f.facts["used_elsewhere"] == 40 and f.facts["seats_left"] == 260


def _codes(i):
    return [f.code for f in i.findings]


def test_findings_for_licences_and_tools():
    i = interpret(_raw(
        licence_lines=(_line(quantity_purchased=450),                       # untagged, 50 unused
                       _line(id="m", sku_reference="Mystery", persona_ids=("b",),
                             quantity_assigned=10, quantity_purchased=10),  # unread, seats ≠ 300
                       _line(id="x", out_of_scope=True)),
        tools=(_tool(raw_cost=D("0")),)))
    codes = _codes(i)
    for code in ("licence_untagged", "unused_seats_unanswered", "seats_vs_headcount",
                 "renewal_date_missing", "license_out_of_scope", "tool_incomplete",
                 "group_without_plan", "license_unread"):
        assert code in codes, code
    tool = next(f for f in i.findings if f.code == "tool_incomplete")
    assert tool.facts["missing"] == ["no cost"]          # the fixture ticks one use
    unread = next(f for f in i.findings if f.code == "license_unread" and f.facts["name"] == "Mystery")
    assert unread.facts["persona_ids"] == ["b"]


def test_findings_for_the_move():
    i = interpret(_raw(
        personas=(RawPersona("a", "A", 100),),
        microsoft_coverage={"today": frozenset({"o1", "o4"}), "m365-e3": frozenset({"o1", "o2"})},
        licence_lines=(_line(persona_ids=("a",), quantity_assigned=100, quantity_purchased=100,
                             linked_bundle_id="today", renewal_date=__import__("datetime").date(2027, 1, 1)),),
        scenarios=(_plan(),)))
    dropped = next(f for f in i.findings if f.code == "capability_dropped")
    assert dropped.outcome_ids == ("o4",)
    gap = next(f for f in i.findings if f.code == "gap_unanswered")
    assert gap.outcome_ids == ("o2",) and gap.facts["count"] == 1
    # A target with no coverage can't be compared at all.
    j = interpret(_raw(personas=(RawPersona("a", "A", 100),),
                       scenarios=(_plan(cover_key="nothing", resolved_bundle_id="nothing"),)))
    assert "target_unmapped" in _codes(j)


def test_headcount_against_employees():
    i = interpret(_raw(employee_count=500))                      # personas add up to 400
    [f] = [f for f in i.findings if f.code == "headcount_vs_employees"]
    assert f.severity == "info" and f.facts == {"total": 400, "employees": 500}
    assert "headcount_vs_employees" not in _codes(interpret(_raw(employee_count=400)))


def test_display_figures_come_from_the_interpreter():
    i = interpret(_raw(
        personas=(RawPersona("a", "A", 100), RawPersona("c", "Carved", 30, parent_persona_id="a")),
        licence_lines=(_line(quantity_purchased=450, price_override=True,
                             overridden_price_annual=D("324")),),
        scenarios=(_plan(discount_pct=D("0.25")),)))
    line = i.licence("l")
    assert line.pct_off_list == D("0.25")                      # 324 vs 432
    assert line.unused_seats == 50 and line.unused_annual == D("16200")
    sc = i.scenarios[0]
    assert sc.discounted_net_annual == D("324.00") and sc.pct_off_list == D("0.25")
    assert {p.id: p.carved_out_seats for p in i.personas} == {"a": 30, "c": 0}
