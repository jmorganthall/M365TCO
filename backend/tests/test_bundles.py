"""Bundle spine: seeded staples, add-on base links, and SKU → bundle mapping."""


def test_bundles_seeded_with_addon_base_links(client):
    bundles = client.get("/api/catalog/bundles").json()
    by_key = {b["key"]: b for b in bundles}
    # Staples present.
    assert {"m365-e3", "m365-e5", "m365-e7", "m365-business-premium",
            "m365-f1", "m365-f3"} <= set(by_key)
    # Add-ons resolve to their base bundle.
    assert by_key["e5-security"]["kind"] == "addon"
    assert by_key["e5-security"]["base_name"] == "Microsoft 365 E3"
    assert by_key["f5-compliance"]["base_name"] == "Microsoft 365 F3"
    # Full bundles have no base.
    assert by_key["m365-e3"]["base_bundle_id"] is None


def test_map_catalog_sku_to_bundle(client):
    csv_text = (
        "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
        "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment\n"
        "Microsoft 365 E3,CFQ7BUNDLE01,0001,M365 E3,P1Y,Annual,US,USD,"
        "384.00,2026-01-01,2026-12-31,432.00,Commercial\n"
    )
    files = {"file": ("price.csv", csv_text, "text/csv")}
    client.post("/api/catalog/import-csv", files=files)
    skus = client.get("/api/catalog/skus").json()
    sku = next(s for s in skus if s["sku_id"] == "0001")
    assert sku["bundle_id"] is None  # unmapped until classified

    e3 = next(b for b in client.get("/api/catalog/bundles").json() if b["key"] == "m365-e3")
    r = client.patch(f"/api/catalog/skus/{sku['id']}/bundle", json={"bundle_id": e3["id"]})
    assert r.status_code == 200 and r.json()["bundle_id"] == e3["id"]
    skus = client.get("/api/catalog/skus").json()
    assert next(s for s in skus if s["sku_id"] == "0001")["bundle_id"] == e3["id"]

    # Unknown bundle rejected.
    assert client.patch(f"/api/catalog/skus/{sku['id']}/bundle", json={"bundle_id": "nope"}).status_code == 422


def test_scenario_targeting_bundle_name_resolves_and_displaces(client):
    """The bug fix: a scenario target that names the bundle ("Microsoft 365 E3"),
    not the old "E3" shortcode, now resolves to the bundle and displaces."""
    eng = client.post("/api/engagements", json={"customer_name": "Bundle Displace"}).json()
    eid = eng["id"]
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    identity = next(o for o in outcomes if "Identity" in o["name"])
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 100}).json()
    okta = client.post(f"/api/engagements/{eid}/third-party",
                       json={"name": "Okta", "raw_cost": 10000, "covered_count": 100}).json()
    client.post(f"/api/engagements/{eid}/coverage",
                json={"outcome_id": identity["id"], "product_kind": "ThirdParty",
                      "third_party_product_id": okta["id"], "coverage": "Full", "ratified": True})
    # Target the full bundle NAME (previously would not match the "E3"-keyed map).
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E3",
                      "target_unit_price_annual": 0, "in_scope": True})
    result = client.post(f"/api/engagements/{eid}/compute").json()
    assert result["dispositions"][0]["disposition"] == "FullyEliminated"

    # The seeded MS coverage is now bundle-keyed (readable bundle names shown).
    cov = client.get(f"/api/engagements/{eid}/coverage").json()
    ms = [c for c in cov if c["product_kind"] == "MicrosoftSku"]
    assert any(c["microsoft_sku_reference"] == "Microsoft 365 E3" for c in ms)
    assert all(c.get("bundle_id") for c in ms)  # every seeded MS entry maps to a bundle


