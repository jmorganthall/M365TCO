"""Explicit per-line price override: a negotiated rate that differs from catalog
list. The SKU/outcome is unchanged; the override drives current + target spend and
supersedes the scenario discount, and is disclosed on the readout."""


def _setup(client):
    eng = client.post("/api/engagements", json={"customer_name": "Override Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 100}).json()
    return eid, p


def test_current_license_override_drives_current_spend(client):
    """List baseline is $600/seat/yr but the customer pays a negotiated $400 — the
    override, not the list, is the load-bearing current spend."""
    eid, p = _setup(client)
    lic = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E5", "quantity_assigned": 100,
        "unit_price_paid_annual": 600, "price_override": True,
        "overridden_price_annual": 400, "persona_ids": [p["id"]]}).json()
    assert lic["price_override"] is True
    assert float(lic["overridden_price_annual"]) == 400
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 600, "in_scope": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    # Current spend uses the override (400 × 100), not the list baseline (600 × 100).
    assert r["scenarios"][0]["current_spend_annual"] == 40000.0


def test_current_license_without_override_uses_list(client):
    """With no override the list baseline is what the customer pays (spend
    unchanged from the pre-override behavior)."""
    eid, p = _setup(client)
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E5", "quantity_assigned": 100,
        "unit_price_paid_annual": 600, "persona_ids": [p["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 600, "in_scope": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    assert r["scenarios"][0]["current_spend_annual"] == 60000.0


def test_scenario_override_supersedes_discount(client):
    """A 50% discount on a $600 list would net $300, but an active override says the
    customer pays $250 flat — the override wins for target spend."""
    eid, p = _setup(client)
    s = client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 600, "target_discount_pct": 0.5,
        "price_override": True, "overridden_price_annual": 250,
        "in_scope": True}).json()
    assert s["price_override"] is True
    r = client.post(f"/api/engagements/{eid}/compute").json()
    assert r["scenarios"][0]["target_spend_annual"] == 25000.0


def test_scenario_override_off_falls_back_to_discounted_net(client):
    """Clearing the override returns the target to the discounted composed net."""
    eid, p = _setup(client)
    s = client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 600, "target_discount_pct": 0.5,
        "price_override": True, "overridden_price_annual": 250,
        "in_scope": True}).json()
    client.patch(f"/api/engagements/{eid}/scenarios/{s['id']}", json={"price_override": False})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    # 600 × (1 − 0.5) × 100 = 30000 (discount applies again).
    assert r["scenarios"][0]["target_spend_annual"] == 30000.0


def test_override_disclosed_in_readout(client):
    """The readout discloses the negotiated override and the implied % off list,
    both in the current-licensing table and the appendix."""
    eid, p = _setup(client)
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E5", "quantity_assigned": 100,
        "unit_price_paid_annual": 600, "price_override": True,
        "overridden_price_annual": 300, "persona_ids": [p["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})
    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "Negotiated price overrides" in html
    assert "−50% vs list" in html          # (600 − 300) / 600
    assert "custom price" in html          # the current-licensing row note


def test_override_survives_clone(client):
    """Cloning an engagement carries the override flag + manual price across."""
    eid, p = _setup(client)
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E5", "quantity_assigned": 100,
        "unit_price_paid_annual": 600, "price_override": True,
        "overridden_price_annual": 375, "persona_ids": [p["id"]]})
    clone = client.post(f"/api/engagements/{eid}/duplicate").json()
    lic = client.get(f"/api/engagements/{clone['id']}/current-licenses").json()[0]
    assert lic["price_override"] is True
    assert float(lic["overridden_price_annual"]) == 375
