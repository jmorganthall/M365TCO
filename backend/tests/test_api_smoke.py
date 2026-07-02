"""End-to-end API smoke test exercising the workshop flow against the engine.

Reproduces the Okta 500-vs-450 scenario through the HTTP layer to prove the
ORM -> engine bridge and the ratified-coverage gate work together.
"""

"""client fixture is provided by conftest.py."""


def test_full_workshop_flow_okta_500_vs_450(client):
    # 1. create engagement (seeds outcomes + MS coverage)
    eng = client.post("/api/engagements", json={"customer_name": "Acme"}).json()
    eid = eng["id"]

    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    identity = next(o for o in outcomes if "Identity" in o["name"])

    # 2. persona
    kw = client.post(
        f"/api/engagements/{eid}/personas",
        json={"name": "Knowledge Worker", "headcount": 450},
    ).json()

    # 3. third-party Okta: $50k/yr covering 500, delivers Identity
    okta = client.post(
        f"/api/engagements/{eid}/third-party",
        json={"name": "Okta", "raw_cost": 50000, "cost_period": "Annual",
              "covered_count": 500, "renewal_date": "2026-09-01"},
    ).json()
    assert float(okta["per_unit_annual_cost"]) == 100.0

    # 4. coverage: Okta -> Identity (ratified)
    client.post(
        f"/api/engagements/{eid}/coverage",
        json={"outcome_id": identity["id"], "product_kind": "ThirdParty",
              "third_party_product_id": okta["id"], "coverage": "Full",
              "ratified": True},
    )

    # 5. scenario: KW -> E3 (E3 covers Identity from the seed library)
    client.post(
        f"/api/engagements/{eid}/scenarios",
        json={"persona_id": kw["id"], "target_sku_reference": "E3",
              "target_unit_price_annual": 0, "in_scope": True},
    )

    # 6. compute
    result = client.post(f"/api/engagements/{eid}/compute").json()
    disp = result["dispositions"][0]
    assert disp["displaced_users"] == 450
    assert disp["residual_count"] == 50
    assert disp["disposition"] == "PartiallyReduced"
    assert disp["residual_annual_cost"] == 5000.0
    assert disp["requires_residual_classification"] is True

    sc = result["scenarios"][0]
    assert sc["current_third_party_offset_annual"] == 45000.0
    assert sc["delta_annual"] == 45000.0
    assert result["rollup"]["net_tco_delta_annual"] == 45000.0
    # partial -> renewal NOT eliminated
    assert result["rollup"]["eliminated_renewal_cycles"] == []

    # 7. record an intended out-of-scope residual -> no longer forces a choice
    client.put(
        f"/api/engagements/{eid}/dispositions/{okta['id']}/override",
        json={"override": "None", "residual_intent": "IntendedOutOfScope"},
    )
    result2 = client.post(f"/api/engagements/{eid}/compute").json()
    assert result2["dispositions"][0]["requires_residual_classification"] is False

    # 8. readout + xlsx render
    assert client.get(f"/api/engagements/{eid}/readout.html").status_code == 200
    assert client.get(f"/api/engagements/{eid}/readout.xlsx").status_code == 200


def test_unratified_ai_suggestion_does_not_feed_math(client):
    eng = client.post("/api/engagements", json={"customer_name": "Beta"}).json()
    eid = eng["id"]
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    identity = next(o for o in outcomes if "Identity" in o["name"])
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 100}).json()
    okta = client.post(f"/api/engagements/{eid}/third-party",
                       json={"name": "Okta", "raw_cost": 10000, "covered_count": 100}).json()
    # UNRATIFIED coverage entry
    client.post(f"/api/engagements/{eid}/coverage",
                json={"outcome_id": identity["id"], "product_kind": "ThirdParty",
                      "third_party_product_id": okta["id"], "coverage": "Full",
                      "ai_suggested": True, "ratified": False})
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": kw["id"], "target_sku_reference": "E3",
                      "target_unit_price_annual": 0})
    result = client.post(f"/api/engagements/{eid}/compute").json()
    # unratified -> Okta not displaced
    assert result["dispositions"][0]["disposition"] == "Unchanged"


def test_global_default_tooling_pct_flows_to_new_engagement(client):
    # Set the global default, then a new engagement (no tooling in the form)
    # inherits it.
    client.put("/api/admin/defaults", json={"default_tooling_pct": 0.25})
    meta = client.get("/api/meta").json()
    assert meta["default_tooling_pct"] == 0.25
    eng = client.post("/api/engagements", json={"customer_name": "Defaults Co"}).json()
    assert float(eng["global_tooling_pct"]) == 0.25
    # Reset so other tests are unaffected.
    client.put("/api/admin/defaults", json={"default_tooling_pct": 0.30})


