"""Regression: legacy coverage markers must never 500 the coverage map.

Coverage is binary (a CoverageMapEntry existing == covered; ENGINE_SPEC), so the
app only ever writes "Full". Engagements created by older versions carry legacy
markers ("Partial"/"None") from when coverage was multi-valued. The `coverage`
column used to be a single-value DB Enum, and a SQLAlchemy Enum raises LookupError
when READING any value outside its tuple — so one stale row 500'd every read of
the table (the coverage map, the engine, exports). It is now a plain String, which
reads legacy values back harmlessly. See models.CoverageMapEntry.coverage.
"""


def _seed_engagement_with_products(client, name):
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    tp = client.post(f"/api/engagements/{eid}/third-party",
                     json={"name": "Vipre Anti-Virus", "raw_cost": 1000}).json()
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    return eid, tp["id"], outcomes


def _write_legacy_coverage(eid, outcome_id, product_id, marker):
    """Insert a coverage row holding a legacy (non-'Full') marker via raw SQL — the
    way an older, multi-valued-coverage app version wrote it — so the test exercises
    the READ path regardless of any write-side validation."""
    import uuid

    from sqlalchemy import text

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            text("INSERT INTO coverage_map_entries "
                 "(id, engagement_id, outcome_id, product_kind, "
                 "third_party_product_id, coverage, ai_suggested, ratified) "
                 "VALUES (:id, :eid, :oid, 'ThirdParty', :tp, :cov, 0, 1)"),
            {"id": str(uuid.uuid4()), "eid": eid, "oid": outcome_id,
             "tp": product_id, "cov": marker},
        )
        db.commit()
    finally:
        db.close()


def test_legacy_coverage_marker_does_not_500_the_read(client):
    eid, tp, outcomes = _seed_engagement_with_products(client, "Legacy Cov Co")
    _write_legacy_coverage(eid, outcomes[0]["id"], tp, "Partial")

    # The read that the coverage map / engine / exports all depend on must survive.
    r = client.get(f"/api/engagements/{eid}/coverage")
    assert r.status_code == 200
    rows = r.json()
    assert any(c["third_party_product_id"] == tp for c in rows)


def test_can_still_add_coverage_when_a_legacy_row_exists(client):
    eid, tp, outcomes = _seed_engagement_with_products(client, "Legacy Add Co")
    _write_legacy_coverage(eid, outcomes[0]["id"], tp, "None")

    # Mapping another outcome to the product (the reported "manually adding
    # third-party outcomes" action) must succeed and be listed back.
    r = client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": outcomes[1]["id"], "product_kind": "ThirdParty",
        "third_party_product_id": tp, "ai_suggested": False, "ratified": True,
    })
    assert r.status_code == 201

    listed = client.get(f"/api/engagements/{eid}/coverage").json()
    mapped = {c["outcome_id"] for c in listed if c["third_party_product_id"] == tp}
    assert {outcomes[0]["id"], outcomes[1]["id"]} <= mapped
