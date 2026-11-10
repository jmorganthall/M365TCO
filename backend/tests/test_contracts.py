"""GUIDED_PLAN G12: a vendor contract broken into cost lines, each replaced on
its own; what the lines don't account for stays until everything is replaced."""


def _setup(client):
    eid = client.post("/api/engagements", json={"customer_name": "Contract Co"}).json()["id"]
    b = f"/api/engagements/{eid}"
    kw = client.post(f"{b}/personas", json={"name": "KW", "headcount": 100}).json()
    outs = {o["seed_key"]: o["id"] for o in client.get(f"{b}/outcomes").json()}
    tool = client.post(f"{b}/third-party", json={
        "name": "SecSuite", "raw_cost": 200000, "renewal_date": "2027-03-31", "persona_ids": [kw["id"]]}).json()
    for k in ("endpoint-edr", "siem"):
        client.post(f"{b}/coverage", json={"outcome_id": outs[k], "product_kind": "ThirdParty",
                                           "third_party_product_id": tool["id"], "ratified": True})
    # The plan replaces EDR but not SIEM.
    client.post(f"{b}/scenarios", json={"persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E5",
                                        "target_unit_price_annual": 0, "in_scope": True})
    return b, kw, tool


def _credit(client, b):
    r = client.get(f"{b}/result").json()
    return sum(o["credited_offset_annual"] for s in r["scenarios"] for o in s["offsets"])


def test_an_unbroken_multi_outcome_tool_gets_no_saving(client):
    b, _, _ = _setup(client)
    assert _credit(client, b) == 0


def test_break_down_changes_nothing_until_amounts_are_typed(client):
    b, _, tool = _setup(client)
    before = client.get(f"{b}/interpreted").json()["summary"]["baseline_total_annual"]
    c = client.post(f"{b}/third-party/{tool['id']}/break-down")
    assert c.status_code == 201, c.text
    contract = c.json()
    assert contract["total_cost"] == "200000.00" and contract["renewal_date"] == "2027-03-31"
    lines = client.get(f"{b}/third-party").json()
    assert len(lines) == 2 and all(l["contract_id"] == contract["id"] for l in lines)
    i = client.get(f"{b}/interpreted").json()
    assert i["summary"]["baseline_total_annual"] == before
    [ic] = i["contracts"]
    assert ic["remainder_annual"] == 200000 and ic["remainder_tool_id"]
    rest = next(t for t in i["tools"] if t["id"] == ic["remainder_tool_id"])
    assert rest["renewal_date"] == "2027-03-31" and len(rest["delivered_outcome_ids"]) == 2
    assert _credit(client, b) == 0
    # Lines at $0 are not "left out" for having no cost.
    assert not [f for f in i["findings"] if f["code"] == "tool_incomplete"]

    # Type the EDR line's share: now that part is replaced, SIEM and the rest stay.
    edr = next(l for l in lines if "Detection" in l["name"])
    client.patch(f"{b}/third-party/{edr['id']}", json={"raw_cost": 80000})
    assert _credit(client, b) == 80000
    [ic] = client.get(f"{b}/interpreted").json()["contracts"]
    assert ic["lines_annual"] == 80000 and ic["remainder_annual"] == 120000


def test_lines_over_the_total_are_flagged_and_delete_removes_the_lines(client):
    b, _, tool = _setup(client)
    contract = client.post(f"{b}/third-party/{tool['id']}/break-down").json()
    lines = client.get(f"{b}/third-party").json()
    for l in lines:
        client.patch(f"{b}/third-party/{l['id']}", json={"raw_cost": 150000})
    codes = [c["code"] for c in client.get(f"{b}/review").json()["checks"]]
    assert "contract_lines_exceed" in codes
    assert client.delete(f"{b}/contracts/{contract['id']}").status_code == 204
    assert client.get(f"{b}/third-party").json() == []
    assert client.get(f"{b}/contracts").json() == []


def test_a_single_outcome_tool_cannot_be_broken_down(client):
    eid = client.post("/api/engagements", json={"customer_name": "One Co"}).json()["id"]
    t = client.post(f"/api/engagements/{eid}/third-party", json={"name": "X", "raw_cost": 10}).json()
    assert client.post(f"/api/engagements/{eid}/third-party/{t['id']}/break-down").status_code == 422
