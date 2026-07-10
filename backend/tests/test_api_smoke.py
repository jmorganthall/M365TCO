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
    # Cost-change convention: target $0 retires $45k of spend -> negative = saving.
    assert sc["delta_annual"] == -45000.0
    assert result["rollup"]["net_tco_delta_annual"] == -45000.0
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
    # All 3 rows imported — every segment is ingested (no Commercial-only filter).
    assert body["inserted"] == 3
    skus = client.get("/api/catalog/skus").json()
    e3 = next(s for s in skus if "E3" in s["sku_title"])
    # P1M 32.00 -> annual 384.00
    assert e3["annual_unit_price"] == 384.0
    e5 = next(s for s in skus if "E5" in s["sku_title"])
    # P1Y 660.00 -> annual 660.00
    assert e5["annual_unit_price"] == 660.0
    # The Education SKU is now present and tagged with its segment.
    edu = next(s for s in skus if s["segment"] == "Education")
    assert abs(edu["annual_unit_price"] - 10.0) < 0.01  # P1Y annualization rounding
    # Filtering to a segment returns only that segment's rows.
    edu_only = client.get("/api/catalog/skus?segment=Education").json()
    assert [s["segment"] for s in edu_only] == ["Education"]
    # The distinct-segments endpoint surfaces what the sheet contained.
    segs = client.get("/api/catalog/segments").json()["segments"]
    assert "Commercial" in segs and "Education" in segs
    assert segs[0] == "Commercial"  # known defaults first


def test_readout_delta_sign_and_color_convention(client):
    """Saving = negative delta, shown green (pos); cost increase = positive delta,
    shown neutral (no red 'neg' class). Spending more isn't styled as an error."""
    # Saving: target price 0 retires the current spend.
    eng = client.post("/api/engagements", json={"customer_name": "Save Co"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 100}).json()
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "M365 E3", "quantity_purchased": 100, "quantity_assigned": 100,
        "unit_price_paid_annual": 300, "persona_ids": [kw["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "E3", "target_unit_price_annual": 0})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    assert r["rollup"]["net_tco_delta_annual"] == -30000.0  # negative = saving
    html_body = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "headline pos" in html_body            # saving -> green
    assert "Annual savings" in html_body
    assert "headline neg" not in html_body        # never red

    # Cost increase: expensive target.
    eng2 = client.post("/api/engagements", json={"customer_name": "Up Co"}).json()
    e2 = eng2["id"]
    kw2 = client.post(f"/api/engagements/{e2}/personas", json={"name": "KW", "headcount": 100}).json()
    client.post(f"/api/engagements/{e2}/current-licenses", json={
        "sku_reference": "x", "quantity_purchased": 100, "quantity_assigned": 100,
        "unit_price_paid_annual": 100, "persona_ids": [kw2["id"]]})
    client.post(f"/api/engagements/{e2}/scenarios", json={
        "persona_id": kw2["id"], "target_sku_reference": "E5", "target_unit_price_annual": 600})
    r2 = client.post(f"/api/engagements/{e2}/compute").json()
    assert r2["rollup"]["net_tco_delta_annual"] == 50000.0  # positive = cost increase
    html2 = client.get(f"/api/engagements/{e2}/readout.html").text
    assert "Annual cost increase" in html2
    assert "headline neg" not in html2            # increase is neutral, not red


def test_readout_branding_applied_and_sanitized(client):
    eng = client.post("/api/engagements", json={"customer_name": "Brand Co"}).json()
    eid = eng["id"]
    tiny_png = ("data:image/png;base64,"
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    out = client.patch(f"/api/engagements/{eid}", json={
        "brand_primary_color": "#0a7d34",
        "brand_accent_color": "red; } body { display:none",  # injection attempt
        "brand_logo_data_url": tiny_png,
    }).json()
    assert out["brand_primary_color"] == "#0a7d34"

    html_body = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "#0a7d34" in html_body           # valid color inlined
    assert "display:none" not in html_body  # malicious accent color rejected
    assert tiny_png[:30] in html_body       # logo embedded


def test_download_existing_catalog_returns_uploaded_file_as_is(client):
    # An unusual layout (extra column, CRLF, trailing note) that the parser would
    # not reproduce — proving the download is the raw upload, not a re-export.
    csv_text = (
        "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
        "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment,Notes\r\n"
        "Microsoft 365 E5,CFQ7TTC0LF8R,0002,M365 E5,P1Y,Annual,US,USD,"
        "660.00,2026-01-01,2026-12-31,684.00,Commercial,internal-tag-xyz\r\n"
    )
    files = {"file": ("my-pricesheet.csv", csv_text, "text/csv")}
    resp = client.post("/api/catalog/import-csv", files=files, data={"catalog_version": "2026-06"})
    assert resp.status_code == 200

    ver = client.get("/api/catalog/version").json()
    assert ver["file_available"] is True
    assert ver["file_name"] == "my-pricesheet.csv"

    dl = client.get("/api/catalog/download")
    assert dl.status_code == 200
    assert dl.content.decode() == csv_text  # byte-for-byte, incl. CRLF + extra column
    assert "my-pricesheet.csv" in dl.headers.get("content-disposition", "")
    assert dl.headers["content-type"].startswith("text/csv")


def test_segment_inheritance_and_line_overrides(client):
    # Global default is Commercial out of the box.
    gd = client.get("/api/admin/defaults").json()
    assert gd["default_segment"] == "Commercial"

    # A new engagement inherits the global default segment ("seed, then own").
    eng = client.post("/api/engagements", json={"customer_name": "Nonprofit Co"}).json()
    eid = eng["id"]
    assert eng["default_segment"] == "Commercial"
    assert eng["default_term_duration"] == "P1Y"
    assert eng["default_billing_plan"] == "Annual"

    # The customer sets its own segment default (a Nonprofit) without touching global.
    eng = client.patch(f"/api/engagements/{eid}", json={"default_segment": "Nonprofit"}).json()
    assert eng["default_segment"] == "Nonprofit"
    assert client.get("/api/admin/defaults").json()["default_segment"] == "Commercial"

    # A line inherits by default (None), and can override per-line.
    lic = client.post(f"/api/engagements/{eid}/current-licenses",
                      json={"sku_reference": "M365 E5", "quantity_purchased": 10}).json()
    assert lic["segment"] is None and lic["term_duration"] is None
    lic = client.patch(f"/api/engagements/{eid}/current-licenses/{lic['id']}",
                       json={"segment": "Education", "term_duration": "P1M",
                             "billing_plan": "Monthly"}).json()
    assert lic["segment"] == "Education"
    assert lic["term_duration"] == "P1M"
    assert lic["billing_plan"] == "Monthly"


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


def test_current_license_persona_tags_roundtrip(client):
    eng = client.post("/api/engagements", json={"customer_name": "Tags Co"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 500}).json()
    fl = client.post(f"/api/engagements/{eid}/personas", json={"name": "FL", "headcount": 200}).json()
    lic = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "E3", "quantity_assigned": 700, "quantity_purchased": 700,
        "unit_price_paid_annual": 100, "persona_ids": [kw["id"], fl["id"]],
    }).json()
    assert set(lic["persona_ids"]) == {kw["id"], fl["id"]}
    # Patch replaces the tag set.
    upd = client.patch(f"/api/engagements/{eid}/current-licenses/{lic['id']}",
                       json={"persona_ids": [kw["id"]]}).json()
    assert upd["persona_ids"] == [kw["id"]]
    # A patch that doesn't mention persona_ids leaves the tags untouched.
    upd2 = client.patch(f"/api/engagements/{eid}/current-licenses/{lic['id']}",
                        json={"quantity_assigned": 650}).json()
    assert upd2["persona_ids"] == [kw["id"]]
    assert upd2["quantity_assigned"] == 650


def test_data_inspector_surfaces_objects_and_refs(client):
    eng = client.post("/api/engagements", json={"customer_name": "Inspect Co"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 500}).json()
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_assigned": 500, "quantity_purchased": 500,
        "unit_price_paid_annual": 384, "persona_ids": [kw["id"]],
    })
    data = client.get(f"/api/engagements/{eid}/inspect").json()
    assert data["engagement"]["customer_name"] == "Inspect Co"
    types = {o["type"]: o for o in data["objects"]}
    assert {"Persona", "CurrentMicrosoftLicense", "ThirdPartyProduct"} <= set(types)
    # Every persisted field is surfaced, including the ones with no edit UI.
    lic = types["CurrentMicrosoftLicense"]
    keys = {f["key"] for f in lic["fields"]}
    assert {"source_tag", "persona_ids", "discount_pct"} <= keys
    # The persona tag reference resolves to the persona name.
    rec = lic["records"][0]
    assert rec["cells"]["persona_ids"]["ref"]["label"] == "KW"
    assert rec["cells"]["persona_ids"]["ref"]["ok"] is True
    # Flow section present.
    assert [s["stage"] for s in data["flow"]] == ["Inputs", "Engine", "Outputs"]


