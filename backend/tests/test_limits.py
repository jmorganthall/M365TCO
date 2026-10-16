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
        "Microsoft 365 Business Basic (no Teams)",
        "Microsoft 365 Business Standard (no Teams)",
        "Microsoft 365 Business Premium (no Teams)",
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


def _cap_engagement(client, name, headcount):
    """One persona on Office 365 E3 today, so Business Premium is gapless for it and
    cheaper than E3 (264 vs 432): the recommender's best plan is Business Premium."""
    client.post("/api/catalog/import-csv", files={"file": ("p.csv", _PRICED_CSV, "text/csv")})
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "Office", "headcount": headcount}).json()
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_assigned": headcount,
        "unit_price_paid_annual": 432, "persona_ids": [p["id"]]})
    return eid, p


def _plan(client, eid, pid, ref, price):
    return client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": pid, "target_sku_reference": ref,
        "target_unit_price_annual": price, "in_scope": True}).json()


def test_business_premium_stays_the_recommendation_over_the_cap(client):
    """The cap always applies (no setting): a 400-person persona whose best plan is
    Business Premium keeps it as the recommendation, flagged over the cap by 100 —
    the split is offered, never a silent swap to the next-best plan (D27)."""
    eid, p = _cap_engagement(client, "OptCap Co", 400)
    res = client.post(f"/api/engagements/{eid}/personas/{p['id']}/bundle-analysis").json()
    bp = next(b for b in res["bundles"] if b["sku_reference"] == "Microsoft 365 Business Premium")
    assert bp["recommended"] is True and bp["cap_limited"] is False
    assert bp["cap_headroom"] == 300 and bp["over_cap_by"] == 100
    cap = next(c for c in res["seat_caps"] if c["cap"] == 300)
    assert cap["consumed"] == 0 and cap["headroom"] == 300


def test_seats_left_are_measured_in_the_future_state(client):
    """Other personas count what they will hold: a persona moving OFF Business frees
    its seats; one moving ONTO Business uses them; one with no plan keeps its current
    Business licences. The persona analysed never counts itself."""
    client.post("/api/catalog/import-csv", files={"file": ("p.csv", _PRICED_CSV, "text/csv")})
    eid = client.post("/api/engagements", json={"customer_name": "Future Cap"}).json()["id"]
    mk = lambda n, hc: client.post(f"/api/engagements/{eid}/personas",  # noqa: E731
                                   json={"name": n, "headcount": hc}).json()
    leaving, staying, joining, me = mk("Leaving", 250), mk("Staying", 30), mk("Joining", 80), mk("Me", 40)
    for who, n in ((leaving, 250), (staying, 30)):
        client.post(f"/api/engagements/{eid}/current-licenses", json={
            "sku_reference": "Microsoft 365 Business Premium", "quantity_assigned": n,
            "unit_price_paid_annual": 264, "persona_ids": [who["id"]]})
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_assigned": 120,
        "unit_price_paid_annual": 432, "persona_ids": [joining["id"], me["id"]]})
    _plan(client, eid, leaving["id"], "Microsoft 365 E3", 432)               # frees 250
    _plan(client, eid, joining["id"], "Microsoft 365 Business Premium", 264)  # uses 80
    res = client.post(f"/api/engagements/{eid}/personas/{me['id']}/bundle-analysis").json()
    cap = next(c for c in res["seat_caps"] if c["cap"] == 300)
    # Staying keeps its 30 current Business seats; Joining adds 80; Leaving's 250 don't count.
    assert cap["consumed"] == 110 and cap["headroom"] == 190


def test_no_business_seats_left_recommends_the_next_best_plan(client):
    """With every Business seat used by other personas there is nothing to split:
    Business Premium is shown but not recommended, and E3 is."""
    eid, p = _cap_engagement(client, "Full Cap", 40)
    other = client.post(f"/api/engagements/{eid}/personas", json={"name": "Others", "headcount": 300}).json()
    _plan(client, eid, other["id"], "Microsoft 365 Business Premium", 264)
    res = client.post(f"/api/engagements/{eid}/personas/{p['id']}/bundle-analysis").json()
    by = {b["sku_reference"]: b for b in res["bundles"]}
    assert by["Microsoft 365 Business Premium"]["cap_limited"] is True
    assert by["Microsoft 365 Business Premium"]["recommended"] is False
    assert by["Microsoft 365 E3"]["recommended"] is True


