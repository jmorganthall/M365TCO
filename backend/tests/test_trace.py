"""Trace (docs/LAYER_AUDIT.md Phase 4): a persona's figures, each traced to the
derived values and entries behind it — and each adding up."""

import pytest


def _engagement(client):
    eid = client.post("/api/engagements", json={"customer_name": "Trace Co"}).json()["id"]
    outcomes = {o["seed_key"]: o for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    kw = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 300}).json()
    fl = client.post(f"/api/engagements/{eid}/personas", json={"name": "FL", "headcount": 100}).json()
    # One line tagged to KW, one untagged (Org-wide: shared 300/400 vs 100/400).
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_assigned": 300,
        "unit_price_paid_annual": 300, "persona_ids": [kw["id"]],
        "price_override": True, "overridden_price_annual": 240})
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E1", "quantity_assigned": 400,
        "unit_price_paid_annual": 120, "persona_ids": []})
    okta = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 4000, "cost_period": "Monthly", "is_managed": True,
        "persona_ids": [kw["id"]]}).json()
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": outcomes["identity-sso"]["id"], "product_kind": "ThirdParty",
        "third_party_product_id": okta["id"], "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 684, "target_discount_pct": 0.1})
    return eid, kw, fl


def test_every_figure_adds_up_and_reaches_the_entries(client):
    eid, kw, fl = _engagement(client)
    t = client.get(f"/api/engagements/{eid}/trace/personas/{kw['id']}").json()
    fig = {f["key"]: f for f in t["figures"]}
    for f in t["figures"]:
        assert f["rule"]
        if f["parts"]:
            assert sum(p["value"] for p in f["parts"]) == pytest.approx(f["value"], abs=0.01), f["key"]

    today = fig["today_microsoft"]
    # 300 × 240 (negotiated) + 400 × 120 × 300/400 = 72,000 + 36,000.
    assert today["value"] == pytest.approx(108000)
    e3 = next(p for p in today["parts"] if p["label"] == "Office 365 E3")
    assert e3["interpreted"]["price_source"] == "override" and e3["raw"]["step"] == "groups"
    e1 = next(p for p in today["parts"] if p["label"] == "Office 365 E1")
    assert e1["interpreted"]["applies_to_assumed"] and e1["interpreted"]["share"] == "300 of 400 people"

    [okta] = fig["tools_retired"]["parts"]
    # $4,000/mo → $48,000/yr, managed at the 30% default → $14,400 for 300 people = $48 each.
    assert okta["interpreted"]["annual_cost"] == 48000 and okta["interpreted"]["effective_annual_cost"] == 14400
    assert okta["interpreted"]["tooling_source"] == "engagement" and okta["value"] == pytest.approx(14400)
    assert okta["raw"]["step"] == "tools" and okta["raw"]["tooling_pct"] is None

    [plan] = fig["target"]["parts"]
    assert plan["interpreted"]["net_annual"] == pytest.approx(615.6)       # 684 × 0.9
    assert fig["target"]["value"] == pytest.approx(300 * 615.6)
    assert fig["change"]["value"] == pytest.approx(
        fig["target"]["value"] - today["value"] - fig["tools_retired"]["value"])
    assert t["headline_items"], "the persona's timed amounts are listed"


def test_a_persona_without_a_plan_traces_what_it_holds(client):
    eid, kw, fl = _engagement(client)
    t = client.get(f"/api/engagements/{eid}/trace/personas/{fl['id']}").json()
    assert not t["persona"]["has_plan"] and [f["key"] for f in t["figures"]] == ["today_microsoft"]
    # Its share of the untagged line: 400 × 120 × 100/400.
    assert t["figures"][0]["value"] == pytest.approx(12000)
    assert client.get(f"/api/engagements/{eid}/trace/personas/nope").status_code == 404
