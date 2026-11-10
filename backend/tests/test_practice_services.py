"""GUIDED_PLAN G13–G15: the practice's own services in a persona's future state.
Their outcomes count in the replacement test, their price is part of the target
(never of the Microsoft totals), and the result splits into sections."""

def _setup(client, name, tool, outcome_keys, managed=False):
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    b = f"/api/engagements/{eid}"
    p = client.post(f"{b}/personas", json={"name": "KW", "headcount": 100}).json()
    outs = {o["seed_key"]: o["id"] for o in client.get(f"{b}/outcomes").json()}
    t = client.post(f"{b}/third-party", json={"name": tool, "raw_cost": 100000, "is_managed": managed,
                                              "tooling_pct": 0.3 if managed else None,
                                              "persona_ids": [p["id"]]}).json()
    for k in outcome_keys:
        client.post(f"{b}/coverage", json={"outcome_id": outs[k], "product_kind": "ThirdParty",
                                           "third_party_product_id": t["id"], "ratified": True})
    s = client.post(f"{b}/scenarios", json={"persona_id": p["id"], "target_sku_reference": "Microsoft 365 E5",
                                            "target_unit_price_annual": 0, "in_scope": True}).json()
    return b, s


def _sections(client, b):
    v = client.get(f"{b}/result").json()["views"]["sections"]
    return {x["key"]: x for x in v["sections"]}, v


def test_calling_service_lets_the_move_replace_a_phone_system(client):
    b, s = _setup(client, "Calling Co", "PhoneCo", ["telephony-pbx", "telephony-pstn"])
    assert _sections(client, b)[0]["Microsoft"]["retired"] == 0      # E5 has no dial tone
    svc = client.post("/api/admin/practice-services", json={
        "name": "Calling service", "kind": "Calling", "price_monthly": 10,
        "outcome_keys": ["telephony-pstn"]}).json()
    r = client.put(f"{b}/scenarios/{s['id']}/services", json={"service_ids": [svc["id"]]})
    assert r.status_code == 200 and r.json()["service_ids"] == [svc["id"]]
    secs, v = _sections(client, b)
    assert secs["Calling"]["retired"] == 100000 and secs["Calling"]["cost_after"] == 12000
    assert secs["Calling"]["change_annual"] == -88000 and secs["Calling"]["services"] == ["Calling service"]
    result = client.get(f"{b}/result").json()
    assert v["combined_change_annual"] == result["rollup"]["net_tco_delta_annual"]
    assert result["rollup"]["target_services_annual"] == 12000


def test_managed_security_service_replaces_the_managed_share(client):
    b, s = _setup(client, "MDR Co", "SOC Co", ["endpoint-edr", "managed-detection-response"], managed=True)
    svc = client.post("/api/admin/practice-services", json={
        "name": "MDR service", "kind": "ManagedSecurity", "price_monthly": 15,
        "outcome_keys": ["managed-detection-response"]}).json()
    client.put(f"{b}/scenarios/{s['id']}/services", json={"service_ids": [svc["id"]]})
    secs, v = _sections(client, b)
    m = secs["ManagedSecurity"]
    assert m["retired"] == 30000                     # the software share the engine counts
    assert m["managed_share_retired"] == 70000       # the managed part (G14)
    assert m["change_annual"] == 18000 - 30000 - 70000
    net = client.get(f"{b}/result").json()["rollup"]["net_tco_delta_annual"]
    assert v["combined_change_annual"] == round(net - 70000, 2)


def test_unknown_outcome_keys_and_archived_services(client):
    r = client.post("/api/admin/practice-services", json={"name": "X", "outcome_keys": ["no-such-key"]})
    assert r.status_code == 422
    svc = client.post("/api/admin/practice-services", json={"name": "Old", "kind": "Other"}).json()
    client.patch(f"/api/admin/practice-services/{svc['id']}", json={"archived": True})
    assert svc["id"] not in [x["id"] for x in client.get("/api/admin/practice-services").json()]
    assert svc["id"] in [x["id"] for x in client.get("/api/admin/practice-services?include_archived=true").json()]


def test_readout_shows_sections_only_with_a_service(client):
    b, s = _setup(client, "Readout Sections Co", "PhoneCo2", ["telephony-pbx", "telephony-pstn"])
    assert "Microsoft 365 and Our Services" not in client.get(f"{b}/readout.html").text
    svc = client.post("/api/admin/practice-services", json={
        "name": "Calling line", "kind": "Calling", "price_monthly": 10, "outcome_keys": ["telephony-pstn"]}).json()
    client.put(f"{b}/scenarios/{s['id']}/services", json={"service_ids": [svc["id"]]})
    html = client.get(f"{b}/readout.html").text
    assert "Microsoft 365 and Our Services" in html and "Calling line" in html
