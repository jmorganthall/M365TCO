"""Bulk AI coverage ("AI suggest all") must never hold the database write lock
while a model call is in flight, and one failing product must not stop the rest.

SQLite has a single writer. The bulk loop used to flush each product's rows and
commit only once at the end, so the first flush held the write lock across every
remaining (slow, networked) model call, and any other write — e.g. a manual
coverage add from the GUI — failed with "database is locked". The model call is
faked here so it can be held "in flight" deterministically.
"""

import threading
import time

from app.routers import admin as admin_router


def _engagement_with_products(client, name, product_names):
    eng = client.post("/api/engagements", json={"customer_name": name}).json()
    eid = eng["id"]
    outcomes = [
        client.post(f"/api/engagements/{eid}/outcomes",
                    json={"name": f"{name} outcome {i}", "description": "", "is_custom": True}).json()
        for i in range(2)
    ]
    products = {
        n: client.post(f"/api/engagements/{eid}/third-party",
                       json={"name": n, "raw_cost": 1000, "covered_count_override": 10}).json()
        for n in product_names
    }
    return eid, outcomes, products


def _tp_coverage(client, eid, tp_id):
    return [c for c in client.get(f"/api/engagements/{eid}/coverage").json()
            if c["product_kind"] == "ThirdParty" and c["third_party_product_id"] == tp_id]


def test_manual_coverage_write_succeeds_while_bulk_ai_call_in_flight(client, monkeypatch):
    eid, outcomes, products = _engagement_with_products(
        client, "Lock Co", ["Lock Tool A", "Lock Tool B", "Lock Tool Mapped"])
    first, second = outcomes

    # One product is already mapped, so the bulk run skips it; the operator adds
    # more coverage to it by hand while the bulk run is busy with the others.
    mapped = products["Lock Tool Mapped"]
    r = client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": first["id"], "product_kind": "ThirdParty",
        "third_party_product_id": mapped["id"], "ai_suggested": False, "ratified": True})
    assert r.status_code == 201

    in_flight, release = threading.Event(), threading.Event()
    calls = []

    def fake_suggest(product_name, outcome_dicts, instructions, model=None, web_search=False):
        calls.append(product_name)
        if len(calls) == 2:  # the 2nd product: hold the model call open
            in_flight.set()
            release.wait(timeout=60)
        return [{"outcome_id": first["id"], "coverage": "Full", "rationale": "fake"}]

    monkeypatch.setattr(admin_router.ai, "is_enabled", lambda: True)
    monkeypatch.setattr(admin_router.ai, "suggest_coverage", fake_suggest)

    bulk = {}

    def run_bulk():
        bulk["resp"] = client.post(f"/api/admin/engagements/{eid}/ai/suggest-coverage-all")

    manual = {}

    def manual_add():
        start = time.monotonic()
        try:
            manual["resp"] = client.post(f"/api/engagements/{eid}/coverage", json={
                "outcome_id": second["id"], "product_kind": "ThirdParty",
                "third_party_product_id": mapped["id"], "ai_suggested": False, "ratified": True})
        except Exception as exc:  # e.g. sqlite3.OperationalError: database is locked
            manual["error"] = exc
        manual["elapsed"] = time.monotonic() - start

    bulk_thread = threading.Thread(target=run_bulk, daemon=True)
    bulk_thread.start()
    manual_thread = threading.Thread(target=manual_add, daemon=True)
    try:
        assert in_flight.wait(timeout=30), "bulk run never reached the 2nd product"
        # The 1st product's rows are already written; the 2nd model call is in flight.
        manual_thread.start()
        manual_thread.join(timeout=5)
        finished_while_in_flight = not manual_thread.is_alive()
    finally:
        release.set()
        bulk_thread.join(timeout=60)
        manual_thread.join(timeout=60)

    assert finished_while_in_flight, "manual coverage write blocked on the bulk run's lock"
    assert "error" not in manual, f"manual coverage write failed: {manual.get('error')!r}"
    assert manual["resp"].status_code == 201
    assert manual["elapsed"] < 5

    assert bulk["resp"].status_code == 200
    body = bulk["resp"].json()
    assert body["errors"] == []
    assert body["products_processed"] == 2
    assert body["skipped_mapped"] == 1
    for name in ("Lock Tool A", "Lock Tool B"):
        rows = _tp_coverage(client, eid, products[name]["id"])
        assert [(c["outcome_id"], c["ai_suggested"], c["ratified"]) for c in rows] == [
            (first["id"], True, False)]
    assert {c["outcome_id"] for c in _tp_coverage(client, eid, mapped["id"])} == {
        first["id"], second["id"]}


def test_failing_product_does_not_block_later_products(client, monkeypatch):
    """A model/network failure on one product, and a DB failure writing another,
    are each reported for that product only; later products are still saved."""
    eid, outcomes, products = _engagement_with_products(
        client, "Partial Co", ["Net Fail Tool", "Db Fail Tool", "Good Tool"])
    first = outcomes[0]

    def fake_suggest(product_name, outcome_dicts, instructions, model=None, web_search=False):
        if product_name == "Net Fail Tool":
            raise RuntimeError("model timed out")
        if product_name == "Db Fail Tool":
            # outcome_id is NOT NULL: the write fails at flush, which leaves the
            # session needing a rollback before it can be used again.
            return [{"outcome_id": None, "coverage": "Full", "rationale": "bad"}]
        return [{"outcome_id": first["id"], "coverage": "Full", "rationale": "ok"}]

    monkeypatch.setattr(admin_router.ai, "is_enabled", lambda: True)
    monkeypatch.setattr(admin_router.ai, "suggest_coverage", fake_suggest)

    r = client.post(f"/api/admin/engagements/{eid}/ai/suggest-coverage-all")
    assert r.status_code == 200
    body = r.json()
    failed = sorted(e.split(":", 1)[0] for e in body["errors"])
    assert failed == ["Db Fail Tool", "Net Fail Tool"]
    assert body["results"] == [{"name": "Good Tool", "created": 1}]
    assert body["products_processed"] == 1
    assert body["suggestions_created"] == 1

    good = _tp_coverage(client, eid, products["Good Tool"]["id"])
    assert [c["outcome_id"] for c in good] == [first["id"]]
    assert _tp_coverage(client, eid, products["Net Fail Tool"]["id"]) == []
    assert _tp_coverage(client, eid, products["Db Fail Tool"]["id"]) == []


def test_single_product_suggest_persists_and_reports_ai_errors(client, monkeypatch):
    eid, outcomes, products = _engagement_with_products(client, "Single Co", ["Solo Tool"])
    tp = products["Solo Tool"]
    monkeypatch.setattr(admin_router.ai, "is_enabled", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("upstream 503")

    monkeypatch.setattr(admin_router.ai, "suggest_coverage", boom)
    r = client.post(f"/api/admin/engagements/{eid}/ai/suggest-coverage",
                    json={"third_party_product_id": tp["id"]})
    assert r.status_code == 502
    assert "AI suggestion failed" in r.json()["detail"]

    monkeypatch.setattr(
        admin_router.ai, "suggest_coverage",
        lambda *a, **k: [{"outcome_id": outcomes[0]["id"], "coverage": "Full", "rationale": "r"}])
    r = client.post(f"/api/admin/engagements/{eid}/ai/suggest-coverage",
                    json={"third_party_product_id": tp["id"]})
    assert r.status_code == 200
    assert [s["outcome_id"] for s in r.json()["suggestions"]] == [outcomes[0]["id"]]
    assert [c["outcome_id"] for c in _tp_coverage(client, eid, tp["id"])] == [outcomes[0]["id"]]
