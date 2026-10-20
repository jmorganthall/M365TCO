"""Library content: Print Management (cloud print, e.g. Universal Print) and End-User
Remote Support (e.g. Intune Remote Help), with their stand-alone plans. Coverage is
checked against Microsoft Learn: the Universal Print licence list (Microsoft 365
E3, E5, F3, Business Premium, stand-alone) and the Intune planning guide's July 2026
packaging (Remote Help in Microsoft 365 E3 through EMS E3, and in E5/E7; no change
to Frontline plans). These tests pin the shape and the upgrade path."""

import json
import pathlib

from sqlalchemy import delete, select

from app import models
from app.db import SessionLocal

SEEDS = pathlib.Path(__file__).resolve().parents[1] / "app" / "seeds"
NEW_OUTCOMES = ("print-management", "remote-support")
NEW_BUNDLES = ("universal-print", "remote-help")


def _coverage():
    data = json.loads((SEEDS / "coverage.json").read_text())
    return {b["bundle"]: {c["outcome"] for c in b["coverage"]} for b in data["bundles"]}


def _with(outcome):
    return {k for k, v in _coverage().items() if outcome in v}


# ---- The seed files --------------------------------------------------------

def test_print_management_is_on_the_plans_that_include_universal_print():
    assert _with("print-management") == {
        "m365-e3", "m365-e5", "m365-e7", "m365-f3", "m365-business-premium",
        "m365-e3-no-teams", "m365-e5-no-teams", "m365-f3-no-teams",
        "m365-business-premium-no-teams", "universal-print"}


def test_remote_support_is_on_the_plans_that_include_remote_help():
    assert _with("remote-support") == {
        "m365-e3", "m365-e5", "m365-e7", "m365-e3-no-teams", "m365-e5-no-teams",
        "ems-e3", "ems-e5", "remote-help"}
    # Frontline and Business plans are unchanged by the July 2026 packaging.
    for k in ("m365-f1", "m365-f3", "m365-business-premium", "intune-p1"):
        assert "remote-support" not in _coverage()[k], k


def test_the_stand_alones_are_add_ons_with_a_name_alias(client):
    bundles = {b["key"]: b for b in client.get("/api/catalog/bundles").json()}
    assert bundles["universal-print"]["kind"] == "addon"
    assert bundles["remote-help"]["kind"] == "addon"
    assert "remote help" in bundles["remote-help"]["aliases"]


def test_a_new_engagement_gets_both_outcomes(client):
    eid = client.post("/api/engagements", json={"customer_name": "Print Co"}).json()["id"]
    keys = {o["seed_key"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    assert set(NEW_OUTCOMES) <= keys


# ---- An existing database / an older engagement ------------------------------

def test_an_existing_database_picks_them_up_and_an_older_engagement_is_offered_them(client):
    from app.main import (_backfill_coverage_corrections, _backfill_new_bundle_coverage,
                          _backfill_new_default_outcomes)
    from app.services import bundles as bundles_service

    eid = client.post("/api/engagements", json={"customer_name": "Older Print Co"}).json()["id"]
    with SessionLocal() as db:
        # The engagement as it was before the library learned these outcomes.
        old = [o.id for o in db.execute(select(models.Outcome).where(
            models.Outcome.engagement_id == eid,
            models.Outcome.seed_key.in_(NEW_OUTCOMES))).scalars()]
        new_ids = [b.id for b in db.execute(select(models.Bundle).where(
            models.Bundle.key.in_(NEW_BUNDLES))).scalars()]
        db.execute(delete(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == eid,
            models.CoverageMapEntry.outcome_id.in_(old)
            | models.CoverageMapEntry.bundle_id.in_(new_ids)))
        db.execute(delete(models.Outcome).where(models.Outcome.id.in_(old)))
        # The global library as it was.
        db.execute(delete(models.DefaultBundleCoverage).where(
            models.DefaultBundleCoverage.outcome_key.in_(NEW_OUTCOMES)
            | models.DefaultBundleCoverage.bundle_key.in_(NEW_BUNDLES)))
        db.execute(delete(models.DefaultOutcome).where(models.DefaultOutcome.key.in_(NEW_OUTCOMES)))
        db.commit()

        # The additive startup paths, in startup order.
        bundles_service.seed_bundles(db)
        _backfill_new_bundle_coverage(db)
        _backfill_new_default_outcomes(db)
        _backfill_coverage_corrections(db)

        pairs = {(c.bundle_key, c.outcome_key)
                 for c in db.execute(select(models.DefaultBundleCoverage)).scalars()}
        assert ("m365-f3", "print-management") in pairs
        assert ("universal-print", "print-management") in pairs
        assert ("m365-e7", "remote-support") in pairs and ("ems-e3", "remote-support") in pairs
        assert ("m365-f3", "remote-support") not in pairs
        assert set(NEW_OUTCOMES) <= set(db.execute(select(models.DefaultOutcome.key)).scalars())
        # The older engagement is not changed by the migration.
        assert not db.execute(select(models.Outcome).where(
            models.Outcome.engagement_id == eid,
            models.Outcome.seed_key.in_(NEW_OUTCOMES))).first()

    updates = client.get(f"/api/engagements/{eid}/library-updates").json()["items"]
    added = {i["outcome_key"] for i in updates if i["kind"] == "outcome_added"}
    assert added == set(NEW_OUTCOMES)
