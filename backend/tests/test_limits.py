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


# A price sheet that gives Business Premium (264) and E3 (432) a catalog ERP, so
# both are priced candidates in the best-bundle optimizer.
_PRICED_CSV = (
    "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
    "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment\n"
    "Microsoft 365 E3,P1,001,M365 E3,P1Y,Annual,US,USD,384,2026-01-01,2026-12-31,432,Commercial\n"
    "Microsoft 365 Business Premium,P2,002,M365 BP,P1Y,Annual,US,USD,264,2026-01-01,2026-12-31,264,Commercial\n"
)


def test_best_bundle_respects_business_cap_when_enabled(client):
    """The best-bundle optimizer only gates on the Business seat cap when the
    engagement opts in. A 400-seat persona can't fit under the 300 cap, so the
    cheaper Business Premium is flagged and de-recommended in favor of E3."""
    client.post("/api/catalog/import-csv", files={"file": ("p.csv", _PRICED_CSV, "text/csv")})
    eng = client.post("/api/engagements", json={"customer_name": "OptCap Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 400}).json()
    # On E3 today → required = E3 outcomes, all covered by Business Premium (gapless).
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_assigned": 400,
        "unit_price_paid_annual": 432, "persona_ids": [p["id"]]})
    url = f"/api/engagements/{eid}/personas/{p['id']}/bundle-analysis"

    # Cap OFF (default): the cheaper Business Premium is recommended, no cap context.
    res = client.post(url).json()
    by = {b["sku_reference"]: b for b in res["bundles"]}
    assert by["Microsoft 365 Business Premium"]["recommended"] is True
    assert by["Microsoft 365 Business Premium"]["cap_limited"] is False
    assert res["seat_caps"] == []

    # Cap ON: 400 > 300 → Business Premium flagged cap_limited and de-recommended; E3 wins.
    client.patch(f"/api/engagements/{eid}", json={"business_cap_enabled": True})
    res = client.post(url).json()
    by = {b["sku_reference"]: b for b in res["bundles"]}
    assert by["Microsoft 365 Business Premium"]["cap_limited"] is True
    assert by["Microsoft 365 Business Premium"]["cap_headroom"] == 300
    assert by["Microsoft 365 Business Premium"]["recommended"] is False
    assert by["Microsoft 365 E3"]["recommended"] is True
    cap = next(c for c in res["seat_caps"] if c["cap"] == 300)
    assert cap["consumed"] == 0 and cap["headroom"] == 300


def test_best_bundle_cap_counts_seats_recommended_for_other_personas(client):
    """Headroom nets out seats already recommended for OTHER personas, and excludes
    the persona being analyzed from its own count."""
    client.post("/api/catalog/import-csv", files={"file": ("p.csv", _PRICED_CSV, "text/csv")})
    eng = client.post("/api/engagements", json={"customer_name": "OptCap2"}).json()
    eid = eng["id"]
    client.patch(f"/api/engagements/{eid}", json={"business_cap_enabled": True})
    big = client.post(f"/api/engagements/{eid}/personas",
                      json={"name": "Big", "headcount": 250}).json()
    small = client.post(f"/api/engagements/{eid}/personas",
                        json={"name": "Small", "headcount": 40}).json()
    # Big is already planned onto Business Premium (in-scope) → 250 Business seats committed.
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": big["id"], "target_sku_reference": "Microsoft 365 Business Premium",
        "target_unit_price_annual": 264, "in_scope": True})
    # Small is on E3 today so Business Premium is gapless for it.
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_assigned": 40,
        "unit_price_paid_annual": 432, "persona_ids": [small["id"]]})

    # Analyzing Small (40): 250 already recommended, 50 left → 40 fits, BP still recommended.
    res = client.post(f"/api/engagements/{eid}/personas/{small['id']}/bundle-analysis").json()
    cap = next(c for c in res["seat_caps"] if c["cap"] == 300)
    assert cap["consumed"] == 250 and cap["headroom"] == 50
    by = {b["sku_reference"]: b for b in res["bundles"]}
    assert by["Microsoft 365 Business Premium"]["cap_limited"] is False
    assert by["Microsoft 365 Business Premium"]["recommended"] is True

    # Analyzing Big (250) itself: its OWN 250 is excluded → consumed 0, headroom 300, fits.
    res2 = client.post(f"/api/engagements/{eid}/personas/{big['id']}/bundle-analysis").json()
    cap2 = next(c for c in res2["seat_caps"] if c["cap"] == 300)
    assert cap2["consumed"] == 0 and cap2["headroom"] == 300
    by2 = {b["sku_reference"]: b for b in res2["bundles"]}
    assert by2["Microsoft 365 Business Premium"]["recommended"] is True


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
