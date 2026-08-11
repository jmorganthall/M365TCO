"""A third-party tool the target displaces but whose covered population is unset
reads as FullyEliminated with 0 seats/$ (ENGINE_SPEC: dollars are 0 until covers
are set). The readout must not print a bare, contradictory "Retire fully · 0 seats
retired" — it shows "—" and says the covered population isn't set.
"""


def _outcome(client, eid, seed_key):
    return next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
                if o["seed_key"] == seed_key)


def test_html_readout_explains_zero_covers_instead_of_contradicting(client):
    eng = client.post("/api/engagements", json={"customer_name": "Covers Unset Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 100}).json()
    identity = _outcome(client, eid, "identity-sso")

    # A tool with a real cost but NO personas tagged and NO covered_count override
    # → covered_count is 0. It delivers identity-sso, which the E3 target covers, so
    # it is displaced → FullyEliminated with 0 seats.
    tool = client.post(f"/api/engagements/{eid}/third-party",
                       json={"name": "Ghostware", "raw_cost": 12000}).json()
    assert tool["covered_count"] == 0
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": identity["id"], "product_kind": "ThirdParty",
        "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    # It is classified FullyEliminated (target covers its outcome)…
    result = client.post(f"/api/engagements/{eid}/compute").json()
    disp = next(d for d in result["dispositions"] if d["third_party_product_name"] == "Ghostware")
    assert disp["disposition"] == "FullyEliminated"
    assert disp["covered_count"] == 0

    # …but the customer readout explains the unset covers rather than printing a
    # contradictory "Retire fully · 0 seats retired".
    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "Third-party tools — what happens to each" in html
    assert "Retire fully" in html
    assert "Covered population not set" in html


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
    assert "Covered population not set" not in html
    # 100 seats retired appears in the dispositions table.
    assert ">100<" in html
