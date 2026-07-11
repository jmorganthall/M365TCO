"""Bundle spine: seeded staples, add-on base links, and SKU → bundle mapping."""


def test_bundles_seeded_with_addon_base_links(client):
    bundles = client.get("/api/catalog/bundles").json()
    by_key = {b["key"]: b for b in bundles}
    # Staples present.
    assert {"m365-e3", "m365-e5", "m365-e7", "m365-business-premium",
            "m365-f1", "m365-f3"} <= set(by_key)
    # Add-ons resolve to their base bundle.
    assert by_key["e5-security"]["kind"] == "addon"
    assert by_key["e5-security"]["base_name"] == "Microsoft 365 E3"
    assert by_key["f5-compliance"]["base_name"] == "Microsoft 365 F3"
    # Full bundles have no base.
    assert by_key["m365-e3"]["base_bundle_id"] is None


def test_map_catalog_sku_to_bundle(client):
    csv_text = (
        "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
        "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment\n"
        "Microsoft 365 E3,CFQ7BUNDLE01,0001,M365 E3,P1Y,Annual,US,USD,"
        "384.00,2026-01-01,2026-12-31,432.00,Commercial\n"
    )
    files = {"file": ("price.csv", csv_text, "text/csv")}
    client.post("/api/catalog/import-csv", files=files)
    skus = client.get("/api/catalog/skus").json()
    sku = next(s for s in skus if s["sku_id"] == "0001")
    assert sku["bundle_id"] is None  # unmapped until classified

    # The SKU carries the suggested-mapping fields (unset until the AI mapper runs).
    assert sku["suggested_bundle_id"] is None
    assert sku["bundle_suggestion_reason"] == ""
    # It shows up on the unmapped work-list the mapper UI drives.
    assert any(s["id"] == sku["id"] for s in client.get("/api/catalog/skus?unmapped=true").json())

    e3 = next(b for b in client.get("/api/catalog/bundles").json() if b["key"] == "m365-e3")
    r = client.patch(f"/api/catalog/skus/{sku['id']}/bundle", json={"bundle_id": e3["id"]})
    assert r.status_code == 200 and r.json()["bundle_id"] == e3["id"]
    assert next(s for s in client.get("/api/catalog/skus").json()
                if s["id"] == sku["id"])["bundle_id"] == e3["id"]
    # Now mapped, that row drops off the unmapped work-list.
    assert not any(s["id"] == sku["id"] for s in client.get("/api/catalog/skus?unmapped=true").json())

    # Rejecting a suggestion is a no-op when there is none, and returns cleanly.
    assert client.post(f"/api/catalog/skus/{sku['id']}/reject-suggestion").status_code == 200

    # Unknown bundle rejected.
    assert client.patch(f"/api/catalog/skus/{sku['id']}/bundle", json={"bundle_id": "nope"}).status_code == 422


def test_addon_eligibility_seeded_and_alacarte(client):
    """Add-on eligibility (the composition logic layer) is seeded from the add-on
    base links: E5 Security → E3 only, F5 Security → F3 only. À-la-carte add-ons
    (no base) carry no eligibility and layer onto any base."""
    bundles = client.get("/api/catalog/bundles").json()
    by_key = {b["key"]: b for b in bundles}

    e3, f3 = by_key["m365-e3"]["id"], by_key["m365-f3"]["id"]
    assert by_key["e5-security"]["eligible_base_ids"] == [e3]
    assert by_key["e5-security"]["alacarte"] is False
    assert by_key["f5-security"]["eligible_base_ids"] == [f3]
    # Teams Phone is à-la-carte (base: null) → eligible for any base.
    assert by_key["teams-phone"]["eligible_base_ids"] == []
    assert by_key["teams-phone"]["alacarte"] is True


