"""Entitlement scope on a current-licensing line: does it entitle its assigned
seats, or the whole population it applies to?

The distinction is load-bearing for quick wins (ENGINE_SPEC 6.10). An UNTAGGED
line was previously read as "everyone holds this", so a 46-seat Microsoft 365 E3
entered org-wide credited thousands of seats of a duplicate MFA tool as
redundant today. A per-user line entitles exactly what was assigned; only a
tenant-wide line covers the whole population.
"""


def _setup(client):
    """Two personas that use a third-party MFA tool, plus the outcome ids the
    seeded coverage map uses for identity."""
    eid = client.post("/api/engagements", json={"customer_name": "Scope Co"}).json()["id"]
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    mfa = next(o for o in outcomes if o["seed_key"] == "identity-mfa")
    ops = client.post(f"/api/engagements/{eid}/personas",
                      json={"name": "Property Ops", "headcount": 2518}).json()
    cor = client.post(f"/api/engagements/{eid}/personas",
                      json={"name": "Corporate", "headcount": 632}).json()
    # Okta covers MFA & Conditional Access for both personas (3150 seats derived).
    okta = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta (MFA)", "raw_cost": 211100.90, "cost_period": "Annual",
        "persona_ids": [ops["id"], cor["id"]]}).json()
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": mfa["id"], "product_kind": "ThirdParty",
        "third_party_product_id": okta["id"], "coverage": "Full", "ratified": True})
    assert okta["covered_count"] == 3150
    return eid, okta


def test_line_defaults_to_per_user_scope(client):
    eid, _ = _setup(client)
    lic = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_purchased": 46,
        "quantity_assigned": 46, "unit_price_paid_annual": 491.40}).json()
    assert lic["coverage_scope"] == "PerUser"


def test_untagged_per_user_line_credits_only_its_seats(client):
    """46 org-wide Microsoft 365 E3 seats make 46 people redundant for the MFA
    tool — not all 3,150 who use it."""
    eid, _ = _setup(client)
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_purchased": 46,
        "quantity_assigned": 46, "unit_price_paid_annual": 491.40})

    qw = client.post(f"/api/engagements/{eid}/compute").json()["rollup"]["quick_wins"]
    assert len(qw) == 1
    assert qw[0]["displaced_today"] == 46
    assert qw[0]["residual_today"] == 3104
    # 46 × (211,100.90 / 3150) = $3,082.74 — not the tool's whole $211k.
    assert qw[0]["credited_annual"] == 3082.74


def test_tenant_wide_line_credits_the_whole_population(client):
    """Marked tenant-wide, the same line is a real org-wide entitlement and the
    whole 3,150 is redundant today."""
    eid, _ = _setup(client)
    lic = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_purchased": 46,
        "quantity_assigned": 46, "unit_price_paid_annual": 491.40}).json()
    upd = client.patch(f"/api/engagements/{eid}/current-licenses/{lic['id']}",
                       json={"coverage_scope": "TenantWide"}).json()
    assert upd["coverage_scope"] == "TenantWide"

    qw = client.post(f"/api/engagements/{eid}/compute").json()["rollup"]["quick_wins"]
    assert qw[0]["displaced_today"] == 3150
    assert qw[0]["credited_annual"] == 211100.90


def test_office_365_does_not_cover_conditional_access(client):
    """Office 365 E3 delivers SSO but not Conditional Access (Entra P1), so a
    persona fully licensed on it is NOT redundant for an MFA/CA tool at all."""
    eid, _ = _setup(client)
    personas = client.get(f"/api/engagements/{eid}/personas").json()
    ops = next(p for p in personas if p["name"] == "Property Ops")
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_purchased": 2518,
        "quantity_assigned": 2518, "unit_price_paid_annual": 327.60,
        "persona_ids": [ops["id"]]})

    assert client.post(f"/api/engagements/{eid}/compute").json()["rollup"]["quick_wins"] == []


def test_scope_is_visible_in_the_data_inspector(client):
    """No hidden data: the field the seat math reads is inspectable."""
    eid, _ = _setup(client)
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_assigned": 46,
        "unit_price_paid_annual": 491.40})
    data = client.get(f"/api/engagements/{eid}/inspect").json()
    lic = next(o for o in data["objects"] if o["type"] == "CurrentMicrosoftLicense")
    scope = next(f for f in lic["fields"] if f["key"] == "coverage_scope")
    assert scope["label"] == "Entitlement scope"
    assert lic["records"][0]["cells"]["coverage_scope"]["display"] == "PerUser"
