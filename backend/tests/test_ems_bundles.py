"""Enterprise Mobility + Security E3/E5 as first-class add-on bundles.

EMS E3/E5 are real, separately-sold SKUs customers hold standalone (commonly
paired with an Office 365 base to approximate M365 E3/E5). Modeling them as
add-ons — with overlapping coverage, not by decomposing M365 — makes their
capability map, so a persona on "Office 365 E3 + EMS E3" no longer looks fully
covered by Office 365 E3 alone. See seeds/bundles.json + seeds/coverage.json.
"""

EMS_E3_OUTCOMES = {"identity-sso", "identity-mfa", "device-management",
                   "information-protection"}
EMS_E5_EXTRA = {"identity-governance", "cloud-app-security"}


def _mk(client, name):
    return client.post("/api/engagements", json={"customer_name": name}).json()["id"]


def _seed_keys_for(coverage_rows, bundle_id, outcome_seed_by_id):
    return {outcome_seed_by_id[c["outcome_id"]] for c in coverage_rows
            if c.get("bundle_id") == bundle_id}


def test_ems_is_a_seeded_addon_bundle(client):
    bundles = client.get("/api/catalog/bundles").json()
    by_name = {b["name"]: b for b in bundles}
    for name in ("Enterprise Mobility + Security E3", "Enterprise Mobility + Security E5"):
        assert name in by_name, f"{name} not seeded"
        assert by_name[name]["kind"] == "addon"


def test_ems_resolves_and_maps_outcomes_in_a_new_engagement(client):
    eid = _mk(client, "EMS Map Co")
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    seed_by_id = {o["id"]: o["seed_key"] for o in outcomes}
    bundles = client.get("/api/catalog/bundles").json()
    ems3 = next(b for b in bundles if b["name"] == "Enterprise Mobility + Security E3")
    ems5 = next(b for b in bundles if b["name"] == "Enterprise Mobility + Security E5")

    cov = client.get(f"/api/engagements/{eid}/coverage").json()
    e3_keys = _seed_keys_for(cov, ems3["id"], seed_by_id)
    e5_keys = _seed_keys_for(cov, ems5["id"], seed_by_id)
    assert e3_keys == EMS_E3_OUTCOMES
    assert e5_keys == EMS_E3_OUTCOMES | EMS_E5_EXTRA


def test_multi_license_persona_no_longer_hides_the_ems_drop(client):
    """The reported case: Office 365 E3 + Enterprise Mobility + Security E3 on one
    persona, targeted at just Office 365 E3. EMS now maps, so it is no longer
    'unmapped' and the target's dropped capability is surfaced."""
    eid = _mk(client, "EMS Drop Co")
    persona = client.post(f"/api/engagements/{eid}/personas",
                          json={"name": "Non-Store Users", "headcount": 700}).json()
    for sku in ("Office 365 E3", "Enterprise Mobility + Security E3"):
        client.post(f"/api/engagements/{eid}/current-licenses",
                    json={"sku_reference": sku, "quantity_assigned": 700,
                          "unit_price_paid_annual": 100, "persona_ids": [persona["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": persona["id"], "target_sku_reference": "Office 365 E3",
                      "target_unit_price_annual": 300, "in_scope": True})

    gaps = client.get(f"/api/engagements/{eid}/coverage-gaps").json()
    p = next(x for x in gaps["personas"] if x["persona_id"] == persona["id"])

    # EMS is a mapped bundle now — not flagged as unmapped.
    refs = {u["sku_reference"] for u in p["unmapped_current_licenses"]}
    assert "Enterprise Mobility + Security E3" not in refs
    # The Office 365 E3 target drops EMS-only capability (MFA/CA + Intune) — surfaced.
    dropped = {o["name"] for o in p["dropped_outcomes"]}
    assert "MFA & Conditional Access" in dropped
    assert "Mobile Device & App Management (MDM/MAM)" in dropped


def test_recommend_a_path_composes_o365_e3_plus_ems_to_preserve_capability(client):
    """With EMS mapped, recommend-a-path sees the EMS outcomes as required and
    composes the Office 365 E3 base with the EMS E3 add-on to close the gap,
    rather than recommending a bare, capability-losing Office 365 E3."""
    eid = _mk(client, "EMS Recommend Co")
    persona = client.post(f"/api/engagements/{eid}/personas",
                          json={"name": "Non-Store Users", "headcount": 700}).json()
    for sku in ("Office 365 E3", "Enterprise Mobility + Security E3"):
        client.post(f"/api/engagements/{eid}/current-licenses",
                    json={"sku_reference": sku, "quantity_assigned": 700,
                          "unit_price_paid_annual": 100, "persona_ids": [persona["id"]]})

    res = client.post(f"/api/engagements/{eid}/personas/{persona['id']}/bundle-analysis").json()
    o365e3 = next(b for b in res["bundles"] if b["sku_reference"] == "Office 365 E3")
    addon_names = {a["name"] for a in o365e3["addons"]}
    assert "Enterprise Mobility + Security E3" in addon_names
    # Composed, Office 365 E3 + EMS E3 covers every required outcome (no gap).
    assert o365e3["covers_all_required"] is True


def test_backfill_adds_ems_to_an_already_seeded_db(client):
    """An existing deployment (seeded before EMS existed) picks EMS up additively:
    seed_bundles inserts the bundle + eligibility, _backfill_new_bundle_coverage
    inserts its default coverage. Existing engagements are untouched (seed law)."""
    from sqlalchemy import delete, select

    from app import models
    from app.db import SessionLocal
    from app.services import bundles as bundles_service

    db = SessionLocal()
    try:
        # Simulate a pre-EMS DB: remove the EMS bundles and their default coverage.
        ems = db.execute(select(models.Bundle).where(
            models.Bundle.key.in_(("ems-e3", "ems-e5")))).scalars().all()
        ems_ids = [b.id for b in ems]
        if ems_ids:
            db.execute(delete(models.AddonEligibility).where(
                models.AddonEligibility.addon_bundle_id.in_(ems_ids)))
        db.execute(delete(models.DefaultBundleCoverage).where(
            models.DefaultBundleCoverage.bundle_key.in_(("ems-e3", "ems-e5"))))
        for b in ems:
            db.delete(b)
        db.commit()
        assert bundles_service.resolve_bundle(db, "Enterprise Mobility + Security E3") is None

        # Re-run the additive startup paths.
        from app.main import _backfill_new_bundle_coverage
        bundles_service.seed_bundles(db)
        _backfill_new_bundle_coverage(db)

        bid = bundles_service.resolve_bundle(db, "Enterprise Mobility + Security E3")
        assert bid is not None
        cov = db.execute(select(models.DefaultBundleCoverage).where(
            models.DefaultBundleCoverage.bundle_key == "ems-e3")).scalars().all()
        assert {c.outcome_key for c in cov} == EMS_E3_OUTCOMES
        # Eligible for the Office 365 bases.
        elig = bundles_service.eligible_base_ids(db, bid)
        assert len(elig) == 3
    finally:
        db.close()
