"""How a tool's covered population reads in the customer readout."""


def _outcome(client, eid, seed_key):
    return next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
                if o["seed_key"] == seed_key)


def test_an_untagged_tool_no_longer_reads_as_zero_covers(client):
    """ENGINE_SPEC 6.0a (G6): a tool with no personas and no seat count is Org-wide
    and covers every persona, so the readout reports real seats, never 0."""
    eng = client.post("/api/engagements", json={"customer_name": "Covers Unset Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 100}).json()
    identity = _outcome(client, eid, "identity-sso")
    tool = client.post(f"/api/engagements/{eid}/third-party",
                       json={"name": "Ghostware", "raw_cost": 12000}).json()
    assert tool["covered_count"] == 100
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": identity["id"], "product_kind": "ThirdParty",
        "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})
    result = client.get(f"/api/engagements/{eid}/result").json()
    disp = next(d for d in result["dispositions"] if d["third_party_product_name"] == "Ghostware")
    assert disp["disposition"] == "FullyEliminated" and disp["covered_count"] == 100
    assert "Users not set" not in client.get(f"/api/engagements/{eid}/readout.html").text


def test_covers_set_still_shows_the_seat_count(client):
    """The clarification only triggers on 0 covers — a tool with a covered
    population still reports its retired seats as a number."""
    eng = client.post("/api/engagements", json={"customer_name": "Covers Set Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 100}).json()
    identity = _outcome(client, eid, "identity-sso")
    tool = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 50000, "covered_count_override": 100}).json()
    assert tool["covered_count"] == 100
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": identity["id"], "product_kind": "ThirdParty",
        "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "Users not set" not in html
    # 100 seats retired appears in the dispositions table.
    assert ">100<" in html
