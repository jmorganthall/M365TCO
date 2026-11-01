"""A scenario stores the plan a person picked (TARGET_SCHEMA §5.2 `base_bundle_key`),
so what it costs and covers never depends on how its typed name resolves. A typed
name with no pick is resolved, as before."""


def _bundles(client):
    return {b["key"]: b for b in client.get("/api/catalog/bundles").json()}


def _setup(client, name):
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 10}).json()
    return eid, p


def _interp_scenario(client, eid):
    return client.get(f"/api/engagements/{eid}/interpreted").json()["scenarios"][0]


def test_a_picked_plan_wins_over_the_name(client):
    b = _bundles(client)
    eid, p = _setup(client, "Picked Co")
    # A label that resolves to nothing, but the person picked E5.
    s = client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "E5 for the field team",
        "bundle_id": b["m365-e5"]["id"], "target_unit_price_annual": 684}).json()
    assert s["bundle_id"] == b["m365-e5"]["id"]
    iv = _interp_scenario(client, eid)
    assert iv["base_bundle_id"] == b["m365-e5"]["id"] and iv["plan_source"] == "picked"
    assert iv["covered_outcome_ids"], "the picked plan's coverage counts"

    # The same label typed with no pick reads as no plan.
    eid2, p2 = _setup(client, "Typed Co")
    client.post(f"/api/engagements/{eid2}/scenarios", json={
        "persona_id": p2["id"], "target_sku_reference": "E5 for the field team",
        "target_unit_price_annual": 684})
    iv2 = _interp_scenario(client, eid2)
    assert iv2["base_bundle_id"] is None and iv2["plan_source"] is None
    assert iv2["covered_outcome_ids"] == []


def test_renaming_to_another_plan_drops_the_pick(client):
    b = _bundles(client)
    eid, p = _setup(client, "Rename Co")
    s = client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E5",
        "bundle_id": b["m365-e5"]["id"], "target_unit_price_annual": 684}).json()
    # A name that still reads as E5 keeps the pick.
    s = client.patch(f"/api/engagements/{eid}/scenarios/{s['id']}",
                     json={"target_sku_reference": b["m365-e5"]["name"]}).json()
    assert s["bundle_id"] == b["m365-e5"]["id"]
    # Typing a different plan's name drops it; the name is resolved instead.
    s = client.patch(f"/api/engagements/{eid}/scenarios/{s['id']}",
                     json={"target_sku_reference": b["m365-e3"]["name"]}).json()
    assert s["bundle_id"] is None
    iv = _interp_scenario(client, eid)
    assert iv["base_bundle_id"] == b["m365-e3"]["id"] and iv["plan_source"] == "name"
    # Picking again sets it, and an unknown plan is refused.
    s = client.patch(f"/api/engagements/{eid}/scenarios/{s['id']}",
                     json={"bundle_id": b["m365-e3"]["id"]}).json()
    assert s["bundle_id"] == b["m365-e3"]["id"]
    r = client.patch(f"/api/engagements/{eid}/scenarios/{s['id']}", json={"bundle_id": "nope"})
    assert r.status_code == 422


def test_the_recommender_offers_the_plan_to_pick(client):
    eid, p = _setup(client, "Reco Co")
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_assigned": 10,
        "unit_price_paid_annual": 300, "persona_ids": [p["id"]]})
    rows = client.post(f"/api/engagements/{eid}/personas/{p['id']}/bundle-analysis").json()["bundles"]
    b = _bundles(client)
    by_id = {x["id"]: x for x in b.values()}
    assert rows and all(by_id[r["base_bundle_id"]]["name"] == r["sku_reference"] for r in rows)
