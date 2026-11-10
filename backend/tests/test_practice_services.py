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
    # Microsoft (E5) replaces the software share; the service replaces the managed part.
    assert secs["Microsoft"]["retired"] == 30000
    m = secs["ManagedSecurity"]
    assert m["retired"] == 70000 and m["managed_share_retired"] == 70000
    assert m["change_annual"] == 18000 - 70000
    net = client.get(f"{b}/result").json()["rollup"]["net_tco_delta_annual"]
    assert v["combined_change_annual"] == net


def test_managed_tools_count_in_full_today_and_only_a_service_replaces_the_managed_part(client):
    b, s = _setup(client, "Managed Today Co", "SOC Two", ["endpoint-edr", "managed-detection-response"], managed=True)
    i = client.get(f"{b}/interpreted").json()
    assert i["summary"]["baseline_tools_annual"] == 100000          # the invoice, not 30%
    secs, v = _sections(client, b)
    assert secs["Microsoft"]["retired"] == 30000                   # E5 retires only the software
    r = client.get(f"{b}/result").json()
    share = next(d for d in r["dispositions"] if d["third_party_product_name"] == "SOC Two (managed service)")
    assert share["disposition"] == "Unchanged" and share["residual_annual_cost"] == 70000


def test_a_service_price_is_locked_until_a_person_applies_a_change(client):
    b, s = _setup(client, "Locked Price Co", "PhoneLock", ["telephony-pbx", "telephony-pstn"])
    svc = client.post("/api/admin/practice-services", json={
        "name": "Line lock", "kind": "Calling", "price_monthly": 10, "outcome_keys": ["telephony-pstn"]}).json()
    client.put(f"{b}/scenarios/{s['id']}/services", json={"service_ids": [svc["id"]]})
    before = _sections(client, b)[0]["Calling"]["cost_after"]
    client.patch(f"/api/admin/practice-services/{svc['id']}", json={"price_monthly": 20})
    assert _sections(client, b)[0]["Calling"]["cost_after"] == before == 12000
    [c] = [c for c in client.get(f"{b}/review").json()["checks"] if c["code"] == "service_price_changed"]
    assert "$20.00" in c["message"] and "$10.00" in c["message"]
    assert client.post(f"{b}/practice-services/{svc['id']}/apply-price").status_code == 204
    assert _sections(client, b)[0]["Calling"]["cost_after"] == 24000
    assert not [c for c in client.get(f"{b}/review").json()["checks"] if c["code"] == "service_price_changed"]


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


# ---- G13a: a service's quote for this customer --------------------------------

def _mdr(client, name):
    b, s = _setup(client, name, "SOC Q", ["endpoint-edr", "managed-detection-response"], managed=True)
    svc = client.post("/api/admin/practice-services", json={
        "name": "MDR quoted", "kind": "ManagedSecurity", "price_monthly": 15,
        "outcome_keys": ["managed-detection-response"]}).json()
    client.put(f"{b}/scenarios/{s['id']}/services", json={"service_ids": [svc["id"]]})
    return b, s, svc


def test_a_quote_is_spread_over_the_people_who_get_the_service(client):
    b, s, svc = _mdr(client, "Quote Co")
    q = client.post(f"{b}/service-quotes", json={"service_id": svc["id"], "total_cost": 1000,
                                                  "cost_period": "Monthly", "users_covered": 80})
    assert q.status_code == 201
    i = client.get(f"{b}/interpreted").json()
    iq = i["service_quotes"][0]
    # 12,000 a year over the 100 people in the group: $10 per user per month.
    assert iq["annual_cost"] == 12000 and iq["people"] == 100 and iq["per_user_monthly"] == 10
    assert i["scenarios"][0]["services_annual"] == 120
    assert any(f["code"] == "service_quote_seats" for f in i["findings"])
    r = client.get(f"{b}/result").json()
    assert r["rollup"]["target_services_annual"] == 12000          # exactly the quote
    m = r["views"]["mdr"]
    assert m["retired_annual"] == 100000 and m["services_annual"] == 12000 and m["change_annual"] == -88000
    assert m["tools"][0]["software_by_microsoft"] and m["services"][0]["users_covered"] == 80
    assert r["views"]["sections"]["combined_change_annual"] == r["rollup"]["net_tco_delta_annual"]
    # One quote per service; a person can go back to the Settings price.
    assert client.post(f"{b}/service-quotes", json={"service_id": svc["id"]}).status_code == 409
    assert client.patch(f"{b}/service-quotes/{q.json()['id']}", json={"total_cost": -1}).status_code == 422
    assert client.delete(f"{b}/service-quotes/{q.json()['id']}").status_code == 204
    assert client.get(f"{b}/result").json()["rollup"]["target_services_annual"] == 18000


def test_a_quote_nobody_gets_is_in_no_number_and_is_flagged(client):
    b, s, svc = _mdr(client, "Unused Quote Co")
    client.post(f"{b}/service-quotes", json={"service_id": svc["id"], "total_cost": 50000})
    client.put(f"{b}/scenarios/{s['id']}/services", json={"service_ids": []})
    i = client.get(f"{b}/interpreted").json()
    f = next(f for f in i["findings"] if f["code"] == "service_quote_unused")
    assert f["facts"]["name"] == "MDR quoted"
    r = client.get(f"{b}/result").json()
    assert r["rollup"]["target_services_annual"] == 0 and r["views"]["mdr"] is None


def test_a_quoted_service_skips_the_settings_price_notice(client):
    b, s, svc = _mdr(client, "Quote Notice Co")
    client.patch(f"/api/admin/practice-services/{svc['id']}", json={"price_monthly": 20})
    codes = lambda: {f["code"] for f in client.get(f"{b}/interpreted").json()["findings"]}  # noqa: E731
    assert "service_price_changed" in codes()
    client.post(f"{b}/service-quotes", json={"service_id": svc["id"], "total_cost": 1})
    assert "service_price_changed" not in codes()


def test_the_managed_part_is_listed_under_its_tool(client):
    b, s, svc = _mdr(client, "Order Co")
    shown = client.get(f"{b}/result").json()["views"]["dispositions_shown"]
    i = shown.index(next(t for t in shown if t.startswith("managed-share:")))
    assert shown[i - 1] == shown[i][len("managed-share:"):]
