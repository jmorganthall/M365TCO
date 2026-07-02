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