def test_split_offer_larger_capped_side_is_the_parent(client):
    """400 on Business Premium with 300 seats left: Office keeps 300 on Business
    Premium; the 100 over the cap are carved onto the best uncapped plan (E3)."""
    eid, p = _cap_engagement(client, "Split Co", 400)
    _plan(client, eid, p["id"], "Microsoft 365 Business Premium", 264)
    offers = client.get(f"/api/engagements/{eid}/seat-cap-splits").json()
    assert len(offers) == 1
    o = offers[0]
    assert (o["kind"], o["capped_seats"], o["other_seats"], o["parent_side"]) == ("split", 300, 100, "capped")
    assert o["other_plan"] == "Microsoft 365 E3"
    assert o["carve_out_name"] == "Office (Over Business User Cap)"
    review = client.get(f"/api/engagements/{eid}/review").json()["checks"]
    assert any(c["code"] == "seat_cap_exceeded" and "Split it on Future state" in c["message"]
               for c in review)

    child = client.post(f"/api/engagements/{eid}/personas/{p['id']}/seat-cap-split", json={}).json()
    assert child["name"] == "Office (Over Business User Cap)" and child["headcount"] == 100
    personas = {x["id"]: x for x in client.get(f"/api/engagements/{eid}/personas").json()}
    assert personas[p["id"]]["name"] == "Office" and personas[p["id"]]["headcount"] == 300
    plans = {s["persona_id"]: s["target_sku_reference"]
             for s in client.get(f"/api/engagements/{eid}/scenarios").json()}
    assert plans == {p["id"]: "Microsoft 365 Business Premium", child["id"]: "Microsoft 365 E3"}
    # Resolved: no offer, no Review check.
    assert client.get(f"/api/engagements/{eid}/seat-cap-splits").json() == []
    review = client.get(f"/api/engagements/{eid}/review").json()["checks"]
    assert not any(c["code"] == "seat_cap_exceeded" for c in review)


def test_split_offer_larger_uncapped_side_is_the_parent(client):
    """2,500 on Business Premium: the parent (2,200) moves to E3 and keeps its name;
    the 300 who fit are carved onto Business Premium as "(Up to 300 Business User
    Cap)". A custom name is honoured."""
    eid, p = _cap_engagement(client, "Big Split", 2500)
    _plan(client, eid, p["id"], "Microsoft 365 Business Premium", 264)
    o = client.get(f"/api/engagements/{eid}/seat-cap-splits").json()[0]
    assert (o["capped_seats"], o["other_seats"], o["parent_side"]) == (300, 2200, "other")
    assert o["carve_out_name"] == "Office (Up to 300 Business User Cap)"
    child = client.post(f"/api/engagements/{eid}/personas/{p['id']}/seat-cap-split",
                        json={"name": "Office small sites"}).json()
    assert child["name"] == "Office small sites" and child["headcount"] == 300
    plans = {s["persona_id"]: (s["target_sku_reference"], float(s["target_unit_price_annual"]))
             for s in client.get(f"/api/engagements/{eid}/scenarios").json()}
    assert plans[p["id"]] == ("Microsoft 365 E3", 432.0)
    assert plans[child["id"]] == ("Microsoft 365 Business Premium", 264.0)
    assert client.get(f"/api/engagements/{eid}/seat-cap-splits").json() == []


def test_a_persona_that_fits_gets_no_offer(client):
    eid, p = _cap_engagement(client, "Fits Co", 250)
    _plan(client, eid, p["id"], "Microsoft 365 Business Premium", 264)
    assert client.get(f"/api/engagements/{eid}/seat-cap-splits").json() == []
    r = client.post(f"/api/engagements/{eid}/personas/{p['id']}/seat-cap-split", json={})
    assert r.status_code == 409


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
