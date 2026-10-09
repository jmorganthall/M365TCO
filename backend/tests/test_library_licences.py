"""Library content from the licence-mapping work (TARGET_SCHEMA §14, D24): the email
outcomes, Exchange Online, the "(no Teams)" suites, the stand-alone licences, and
bundle name aliases as first-class data. Each coverage row is checked against
Microsoft's service descriptions (see the PR); these tests pin the shape."""

import json
import pathlib

import pytest

SEEDS = pathlib.Path(__file__).resolve().parents[1] / "app" / "seeds"


def _coverage():
    data = json.loads((SEEDS / "coverage.json").read_text())
    return {b["bundle"]: {c["outcome"] for c in b["coverage"]} for b in data["bundles"]}


# ---- The seed files --------------------------------------------------------

def test_every_seeded_bundle_has_coverage_of_seeded_outcomes():
    bundles = {b["key"] for b in json.loads((SEEDS / "bundles.json").read_text())["bundles"]}
    outcomes = {o["key"] for o in json.loads((SEEDS / "outcomes.json").read_text())["outcomes"]}
    cov = _coverage()
    assert set(cov) == bundles
    assert all(o in outcomes for v in cov.values() for o in v)
    assert {"email-calendar", "mailbox-full"} <= outcomes


def test_email_outcomes_separate_frontline_from_the_rest():
    cov = _coverage()
    full = {k for k, v in cov.items() if "mailbox-full" in v}
    email = {k for k, v in cov.items() if "email-calendar" in v}
    # A full-size mailbox (50 GB or more) everywhere but Frontline and Kiosk.
    for k in ("o365-e1", "o365-e3", "o365-e5", "m365-e3", "m365-e5", "m365-e7",
              "m365-business-basic", "m365-business-standard", "m365-business-premium",
              "exo-p1", "exo-p2"):
        assert k in full and k in email, k
    # F3 and Exchange Online Kiosk: a 2 GB mailbox — email, not full-size.
    for k in ("m365-f3", "exo-kiosk"):
        assert k in email and k not in full, k
    # F1 has no mailbox rights at all.
    assert "m365-f1" not in email and "m365-f1-no-teams" not in email


def test_exchange_online_plan_2_adds_archiving_and_hold_over_plan_1():
    cov = _coverage()
    assert cov["exo-p2"] - cov["exo-p1"] == {"information-governance"}


def test_e3_includes_defender_for_office_plan_1_from_july_2026():
    cov = _coverage()
    for k in ("o365-e3", "m365-e3", "o365-e3-no-teams", "m365-e3-no-teams"):
        assert "email-atp" in cov[k], k


@pytest.mark.parametrize("parent", [
    "m365-business-basic", "m365-business-standard", "m365-business-premium",
    "m365-f1", "m365-f3", "o365-e1", "o365-e3", "o365-e5", "m365-e3", "m365-e5"])
def test_a_no_teams_suite_is_its_parent_without_chat(parent):
    cov = _coverage()
    assert "chat-meetings" in cov[parent]
    assert cov[f"{parent}-no-teams"] == cov[parent] - {"chat-meetings"}


# ---- Name aliases (BundleAlias) --------------------------------------------

@pytest.mark.parametrize("typed,key", [
    ("Exchange Online (Plan 2)", "exo-p2"),     # the bundle's own name
    ("EXO P2", "exo-p2"),
    ("exchange online  plan 1", "exo-p1"),      # case and spacing don't matter
    ("O365 E3 (no Teams)", "o365-e3-no-teams"),
    ("Microsoft 365 E3 EEA (no Teams)", "m365-e3-no-teams"),
    ("Microsoft Teams EEA", "teams-enterprise"),
    ("Azure AD Premium P1", "entra-id-p1"),
    ("E3", "m365-e3"),                          # legacy shortcodes still resolve
    ("EMS E3", "ems-e3"),
])
def test_customer_wording_resolves_to_the_plan(client, typed, key):
    from app.db import SessionLocal
    from app.services import bundles as bundles_service

    by_key = {b["key"]: b["id"] for b in client.get("/api/catalog/bundles").json()}
    with SessionLocal() as db:
        assert bundles_service.resolve_bundle(db, typed) == by_key[key]


def test_aliases_are_listed_and_edited_in_settings(client):
    bundles = {b["key"]: b for b in client.get("/api/catalog/bundles").json()}
    exo = bundles["exo-p2"]
    assert "exo p2" in exo["aliases"]
    url = f"/api/catalog/bundles/{exo['id']}/aliases"
    r = client.put(url, json={"aliases": exo["aliases"] + ["  Exchange   Online P2 (custom SKU) "]})
    assert r.status_code == 200
    assert "exchange online p2 (custom sku)" in r.json()["aliases"]          # normalized
    # An alias belongs to one bundle.
    e3 = bundles["m365-e3"]
    clash = client.put(f"/api/catalog/bundles/{e3['id']}/aliases", json={"aliases": ["EXO P2"]})
    assert clash.status_code == 409
    client.put(url, json={"aliases": exo["aliases"]})                          # restore
    assert "exchange online p2 (custom sku)" not in next(
        b for b in client.get("/api/catalog/bundles").json() if b["key"] == "exo-p2")["aliases"]


def test_deleting_an_operator_bundle_removes_its_aliases(client):
    from sqlalchemy import select

    from app import models
    from app.db import SessionLocal

    b = client.post("/api/catalog/bundles", json={"key": "op-plan", "name": "Operator Plan"}).json()
    client.put(f"/api/catalog/bundles/{b['id']}/aliases", json={"aliases": ["Op Plan"]})
    assert client.delete(f"/api/catalog/bundles/{b['id']}").status_code == 200
    with SessionLocal() as db:
        assert db.execute(select(models.BundleAlias).where(
            models.BundleAlias.alias == "op plan")).first() is None


