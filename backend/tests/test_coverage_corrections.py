"""Licensing corrections to the default coverage spine, and M365 E7 = E5 + AI.

- Endpoint Privilege Management is on M365 E5/E7 only: from July 2026 E3 gets
  Intune Plan 2, Remote Help and Advanced Analytics; E5/E7 add EPM (Microsoft
  Learn, Intune planning guide). The E3 pairs are retired from the global template.
- Threat & Vulnerability Management added to F5 Security and Business Premium.
- Device Management added to Frontline F1 (it includes Intune).
- Two new outcomes — AI Assistant and AI Agentic Governance — and E7 carries the
  full E5 set plus both.
See seeds/coverage.json, seeds/outcomes.json, and the main.py backfills.
"""


def _seed_keys_by_bundle(client, eid):
    """{bundle_key: {seed_key,...}} for the engagement's seeded Microsoft coverage."""
    outs = {o["id"]: o["seed_key"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    keyof = {b["id"]: b["key"] for b in client.get("/api/catalog/bundles").json()}
    cov = client.get(f"/api/engagements/{eid}/coverage").json()
    out = {}
    for c in cov:
        if c["product_kind"] == "MicrosoftSku" and c.get("bundle_id"):
            out.setdefault(keyof.get(c["bundle_id"]), set()).add(outs.get(c["outcome_id"]))
    return out


def test_coverage_corrections_applied(client):
    eid = client.post("/api/engagements", json={"customer_name": "Corrections Co"}).json()["id"]
    cov = _seed_keys_by_bundle(client, eid)

    # EPM is E5 and up, not E3.
    assert "endpoint-privilege-management" not in cov["m365-e3"]
    assert "endpoint-privilege-management" not in cov["m365-e3-no-teams"]
    assert "endpoint-privilege-management" in cov["m365-e5"]
    assert "endpoint-privilege-management" in cov["m365-e7"]
    # TVM added to F5 Security and Business Premium.
    assert "threat-vuln-management" in cov["f5-security"]
    assert "threat-vuln-management" in cov["m365-business-premium"]
    # Device Management added to Frontline F1.
    assert "device-management" in cov["m365-f1"]


def test_ai_outcomes_and_e7_composition(client):
    eid = client.post("/api/engagements", json={"customer_name": "E7 Co"}).json()["id"]
    outs = {o["seed_key"]: o for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    # The two AI outcomes ship in the library.
    assert outs["ai-assistant"]["name"] == "AI Assistant"
    assert outs["ai-agentic-governance"]["name"] == "AI Agentic Governance"

    cov = _seed_keys_by_bundle(client, eid)
    # E7 = the full E5 set + the two AI outcomes.
    assert cov["m365-e7"] == cov["m365-e5"] | {"ai-assistant", "ai-agentic-governance"}


def _epm_tool_disposition(client, name, target, price):
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 100}).json()
    epm = next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
               if o["seed_key"] == "endpoint-privilege-management")
    tool = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "BeyondTrust EPM", "raw_cost": 20000, "covered_count_override": 100}).json()
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": epm["id"], "product_kind": "ThirdParty",
        "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": target,
        "target_unit_price_annual": price, "in_scope": True})

    result = client.post(f"/api/engagements/{eid}/compute").json()
    return next(d for d in result["dispositions"] if d["third_party_product_name"] == "BeyondTrust EPM")


def test_e5_displaces_a_third_party_epm_tool_and_e3_does_not(client):
    """A move to M365 E5 displaces a third-party Endpoint Privilege Management tool;
    a move to M365 E3 doesn't, because EPM is E5 and up."""
    assert _epm_tool_disposition(client, "EPM E5 Co", "Microsoft 365 E5", 684)["disposition"] == "FullyEliminated"
    assert _epm_tool_disposition(client, "EPM E3 Co", "Microsoft 365 E3", 432)["disposition"] != "FullyEliminated"


def test_backfill_reconciles_an_already_seeded_template(client):
    """An existing deployment converges via the additive corrections backfill: the
    correction rows (incl. EPM on E5) and E7's AI outcomes are (re)inserted;
    idempotent. Simulates a template that had those pairs dropped."""
    from sqlalchemy import and_, or_, select

    from app import models
    from app.db import SessionLocal
    from app.main import _backfill_coverage_corrections, _COVERAGE_CORRECTIONS

    db = SessionLocal()
    try:
        # Simulate the pre-fix state: drop the correction pairs (incl. E5 EPM).
        conds = [and_(models.DefaultBundleCoverage.bundle_key == bk,
                      models.DefaultBundleCoverage.outcome_key == ok)
                 for bk, ok in _COVERAGE_CORRECTIONS]
        db.execute(models.DefaultBundleCoverage.__table__.delete().where(or_(*conds)))
        db.commit()

        _backfill_coverage_corrections(db)
        _backfill_coverage_corrections(db)   # idempotent

        have = {(c.bundle_key, c.outcome_key) for c in
                db.execute(select(models.DefaultBundleCoverage)).scalars()}
        for pair in _COVERAGE_CORRECTIONS:
            assert pair in have
        assert ("m365-e5", "endpoint-privilege-management") in have
        assert ("m365-e7", "ai-assistant") in have
        assert ("m365-e7", "ai-agentic-governance") in have
    finally:
        db.close()


def test_retired_e3_epm_leaves_the_template_and_is_offered_to_existing_engagements(client):
    """A deployment seeded when EPM was on E3 loses those pairs from the global
    template at startup (by explicit pair, idempotent), and an engagement created
    back then is offered the removal on Library updates; nothing changes in it
    until someone applies it."""
    from sqlalchemy import select

    from app import models
    from app.db import SessionLocal
    from app.main import RETIRED_COVERAGE_PAIRS, _retire_coverage_pairs

    db = SessionLocal()
    try:
        for b, o in RETIRED_COVERAGE_PAIRS:
            db.add(models.DefaultBundleCoverage(bundle_key=b, outcome_key=o, coverage="Full"))
        db.commit()
    finally:
        db.close()
    eid = client.post("/api/engagements", json={"customer_name": "Old EPM Co"}).json()["id"]
    assert "endpoint-privilege-management" in _seed_keys_by_bundle(client, eid)["m365-e3"]

    db = SessionLocal()
    try:
        _retire_coverage_pairs(db)
        _retire_coverage_pairs(db)  # idempotent
        have = {(c.bundle_key, c.outcome_key) for c in
                db.execute(select(models.DefaultBundleCoverage)).scalars()}
    finally:
        db.close()
    assert not set(RETIRED_COVERAGE_PAIRS) & have
    assert ("m365-e5", "endpoint-privilege-management") in have

    # The engagement keeps its copy, and the review offers the removal.
    assert "endpoint-privilege-management" in _seed_keys_by_bundle(client, eid)["m365-e3"]
    items = client.get(f"/api/engagements/{eid}/library-updates").json()
    items = items["items"]
    keyof = {b["id"]: b["key"] for b in client.get("/api/catalog/bundles").json()}
    removed = {keyof.get(i.get("bundle_id")) for i in items if i["kind"] == "coverage_removed"}
    assert "m365-e3" in removed
