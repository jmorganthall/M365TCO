"""A carve-out holds what its parent holds (TARGET_SCHEMA D11). Tags added to one
side after the carve don't reach the other; the drift check says so, and Match
parent copies the parent's tags onto the carve-out."""


def _family(client):
    eid = client.post("/api/engagements", json={"customer_name": "Drift Co"}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas", json={"name": "Office", "headcount": 1000}).json()
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_assigned": 1000,
        "unit_price_paid_annual": 276, "persona_ids": [p["id"]]})
    child = client.post(f"/api/engagements/{eid}/personas/{p['id']}/carve",
                        json={"seats": 200, "name": "Carve"}).json()
    return eid, p, child


def test_a_carve_out_that_matches_its_parent_is_not_listed(client):
    eid, _, _ = _family(client)
    assert client.get(f"/api/engagements/{eid}/carve-out-drift").json() == []


def test_a_tool_tagged_after_the_carve_is_missing_and_match_parent_copies_it(client):
    eid, p, child = _family(client)
    tool = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "MDM", "raw_cost": 80000, "cost_period": "Annual",
        "persona_ids": [p["id"]]}).json()
    assert tool["covered_count"] == 800  # the parent only
    [row] = client.get(f"/api/engagements/{eid}/carve-out-drift").json()
    assert (row["persona_id"], row["missing_tools"], row["extra_tools"]) == (child["id"], ["MDM"], [])

    client.post(f"/api/engagements/{eid}/personas/{child['id']}/match-parent")
    assert client.get(f"/api/engagements/{eid}/carve-out-drift").json() == []
    tp = next(t for t in client.get(f"/api/engagements/{eid}/third-party").json() if t["id"] == tool["id"])
    assert set(tp["persona_ids"]) == {p["id"], child["id"]}
    assert tp["covered_count"] == 1000  # covers follow the tags


def test_match_parent_removes_tags_the_parent_does_not_have(client):
    eid, p, child = _family(client)
    lic = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E5", "quantity_assigned": 200,
        "unit_price_paid_annual": 600, "persona_ids": [child["id"]]}).json()
    [row] = client.get(f"/api/engagements/{eid}/carve-out-drift").json()
    assert row["extra_licenses"] == ["Microsoft 365 E5"] and row["missing_licenses"] == []
    client.post(f"/api/engagements/{eid}/personas/{child['id']}/match-parent")
    assert client.get(f"/api/engagements/{eid}/carve-out-drift").json() == []
    line = next(l for l in client.get(f"/api/engagements/{eid}/current-licenses").json() if l["id"] == lic["id"])
    assert line["persona_ids"] == []


def test_match_parent_needs_a_carve_out(client):
    eid, p, _ = _family(client)
    assert client.post(f"/api/engagements/{eid}/personas/{p['id']}/match-parent").status_code == 422