def test_addon_eligibility_crud_and_validation(client):
    bundles = client.get("/api/catalog/bundles").json()
    by_key = {b["key"]: b for b in bundles}
    e5sec = by_key["e5-security"]["id"]
    e3, e5 = by_key["m365-e3"]["id"], by_key["m365-e5"]["id"]

    # Broaden E5 Security to E3 + E5.
    r = client.put(f"/api/catalog/bundles/{e5sec}/eligibility",
                   json={"base_bundle_ids": [e3, e5]})
    assert r.status_code == 200 and set(r.json()["eligible_base_ids"]) == {e3, e5}
    assert r.json()["alacarte"] is False

    # Empty set = à-la-carte.
    r = client.put(f"/api/catalog/bundles/{e5sec}/eligibility", json={"base_bundle_ids": []})
    assert r.status_code == 200 and r.json()["alacarte"] is True

    # Validation: a base bundle can't take eligibility; unknown/non-base ids rejected.
    assert client.put(f"/api/catalog/bundles/{e3}/eligibility",
                      json={"base_bundle_ids": []}).status_code == 422
    assert client.put(f"/api/catalog/bundles/{e5sec}/eligibility",
                      json={"base_bundle_ids": ["nope"]}).status_code == 422
    assert client.put(f"/api/catalog/bundles/{e5sec}/eligibility",
                      json={"base_bundle_ids": [by_key["f5-security"]["id"]]}).status_code == 422

    # Restore the seeded eligibility so other tests aren't affected.
    client.put(f"/api/catalog/bundles/{e5sec}/eligibility", json={"base_bundle_ids": [e3]})


def test_scenario_addon_eligibility_enforced(client):
    """A scenario can only add an add-on eligible for its base bundle: F5 Security
    (F3-only) is rejected on an E3 base; E5 Security (E3) and an à-la-carte add-on
    are accepted."""
    eng = client.post("/api/engagements", json={"customer_name": "Elig Co"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 10}).json()
    by_key = {b["key"]: b for b in client.get("/api/catalog/bundles").json()}
    f5sec, e5sec, teams = (by_key["f5-security"]["id"],
                           by_key["e5-security"]["id"], by_key["teams-phone"]["id"])

    # F5 Security (eligible for F3 only) onto an E3 base → 422.
    r = client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True,
        "addons": [{"bundle_id": f5sec, "unit_price_annual": 10}]})
    assert r.status_code == 422 and "not eligible" in r.json()["detail"]

    # E5 Security (E3) + Teams Phone (à-la-carte) onto E3 → accepted.
    r = client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True,
        "addons": [{"bundle_id": e5sec, "unit_price_annual": 100},
                   {"bundle_id": teams, "unit_price_annual": 80}]})
    assert r.status_code == 201
    sid = r.json()["id"]

    # PATCH is enforced too — adding the ineligible F5 Security is rejected.
    r = client.patch(f"/api/engagements/{eid}/scenarios/{sid}", json={
        "addons": [{"bundle_id": f5sec, "unit_price_annual": 10}]})
    assert r.status_code == 422


def test_optimizer_respects_addon_eligibility(client):
    """Restricting an à-la-carte add-on away from a base removes it from that base's
    recommend-a-path composition. Defender for Endpoint P2 (normally à-la-carte,
    free, closes E3's Endpoint gap) is restricted to F3 → E3 must fall back to the
    priced E5 Security add-on instead."""
    eng = client.post("/api/engagements", json={"customer_name": "Opt Elig"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 100}).json()
    endpoint = next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
                    if "Endpoint Security" in o["name"])
    edr = client.post(f"/api/engagements/{eid}/third-party",
                      json={"name": "CrowdStrike", "raw_cost": 20000, "covered_count": 100}).json()
    client.post(f"/api/engagements/{eid}/coverage",
                json={"outcome_id": endpoint["id"], "product_kind": "ThirdParty",
                      "third_party_product_id": edr["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E5", "quantity_assigned": 100,
        "unit_price_paid_annual": 0, "persona_ids": [kw["id"]]})

    by_key = {b["key"]: b for b in client.get("/api/catalog/bundles").json()}
    defender = by_key["defender-endpoint-p2"]["id"]
    f3 = by_key["m365-f3"]["id"]
    # Restrict the free Endpoint add-on to F3 only, so E3 can't use it.
    client.put(f"/api/catalog/bundles/{defender}/eligibility", json={"base_bundle_ids": [f3]})
    try:
        res = client.post(f"/api/engagements/{eid}/personas/{kw['id']}/bundle-analysis",
                          json={"prices": {"Microsoft 365 E3": 400,
                                           "Microsoft 365 E5 Security": 100}}).json()
        e3 = {b["sku_reference"]: b for b in res["bundles"]}["Microsoft 365 E3"]
        assert "Endpoint Security" not in e3["gap_outcomes"]  # still closed…
        # …but no longer for free — the E5 Security add-on (100) is now chosen.
        assert e3["addon_total_annual"] == 100.0
        assert not any("Defender for Endpoint" in a["name"] for a in e3["addons"])
    finally:
        client.put(f"/api/catalog/bundles/{defender}/eligibility", json={"base_bundle_ids": []})


def test_scenario_targeting_bundle_name_resolves_and_displaces(client):
    """The bug fix: a scenario target that names the bundle ("Microsoft 365 E3"),
    not the old "E3" shortcode, now resolves to the bundle and displaces."""
    eng = client.post("/api/engagements", json={"customer_name": "Bundle Displace"}).json()
    eid = eng["id"]
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    identity = next(o for o in outcomes if "Identity" in o["name"])
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 100}).json()
    okta = client.post(f"/api/engagements/{eid}/third-party",
                       json={"name": "Okta", "raw_cost": 10000, "covered_count": 100}).json()
    client.post(f"/api/engagements/{eid}/coverage",
                json={"outcome_id": identity["id"], "product_kind": "ThirdParty",
                      "third_party_product_id": okta["id"], "coverage": "Full", "ratified": True})
    # Target the full bundle NAME (previously would not match the "E3"-keyed map).
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E3",
                      "target_unit_price_annual": 0, "in_scope": True})
    result = client.post(f"/api/engagements/{eid}/compute").json()
    assert result["dispositions"][0]["disposition"] == "FullyEliminated"

    # The seeded MS coverage is now bundle-keyed (readable bundle names shown).
    cov = client.get(f"/api/engagements/{eid}/coverage").json()
    ms = [c for c in cov if c["product_kind"] == "MicrosoftSku"]
    assert any(c["microsoft_sku_reference"] == "Microsoft 365 E3" for c in ms)
    assert all(c.get("bundle_id") for c in ms)  # every seeded MS entry maps to a bundle


