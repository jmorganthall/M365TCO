"""One-off Microsoft licences (TARGET_SCHEMA D31 (a), ENGINE_SPEC 6.0, 6.8,
GUIDED_PLAN G23): seats no persona holds are carried forward at the same cost
before and after, so they never change a delta. They show only in the expected
total Microsoft spend."""

from dataclasses import replace
from decimal import Decimal as D

from tco_engine.engine import compute
from tco_engine.interpret import (
    RawEngagement, RawLicenseLine, RawLimit, RawPersona, RawScenario, interpret, to_engine,
)


def _raw(**kw):
    base = dict(
        id="e",
        personas=(RawPersona("a", "A", 100), RawPersona("b", "B", 300)),
        licence_lines=(), tools=(), scenarios=(),
        microsoft_coverage={"m365-e3": frozenset({"o1", "o2"}), "bp": frozenset({"o1"})},
        tool_coverage={},
    )
    base.update(kw)
    return RawEngagement(**base)


def _line(**kw):
    base = dict(id="l", sku_reference="Microsoft 365 E3", quantity_assigned=400,
                quantity_purchased=400, list_price_annual=D("432"), price_override=False,
                overridden_price_annual=None, persona_ids=(), resolved_bundle_id="m365-e3")
    base.update(kw)
    return RawLicenseLine(**base)


def _plan(**kw):
    base = dict(id="s", persona_id="a", target_sku_reference="Microsoft 365 E3",
                list_price_annual=D("300"), discount_pct=None, price_override=False,
                overridden_price_annual=None, in_scope=True, resolved_bundle_id="m365-e3")
    base.update(kw)
    return RawScenario(**base)


def _codes(i, licence_id="l"):
    return {f.code for f in i.findings if f.licence_id == licence_id}


def _rollup(raw):
    return compute(to_engine(interpret(raw))).rollup


# ---------------------------------------------------------------------------
# The interpreter: which seats are one-off
# ---------------------------------------------------------------------------

def test_a_line_marked_one_off_is_held_by_no_persona():
    i = interpret(_raw(licence_lines=(_line(id="x", quantity_assigned=2, quantity_purchased=2,
                                            one_off=True),)))
    x = i.licence("x")
    assert x.one_off and x.applies_to == () and not x.assumed
    assert x.one_off_seats == 2 and x.one_off_annual == D("864")
    assert not any(lid == "x" for lid, _ in i.licence_shares)
    # Nothing to tag, nothing to ask: it was answered by marking it.
    assert _codes(i, "x") == set()


def test_extra_seats_are_one_off_until_the_personas_are_said_to_hold_them():
    # A has 100 people; the line has 120 seats.
    line = _line(persona_ids=("a",), quantity_assigned=120, quantity_purchased=120)
    i = interpret(_raw(licence_lines=(line,)))
    l = i.licence("l")
    assert l.seats_question == "extra" and not l.one_off
    assert l.one_off_seats == 20 and l.one_off_annual == D("8640")
    assert i.licence_shares[("l", "a")] == D("43200")                 # 100 × 432
    assert "licence_extra_seats_unanswered" in _codes(i)
    assert "seats_vs_headcount" not in _codes(i)

    held = interpret(_raw(licence_lines=(replace(line, seats_answer="HeldByPersonas"),)))
    assert held.licence("l").one_off_seats == 0
    assert held.licence_shares[("l", "a")] == D("51840")              # 120 × 432
    assert _codes(held) == set()

    answered = interpret(_raw(licence_lines=(replace(line, seats_answer="OneOff"),)))
    assert answered.licence("l").one_off_seats == 20
    assert _codes(answered) == set()


def test_an_untagged_line_with_fewer_seats_than_people_asks_who_holds_it():
    # 46 seats, 400 people, no persona: nobody can say who holds it.
    line = _line(quantity_assigned=46, quantity_purchased=46)
    i = interpret(_raw(licence_lines=(line,)))
    l = i.licence("l")
    assert l.seats_question == "holders" and l.one_off and l.applies_to == ()
    assert l.one_off_annual == D("19872")                             # 46 × 432
    assert not any(lid == "l" for lid, _ in i.licence_shares)
    assert "licence_holders_unknown" in _codes(i)
    assert "licence_untagged" not in _codes(i)

    everyone = interpret(_raw(licence_lines=(replace(line, seats_answer="HeldByPersonas"),)))
    e = everyone.licence("l")
    assert not e.one_off and e.applies_to == ("a", "b") and e.one_off_annual == 0
    assert sum(everyone.licence_shares.values()) == D("19872")
    assert _codes(everyone) == {"licence_untagged"}


def test_a_tenant_wide_line_is_never_one_off():
    i = interpret(_raw(licence_lines=(_line(quantity_assigned=1, quantity_purchased=1,
                                            coverage_scope="TenantWide"),)))
    l = i.licence("l")
    assert l.seats_question is None and l.one_off_seats == 0 and not l.one_off


