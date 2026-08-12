"""Licensing corrections to the default coverage spine, and M365 E7 = E5 + AI.

- Endpoint Privilege Management stays on E3/E5 (Microsoft folded the Intune Suite,
  which includes EPM, into those suites).
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

    # EPM stays on E3/E5 — the Intune Suite (which includes it) is now in those suites.
    assert "endpoint-privilege-management" in cov["m365-e3"]
    assert "endpoint-privilege-management" in cov["m365-e5"]
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


def test_e3_displaces_a_third_party_epm_tool(client):
    """A move to M365 E3 displaces a third-party Endpoint Privilege Management tool,
    because the Intune Suite (EPM) is now included in E3."""
    eid = client.post("/api/engagements", json={"customer_name": "EPM Displace Co"}).json()["id"]
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
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    result = client.post(f"/api/engagements/{eid}/compute").json()
    disp = next(d for d in result["dispositions"] if d["third_party_product_name"] == "BeyondTrust EPM")
    assert disp["disposition"] == "FullyEliminated"  # E3 covers EPM → displaced


def test_backfill_reconciles_an_already_seeded_template(client):
    """An existing deployment converges via the additive corrections backfill: the
    correction rows (incl. EPM back on E3/E5) and E7's AI outcomes are (re)inserted;
    idempotent. Simulates a template that had those pairs dropped (e.g. by the earlier
    mistaken EPM retirement)."""
    from sqlalchemy import and_, or_, select

    from app import models
    from app.db import SessionLocal
    from app.main import _backfill_coverage_corrections, _COVERAGE_CORRECTIONS

    db = SessionLocal()
    try:
        # Simulate the pre-fix state: drop the correction pairs (incl. E3/E5 EPM).
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
        assert ("m365-e3", "endpoint-privilege-management") in have
        assert ("m365-e5", "endpoint-privilege-management") in have
        assert ("m365-e7", "ai-assistant") in have
        assert ("m365-e7", "ai-agentic-governance") in have
    finally:
        db.close()