def test_scenario_composes_base_plus_addons_with_discount(client):
    """Future state = base bundle + add-on bundles: union outcomes, sum list
    prices, apply the discount to the net the engine uses."""
    eng = client.post("/api/engagements", json={"customer_name": "Compose Co"}).json()
    eid = eng["id"]
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    ident = next(o for o in outcomes if "Identity" in o["name"])
    endpoint = next(o for o in outcomes if "Endpoint Security" in o["name"])
    kw = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 100}).json()

    # Two third-party tools: one Identity (covered by E3 base), one Endpoint
    # Security (covered only by the E5 Security add-on).
    idp = client.post(f"/api/engagements/{eid}/third-party",
                      json={"name": "Okta", "raw_cost": 10000, "covered_count": 100}).json()
    edr = client.post(f"/api/engagements/{eid}/third-party",
                      json={"name": "CrowdStrike", "raw_cost": 20000, "covered_count": 100}).json()
    for tp, oc in [(idp, ident), (edr, endpoint)]:
        client.post(f"/api/engagements/{eid}/coverage",
                    json={"outcome_id": oc["id"], "product_kind": "ThirdParty",
                          "third_party_product_id": tp["id"], "coverage": "Full", "ratified": True})

    e5sec = next(b for b in client.get("/api/catalog/bundles").json() if b["key"] == "e5-security")

    # Base E3 @ 400 list + E5 Security add-on @ 100 list, 10% discount → net 450/seat.
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "target_discount_pct": 0.10, "in_scope": True,
        "addons": [{"bundle_id": e5sec["id"], "unit_price_annual": 100}],
    })
    result = client.post(f"/api/engagements/{eid}/compute").json()
    sc = result["scenarios"][0]
    # net = (400 + 100) * 0.9 = 450; target spend = 100 * 450 = 45000.
    assert sc["target_spend_annual"] == 45000.0
    # Both tools displaced: E3 covers Identity, the E5 Security add-on covers Endpoint.
    disps = {d["third_party_product_id"]: d["disposition"] for d in result["dispositions"]}
    assert disps[idp["id"]] == "FullyEliminated"
    assert disps[edr["id"]] == "FullyEliminated"

    # Round-trip: the scenario exposes its add-ons and discount.
    scen = client.get(f"/api/engagements/{eid}/scenarios").json()[0]
    assert float(scen["target_discount_pct"]) == 0.10
    assert scen["addons"][0]["bundle_id"] == e5sec["id"]


def test_recommend_path_composes_base_plus_gap_closing_addon(client):
    """Recommend-a-path (bundle-analysis) composes a base bundle with the cheapest
    add-ons that close the persona's gaps, not just single SKUs. A persona on E5
    today (which covers Endpoint Security) evaluated against an E3 base leaves an
    Endpoint-Security gap — the composition must surface the E5 Security add-on."""
    eng = client.post("/api/engagements", json={"customer_name": "Path Co"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 100}).json()

    # Third-party EDR delivers Endpoint Security — only a path that covers that
    # outcome displaces it.
    endpoint = next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
                    if "Endpoint Security" in o["name"])
    edr = client.post(f"/api/engagements/{eid}/third-party",
                      json={"name": "CrowdStrike", "raw_cost": 20000, "covered_count": 100}).json()
    client.post(f"/api/engagements/{eid}/coverage",
                json={"outcome_id": endpoint["id"], "product_kind": "ThirdParty",
                      "third_party_product_id": edr["id"], "coverage": "Full", "ratified": True})

    # Persona is on Microsoft 365 E5 today → its seeded coverage (incl. Endpoint
    # Security) is the "required" baseline the recommendation must not drop.
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E5", "quantity_assigned": 100,
        "unit_price_paid_annual": 0, "persona_ids": [kw["id"]]})

    # Price E3 at 400 and the E5 Security add-on at 100; other add-ons have no
    # catalog price (0). The composition should pick the cheapest set that closes
    # the gaps — here Defender for Endpoint P2 (free, covers Endpoint Security) is
    # cheaper than the E5 Security bundle, so it is chosen.
    res = client.post(f"/api/engagements/{eid}/personas/{kw['id']}/bundle-analysis",
                      json={"prices": {"Microsoft 365 E3": 400, "Microsoft 365 E5 Security": 100}}).json()
    by_ref = {b["sku_reference"]: b for b in res["bundles"]}
    e3 = by_ref["Microsoft 365 E3"]

    # E3 alone leaves an Endpoint-Security gap; the composition adds the cheapest
    # add-on(s) to close it, so the composed E3 path covers Endpoint Security.
    assert e3["addons"], "E3 should compose add-ons to close its gaps"
    assert "Endpoint Security" not in e3["gap_outcomes"]         # gap closed by an add-on
    assert any("Endpoint Security" in a["closes"] for a in e3["addons"])
    assert e3["target_unit_price_annual"] == 400.0 + e3["addon_total_annual"]  # base + add-ons
    assert "CrowdStrike" in e3["displaced_products"]             # composed path displaces the EDR

    # Cheapest cover: a free add-on that covers Endpoint Security beats the priced
    # E5 Security bundle, so no add-on cost is incurred to close that gap.
    assert e3["addon_total_annual"] == 0.0