def test_a_tagged_line_with_fewer_seats_keeps_the_old_reading():
    # The tagged personas hold it; the seat count is only an info finding.
    i = interpret(_raw(licence_lines=(_line(persona_ids=("b",), quantity_assigned=250,
                                            quantity_purchased=250),)))
    l = i.licence("l")
    assert l.seats_question is None and l.one_off_seats == 0
    assert "seats_vs_headcount" in _codes(i)


def test_one_off_seats_hold_cap_seats_as_unattributed():
    cap = RawLimit(key="biz", name="Business", limit_type="max_total_seats", max_quantity=300,
                   member_bundle_ids=frozenset({"bp"}))
    i = interpret(_raw(
        licence_limits=(cap,),
        licence_lines=(_line(id="bp-line", persona_ids=("b",), quantity_assigned=310,
                             quantity_purchased=310, resolved_bundle_id="bp"),)))
    [c] = i.seat_caps
    assert c.unattributed == 10                                       # 310 seats for 300 people
    assert c.usage["b"] == 300


# ---------------------------------------------------------------------------
# The engine: one-off spend never changes a delta
# ---------------------------------------------------------------------------

def test_a_one_off_licence_changes_no_delta_only_the_expected_total():
    base = _raw(licence_lines=(_line(persona_ids=("a",), quantity_assigned=100,
                                     quantity_purchased=100),),
                scenarios=(_plan(),))
    extra = replace(base, licence_lines=base.licence_lines + (
        _line(id="x", sku_reference="Visio Plan 2", quantity_assigned=3, quantity_purchased=3,
              list_price_annual=D("180"), one_off=True, resolved_bundle_id=None),))
    r0, r1 = _rollup(base), _rollup(extra)
    assert r1.net_tco_delta_annual == r0.net_tco_delta_annual
    assert r1.existing_microsoft_annual == r0.existing_microsoft_annual
    assert r1.one_off_microsoft_annual == D("540.00") and r0.one_off_microsoft_annual == 0
    assert r1.expected_microsoft_today_annual - r0.expected_microsoft_today_annual == D("540.00")
    assert r1.expected_microsoft_after_annual - r0.expected_microsoft_after_annual == D("540.00")


def test_extra_seats_are_carried_forward_unless_the_personas_hold_them():
    line = _line(persona_ids=("a",), quantity_assigned=120, quantity_purchased=120)
    one_off = _rollup(_raw(licence_lines=(line,), scenarios=(_plan(),)))
    held = _rollup(_raw(licence_lines=(replace(line, seats_answer="HeldByPersonas"),),
                        scenarios=(_plan(),)))
    # A's 100 people move; the 20 extra seats stay unless A holds them.
    assert one_off.existing_microsoft_annual == D("43200.00")
    assert held.existing_microsoft_annual == D("51840.00")
    assert one_off.one_off_microsoft_annual == D("8640.00")
    assert one_off.expected_microsoft_today_annual == held.expected_microsoft_today_annual


def test_the_expected_totals_bridge_to_the_microsoft_part_of_the_delta():
    raw = _raw(licence_lines=(
        _line(persona_ids=("a",), quantity_assigned=120, quantity_purchased=120),
        _line(id="m", persona_ids=("b",), quantity_assigned=300, quantity_purchased=300),
        _line(id="x", quantity_assigned=2, quantity_purchased=2, one_off=True)),
        scenarios=(_plan(),))
    r = _rollup(raw)
    assert r.expected_microsoft_today_annual == D("182304.00")        # 422 × 432
    assert (r.expected_microsoft_after_annual - r.expected_microsoft_today_annual
            == r.target_microsoft_annual - r.existing_microsoft_annual)


def test_the_rollup_serves_the_expected_totals(client):
    eid = client.post("/api/engagements", json={"customer_name": "One-off Co"}).json()["id"]
    client.post(f"/api/engagements/{eid}/personas", json={"name": "A", "headcount": 10})
    lic = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Visio Plan 2", "quantity_purchased": 2, "quantity_assigned": 2,
        "unit_price_paid_annual": 180, "one_off": True}).json()
    assert lic["one_off"] is True and lic["seats_answer"] is None
    rollup = client.post(f"/api/engagements/{eid}/compute").json()["rollup"]
    assert rollup["one_off_microsoft_annual"] == 360
    assert rollup["expected_microsoft_today_annual"] == 360
    assert rollup["expected_microsoft_after_annual"] == 360
    assert rollup["net_tco_delta_annual"] == 0
    upd = client.patch(f"/api/engagements/{eid}/current-licenses/{lic['id']}",
                       json={"seats_answer": "HeldByPersonas"}).json()
    assert upd["seats_answer"] == "HeldByPersonas"
