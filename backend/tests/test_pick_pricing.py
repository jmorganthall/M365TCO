"""Picking a SKU is priced on the server (LAYER_AUDIT Phase 2): the GUI sends the
catalog row it picked, and the server sets the name and quotes the row's list
price — so the browser never decides a price. Changing a licence line's basis,
or reverting it to list, requotes it on the server the same way a plan is."""

from tests.sweep_services import load_catalog


def _skus(client):
    return {s["sku_title"]: s for s in client.get("/api/catalog/skus").json()}


def _setup(client, name):
    load_catalog(client)
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 10}).json()
    return eid, p


def test_a_licence_pick_names_and_prices_the_line(client):
    eid, _ = _setup(client, "Pick Licence Co")
    sku = _skus(client)
    base = f"/api/engagements/{eid}/current-licenses"
    # A new line on a picked SKU, no price typed: the list price.
    l = client.post(base, json={"catalog_sku_id": sku["Office 365 E3"]["id"],
                                "quantity_purchased": 10, "quantity_assigned": 10}).json()
    assert l["sku_reference"] == "Office 365 E3"
    assert float(l["unit_price_paid_annual"]) == sku["Office 365 E3"]["annual_erp_price"]
    assert l["source_tag"] == "ListPrice"
    # A typed price on a new line wins.
    t = client.post(base, json={"catalog_sku_id": sku["Office 365 E3"]["id"],
                                "unit_price_paid_annual": 300}).json()
    assert float(t["unit_price_paid_annual"]) == 300

    # A negotiated price, then re-picking the same SKU: kept.
    client.patch(f"{base}/{l['id']}", json={"price_override": True, "overridden_price_annual": 250})
    same = client.patch(f"{base}/{l['id']}", json={"catalog_sku_id": sku["Office 365 E3"]["id"]}).json()
    assert same["price_override"] and float(same["overridden_price_annual"]) == 250
    # Picking a different SKU: its list price, and the negotiated price ends.
    other = client.patch(f"{base}/{l['id']}", json={"catalog_sku_id": sku["Microsoft 365 E5"]["id"]}).json()
    assert other["sku_reference"] == "Microsoft 365 E5"
    assert float(other["unit_price_paid_annual"]) == sku["Microsoft 365 E5"]["annual_erp_price"]
    assert not other["price_override"] and other["overridden_price_annual"] is None


def test_revert_to_list_and_a_basis_change_requote_on_the_server(client):
    eid, _ = _setup(client, "Requote Licence Co")
    base = f"/api/engagements/{eid}/current-licenses"
    l = client.post(base, json={"sku_reference": "Microsoft 365 E3", "unit_price_paid_annual": 1,
                                "price_override": True, "overridden_price_annual": 400}).json()
    r = client.post(f"{base}/{l['id']}/list-price").json()
    assert not r["price_override"] and float(r["unit_price_paid_annual"]) == 491.4
    assert float(r["overridden_price_annual"]) == 400       # kept for a later re-enable
    # A basis change requotes; a price sent with it wins.
    client.patch(f"{base}/{l['id']}", json={"unit_price_paid_annual": 1})
    b = client.patch(f"{base}/{l['id']}", json={"billing_plan": "Monthly"}).json()
    assert float(b["unit_price_paid_annual"]) == 491.4
    c = client.patch(f"{base}/{l['id']}", json={"billing_plan": "Annual", "unit_price_paid_annual": 7}).json()
    assert float(c["unit_price_paid_annual"]) == 7


def test_a_plan_pick_names_prices_and_links_the_plan(client):
    eid, p = _setup(client, "Pick Plan Co")
    sku = _skus(client)
    s = client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "catalog_sku_id": sku["Microsoft 365 E3"]["id"]}).json()
    assert s["target_sku_reference"] == "Microsoft 365 E3"
    assert float(s["target_unit_price_annual"]) == 491.4
    url = f"/api/engagements/{eid}/scenarios/{s['id']}"
    client.patch(url, json={"price_override": True, "overridden_price_annual": 300})
    e5 = client.patch(url, json={"catalog_sku_id": sku["Microsoft 365 E5"]["id"]}).json()
    assert e5["target_sku_reference"] == "Microsoft 365 E5"
    assert float(e5["target_unit_price_annual"]) == 684
    assert not e5["price_override"]
    assert client.patch(url, json={"catalog_sku_id": "gone"}).status_code == 422


def test_a_free_price_list_plan_is_not_a_missing_price(client):
    """$0 from the price list is a real price (a free or donated plan); $0 because
    nothing was found is flagged (licence_price_missing)."""
    eid, p = _setup(client, "Free Plan Co")
    base = f"/api/engagements/{eid}"
    free = client.post(f"{base}/current-licenses", json={
        "sku_reference": "A donated plan", "unit_price_paid_annual": 0, "source_tag": "ListPrice",
        "quantity_purchased": 10, "quantity_assigned": 10, "persona_ids": [p["id"]]}).json()
    unknown = client.post(f"{base}/current-licenses", json={
        "sku_reference": "Nothing on the price list", "unit_price_paid_annual": 0,
        "quantity_purchased": 10, "quantity_assigned": 10, "persona_ids": [p["id"]]}).json()
    i = client.get(f"{base}/interpreted").json()
    known = {l["id"]: l["price_known"] for l in i["licence_lines"]}
    assert known[free["id"]] is True and known[unknown["id"]] is False
    missing = [f for f in i["findings"] if f["code"] == "licence_price_missing"]
    assert [f["licence_id"] for f in missing] == [unknown["id"]]
    assert any(c["code"] == "licence_price_missing" for c in client.get(f"{base}/review").json()["checks"])
