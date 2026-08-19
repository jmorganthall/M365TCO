"""Persona carve-out: modelling a PARTIAL move as real, visible data.

The licensing unit here is the persona — one persona, one scenario, one target —
so "300 of these 2,518 people move to Business Premium" cannot be expressed
inside one persona without inventing a hidden sub-population. Carving creates a
second persona instead, linked to the first, that every existing consumer already
knows how to cost.

This replaced the automatic Business Premium swap, which moved WHOLE personas
under a 300-seat tenant cap and so could never fire on a persona larger than 300.
"""

_CSV = (
    "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
    "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment\n"
    "Microsoft 365 E3,C1,001,Microsoft 365 E3,P1Y,Annual,US,USD,384,2026-01-01,2026-12-31,432,Commercial\n"
    "Microsoft 365 Business Premium,C2,002,Microsoft 365 Business Premium,P1Y,Annual,US,USD,264,2026-01-01,2026-12-31,264,Commercial\n"
)


def _setup(client, headcount=2518):
    """One large persona on Office 365 E3 today, targeting Microsoft 365 E3 —
    the shape the old swap could never act on."""
    client.post("/api/catalog/import-csv", files={"file": ("p.csv", _CSV, "text/csv")})
    eid = client.post("/api/engagements", json={"customer_name": "Carve Co"}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "Property Ops", "headcount": headcount}).json()
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_purchased": headcount,
        "quantity_assigned": headcount, "unit_price_paid_annual": 432,
        "persona_ids": [p["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 432, "in_scope": True})
    return eid, p


def test_carve_preview_reports_exactly_what_would_be_copied(client):
    """The GUI warns before carving, and the warning is built from this — so it
    lists what the carve would ACTUALLY copy rather than a frontend guess that can
    drift from the endpoint's behaviour."""
    eid, parent = _setup(client)
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    desktop = next(o for o in outcomes if o["seed_key"] == "desktop-software")
    client.patch(f"/api/engagements/{eid}/personas/{parent['id']}",
                 json={"required_outcome_ids": [desktop["id"]]})
    client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 50000, "persona_ids": [parent["id"]]})

    pv = client.get(f"/api/engagements/{eid}/personas/{parent['id']}/carve-preview").json()
    assert pv["persona_name"] == "Property Ops" and pv["headcount"] == 2518
    assert pv["current_licenses"] == ["Office 365 E3"]
    assert pv["third_party_tools"] == ["Okta"]
    assert pv["required_capabilities"] == [desktop["name"]]
    assert pv["has_scenario"] is True
    assert pv["scenario_target"] == "Microsoft 365 E3"


def test_carve_preview_shows_an_empty_baseline_as_empty(client):
    """Carving before the baseline is entered copies nothing — the operator's cue
    that they are too early. The preview must say so rather than imply inheritance
    that will not happen."""
    eid = client.post("/api/engagements", json={"customer_name": "Early Co"}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 500}).json()

    pv = client.get(f"/api/engagements/{eid}/personas/{p['id']}/carve-preview").json()
    assert pv["current_licenses"] == []
    assert pv["third_party_tools"] == []
    assert pv["required_capabilities"] == []
    assert pv["has_scenario"] is False and pv["scenario_target"] == ""


def test_carve_preview_matches_what_carving_actually_copies(client):
    """The promise and the act must agree: everything the preview lists is what the
    carve-out ends up holding."""
    eid, parent = _setup(client)
    client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 50000, "persona_ids": [parent["id"]]})
    pv = client.get(f"/api/engagements/{eid}/personas/{parent['id']}/carve-preview").json()

    child = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                        json={"seats": 300}).json()

    got_licences = sorted(l["sku_reference"] for l in
                          client.get(f"/api/engagements/{eid}/current-licenses").json()
                          if child["id"] in l["persona_ids"])
    got_tools = sorted(t["name"] for t in
                       client.get(f"/api/engagements/{eid}/third-party").json()
                       if child["id"] in t["persona_ids"])
    assert got_licences == pv["current_licenses"]
    assert got_tools == pv["third_party_tools"]


def test_carve_onto_a_different_plan_is_priced_as_that_plan(client):
    """A carve-out onto Business Premium must be quoted at Business Premium's rate.
    Inheriting the parent's $/seat would put an E3 price on a BP row — a number on
    the readout nobody could buy."""
    eid, parent = _setup(client)
    child = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve", json={
        "seats": 300, "target_sku_reference": "Microsoft 365 Business Premium"}).json()

    scenarios = client.get(f"/api/engagements/{eid}/scenarios").json()
    child_scenario = next(s for s in scenarios if s["persona_id"] == child["id"])
    assert float(child_scenario["target_unit_price_annual"]) == 264.0   # not the parent's 432
    # An explicit price still wins over the requote.
    other = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve", json={
        "seats": 50, "target_sku_reference": "Microsoft 365 Business Premium",
        "target_unit_price_annual": 199}).json()
    scenarios = client.get(f"/api/engagements/{eid}/scenarios").json()
    assert float(next(s for s in scenarios
                      if s["persona_id"] == other["id"])["target_unit_price_annual"]) == 199.0


def test_carve_moves_seats_and_records_where_they_came_from(client):
    eid, parent = _setup(client)
    child = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve", json={
        "seats": 300, "target_sku_reference": "Microsoft 365 Business Premium",
        "target_unit_price_annual": 264}).json()

    assert child["headcount"] == 300
    assert child["parent_persona_id"] == parent["id"]
    # Default name says what it is, so the split reads as itself in the list.
    assert child["name"] == "Property Ops — Microsoft 365 Business Premium"

    personas = client.get(f"/api/engagements/{eid}/personas").json()
    # Population conserved: the seats moved, they were not created.
    assert sum(p["headcount"] for p in personas) == 2518
    assert next(p for p in personas if p["id"] == parent["id"])["headcount"] == 2218