def test_scenario_composes_base_plus_addons_with_discount(client):
    """Future state = base bundle + add-on bundles: union outcomes, sum list
    prices, apply the discount to the net the engine uses."""
    eng = client.post("/api/engagements", json={"customer_name": "Compose Co"}).json()
    eid = eng["id"]
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    ident = next(o for o in outcomes if "Identity" in o["name"])
    endpoint = next(o for o in outcomes if "Endpoint Security" in o["name"])
    kw = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 100}).json()

    # Two third-party tools: one Identity (covered by E3 base), one Endpoint
    # Security (covered only by the E5 Security add-on).
    idp = client.post(f"/api/engagements/{eid}/third-party",
                      json={"name": "Okta", "raw_cost": 10000, "covered_count": 100}).json()
    edr = client.post(f"/api/engagements/{eid}/third-party",
                      json={"name": "CrowdStrike", "raw_cost": 20000, "covered_count": 100}).json()
    for tp, oc in [(idp, ident), (edr, endpoint)]:
        client.post(f"/api/engagements/{eid}/coverage",
                    json={"outcome_id": oc["id"], "product_kind": "ThirdParty",
                          "third_party_product_id": tp["id"], "coverage": "Full", "ratified": True})

    e5sec = next(b for b in client.get("/api/catalog/bundles").json() if b["key"] == "e5-security")

    # Base E3 @ 400 list + E5 Security add-on @ 100 list, 10% discount → net 450/seat.
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "target_discount_pct": 0.10, "in_scope": True,
        "addons": [{"bundle_id": e5sec["id"], "unit_price_annual": 100}],
    })
    result = client.post(f"/api/engagements/{eid}/compute").json()
    sc = result["scenarios"][0]
    # net = (400 + 100) * 0.9 = 450; target spend = 100 * 450 = 45000.
    assert sc["target_spend_annual"] == 45000.0
    # Both tools displaced: E3 covers Identity, the E5 Security add-on covers Endpoint.
    disps = {d["third_party_product_id"]: d["disposition"] for d in result["dispositions"]}
    assert disps[idp["id"]] == "FullyEliminated"
    assert disps[edr["id"]] == "FullyEliminated"

    # Round-trip: the scenario exposes its add-ons and discount.
    scen = client.get(f"/api/engagements/{eid}/scenarios").json()[0]
    assert float(scen["target_discount_pct"]) == 0.10
    assert scen["addons"][0]["bundle_id"] == e5sec["id"]