def test_price_sheet_csv_import(client):
    csv_text = (
        "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
        "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment\n"
        "Microsoft 365 E3,CFQ7TTC0LF8Q,0001,M365 E3,P1M,Monthly,US,USD,"
        "32.00,2026-01-01,2026-12-31,36.00,Commercial\n"
        "Microsoft 365 E5,CFQ7TTC0LF8R,0002,M365 E5,P1Y,Annual,US,USD,"
        "660.00,2026-01-01,2026-12-31,684.00,Commercial\n"
        "Education SKU,CFQ7TTC0LF8S,0003,Edu,P1Y,Annual,US,USD,"
        "10.00,2026-01-01,2026-12-31,12.00,Education\n"
    )
    files = {"file": ("price.csv", csv_text, "text/csv")}
    resp = client.post("/api/catalog/import-csv", files=files,
                       data={"catalog_version": "2026-06"})
    body = resp.json()
    assert resp.status_code == 200
    # 2 commercial rows imported, 1 education filtered
    assert body["inserted"] == 2
    skus = client.get("/api/catalog/skus").json()
    e3 = next(s for s in skus if "E3" in s["sku_title"])
    # P1M 32.00 -> annual 384.00
    assert e3["annual_unit_price"] == 384.0
    e5 = next(s for s in skus if "E5" in s["sku_title"])
    # P1Y 660.00 -> annual 660.00
    assert e5["annual_unit_price"] == 660.0


def test_price_sheet_tab_delimited_import(client):
    # Same data, tab-delimited (e.g. exported/round-tripped through Excel).
    header = "\t".join([
        "ChangeIndicator", "ProductTitle", "ProductId", "SkuId", "SkuTitle",
        "TermDuration", "BillingPlan", "Market", "Currency", "UnitPrice",
        "EffectiveStartDate", "EffectiveEndDate", "ERP Price", "Segment",
    ])
    row = "\t".join([
        "New", "Microsoft 365 E5", "CFQ7TTC0LF8R", "0009", "M365 E5 Tab",
        "P1Y", "Annual", "US", "USD", "660.00",
        "2026-07-01", "2026-12-31", "684.00", "Commercial",
    ])
    files = {"file": ("price.csv", header + "\n" + row + "\n", "text/csv")}
    resp = client.post("/api/catalog/import-csv", files=files, data={"catalog_version": "2026-07"})
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 1
    skus = client.get("/api/catalog/skus").json()
    e5 = next(s for s in skus if s["sku_title"] == "M365 E5 Tab")
    assert e5["annual_unit_price"] == 660.0
    assert e5["annual_erp_price"] == 684.0


def test_csv_import_clears_stale_pricing_badge(client):
    # A manual CSV upload must make pricing read fresh — no price-sync API auth,
    # no cached sheet on disk. Freshness now counts the CSV import provenance.
    csv_text = (
        "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
        "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment\n"
        "Microsoft 365 E5,CFQ7TTC0LF8R,0042,M365 E5 Fresh,P1Y,Annual,US,USD,"
        "660.00,2026-01-01,2026-12-31,684.00,Commercial\n"
    )
    files = {"file": ("price.csv", csv_text, "text/csv")}
    assert client.post("/api/catalog/import-csv", files=files).status_code == 200

    st = client.get("/api/pricesync/status").json()
    # CSV-only operator: not "configured" for the API pull, but pricing is fresh.
    assert st["state"] == "fresh"
    assert st["data_month"]  # a data month is now set (not None/"")
    assert st["data_source"] == "CSV upload"


def test_csv_last_updated_date_drives_data_month(client):
    # When the sheet carries a LastUpdatedDate, the data month comes from it
    # (not the upload date). A sheet last updated in 2020 reads as that month.
    csv_text = (
        "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
        "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment,"
        "LastUpdatedDate\n"
        "Microsoft 365 E5,CFQ7TTC0LF8R,0043,M365 E5 Dated,P1Y,Annual,US,USD,"
        "660.00,2020-01-01,2020-12-31,684.00,Commercial,2020-03-15T00:00:00.0000000Z\n"
    )
    files = {"file": ("price.csv", csv_text, "text/csv")}
    r = client.post("/api/catalog/import-csv", files=files)
    assert r.status_code == 200
    assert r.json()["data_month"] == "2020-03"

    st = client.get("/api/pricesync/status").json()
    assert st["data_month"] == "2020-03"
    assert st["data_source"] == "CSV upload"
