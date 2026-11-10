"""Reads never write, and every read reflects every edit (TARGET_SCHEMA §1.2, D18).

The calculated result is built on read by one pure path (services/results). These
tests pin the two promises the GUI relies on: opening any view or export changes
nothing stored, and an edit shows up on the very next read with no "recompute"
call in between.
"""

import contextlib

from sqlalchemy import event


@contextlib.contextmanager
def _no_writes():
    """Fail on any INSERT/UPDATE/DELETE/DDL issued while the block runs."""
    from app.db import engine

    writes: list[str] = []

    def _watch(conn, cursor, statement, params, context, executemany):
        head = statement.lstrip().split(None, 1)[0].upper()
        if head in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE"}:
            writes.append(statement.strip().splitlines()[0])

    event.listen(engine, "before_cursor_execute", _watch)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", _watch)
    assert not writes, f"a read wrote to the database: {writes}"


def _engagement(client):
    eid = client.post("/api/engagements", json={"customer_name": "Reads Co"}).json()["id"]
    outcomes = client.get(f"/api/engagements/{eid}/outcomes").json()
    identity = next(o for o in outcomes if o["seed_key"] == "identity-sso")
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 450}).json()
    lic = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_purchased": 450,
        "quantity_assigned": 450, "unit_price_paid_annual": 300,
        "persona_ids": [kw["id"]]}).json()
    okta = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 50000, "covered_count_override": 500,
        "persona_ids": [kw["id"]]}).json()
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": identity["id"], "product_kind": "ThirdParty",
        "third_party_product_id": okta["id"], "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "E3",
        "target_unit_price_annual": 400})
    client.patch(f"/api/engagements/{eid}/third-party/{okta['id']}",
                 json={"extra_seats_answer": "PeopleWhoStay"})
    return eid, kw, lic, okta


def test_views_and_exports_write_nothing(client):
    eid, *_ = _engagement(client)
    with _no_writes():
        for path in ("result", "readout.html", "readout.xlsx", "review",
                     "coverage-gaps", "narrative", "snapshots"):
            r = client.get(f"/api/engagements/{eid}/{path}")
            assert r.status_code == 200, (path, r.text)
        # The deprecated alias is a read too.
        assert client.post(f"/api/engagements/{eid}/compute").status_code == 200
        assert client.get("/api/workshop-value").status_code == 200


def test_an_edit_shows_on_the_next_read(client):
    eid, kw, lic, _ = _engagement(client)
    before = client.get(f"/api/engagements/{eid}/result").json()
    existing = before["rollup"]["existing_microsoft_annual"]
    assert existing == 450 * 300

    client.patch(f"/api/engagements/{eid}/current-licenses/{lic['id']}",
                 json={"unit_price_paid_annual": 360})
    after = client.get(f"/api/engagements/{eid}/result").json()
    assert after["rollup"]["existing_microsoft_annual"] == 450 * 360

    # A headcount edit reaches the scenario and the tool's displacement too.
    client.patch(f"/api/engagements/{eid}/personas/{kw['id']}", json={"headcount": 400})
    again = client.get(f"/api/engagements/{eid}/result").json()
    assert again["scenarios"][0]["headcount"] == 400
    assert again["dispositions"][0]["displaced_users"] == 400


def test_no_get_endpoint_writes(client):
    """Tripwire over the whole API: every GET route, called for a populated
    engagement, writes nothing. Routes needing ids other than the engagement's are
    skipped; their writes would show in their own module's tests."""
    import re

    from app.main import app

    eid, kw, lic, okta = _engagement(client)
    ids = {"engagement_id": eid, "eid": eid, "persona_id": kw["id"],
           "license_id": lic["id"], "tp_id": okta["id"]}
    called = []
    with _no_writes():
        for route in app.routes:
            if "GET" not in getattr(route, "methods", set()) or not route.path.startswith("/api"):
                continue
            params = re.findall(r"{(\w+)}", route.path)
            if any(p not in ids for p in params):
                continue
            path = route.path
            for p in params:
                path = path.replace("{" + p + "}", ids[p])
            r = client.get(path)
            called.append((route.path, r.status_code))
    assert len(called) > 20, called