def test_default_coverage_seeds_and_editing_affects_new_engagements_only(client):
    """The global default coverage library (E3): seeded from the file, editable, and
    the template new engagements inherit — edits never touch existing engagements."""
    lib = client.get("/api/admin/default-coverage").json()
    assert lib, "default coverage library should seed from coverage.json"
    # E3 covers identity-access by default; it does NOT cover endpoint-security.
    e3 = [r for r in lib if r["bundle_key"] == "m365-e3"]
    assert any(r["outcome_key"] == "identity-access" for r in e3)
    assert not any(r["outcome_key"] == "endpoint-security" for r in e3)

    # Engagement A, created BEFORE the edit.
    a = client.post("/api/engagements", json={"customer_name": "Before"}).json()

    # Add identity-access to E7 (seeded with EMPTY coverage) in the default library
    # — a throwaway pairing so the shared default library isn't left mutated.
    r = client.post("/api/admin/default-coverage",
                    json={"bundle_key": "m365-e7", "outcome_key": "identity-access", "coverage": "Full"})
    assert r.status_code == 201
    added_id = r.json()["id"]

    # Engagement B, created AFTER the edit.
    b = client.post("/api/engagements", json={"customer_name": "After"}).json()

    def e7_covers_identity(eid):
        cov = client.get(f"/api/engagements/{eid}/coverage").json()
        outs = client.get(f"/api/engagements/{eid}/outcomes").json()
        ident = next(o for o in outs if "Identity" in o["name"])
        return any(c["product_kind"] == "MicrosoftSku"
                   and c["microsoft_sku_reference"] == "Microsoft 365 E7"
                   and c["outcome_id"] == ident["id"] for c in cov)

    assert e7_covers_identity(b["id"]) is True    # inherited the edit
    assert e7_covers_identity(a["id"]) is False   # existing engagement untouched

    # Restore the shared default library (don't leak state into other tests).
    assert client.delete(f"/api/admin/default-coverage/{added_id}").status_code == 204


def test_persona_requirements_crud_and_validation(client):
    """Per-persona required capabilities (Personas tab) round-trip and reconcile."""
    eng = client.post("/api/engagements", json={"customer_name": "Reqs Co"}).json()
    eid = eng["id"]
    outs = {o["name"]: o["id"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    p = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 100}).json()
    assert p["required_outcome_ids"] == []

    desk, store = outs["Desktop Software"], outs["Full-Size Cloud Storage"]
    r = client.patch(f"/api/engagements/{eid}/personas/{p['id']}",
                     json={"required_outcome_ids": [desk, store, "bogus"]})
    assert r.status_code == 200
    assert set(r.json()["required_outcome_ids"]) == {desk, store}  # invalid id dropped

    # Reconcile down to one (no UNIQUE re-insert error).
    r = client.patch(f"/api/engagements/{eid}/personas/{p['id']}",
                     json={"required_outcome_ids": [desk]})
    assert r.json()["required_outcome_ids"] == [desk]
    # Editing another field leaves requirements untouched (None = unchanged).
    r = client.patch(f"/api/engagements/{eid}/personas/{p['id']}", json={"headcount": 120})
    assert r.json()["required_outcome_ids"] == [desk] and r.json()["headcount"] == 120


