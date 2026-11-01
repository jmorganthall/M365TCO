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


def test_dropping_stored_covers_keeps_a_typed_value_as_the_override(client):
    """A database from before covers became derived holds people-covered values a
    person typed in the stored column. Before that column is dropped, a non-zero
    value that differs from the tagged personas' headcount becomes the explicit
    override, so no number changes; one equal to the personas' sum, or zero, is
    left to derive."""
    from sqlalchemy import text

    from app.db import _preserve_manual_covers, engine

    eid = _mk_engagement(client, "Covers Upgrade Co")
    kw = _mk_persona(client, eid, "KW", 100)
    rows = {n: client.post(f"/api/engagements/{eid}/third-party", json={
        "name": n, "raw_cost": 4000, **({"persona_ids": [kw["id"]]} if n != "Untagged" else {})}).json()
        for n in ("Typed", "SameAsPersonas", "Zero", "Untagged")}

    # Recreate the legacy column with the values an old version stored.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE third_party_products ADD COLUMN covered_count INTEGER"))
        for name, value in (("Typed", 40), ("SameAsPersonas", 100), ("Zero", 0), ("Untagged", 70)):
            conn.execute(text("UPDATE third_party_products SET covered_count = :v WHERE id = :i"),
                         {"v": value, "i": rows[name]["id"]})
    try:
        _preserve_manual_covers()
        _preserve_manual_covers()  # idempotent
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE third_party_products DROP COLUMN covered_count"))

    got = {t["name"]: t for t in client.get(f"/api/engagements/{eid}/third-party").json()}
    assert got["Typed"]["covered_count_override"] == 40 and got["Typed"]["covered_count"] == 40
    assert got["Untagged"]["covered_count_override"] == 70
    assert got["SameAsPersonas"]["covered_count_override"] is None
    assert got["SameAsPersonas"]["covered_count"] == 100
    assert got["Zero"]["covered_count_override"] is None and got["Zero"]["covered_count"] == 100
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


def test_tooling_inherits_the_engagement_default_until_a_person_sets_it(client):
    """A managed tool with no tooling % of its own stores NULL and follows the
    engagement's default, including when the default changes later (TARGET_SCHEMA
    §4.5). Setting it stores the person's value; clearing it inherits again."""
    eid = _mk_engagement(client, "Tooling Co")
    kw = _mk_persona(client, eid, "KW", 100)
    t = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "MSSP", "raw_cost": 10000, "is_managed": True, "persona_ids": [kw["id"]]}).json()
    assert t["tooling_pct"] is None and t["tooling_source"] == "engagement"
    assert float(t["effective_tooling_pct"]) == 0.30 and float(t["effective_annual_cost"]) == 3000.0

    client.patch(f"/api/engagements/{eid}", json={"global_tooling_pct": 0.5})
    [t] = client.get(f"/api/engagements/{eid}/third-party").json()
    assert t["tooling_pct"] is None and float(t["effective_annual_cost"]) == 5000.0

    t = client.patch(f"/api/engagements/{eid}/third-party/{t['id']}", json={"tooling_pct": 0.2}).json()
    assert float(t["tooling_pct"]) == 0.2 and t["tooling_source"] == "tool"
    assert float(t["effective_annual_cost"]) == 2000.0
    t = client.patch(f"/api/engagements/{eid}/third-party/{t['id']}", json={"tooling_pct": None}).json()
    assert t["tooling_pct"] is None and float(t["effective_annual_cost"]) == 5000.0