def test_carve_out_inherits_what_those_people_already_hold(client):
    """They are the same people they were a moment ago — same licensing, same
    tools, same required capabilities. Only their future target differs."""
    eid, parent = _setup(client)
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    desktop = next(o for o in outcomes if o["seed_key"] == "desktop-software")
    client.patch(f"/api/engagements/{eid}/personas/{parent['id']}",
                 json={"required_outcome_ids": [desktop["id"]]})
    tool = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 50000, "persona_ids": [parent["id"]]}).json()

    child = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                        json={"seats": 300}).json()

    lic = client.get(f"/api/engagements/{eid}/current-licenses").json()[0]
    assert child["id"] in lic["persona_ids"]
    tools = client.get(f"/api/engagements/{eid}/third-party").json()
    assert child["id"] in tools[0]["persona_ids"]
    assert client.get(f"/api/engagements/{eid}/personas").json()
    kid = next(p for p in client.get(f"/api/engagements/{eid}/personas").json()
               if p["id"] == child["id"])
    assert kid["required_outcome_ids"] == [desktop["id"]]
    # Covers derive from tagged headcounts: parent + child still sum to the whole.
    assert tools[0]["covered_count"] == 2518
    assert tool["covered_count"] == 2518


def test_carve_splits_todays_spend_instead_of_moving_it(client):
    """A carve-out changes the FUTURE state only. Today's bill is the same money,
    now shown against two rows in proportion to where the people went — the carved
    seats must not read as a population that holds nothing."""
    eid, parent = _setup(client)
    before = client.post(f"/api/engagements/{eid}/compute").json()
    total_before = before["rollup"]["existing_microsoft_annual"]

    client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve", json={
        "seats": 300, "target_sku_reference": "Microsoft 365 Business Premium",
        "target_unit_price_annual": 264})

    after = client.post(f"/api/engagements/{eid}/compute").json()
    assert after["rollup"]["existing_microsoft_annual"] == total_before
    by_persona = {s["persona_id"]: s["current_microsoft_annual"] for s in after["scenarios"]}
    child_id = next(p["id"] for p in client.get(f"/api/engagements/{eid}/personas").json()
                    if p["parent_persona_id"] == parent["id"])
    # 2518 seats × $432 = $1,087,776, split 2218 / 300.
    assert by_persona[parent["id"]] == 958176.0
    assert by_persona[child_id] == 129600.0
    assert by_persona[parent["id"]] + by_persona[child_id] == total_before


def test_carved_seats_count_against_the_business_cap(client):
    """The 300-seat Business cap is what makes carving necessary, so the carve-out's
    seats must be counted by the same guardrail — no special-casing."""
    eid, parent = _setup(client)
    client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve", json={
        "seats": 300, "target_sku_reference": "Microsoft 365 Business Premium",
        "target_unit_price_annual": 264})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    biz = next(l for l in r["license_limits"] if l["key"] == "m365-business-seat-cap")
    assert biz["target_seats"] == 300 and biz["violated"] is False


def test_deleting_a_carve_out_returns_its_seats(client):
    """A split you cannot reverse is a trap: undoing it must restore the
    population, not quietly shrink the customer."""
    eid, parent = _setup(client)
    child = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                        json={"seats": 300}).json()
    client.delete(f"/api/engagements/{eid}/personas/{child['id']}")

    personas = client.get(f"/api/engagements/{eid}/personas").json()
    assert len(personas) == 1
    assert personas[0]["headcount"] == 2518


def test_deleting_the_parent_keeps_the_carve_outs_people(client):
    """Removing the persona a carve-out came from must not delete real people as a
    side effect — the carve-out survives as a standalone persona."""
    eid, parent = _setup(client)
    child = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                        json={"seats": 300}).json()
    client.delete(f"/api/engagements/{eid}/personas/{parent['id']}")

    personas = client.get(f"/api/engagements/{eid}/personas").json()
    assert [p["id"] for p in personas] == [child["id"]]
    assert personas[0]["headcount"] == 300
    assert personas[0]["parent_persona_id"] is None


def test_carve_refuses_to_empty_a_persona_or_nest(client):
    eid, parent = _setup(client, headcount=400)
    whole = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                        json={"seats": 400})
    assert whole.status_code == 422 and "leave nothing behind" in whole.text
    assert client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                       json={"seats": 0}).status_code == 422

    child = client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                        json={"seats": 100}).json()
    nested = client.post(f"/api/engagements/{eid}/personas/{child['id']}/carve",
                         json={"seats": 10})
    assert nested.status_code == 422 and "lineage" in nested.text


def test_duplicating_an_engagement_keeps_the_lineage(client):
    eid, parent = _setup(client)
    client.post(f"/api/engagements/{eid}/personas/{parent['id']}/carve",
                json={"seats": 300})
    dup = client.post(f"/api/engagements/{eid}/duplicate").json()

    personas = client.get(f"/api/engagements/{dup['id']}/personas").json()
    child = next(p for p in personas if p["parent_persona_id"])
    new_parent = next(p for p in personas if not p["parent_persona_id"])
    # The link points at the COPY's parent, not the original engagement's.
    assert child["parent_persona_id"] == new_parent["id"]
    assert {p["headcount"] for p in personas} == {2218, 300}
