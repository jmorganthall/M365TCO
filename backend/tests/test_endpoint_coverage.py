"""Endpoint Protection (EPP) belongs on the base suites that entitle it, so a move
to them displaces a basic third-party antivirus (e.g. Vipre). Microsoft Defender
for Endpoint P1 ships in M365 E3, and Defender Antivirus rides Windows Enterprise
E3 (E3/E7/F3); EDR (Defender for Endpoint P2) is E5/E7 only. See seeds/coverage.json.
"""


def _bundle_id(client, name):
    return next(b["id"] for b in client.get("/api/catalog/bundles").json() if b["name"] == name)


def _bundle_outcomes(client, eid, bundle_name):
    """Seed keys the given Microsoft bundle covers in this engagement."""
    bid = _bundle_id(client, bundle_name)
    outs = client.get(f"/api/engagements/{eid}/outcomes").json()
    seed_by_id = {o["id"]: o["seed_key"] for o in outs}
    cov = client.get(f"/api/engagements/{eid}/coverage").json()
    return {seed_by_id[c["outcome_id"]] for c in cov if c.get("bundle_id") == bid}


def test_base_suites_cover_endpoint_protection(client):
    eid = client.post("/api/engagements", json={"customer_name": "Endpoint Co"}).json()["id"]
    e3 = _bundle_outcomes(client, eid, "Microsoft 365 E3")
    f3 = _bundle_outcomes(client, eid, "Microsoft 365 F3")
    e7 = _bundle_outcomes(client, eid, "Microsoft 365 E7")

    assert "endpoint-protection" in e3      # Defender for Endpoint P1 + Defender AV
    assert "endpoint-edr" not in e3         # EDR is P2/E5 — not E3
    assert "endpoint-protection" in f3      # Defender AV via Windows E3
    assert "endpoint-edr" not in f3
    assert {"endpoint-protection", "endpoint-edr"} <= e7  # E7 ⊇ E5


def test_m365_e3_displaces_a_basic_antivirus(client):
    """The reported case: a persona moving to M365 E3 now displaces a third-party
    antivirus whose only outcome is Endpoint Protection (EPP)."""
    eid = client.post("/api/engagements", json={"customer_name": "AV Displace Co"}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "Non-Store Users", "headcount": 700}).json()
    epp = next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
               if o["seed_key"] == "endpoint-protection")
    vipre = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Vipre Anti-Virus", "raw_cost": 12000, "covered_count_override": 700}).json()
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": epp["id"], "product_kind": "ThirdParty",
        "third_party_product_id": vipre["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    result = client.post(f"/api/engagements/{eid}/compute").json()
    disp = next(d for d in result["dispositions"] if d["third_party_product_name"] == "Vipre Anti-Virus")
    assert disp["disposition"] == "FullyEliminated"
    assert disp["displaced_users"] == 700


def test_backfill_adds_endpoint_coverage_to_seeded_template(client):
    """An existing deployment picks up the new endpoint coverage additively; only
    the explicit (bundle, outcome) rows are added, and it never grants E3 EDR."""
    from sqlalchemy import delete, select

    from app import models
    from app.db import SessionLocal
    from app.main import _backfill_endpoint_protection_coverage, _ENDPOINT_COVERAGE_BACKFILL

    db = SessionLocal()
    try:
        db.execute(delete(models.DefaultBundleCoverage).where(
            models.DefaultBundleCoverage.bundle_key.in_(("m365-e3", "m365-f3", "m365-e7")),
            models.DefaultBundleCoverage.outcome_key.in_(("endpoint-protection", "endpoint-edr"))))
        db.commit()
        _backfill_endpoint_protection_coverage(db)
        _backfill_endpoint_protection_coverage(db)  # idempotent

        pairs = {(c.bundle_key, c.outcome_key) for c in
                 db.execute(select(models.DefaultBundleCoverage)).scalars()}
        for pair in _ENDPOINT_COVERAGE_BACKFILL:
            assert pair in pairs
        assert ("m365-e3", "endpoint-edr") not in pairs  # E3 has no EDR
    finally:
        db.close()
