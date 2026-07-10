"""Unit tests for the pure reconciliation engine (PRD Section 6 + 12).

Covers the mandated cases:
  - the worked Okta 500-versus-450 partial-displacement case
  - the renewal-elimination gating rule
  - the override disclosure rule
plus the managed split, in-scope toggle recompute, and negative-delta honesty.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from tco_engine import (
    CandidateBundle,
    Coverage,
    CurrentLicenseLine,
    Disposition,
    Engagement,
    Override,
    Persona,
    PersonaScenario,
    ResidualIntent,
    ThirdPartyProduct,
    analyze_bundles,
    compute,
)

D = Decimal

# Outcome ids used across tests
IDENTITY = "identity-mfa"
EMAIL_SEC = "email-security"
ENDPOINT = "endpoint-mgmt"


def _engagement(personas, products, scenarios, current=None):
    # `current` stays a {persona_id: [lines]} map for test convenience; flatten it
    # into the engine's tagged-line list, tagging each line with its persona.
    lines = []
    for pid, pls in (current or {}).items():
        for pl in pls:
            lines.append(replace(pl, persona_ids=(pid,)))
    return Engagement(
        id="eng-1",
        personas=personas,
        third_party_products=products,
        scenarios=scenarios,
        current_licenses=lines,
    )


def test_untagged_current_license_counts_orgwide_and_is_retired():
    """An untagged current Microsoft license (the operator didn't attribute it to
    a persona) must still count as current spend and be retired when the persona
    moves — not silently vanish. Mirrors the reported Business Premium -> E5 case."""
    kw = Persona(id="kw", name="All Employees", headcount=250)
    # 255 Business Premium @ $211.20/yr, entered WITHOUT a persona tag.
    bp = CurrentLicenseLine(
        quantity_assigned=255, unit_price_paid_annual=D("211.20"), persona_ids=()
    )
    scenario = PersonaScenario(
        id="s", persona_id="kw", target_sku_reference="E5",
        target_unit_price_annual=D("720"),  # 250 * 720 = 180000
    )
    eng = Engagement(
        id="e", personas=[kw], third_party_products=[], scenarios=[scenario],
        current_licenses=[bp],
    )
    res = compute(eng)
    sc = res.scenarios[0]
    # 255 * 211.20 = 53856 attributed to the sole scenario persona (full share).
    assert sc.current_microsoft_annual == D("53856.00")
    assert sc.current_spend_annual == D("53856.00")
    assert sc.target_spend_annual == D("180000.00")
    # cost increase: 180000 - 53856 = 126144 (positive = costs more)
    assert sc.delta_annual == D("126144.00")
    r = res.rollup
    assert r.existing_microsoft_annual == D("53856.00")  # the "retired" line, non-zero
    assert r.net_tco_delta_annual == D("126144.00")


def test_quick_wins_flags_duplicates_current_licensing_already_covers():
    """A third-party product whose outcomes the CURRENT licensing already delivers
    is a quick win — droppable today, with no scenario move. Save-$X-today."""
    kw = Persona(id="kw", name="All Employees", headcount=250)
    # Current Business Premium already delivers Identity (255 seats).
    bp = CurrentLicenseLine(
        quantity_assigned=255, unit_price_paid_annual=D("211.20"),
        covered_outcome_ids=frozenset({IDENTITY}),
    )
    okta = ThirdPartyProduct(
        id="okta", name="Okta", annual_cost=D("45000"), covered_count=250,
        delivered_outcome_ids=frozenset({IDENTITY}),  # duplicated today
    )
    sophos = ThirdPartyProduct(
        id="sophos", name="Sophos XDR", annual_cost=D("10800"), covered_count=300,
        delivered_outcome_ids=frozenset({ENDPOINT}),  # NOT covered by current BP
    )
    scenario = PersonaScenario(
        id="s", persona_id="kw", target_sku_reference="E5",
        target_unit_price_annual=D("0"),  # isolate quick-win logic from the move
    )
    res = compute(Engagement(
        id="e", personas=[kw], third_party_products=[okta, sophos],
        scenarios=[scenario], current_licenses=[bp],
    ))
    r = res.rollup
    # Okta duplicates current Identity coverage -> $45k quick win; Sophos does not.
    assert {q.third_party_product_id for q in r.quick_wins} == {"okta"}
    assert r.quick_win_savings_annual == D("45000.00")
    qw = r.quick_wins[0]
    assert qw.displaced_today == 250 and qw.residual_today == 0
    assert qw.credited_annual == D("45000.00")
    assert qw.duplicated_outcome_ids == [IDENTITY]


def test_okta_500_vs_450_partial_displacement():
    """Section 6.3 worked example. Okta covers 500; 450 Knowledge Workers
    displace it; 50 units remain and surface as a residual."""
    kw = Persona(id="kw", name="Knowledge Worker", headcount=450)
    okta = ThirdPartyProduct(
        id="okta",
        name="Okta",
        annual_cost=D("50000"),  # 500 * 100/yr
        covered_count=500,
        is_managed=False,
        delivered_outcome_ids=frozenset({IDENTITY}),
        renewal_date="2026-09-01",
    )
    scenario = PersonaScenario(
        id="kw-e3",
        persona_id="kw",
        target_sku_reference="E3",
        target_unit_price_annual=D("0"),  # isolate the offset math
        target_covered_outcome_ids=frozenset({IDENTITY}),
    )
    res = compute(_engagement([kw], [okta], [scenario]))

    disp = res.dispositions[0]
    assert disp.displaced_users == 450
    assert disp.residual_count == 50
    assert disp.disposition == Disposition.PARTIALLY_REDUCED
    # per-unit = 50000 / 500 = 100/yr; residual = 50 * 100 = 5000
    assert disp.per_unit_annual_cost == D("100")
    assert disp.residual_annual_cost == D("5000.00")
    # residual exists and is unclassified -> must force a choice
    assert disp.requires_residual_classification is True

    # offset credited to the persona = 450 * 100 = 45000
    sc = res.scenarios[0]
    assert sc.current_third_party_offset_annual == D("45000.00")
    assert sc.current_spend_annual == D("45000.00")
    assert sc.delta_annual == D("-45000.00")  # all saving (negative = saving), target price 0

    # residual surfaces in rollup
    assert res.rollup.residual_third_party_cost_annual == D("5000.00")
    # NOT fully eliminated -> renewal NOT eligible for elimination
    assert res.rollup.eliminated_renewal_cycles == []


def test_full_elimination_when_displaced_covers_population():
    kw = Persona(id="kw", name="Knowledge Worker", headcount=500)
    okta = ThirdPartyProduct(
        id="okta",
        name="Okta",
        annual_cost=D("50000"),
        covered_count=500,
        delivered_outcome_ids=frozenset({IDENTITY}),
        renewal_date="2026-09-01",
    )
    scenario = PersonaScenario(
        id="kw-e3",
        persona_id="kw",
        target_sku_reference="E3",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({IDENTITY}),
    )
    res = compute(_engagement([kw], [okta], [scenario]))
    disp = res.dispositions[0]
    assert disp.disposition == Disposition.FULLY_ELIMINATED
    assert disp.residual_count == 0
    assert res.rollup.fully_eliminated_tools == ["Okta"]
    # Rule 1: renewal eligible only on full elimination
    assert len(res.rollup.eliminated_renewal_cycles) == 1
    assert res.rollup.eliminated_renewal_cycles[0].renewal_date == "2026-09-01"


def test_renewal_gating_partial_does_not_eliminate_cycle():
    """Section 6.9 rule 1: a product with any residual still renews."""
    kw = Persona(id="kw", name="KW", headcount=100)
    tool = ThirdPartyProduct(
        id="t",
        name="Tool",
        annual_cost=D("20000"),
        covered_count=200,
        delivered_outcome_ids=frozenset({EMAIL_SEC}),
        renewal_date="2026-12-01",
    )
    scenario = PersonaScenario(
        id="s",
        persona_id="kw",
        target_sku_reference="E5",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({EMAIL_SEC}),
    )
    res = compute(_engagement([kw], [tool], [scenario]))
    assert res.dispositions[0].disposition == Disposition.PARTIALLY_REDUCED
    assert res.rollup.eliminated_renewal_cycles == []


def test_override_force_full_elimination_requires_reason_and_zeros_residual():
    """Section 6.9 rule 2 + 6.4 override branch."""
    kw = Persona(id="kw", name="KW", headcount=100)
    tool = ThirdPartyProduct(
        id="t",
        name="Tool",
        annual_cost=D("20000"),
        covered_count=200,
        delivered_outcome_ids=frozenset({EMAIL_SEC}),
        renewal_date="2026-12-01",
        override=Override.FORCE_FULL_ELIMINATION,
        override_reason="Customer confirmed remaining 100 are decommissioned.",
    )
    scenario = PersonaScenario(
        id="s",
        persona_id="kw",
        target_sku_reference="E5",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({EMAIL_SEC}),
    )
    res = compute(_engagement([kw], [tool], [scenario]))
    disp = res.dispositions[0]
    assert disp.disposition == Disposition.FULLY_ELIMINATED
    assert disp.residual_count == 0
    assert disp.residual_annual_cost == D("0")
    assert disp.override == Override.FORCE_FULL_ELIMINATION
    assert disp.override_reason  # prints on the readout
    # override forces elimination -> renewal now eligible
    assert len(res.rollup.eliminated_renewal_cycles) == 1


def test_intended_out_of_scope_residual_is_not_an_override():
    kw = Persona(id="kw", name="KW", headcount=450)
    okta = ThirdPartyProduct(
        id="okta",
        name="Okta",
        annual_cost=D("50000"),
        covered_count=500,
        delivered_outcome_ids=frozenset({IDENTITY}),
        residual_intent=ResidualIntent.INTENDED_OUT_OF_SCOPE,
    )
    scenario = PersonaScenario(
        id="s",
        persona_id="kw",
        target_sku_reference="E3",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({IDENTITY}),
    )
    res = compute(_engagement([kw], [okta], [scenario]))
    disp = res.dispositions[0]
    assert disp.disposition == Disposition.PARTIALLY_REDUCED
    assert disp.residual_intent == ResidualIntent.INTENDED_OUT_OF_SCOPE
    # classified -> no longer forces a choice
    assert disp.requires_residual_classification is False


def test_managed_split_uses_tooling_pct():
    """Section 6.5. A managed product counts at tooling_pct of cost."""
    kw = Persona(id="kw", name="KW", headcount=100)
    mdr = ThirdPartyProduct(
        id="r7",
        name="Rapid7 MDR",
        annual_cost=D("100000"),
        covered_count=100,
        is_managed=True,
        tooling_pct=D("0.20"),  # heavily managed
        delivered_outcome_ids=frozenset({ENDPOINT}),
    )
    scenario = PersonaScenario(
        id="s",
        persona_id="kw",
        target_sku_reference="E5",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({ENDPOINT}),
    )
    res = compute(_engagement([kw], [mdr], [scenario]))
    disp = res.dispositions[0]
    # effective = 100000 * 0.20 = 20000; per unit = 200
    assert disp.effective_annual_cost == D("20000.00")
    assert disp.per_unit_annual_cost == D("200")
    # full elimination, offset credited = 100 * 200 = 20000
    assert res.scenarios[0].current_third_party_offset_annual == D("20000.00")


def test_unmanaged_product_counts_full_cost():
    kw = Persona(id="kw", name="KW", headcount=100)
    tool = ThirdPartyProduct(
        id="t",
        name="Tool",
        annual_cost=D("10000"),
        covered_count=100,
        is_managed=False,
        delivered_outcome_ids=frozenset({EMAIL_SEC}),
    )
    scenario = PersonaScenario(
        id="s",
        persona_id="kw",
        target_sku_reference="E5",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({EMAIL_SEC}),
    )
    res = compute(_engagement([kw], [tool], [scenario]))
    assert res.dispositions[0].effective_annual_cost == D("10000")


def test_displacement_requires_all_outcomes_covered():
    """Section 6.6: product delivers an outcome the SKU lacks -> not displaced."""
    kw = Persona(id="kw", name="KW", headcount=100)
    tool = ThirdPartyProduct(
        id="t",
        name="MultiTool",
        annual_cost=D("10000"),
        covered_count=100,
        delivered_outcome_ids=frozenset({EMAIL_SEC, ENDPOINT}),
    )
    scenario = PersonaScenario(
        id="s",
        persona_id="kw",
        target_sku_reference="E3",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({EMAIL_SEC}),  # missing ENDPOINT
    )
    res = compute(_engagement([kw], [tool], [scenario]))
    disp = res.dispositions[0]
    assert disp.disposition == Disposition.UNCHANGED
    assert disp.displaced_users == 0
    assert res.scenarios[0].current_third_party_offset_annual == D("0.00")


def test_in_scope_toggle_recompute_flips_disposition():
    """Section 6.7: removing a persona recomputes dispositions totally."""
    kw = Persona(id="kw", name="KW", headcount=300)
    fl = Persona(id="fl", name="Frontline", headcount=200)
    tool = ThirdPartyProduct(
        id="t",
        name="Tool",
        annual_cost=D("50000"),
        covered_count=500,
        delivered_outcome_ids=frozenset({IDENTITY}),
        renewal_date="2027-01-01",
    )
    kw_s = PersonaScenario(
        id="kw-s",
        persona_id="kw",
        target_sku_reference="E3",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({IDENTITY}),
    )
    fl_s = PersonaScenario(
        id="fl-s",
        persona_id="fl",
        target_sku_reference="F3",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({IDENTITY}),
    )
    # both in scope -> 500 displaced -> fully eliminated
    res_all = compute(_engagement([kw, fl], [tool], [kw_s, fl_s]))
    assert res_all.dispositions[0].disposition == Disposition.FULLY_ELIMINATED
    assert len(res_all.rollup.eliminated_renewal_cycles) == 1

    # toggle frontline out -> only 300 displaced -> partially reduced, renewal back
    fl_s.in_scope = False
    res_one = compute(_engagement([kw, fl], [tool], [kw_s, fl_s]))
    assert res_one.dispositions[0].disposition == Disposition.PARTIALLY_REDUCED
    assert res_one.dispositions[0].residual_count == 200
    assert res_one.rollup.eliminated_renewal_cycles == []


def test_negative_delta_shown_honestly():
    """Section 6.2: M365 uplift can exceed the offset; show it as a cost."""
    kw = Persona(id="kw", name="KW", headcount=100)
    # current MS spend small, no third party; target SKU expensive
    current = {
        "kw": [CurrentLicenseLine(quantity_assigned=100, unit_price_paid_annual=D("100"))]
    }
    scenario = PersonaScenario(
        id="s",
        persona_id="kw",
        target_sku_reference="E5",
        target_unit_price_annual=D("600"),  # big uplift
    )
    res = compute(_engagement([kw], [], [scenario], current))
    sc = res.scenarios[0]
    # current = 100*100 = 10000; target = 100*600 = 60000; delta = +50000 (cost increase)
    assert sc.current_spend_annual == D("10000.00")
    assert sc.target_spend_annual == D("60000.00")
    assert sc.delta_annual == D("50000.00")
    assert res.rollup.net_tco_delta_annual == D("50000.00")


def test_line_shared_by_personas_splits_cost_by_headcount():
    """A license tagged to two personas distributes its total cost across their
    combined headcount — no double counting (Section 6.2)."""
    kw = Persona(id="kw", name="KW", headcount=500)
    fl = Persona(id="fl", name="FL", headcount=200)
    # One line of 700 seats @ 100 = 70000 total, applied to both personas.
    line = CurrentLicenseLine(quantity_assigned=700, unit_price_paid_annual=D("100"),
                              persona_ids=("kw", "fl"))
    s_kw = PersonaScenario(id="skw", persona_id="kw", target_sku_reference="E3",
                           target_unit_price_annual=D("0"))
    s_fl = PersonaScenario(id="sfl", persona_id="fl", target_sku_reference="F3",
                           target_unit_price_annual=D("0"))
    eng = Engagement(id="e", personas=[kw, fl], scenarios=[s_kw, s_fl],
                     current_licenses=[line])
    res = compute(eng)
    by = {r.persona_id: r for r in res.scenarios}
    # 70000 split 500:200 -> 50000 / 20000; the two sum back to the line total.
    assert by["kw"].current_microsoft_annual == D("50000.00")
    assert by["fl"].current_microsoft_annual == D("20000.00")
    # Both targets are $0, so the move retires all $70k of current spend: a saving.
    assert res.rollup.net_tco_delta_annual == D("-70000.00")


def test_rollup_excludes_out_of_scope_scenarios():
    kw = Persona(id="kw", name="KW", headcount=100)
    fl = Persona(id="fl", name="FL", headcount=50)
    s1 = PersonaScenario(
        id="s1", persona_id="kw", target_sku_reference="E3",
        target_unit_price_annual=D("0"), in_scope=True,
    )
    s2 = PersonaScenario(
        id="s2", persona_id="fl", target_sku_reference="F3",
        target_unit_price_annual=D("0"), in_scope=False,
    )
    current = {
        "kw": [CurrentLicenseLine(100, D("100"))],
        "fl": [CurrentLicenseLine(50, D("50"))],
    }
    res = compute(_engagement([kw, fl], [], [s1, s2], current))
    # only kw counted in rollup and headcount (target $0 -> retires $10k = saving)
    assert res.rollup.net_tco_delta_annual == D("-10000.00")
    assert res.rollup.in_scope_persona_headcount == 100


def test_spend_bridge_components_build_to_net_delta():
    """Section 6.8 spend bridge: existing Microsoft + freed-up third-party −
    target Microsoft == net delta, over the in-scope set only."""
    kw = Persona(id="kw", name="KW", headcount=100)
    okta = ThirdPartyProduct(
        id="okta", name="Okta", annual_cost=D("50000"), covered_count=100,
        delivered_outcome_ids=frozenset({IDENTITY}),
    )
    current = {"kw": [CurrentLicenseLine(quantity_assigned=100, unit_price_paid_annual=D("300"))]}
    scenario = PersonaScenario(
        id="s", persona_id="kw", target_sku_reference="E5",
        target_unit_price_annual=D("500"),
        target_covered_outcome_ids=frozenset({IDENTITY}),
    )
    res = compute(_engagement([kw], [okta], [scenario], current))
    r = res.rollup
    # existing MS = 100*300 = 30000; freed-up third-party (offset) = full 50000;
    # target = 100*500 = 50000; net = 30000 + 50000 - 50000 = 30000.
    assert r.existing_microsoft_annual == D("30000.00")
    assert r.existing_third_party_annual == D("50000.00")
    assert r.target_microsoft_annual == D("50000.00")
    assert (
        r.target_microsoft_annual
        - r.existing_microsoft_annual
        - r.existing_third_party_annual
        == r.net_tco_delta_annual
    )
    # one freed-up product, crediting the full offset
    assert len(r.freed_third_party) == 1
    assert r.freed_third_party[0].third_party_product_name == "Okta"
    assert r.freed_third_party[0].credited_annual == D("50000.00")


def test_freed_third_party_zero_credit_when_covered_count_zero():
    """A displaced/eliminated product with covered_count 0 has per-unit cost 0,
    so it frees $0 — surfaced honestly rather than as silent savings."""
    kw = Persona(id="kw", name="KW", headcount=200)
    edr = ThirdPartyProduct(
        id="edr", name="EDR Tool", annual_cost=D("102000"), covered_count=0,
        delivered_outcome_ids=frozenset({ENDPOINT}),
    )
    scenario = PersonaScenario(
        id="s", persona_id="kw", target_sku_reference="E5",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({ENDPOINT}),
    )
    res = compute(_engagement([kw], [edr], [scenario]))
    # displaced (covers ENDPOINT) and fully eliminated, but $0 freed
    assert res.dispositions[0].disposition == Disposition.FULLY_ELIMINATED
    assert res.rollup.existing_third_party_annual == D("0.00")
    assert len(res.rollup.freed_third_party) == 1
    assert res.rollup.freed_third_party[0].credited_annual == D("0.00")


def test_freed_third_party_excludes_out_of_scope_scenarios():
    """The bridge and freed-up list count in-scope scenarios only."""
    kw = Persona(id="kw", name="KW", headcount=100)
    okta = ThirdPartyProduct(
        id="okta", name="Okta", annual_cost=D("50000"), covered_count=100,
        delivered_outcome_ids=frozenset({IDENTITY}),
    )
    scenario = PersonaScenario(
        id="s", persona_id="kw", target_sku_reference="E3",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({IDENTITY}), in_scope=False,
    )
    res = compute(_engagement([kw], [okta], [scenario]))
    assert res.rollup.existing_third_party_annual == D("0.00")
    assert res.rollup.freed_third_party == []


def test_product_with_no_outcomes_is_never_displaced():
    kw = Persona(id="kw", name="KW", headcount=100)
    tool = ThirdPartyProduct(
        id="t", name="Mystery", annual_cost=D("1000"), covered_count=100,
        delivered_outcome_ids=frozenset(),
    )
    scenario = PersonaScenario(
        id="s", persona_id="kw", target_sku_reference="E3",
        target_unit_price_annual=D("0"),
        target_covered_outcome_ids=frozenset({IDENTITY}),
    )
    res = compute(_engagement([kw], [tool], [scenario]))
    assert res.dispositions[0].disposition == Disposition.UNCHANGED


# ---- Best-bundle optimizer (tco_engine.optimizer) ----
A, B, C = "outcome-a", "outcome-b", "outcome-c"


def test_analyze_bundles_recommends_max_savings_no_gap():
    # One third-party product P delivers A at $100/user/yr effective.
    p = ThirdPartyProduct(
        id="p", name="P", annual_cost=D("10000"), covered_count=100,
        delivered_outcome_ids=frozenset({A}),
    )
    candidates = [
        # E5-like: covers A+B, $60/seat
        CandidateBundle("E5", frozenset({A, B}), D("60")),
        # F-like: covers A only, $30/seat (cheaper, still displaces P)
        CandidateBundle("F", frozenset({A}), D("30")),
        # G-like: covers C only -> gap on required A, $10/seat
        CandidateBundle("G", frozenset({C}), D("10")),
    ]
    res = analyze_bundles(
        headcount=100,
        current_microsoft_annual=D("0"),
        required_outcome_ids=frozenset({A}),
        current_capability_outcome_ids=frozenset({A}),
        candidates=candidates,
        third_party_products=[p],
    )
    by_ref = {b.sku_reference: b for b in res}

    # F: target 3000, offset 10000, delta -7000 (biggest saving, best no-gap)
    assert by_ref["F"].delta_annual == D("-7000.00")
    # E5: target 6000, offset 10000, delta -4000; adds outcome B
    assert by_ref["E5"].delta_annual == D("-4000.00")
    assert B in by_ref["E5"].added_outcome_ids
    # G: does not cover required A -> gap, not recommended
    assert by_ref["G"].covers_all_required is False
    assert A in by_ref["G"].gap_outcome_ids
    assert by_ref["G"].recommended is False

    # Recommended = biggest-saving (lowest-delta), no-gap, priced bundle = F, sorted first.
    assert res[0].sku_reference == "F"
    assert res[0].recommended is True


def test_analyze_bundles_net_increase_still_shows_added_outcomes():
    # No third party; current MS cheap; bundle pricier but adds capabilities.
    candidates = [CandidateBundle("E5", frozenset({A, B, C}), D("50"))]
    res = analyze_bundles(
        headcount=100,
        current_microsoft_annual=D("1000"),  # they pay little today
        required_outcome_ids=frozenset({A}),
        current_capability_outcome_ids=frozenset({A}),
        candidates=candidates,
        third_party_products=[],
    )
    b = res[0]
    # target 5000 > current 1000 -> positive delta (net cost increase), shown honestly
    assert b.delta_annual == D("4000.00")
    # but new capabilities B and C surface as the upside
    assert set(b.added_outcome_ids) == {B, C}
    # net increase with no no-gap-priced... it still covers required A (no gap)
    assert b.covers_all_required is True