def test_third_party_persona_tags_roundtrip(client):
    eng = client.post("/api/engagements", json={"customer_name": "TP Tags Co"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 500}).json()
    fl = client.post(f"/api/engagements/{eid}/personas", json={"name": "FL", "headcount": 200}).json()
    tp = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 50000, "covered_count": 700,
        "persona_ids": [kw["id"], fl["id"]],
    }).json()
    assert set(tp["persona_ids"]) == {kw["id"], fl["id"]}
    # Patch that omits persona_ids leaves tags intact but recomputes derived cost.
    upd = client.patch(f"/api/engagements/{eid}/third-party/{tp['id']}",
                       json={"is_managed": True, "tooling_pct": 0.3}).json()
    assert set(upd["persona_ids"]) == {kw["id"], fl["id"]}
    assert float(upd["effective_annual_cost"]) == 15000.0  # 50000 * 0.3
    # Replace the tag set.
    upd2 = client.patch(f"/api/engagements/{eid}/third-party/{tp['id']}",
                        json={"persona_ids": [kw["id"]]}).json()
    assert upd2["persona_ids"] == [kw["id"]]
    # Inspector surfaces the tags on the product, resolved to names.
    data = client.get(f"/api/engagements/{eid}/inspect").json()
    tpo = [o for o in data["objects"] if o["type"] == "ThirdPartyProduct"][0]
    assert "persona_ids" in {f["key"] for f in tpo["fields"]}
    assert tpo["records"][0]["cells"]["persona_ids"]["ref"]["label"] == "KW"
