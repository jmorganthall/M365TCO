"""Third-party covers derivation: covered_count is the combined headcount of the
tagged personas, kept live as tags/headcounts change, with an optional operator
override (covered_count_override) that always wins. See DATA_MODEL §4.6."""


def _mk_engagement(client, name):
    eng = client.post("/api/engagements", json={"customer_name": name}).json()
    return eng["id"]


def _mk_persona(client, eid, name, headcount):
    return client.post(
        f"/api/engagements/{eid}/personas", json={"name": name, "headcount": headcount}
    ).json()


def test_covers_derives_from_selected_personas(client):
    eid = _mk_engagement(client, "Covers Derive Co")
    kw = _mk_persona(client, eid, "KW", 400)
    fl = _mk_persona(client, eid, "FL", 100)

    # No personas, no override → covers 0 (and zero per-unit).
    tp = client.post(f"/api/engagements/{eid}/third-party",
                     json={"name": "Okta", "raw_cost": 50000}).json()
    assert tp["covered_count"] == 0
    assert float(tp["per_unit_annual_cost"]) == 0.0

    # Selecting a persona sets covers to its headcount.
    tp = client.patch(f"/api/engagements/{eid}/third-party/{tp['id']}",
                      json={"persona_ids": [kw["id"]]}).json()
    assert tp["covered_count"] == 400
    assert tp["persona_covered_count"] == 400
    assert float(tp["per_unit_annual_cost"]) == 125.0  # 50000 / 400

    # Selecting multiple personas sums their headcounts.
    tp = client.patch(f"/api/engagements/{eid}/third-party/{tp['id']}",
                      json={"persona_ids": [kw["id"], fl["id"]]}).json()
    assert tp["covered_count"] == 500
    assert float(tp["per_unit_annual_cost"]) == 100.0  # 50000 / 500

    # Deselecting drops back to the remaining persona's headcount.
    tp = client.patch(f"/api/engagements/{eid}/third-party/{tp['id']}",
                      json={"persona_ids": [fl["id"]]}).json()
    assert tp["covered_count"] == 100


def test_override_wins_and_clearing_reverts_to_derived(client):
    eid = _mk_engagement(client, "Covers Override Co")
    kw = _mk_persona(client, eid, "KW", 300)

    # Override on create wins over the persona sum (covers more than the tags).
    tp = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 60000,
        "persona_ids": [kw["id"]], "covered_count_override": 600,
    }).json()
    assert tp["covered_count"] == 600
    assert tp["covered_count_override"] == 600
    assert tp["persona_covered_count"] == 300
    assert float(tp["per_unit_annual_cost"]) == 100.0  # 60000 / 600

    # Any override is allowed — including one below the persona sum.
    tp = client.patch(f"/api/engagements/{eid}/third-party/{tp['id']}",
                      json={"covered_count_override": 200}).json()
    assert tp["covered_count"] == 200

    # Clearing the override (explicit null) reverts to the derived sum.
    tp = client.patch(f"/api/engagements/{eid}/third-party/{tp['id']}",
                      json={"covered_count_override": None}).json()
    assert tp["covered_count_override"] is None
    assert tp["covered_count"] == 300
    assert float(tp["per_unit_annual_cost"]) == 200.0  # 60000 / 300


def test_persona_headcount_change_and_delete_resync_covers(client):
    eid = _mk_engagement(client, "Covers Resync Co")
    kw = _mk_persona(client, eid, "KW", 250)
    fl = _mk_persona(client, eid, "FL", 50)

    tp = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "CrowdStrike", "raw_cost": 30000,
        "persona_ids": [kw["id"], fl["id"]],
    }).json()
    assert tp["covered_count"] == 300

    # Editing a tagged persona's headcount re-derives covers (and per-unit).
    client.patch(f"/api/engagements/{eid}/personas/{kw['id']}", json={"headcount": 550})
    tp = next(t for t in client.get(f"/api/engagements/{eid}/third-party").json()
              if t["id"] == tp["id"])
    assert tp["covered_count"] == 600
    assert float(tp["per_unit_annual_cost"]) == 50.0  # 30000 / 600

    # Deleting a tagged persona removes its headcount from the derived sum.
    client.delete(f"/api/engagements/{eid}/personas/{fl['id']}")
    tp = next(t for t in client.get(f"/api/engagements/{eid}/third-party").json()
              if t["id"] == tp["id"])
    assert tp["covered_count"] == 550

    # An overridden product is untouched by persona edits.
    tp2 = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Zscaler", "raw_cost": 10000,
        "persona_ids": [kw["id"]], "covered_count_override": 1000,
    }).json()
    client.patch(f"/api/engagements/{eid}/personas/{kw['id']}", json={"headcount": 700})
    tp2 = next(t for t in client.get(f"/api/engagements/{eid}/third-party").json()
               if t["id"] == tp2["id"])
    assert tp2["covered_count"] == 1000
    assert tp2["persona_covered_count"] == 700


def test_startup_backfill_preserves_manual_covers(client):
    """Rows from before covers became derived: a non-zero manual value that
    disagrees with the persona sum becomes an explicit override (math unchanged);
    an unfilled zero with tagged personas re-derives from them."""
    from app import models
    from app.db import SessionLocal
    from app.main import _backfill_third_party_covers_override

    eid = _mk_engagement(client, "Covers Backfill Co")
    kw = _mk_persona(client, eid, "KW", 100)
    tagged = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "LegacyManual", "raw_cost": 4000, "persona_ids": [kw["id"]]}).json()
    zeroed = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "LegacyZero", "raw_cost": 5000, "persona_ids": [kw["id"]]}).json()
    untagged = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "LegacyUntagged", "raw_cost": 7000}).json()

    db = SessionLocal()
    try:
        # Simulate pre-migration rows: manual covers values, no override column.
        db.get(models.ThirdPartyProduct, tagged["id"]).covered_count = 40
        db.get(models.ThirdPartyProduct, zeroed["id"]).covered_count = 0
        db.get(models.ThirdPartyProduct, untagged["id"]).covered_count = 70
        db.commit()
        _backfill_third_party_covers_override(db)
        _backfill_third_party_covers_override(db)  # idempotent
    finally:
        db.close()

    rows = {t["name"]: t for t in client.get(f"/api/engagements/{eid}/third-party").json()}
    # Manual 40 vs persona sum 100 → preserved as an override.
    assert rows["LegacyManual"]["covered_count_override"] == 40
    assert rows["LegacyManual"]["covered_count"] == 40
    # Unfilled 0 with a tagged persona → re-derived to the persona sum.
    assert rows["LegacyZero"]["covered_count_override"] is None
    assert rows["LegacyZero"]["covered_count"] == 100
    assert float(rows["LegacyZero"]["per_unit_annual_cost"]) == 50.0  # 5000 / 100
    # Manual 70 with no tags → preserved as an override.
    assert rows["LegacyUntagged"]["covered_count_override"] == 70
    assert rows["LegacyUntagged"]["covered_count"] == 70


def test_duplicate_carries_covers_override(client):
    eid = _mk_engagement(client, "Covers Dup Co")
    kw = _mk_persona(client, eid, "KW", 120)
    client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 12000,
        "persona_ids": [kw["id"]], "covered_count_override": 240,
    })
    dup = client.post(f"/api/engagements/{eid}/duplicate").json()
    tps = client.get(f"/api/engagements/{dup['id']}/third-party").json()
    tp = next(t for t in tps if t["name"] == "Okta")
    assert tp["covered_count_override"] == 240
    assert tp["covered_count"] == 240
    assert tp["persona_covered_count"] == 120