# ---- In an engagement ------------------------------------------------------

def test_exchange_online_plan_2_is_read_and_a_move_to_f1_shows_the_mailbox_lost(client):
    """The case that started this work: a Contractor group on Exchange Online (Plan
    2) only. It now maps to outcomes, so moving it to F1 shows what is lost."""
    eng = client.post("/api/engagements", json={"customer_name": "Contractor Co"}).json()
    eid = eng["id"]
    pid = client.post(f"/api/engagements/{eid}/personas",
                      json={"name": "Contractors", "headcount": 250}).json()["id"]
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Exchange Online (Plan 2)", "quantity_purchased": 250,
        "quantity_assigned": 250, "unit_price_paid_annual": 96, "persona_ids": [pid]})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": pid, "target_sku_reference": "Microsoft 365 F1",
        "target_unit_price_annual": 27, "in_scope": True})
    gaps = client.get(f"/api/engagements/{eid}/coverage-gaps").json()["personas"][0]
    assert gaps["unmapped_current_licenses"] == []
    readout = client.post(f"/api/engagements/{eid}/compute").json()
    from app.db import SessionLocal
    from app.services import compute
    with SessionLocal() as db:
        dropped = compute.dropped_capability(db, eid, readout)
    lost = {o["name"] for d in dropped for o in d["outcomes"]}
    assert {"Email & Calendar", "Full-Size Mailbox"} <= lost


def test_a_new_engagement_gets_the_new_outcomes_and_plans(client):
    eng = client.post("/api/engagements", json={"customer_name": "New Library Co"}).json()
    keys = {o["seed_key"] for o in client.get(f"/api/engagements/{eng['id']}/outcomes").json()}
    assert {"email-calendar", "mailbox-full"} <= keys


# ---- An existing database (seeded before this content) ----------------------

NEW_BUNDLES = ("exo-p1", "exo-p2", "m365-e3-no-teams", "m365-business-basic-no-teams",
               "teams-enterprise", "defender-business")


def test_an_existing_database_picks_the_content_up_without_touching_engagements(client):
    from sqlalchemy import delete, func, select

    from app import models
    from app.db import SessionLocal
    from app.main import (_backfill_coverage_corrections, _backfill_new_bundle_coverage,
                          _backfill_new_default_outcomes)
    from app.services import bundles as bundles_service
    from app.services import limits as limits_service

    eng = client.post("/api/engagements", json={"customer_name": "Old Engagement Co"}).json()
    with SessionLocal() as db:
        def engagement_rows():
            return db.execute(select(func.count()).select_from(models.CoverageMapEntry).where(
                models.CoverageMapEntry.engagement_id == eng["id"])).scalar()
        # Simulate the database as it was: no new plans, outcomes, aliases or rows
        # (the engagement then holds only the coverage the old library gave it).
        new = db.execute(select(models.Bundle).where(models.Bundle.key.in_(NEW_BUNDLES))).scalars().all()
        ids = [b.id for b in new]
        db.execute(delete(models.LicenseLimitMember).where(models.LicenseLimitMember.bundle_id.in_(ids)))
        db.execute(delete(models.AddonEligibility).where(
            models.AddonEligibility.addon_bundle_id.in_(ids) | models.AddonEligibility.base_bundle_id.in_(ids)))
        db.execute(delete(models.BundleAlias).where(models.BundleAlias.bundle_id.in_(ids)))
        db.execute(delete(models.DefaultBundleCoverage).where(
            models.DefaultBundleCoverage.bundle_key.in_(NEW_BUNDLES)
            | models.DefaultBundleCoverage.outcome_key.in_(("email-calendar", "mailbox-full"))
            | ((models.DefaultBundleCoverage.bundle_key == "m365-e3")
               & (models.DefaultBundleCoverage.outcome_key == "email-atp"))))
        db.execute(delete(models.DefaultOutcome).where(
            models.DefaultOutcome.key.in_(("email-calendar", "mailbox-full"))))
        db.execute(delete(models.CoverageMapEntry).where(models.CoverageMapEntry.bundle_id.in_(ids)))
        db.execute(delete(models.ScenarioAddon).where(models.ScenarioAddon.bundle_id.in_(ids)))
        for b in new:
            db.delete(b)
        db.commit()
        assert bundles_service.resolve_bundle(db, "Exchange Online (Plan 2)") is None
        before = engagement_rows()

        # The additive startup paths, in startup order.
        bundles_service.seed_bundles(db)
        limits_service.backfill_limit_members(db)
        _backfill_new_bundle_coverage(db)
        _backfill_new_default_outcomes(db)
        _backfill_coverage_corrections(db)

        assert bundles_service.resolve_bundle(db, "EXO P2") is not None
        pairs = {(c.bundle_key, c.outcome_key) for c in db.execute(select(models.DefaultBundleCoverage)).scalars()}
        assert ("exo-p2", "mailbox-full") in pairs and ("o365-e3", "mailbox-full") in pairs
        assert ("m365-e3", "email-atp") in pairs
        assert ("m365-f3", "email-calendar") in pairs and ("m365-f3", "mailbox-full") not in pairs
        assert {"email-calendar", "mailbox-full"} <= set(db.execute(select(models.DefaultOutcome.key)).scalars())
        lim = db.execute(select(models.LicenseLimit).where(
            models.LicenseLimit.key == "m365-business-seat-cap")).scalar_one()
        bb_nt = bundles_service.resolve_bundle(db, "Microsoft 365 Business Basic (no Teams)")
        assert bb_nt in limits_service.member_bundle_ids(db, lim.id)
        # The existing engagement keeps exactly the coverage it was created with.
        assert engagement_rows() == before