def test_persona_requirement_feeds_recommend_path_gap(client):
    """A persona that REQUIRES Desktop Software shows a gap on a Frontline bundle
    (F3) that lacks it, but not on E3 which covers it — even with no current
    license delivering the capability."""
    eng = client.post("/api/engagements", json={"customer_name": "Gap Co"}).json()
    eid = eng["id"]
    outs = {o["name"]: o["id"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    p = client.post(f"/api/engagements/{eid}/personas", json={"name": "KW", "headcount": 100}).json()
    client.patch(f"/api/engagements/{eid}/personas/{p['id']}",
                 json={"required_outcome_ids": [outs["Desktop Software"]]})

    res = client.post(f"/api/engagements/{eid}/personas/{p['id']}/bundle-analysis",
                      json={"prices": {}}).json()
    assert "Desktop Software" in [o["name"] for o in res["required_outcomes"]]
    by_ref = {b["sku_reference"]: b for b in res["bundles"]}
    assert "Desktop Software" in by_ref["Microsoft 365 F3"]["gap_outcomes"]      # Frontline gap
    assert "Desktop Software" not in by_ref["Microsoft 365 E3"]["gap_outcomes"]  # mainline covers it


def test_new_differentiator_outcomes_split_frontline_from_mainline(client):
    """Desktop Software + Full-Size Cloud Storage are seeded outcomes that mainline
    bundles (E3/E5/BP/E7) cover and Frontline bundles (F1/F3) do not — the
    differentiator the persona requirements will key on."""
    eng = client.post("/api/engagements", json={"customer_name": "Split Co"}).json()
    eid = eng["id"]
    outs = client.get(f"/api/engagements/{eid}/outcomes").json()
    by_name = {o["name"]: o["id"] for o in outs}
    assert "Desktop Software" in by_name and "Full-Size Cloud Storage" in by_name

    cov = client.get(f"/api/engagements/{eid}/coverage").json()

    def covers(bundle_name, outcome_id):
        return any(c["product_kind"] == "MicrosoftSku"
                   and c["microsoft_sku_reference"] == bundle_name
                   and c["outcome_id"] == outcome_id for c in cov)

    desk = by_name["Desktop Software"]
    store = by_name["Full-Size Cloud Storage"]
    # Mainline covers both.
    for b in ("Microsoft 365 E3", "Microsoft 365 E5", "Microsoft 365 Business Premium"):
        assert covers(b, desk) and covers(b, store), b
    # Frontline covers neither.
    for b in ("Microsoft 365 F1", "Microsoft 365 F3"):
        assert not covers(b, desk) and not covers(b, store), b


def test_binary_coverage_backfill_collapses_partial(client):
    """The one-time migration flips any legacy 'Partial' coverage to the single
    'Full' marker (coverage is now binary)."""
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.main import _backfill_binary_coverage

    eng = client.post("/api/engagements", json={"customer_name": "Binary Co"}).json()
    db = SessionLocal()
    try:
        # Force a legacy value in via raw SQL (the ORM enum no longer permits it).
        db.execute(text("UPDATE coverage_map_entries SET coverage='Partial' "
                        "WHERE engagement_id=:e"), {"e": eng["id"]})
        db.commit()
        assert db.execute(text("SELECT COUNT(*) FROM coverage_map_entries "
                               "WHERE coverage='Partial'")).scalar() > 0
        _backfill_binary_coverage(db)
        assert db.execute(text("SELECT COUNT(*) FROM coverage_map_entries "
                               "WHERE coverage='Partial'")).scalar() == 0
    finally:
        db.close()


def test_new_outcome_backfill_is_additive_for_existing_dbs(client):
    """On an ALREADY-seeded DB, the startup backfill inserts a missing default
    outcome (by key) and its default bundle coverage — without disturbing others."""
    from sqlalchemy import delete, select
    from app.db import SessionLocal
    from app.main import _backfill_new_default_outcomes
    from app import models

    client.get("/api/admin/default-coverage")  # ensure seeded
    db = SessionLocal()
    try:
        # Simulate a pre-existing DB that predates the new outcome.
        db.execute(delete(models.DefaultBundleCoverage).where(
            models.DefaultBundleCoverage.outcome_key == "desktop-software"))
        row = db.execute(select(models.DefaultOutcome).where(
            models.DefaultOutcome.key == "desktop-software")).scalar_one()
        db.delete(row)
        db.commit()

        _backfill_new_default_outcomes(db)

        assert db.execute(select(models.DefaultOutcome).where(
            models.DefaultOutcome.key == "desktop-software")).first() is not None
        pairs = {(c.bundle_key, c.outcome_key)
                 for c in db.execute(select(models.DefaultBundleCoverage)).scalars()}
        assert ("m365-e3", "desktop-software") in pairs      # mainline coverage restored
        assert ("m365-f3", "desktop-software") not in pairs  # Frontline still excluded
    finally:
        db.close()


def test_default_coverage_validation_and_crud(client):
    # Operate on a throwaway entry (E7 has empty seed coverage) so no seed row is
    # mutated for other tests.
    created = client.post("/api/admin/default-coverage",
                          json={"bundle_key": "m365-e7", "outcome_key": "collaboration-productivity"})
    assert created.status_code == 201
    entry = created.json()
    assert entry["coverage"] == "Full"  # coverage is binary — stored as the single marker
    # Unknown keys rejected.
    assert client.post("/api/admin/default-coverage",
                       json={"bundle_key": "nope", "outcome_key": "identity-access"}).status_code == 422
    assert client.post("/api/admin/default-coverage",
                       json={"bundle_key": "m365-e7", "outcome_key": "nope"}).status_code == 422
    # Duplicate pair rejected.
    dup = client.post("/api/admin/default-coverage",
                      json={"bundle_key": entry["bundle_key"], "outcome_key": entry["outcome_key"]})
    assert dup.status_code == 409
    # Delete (restores state).
    assert client.delete(f"/api/admin/default-coverage/{entry['id']}").status_code == 204


def test_bundle_library_crud_and_shape_validation(client):
    """Operator-editable bundle library: create/edit, with base/add-on shape rules."""
    # Create a base bundle.
    base = client.post("/api/catalog/bundles",
                       json={"key": "acme-suite", "name": "Acme Suite", "kind": "bundle",
                             "sort_order": 90}).json()
    assert base["kind"] == "bundle" and base["base_bundle_id"] is None

    # Duplicate key rejected.
    assert client.post("/api/catalog/bundles",
                       json={"key": "acme-suite", "name": "Dup"}).status_code == 409
    # An add-on must name a base; a base cannot have a base.
    assert client.post("/api/catalog/bundles",
                       json={"key": "x", "name": "X", "kind": "addon"}).status_code == 422
    assert client.post("/api/catalog/bundles",
                       json={"key": "y", "name": "Y", "kind": "bundle",
                             "base_bundle_id": base["id"]}).status_code == 422

    # Valid add-on onto the base.
    addon = client.post("/api/catalog/bundles",
                        json={"key": "acme-secplus", "name": "Acme Security Plus",
                              "kind": "addon", "base_bundle_id": base["id"]}).json()
    assert addon["base_bundle_id"] == base["id"]

    # Edit: rename + re-sort.
    r = client.patch(f"/api/catalog/bundles/{base['id']}",
                     json={"name": "Acme Suite (2026)", "sort_order": 95})
    assert r.status_code == 200 and r.json()["name"] == "Acme Suite (2026)"

    # Delete is blocked while the add-on still bases on it.
    r = client.delete(f"/api/catalog/bundles/{base['id']}")
    assert r.status_code == 409 and "add-on" in r.json()["detail"]

    # Remove the add-on, then the base deletes cleanly.
    assert client.delete(f"/api/catalog/bundles/{addon['id']}").status_code == 200
    assert client.delete(f"/api/catalog/bundles/{base['id']}").status_code == 200
    keys = {b["key"] for b in client.get("/api/catalog/bundles").json()}
    assert "acme-suite" not in keys and "acme-secplus" not in keys


def test_delete_bundle_blocked_by_sku_mapping(client):
    """A bundle mapped from a catalog SKU can't be deleted until the SKU is unmapped."""
    b = client.post("/api/catalog/bundles",
                    json={"key": "mapped-bundle", "name": "Mapped Bundle"}).json()
    csv_text = (
        "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
        "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment\n"
        "Mapped Bundle,CFQ7MAP,0009,Mapped Bundle,P1Y,Annual,US,USD,"
        "100.00,2026-01-01,2026-12-31,120.00,Commercial\n"
    )
    client.post("/api/catalog/import-csv", files={"file": ("p.csv", csv_text, "text/csv")})
    sku = next(s for s in client.get("/api/catalog/skus").json() if s["sku_id"] == "0009")
    client.patch(f"/api/catalog/skus/{sku['id']}/bundle", json={"bundle_id": b["id"]})

    r = client.delete(f"/api/catalog/bundles/{b['id']}")
    assert r.status_code == 409 and "SKU" in r.json()["detail"]

    # Unmap, then it deletes.
    client.patch(f"/api/catalog/skus/{sku['id']}/bundle", json={"bundle_id": None})
    assert client.delete(f"/api/catalog/bundles/{b['id']}").status_code == 200


def test_edit_bundle_coverage_resolves_bundle_and_feeds_displacement(client):
    """The GUI coverage editor: adding an outcome to a bundle's coverage (by bundle
    name) resolves onto the bundle (bundle_id set, ratified) and immediately feeds
    the displacement test — a scenario targeting that bundle now displaces a tool
    delivering the newly-covered outcome."""
    eng = client.post("/api/engagements", json={"customer_name": "Edit Cov"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 50}).json()

    # A custom outcome no seeded bundle covers, plus a tool delivering it.
    oc = client.post(f"/api/engagements/{eid}/outcomes",
                     json={"name": "Bespoke Capability", "is_custom": True}).json()
    tool = client.post(f"/api/engagements/{eid}/third-party",
                       json={"name": "NicheTool", "raw_cost": 5000, "covered_count": 50}).json()
    client.post(f"/api/engagements/{eid}/coverage",
                json={"outcome_id": oc["id"], "product_kind": "ThirdParty",
                      "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True})

    # Edit E3's coverage in the GUI: map the custom outcome onto the bundle by name.
    entry = client.post(f"/api/engagements/{eid}/coverage",
                        json={"outcome_id": oc["id"], "product_kind": "MicrosoftSku",
                              "microsoft_sku_reference": "Microsoft 365 E3",
                              "coverage": "Full", "ratified": True}).json()
    assert entry["bundle_id"]        # resolved onto the E3 bundle, not left as free text
    assert entry["ratified"] is True

    # A scenario targeting E3 now displaces the tool (E3 covers the outcome via the edit).
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E3",
                      "target_unit_price_annual": 0, "in_scope": True})
    result = client.post(f"/api/engagements/{eid}/compute").json()
    disp = {d["third_party_product_id"]: d["disposition"] for d in result["dispositions"]}
    assert disp[tool["id"]] == "FullyEliminated"

    # Removing the coverage entry reverses it — the tool is no longer displaced.
    client.request("DELETE", f"/api/engagements/{eid}/coverage/{entry['id']}")
    result = client.post(f"/api/engagements/{eid}/compute").json()
    disp = {d["third_party_product_id"]: d["disposition"] for d in result["dispositions"]}
    assert disp[tool["id"]] == "Unchanged"


def test_recommend_path_composes_base_plus_gap_closing_addon(client):
    """Recommend-a-path (bundle-analysis) composes a base bundle with the cheapest
    add-ons that close the persona's gaps, not just single SKUs. A persona on E5
    today (which covers Endpoint Security) evaluated against an E3 base leaves an
    Endpoint-Security gap — the composition must surface the E5 Security add-on."""
    eng = client.post("/api/engagements", json={"customer_name": "Path Co"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 100}).json()

    # Third-party EDR delivers Endpoint Security — only a path that covers that
    # outcome displaces it.
    endpoint = next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
                    if "Endpoint Security" in o["name"])
    edr = client.post(f"/api/engagements/{eid}/third-party",
                      json={"name": "CrowdStrike", "raw_cost": 20000, "covered_count": 100}).json()
    client.post(f"/api/engagements/{eid}/coverage",
                json={"outcome_id": endpoint["id"], "product_kind": "ThirdParty",
                      "third_party_product_id": edr["id"], "coverage": "Full", "ratified": True})

    # Persona is on Microsoft 365 E5 today → its seeded coverage (incl. Endpoint
    # Security) is the "required" baseline the recommendation must not drop.
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E5", "quantity_assigned": 100,
        "unit_price_paid_annual": 0, "persona_ids": [kw["id"]]})

    # Price E3 at 400 and the E5 Security add-on at 100; other add-ons have no
    # catalog price (0). The composition should pick the cheapest set that closes
    # the gaps — here Defender for Endpoint P2 (free, covers Endpoint Security) is
    # cheaper than the E5 Security bundle, so it is chosen.
    res = client.post(f"/api/engagements/{eid}/personas/{kw['id']}/bundle-analysis",
                      json={"prices": {"Microsoft 365 E3": 400, "Microsoft 365 E5 Security": 100}}).json()
    by_ref = {b["sku_reference"]: b for b in res["bundles"]}
    e3 = by_ref["Microsoft 365 E3"]

    # E3 alone leaves an Endpoint-Security gap; the composition adds the cheapest
    # add-on(s) to close it, so the composed E3 path covers Endpoint Security.
    assert e3["addons"], "E3 should compose add-ons to close its gaps"
    assert "Endpoint Security" not in e3["gap_outcomes"]         # gap closed by an add-on
    assert any("Endpoint Security" in a["closes"] for a in e3["addons"])
    assert e3["target_unit_price_annual"] == 400.0 + e3["addon_total_annual"]  # base + add-ons
    assert "CrowdStrike" in e3["displaced_products"]             # composed path displaces the EDR

    # Cheapest cover: a free add-on that covers Endpoint Security beats the priced
    # E5 Security bundle, so no add-on cost is incurred to close that gap.
    assert e3["addon_total_annual"] == 0.0
