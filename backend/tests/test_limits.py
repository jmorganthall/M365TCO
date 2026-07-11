"""License limits: the tenant-wide seat-cap engine over the Bundle spine."""


def _biz(client):
    return next(l for l in client.get("/api/admin/license-limits").json()["limits"]
                if l["key"] == "m365-business-seat-cap")


def test_license_limit_seeded_with_business_family(client):
    """The Business 300-seat cap seeds with all three Business bundles as members."""
    lim = _biz(client)
    assert lim["max_quantity"] == 300
    assert lim["limit_type"] == "max_total_seats"
    assert set(lim["member_bundle_names"]) == {
        "Microsoft 365 Business Basic",
        "Microsoft 365 Business Standard",
        "Microsoft 365 Business Premium",
    }
    # The two new Business staples are seeded bundles with default coverage.
    bundles = {b["key"] for b in client.get("/api/catalog/bundles").json()}
    assert {"m365-business-basic", "m365-business-standard"} <= bundles


def test_license_limit_evaluation_current_and_future(client):
    """Evaluation aggregates tenant-wide: current-license seats + in-scope scenario
    headcount on member bundles, flagged when over the cap; a total recompute clears
    it when a persona leaves scope."""
    eng = client.post("/api/engagements", json={"customer_name": "Cap Co"}).json()
    eid = eng["id"]
    p1 = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 200}).json()
    p2 = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "Ops", "headcount": 140}).json()
    for p in (p1, p2):
        client.post(f"/api/engagements/{eid}/scenarios", json={
            "persona_id": p["id"], "target_sku_reference": "Microsoft 365 Business Premium",
            "target_unit_price_annual": 264, "in_scope": True})
    # 50 existing seats on Business Standard (current state).
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 Business Standard", "quantity_assigned": 50,
        "unit_price_paid_annual": 150, "persona_ids": [p1["id"]]})

    ll = next(l for l in client.post(f"/api/engagements/{eid}/compute").json()["license_limits"]
              if l["key"] == "m365-business-seat-cap")
    assert ll["current_seats"] == 50
    assert ll["target_seats"] == 340          # 200 + 140, summed across personas
    assert ll["target_over_by"] == 40         # 340 - 300
    assert ll["violated"] is True

    # Take one persona out of scope → total recompute drops target to 200, clears it.
    scen = client.get(f"/api/engagements/{eid}/scenarios").json()
    other = next(s for s in scen if s["persona_id"] == p2["id"])
    client.patch(f"/api/engagements/{eid}/scenarios/{other['id']}", json={"in_scope": False})
    ll2 = next(l for l in client.post(f"/api/engagements/{eid}/compute").json()["license_limits"]
               if l["key"] == "m365-business-seat-cap")
    assert ll2["target_seats"] == 200 and ll2["violated"] is False


def test_license_limit_counts_scenario_once(client):
    """A scenario whose base OR an add-on touches a member bundle counts the
    persona's headcount once — never double-counted."""
    eng = client.post("/api/engagements", json={"customer_name": "Once Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 100}).json()
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 Business Premium",
        "target_unit_price_annual": 264, "in_scope": True})
    ll = next(l for l in client.post(f"/api/engagements/{eid}/compute").json()["license_limits"]
              if l["key"] == "m365-business-seat-cap")
    assert ll["target_seats"] == 100  # once, not 100-per-member-bundle


def test_license_limit_crud_and_validation(client):
    bundles = {b["key"]: b for b in client.get("/api/catalog/bundles").json()}
    f1, f3 = bundles["m365-f1"]["id"], bundles["m365-f3"]["id"]

    # Create a new limit with an initial member set.
    created = client.post("/api/admin/license-limits", json={
        "name": "Frontline seat cap", "max_quantity": 500,
        "member_bundle_ids": [f1, f3]})
    assert created.status_code == 201
    lim = created.json()
    assert lim["max_quantity"] == 500 and set(lim["member_bundle_ids"]) == {f1, f3}

    # Validation: unknown bundle, bad limit_type, negative cap.
    assert client.post("/api/admin/license-limits",
                       json={"name": "X", "member_bundle_ids": ["nope"]}).status_code == 422
    assert client.post("/api/admin/license-limits",
                       json={"name": "X", "limit_type": "bogus"}).status_code == 422
    assert client.post("/api/admin/license-limits",
                       json={"name": "X", "max_quantity": -5}).status_code == 422

    # Edit the cap; replace the member set down to one.
    r = client.patch(f"/api/admin/license-limits/{lim['id']}", json={"max_quantity": 400})
    assert r.status_code == 200 and r.json()["max_quantity"] == 400
    r = client.put(f"/api/admin/license-limits/{lim['id']}/members",
                   json={"member_bundle_ids": [f1]})
    assert r.json()["member_bundle_ids"] == [f1]
    assert client.put(f"/api/admin/license-limits/{lim['id']}/members",
                      json={"member_bundle_ids": ["nope"]}).status_code == 422

    # Delete removes it (and its members) cleanly.
    assert client.delete(f"/api/admin/license-limits/{lim['id']}").status_code == 204
    keys = {l["id"] for l in client.get("/api/admin/license-limits").json()["limits"]}
    assert lim["id"] not in keys


def test_new_bundle_coverage_backfill_is_additive(client):
    """On an already-seeded DB, the startup backfill seeds default coverage for a
    brand-new bundle (Business Standard) without disturbing existing bundles."""
    from sqlalchemy import delete, select
    from app.db import SessionLocal
    from app.main import _backfill_new_bundle_coverage
    from app import models

    client.get("/api/admin/default-coverage")  # ensure seeded
    db = SessionLocal()
    try:
        # Simulate a DB that predates the Business Standard bundle coverage.
        db.execute(delete(models.DefaultBundleCoverage).where(
            models.DefaultBundleCoverage.bundle_key == "m365-business-standard"))
        db.commit()

        _backfill_new_bundle_coverage(db)

        keys = {(c.bundle_key, c.outcome_key)
                for c in db.execute(select(models.DefaultBundleCoverage)).scalars()}
        assert ("m365-business-standard", "desktop-software") in keys  # restored
        assert ("m365-e3", "desktop-software") in keys                 # untouched
    finally:
        db.close()
