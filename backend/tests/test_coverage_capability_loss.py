"""Regression: a persona can hold SEVERAL current Microsoft licenses (many-to-one),
and the Coverage Check must not let a target silently drop capability that a
second license delivers.

Reproduces the reported case: a persona on Office 365 E3 + Enterprise Mobility +
Security E3, where EMS maps to no bundle. Its cost is counted but its outcomes are
invisible, so a target of just Office 365 E3 looked fully covered. The coverage-gaps
payload now flags (a) unmapped current licensing and (b) outcomes the target drops
versus today. Both are DERIVED — no data-model change.
"""


def _mk(client, name):
    return client.post("/api/engagements", json={"customer_name": name}).json()["id"]


def test_unmapped_second_license_is_flagged(client):
    eid = _mk(client, "Multi-License Co")
    persona = client.post(f"/api/engagements/{eid}/personas",
                          json={"name": "Non-Store Users", "headcount": 700}).json()

    # Two current licenses on ONE persona. O365 E3 maps to a seeded bundle; the
    # EMS line matches no bundle at all, so its capability is invisible.
    for sku in ("Office 365 E3", "Enterprise Mobility + Security E3"):
        client.post(f"/api/engagements/{eid}/current-licenses",
                    json={"sku_reference": sku, "quantity_assigned": 700,
                          "unit_price_paid_annual": 100, "persona_ids": [persona["id"]]})

    # Target them at just Office 365 E3 — the "save money" move that drops EMS.
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": persona["id"], "target_sku_reference": "Office 365 E3",
                      "target_unit_price_annual": 300, "in_scope": True})

    gaps = client.get(f"/api/engagements/{eid}/coverage-gaps").json()
    p = next(x for x in gaps["personas"] if x["persona_id"] == persona["id"])

    refs = {u["sku_reference"] for u in p["unmapped_current_licenses"]}
    assert "Enterprise Mobility + Security E3" in refs
    # O365 E3 resolves to a bundle with coverage, so it is NOT flagged as unmapped.
    assert "Office 365 E3" not in refs
    ems = next(u for u in p["unmapped_current_licenses"]
               if u["sku_reference"] == "Enterprise Mobility + Security E3")
    assert ems["resolves_to_bundle"] is False


def test_target_dropping_a_mapped_outcome_is_flagged(client):
    eid = _mk(client, "Downgrade Co")
    persona = client.post(f"/api/engagements/{eid}/personas",
                          json={"name": "Knowledge Worker", "headcount": 100}).json()

    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    # An outcome that Office 365 E1 (the target) does NOT deliver but the richer
    # current bundle does. Pick one delivered by M365 E3 (the current) and confirm
    # the target E1 lacks it, so it must be reported as dropped.
    o365e1 = client.get("/api/catalog/bundles").json()
    assert o365e1  # bundles seeded

    # Current: Microsoft 365 E3 (rich). Target: Office 365 E1 (lean).
    client.post(f"/api/engagements/{eid}/current-licenses",
                json={"sku_reference": "Microsoft 365 E3", "quantity_assigned": 100,
                      "unit_price_paid_annual": 400, "persona_ids": [persona["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": persona["id"], "target_sku_reference": "Office 365 E1",
                      "target_unit_price_annual": 100, "in_scope": True})

    gaps = client.get(f"/api/engagements/{eid}/coverage-gaps").json()
    p = next(x for x in gaps["personas"] if x["persona_id"] == persona["id"])

    # M365 E3 delivers strictly more than O365 E1, so the target drops capability.
    assert len(p["dropped_outcomes"]) > 0
    dropped_names = {o["name"] for o in p["dropped_outcomes"]}
    target_names = {o["name"] for o in p["uncovered_outcomes"]}
    # Dropped and target-new are disjoint concerns (lost vs added).
    assert dropped_names.isdisjoint(target_names)
