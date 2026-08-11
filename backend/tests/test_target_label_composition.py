"""A scenario's target is base bundle + add-ons. The engine composes coverage and
price from both, but carries only the base name — so the readout used to show just
the base. compute.attach_target_labels adds a composed `target_label` /
`target_addons` that every display uses (e.g. "Office 365 E3 + Enterprise Mobility
+ Security E3").
"""


def _bundle_id(client, name):
    return next(b["id"] for b in client.get("/api/catalog/bundles").json() if b["name"] == name)


def test_composed_target_label_and_readout(client):
    eid = client.post("/api/engagements", json={"customer_name": "Compose Co"}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "Non-Store Users", "headcount": 700}).json()
    scen = client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Office 365 E3",
        "target_unit_price_annual": 327.60, "in_scope": True}).json()

    # Layer the EMS E3 add-on on (eligible for the Office 365 bases).
    ems = _bundle_id(client, "Enterprise Mobility + Security E3")
    client.patch(f"/api/engagements/{eid}/scenarios/{scen['id']}",
                 json={"addons": [{"bundle_id": ems, "unit_price_annual": 88.20}]})

    result = client.post(f"/api/engagements/{eid}/compute").json()
    s = next(x for x in result["scenarios"] if x["persona_id"] == p["id"])
    assert s["target_addons"] == ["Enterprise Mobility + Security E3"]
    assert s["target_label"] == "Office 365 E3 + Enterprise Mobility + Security E3"

    # The composed name shows on the customer HTML readout, not just the base.
    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "Office 365 E3 + Enterprise Mobility + Security E3" in html


def test_no_addons_labels_by_base_alone(client):
    eid = client.post("/api/engagements", json={"customer_name": "Base Only Co"}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 100}).json()
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    result = client.post(f"/api/engagements/{eid}/compute").json()
    s = next(x for x in result["scenarios"] if x["persona_id"] == p["id"])
    assert s["target_addons"] == []
    assert s["target_label"] == "Microsoft 365 E3"
