"""The guided walkthrough's server side (docs/WALKTHROUGH.md): the one help-text
file both the tooltips and the PDF read, the Review step's checks and left-out
list, and the customer PDF with its Presented snapshot."""

import base64
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _eng(client, name="Flow Co"):
    return client.post("/api/engagements", json={"customer_name": name}).json()["id"]


# ---- Help text ----------------------------------------------------------------

def test_help_text_has_steps_fields_and_method(client):
    h = client.get("/api/help-text").json()
    assert set(h["steps"]) == {"customer", "groups", "tools", "future", "gaps", "review", "summary"}
    for key, f in h["fields"].items():
        assert f["ask"].strip() and f["why"].strip(), key
    assert [m["title"] for m in h["method"]][:2] == ["What we compared", "Groups"]


def test_every_tooltip_in_the_gui_has_help_text(client):
    """A tooltip key with no text would silently render nothing — the GUI and
    the help file must agree."""
    h = client.get("/api/help-text").json()
    used = set()
    for path in FRONTEND.rglob("*.jsx"):
        used |= set(re.findall(r'<Help k="([^"]+)"', path.read_text(encoding="utf-8")))
    assert used, "no tooltips found — the scan is broken"
    assert used <= set(h["fields"]), f"missing help text for {sorted(used - set(h['fields']))}"


# ---- Review -------------------------------------------------------------------

def _review(client, eid):
    return client.get(f"/api/engagements/{eid}/review").json()


def test_review_lists_what_is_unanswered_and_left_out(client):
    eid = _eng(client)
    kw = client.post(f"/api/engagements/{eid}/personas", json={"name": "Office", "headcount": 100}).json()
    client.post(f"/api/engagements/{eid}/personas", json={"name": "Field", "headcount": 20})
    lic = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_purchased": 110, "quantity_assigned": 100,
        "unit_price_paid_annual": 400, "persona_ids": [kw["id"]]}).json()
    client.post(f"/api/engagements/{eid}/third-party", json={"name": "Free Tool", "raw_cost": 0,
                                                            "persona_ids": [kw["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 600, "in_scope": True})
    client.patch(f"/api/engagements/{eid}", json={"employee_count": 150})

    r = _review(client, eid)
    codes = {c["code"] for c in r["checks"]}
    assert {"unused_seats_unanswered", "tool_incomplete", "group_without_plan",
            "gap_unanswered", "headcount_vs_employees", "agreement_renewal_missing"} <= codes
    whats = " | ".join(x["what"] for x in r["left_out"])
    assert "10 unused Microsoft 365 E3 seats" in whats and "Free Tool" in whats
    assert all(c["step"] in {"customer", "groups", "tools", "future", "gaps", "review", "summary"}
               for c in r["checks"])
    # Steps come in walkthrough order.
    order = ["customer", "groups", "tools", "future", "gaps", "review", "summary"]
    assert [order.index(c["step"]) for c in r["checks"]] == sorted(order.index(c["step"]) for c in r["checks"])

    # Answering removes the items.
    client.patch(f"/api/engagements/{eid}/current-licenses/{lic['id']}",
                 json={"unused_seats_answer": "Intended"})
    assert "unused_seats_unanswered" not in {c["code"] for c in _review(client, eid)["checks"]}


def test_review_never_writes(client):
    from app import models
    from app.db import SessionLocal

    eid = _eng(client, "Read Only Co")
    with SessionLocal() as db:
        before = db.get(models.Engagement, eid).updated_at
    _review(client, eid)
    with SessionLocal() as db:
        assert db.get(models.Engagement, eid).updated_at == before


# ---- The customer PDF ---------------------------------------------------------

PNG_1PX = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
           "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _full_engagement(client, name="Report & <Co>"):
    eid = _eng(client, name)
    client.patch(f"/api/engagements/{eid}", json={"brand_logo_data_url": PNG_1PX})
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "Office", "headcount": 100}).json()
    client.patch(f"/api/engagements/{eid}/personas/{kw['id']}", json={"description": "Desk-based staff"})
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_purchased": 120, "quantity_assigned": 100,
        "unit_price_paid_annual": 400, "persona_ids": [kw["id"]], "unused_seats_answer": "NotNeeded"})
    edr = next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
               if o["seed_key"] == "endpoint-edr")
    tool = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "EDR Tool", "raw_cost": 12000, "persona_ids": [kw["id"]],
        "renewal_date": "2027-06-01"}).json()
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": edr["id"], "product_kind": "ThirdParty",
        "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 600, "in_scope": True})
    return eid


def _pages(pdf: bytes) -> int:
    return max(int(n) for n in re.findall(rb"/Count (\d+)", pdf))


def test_customer_pdf_records_a_presented_snapshot(client):
    eid = _full_engagement(client)
    r = client.post(f"/api/engagements/{eid}/customer-report.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert 'filename="Report-Co-M365-TCO-' in r.headers["content-disposition"]
    # Title, overview, one group, how we calculated it.
    assert _pages(r.content) >= 4
    first = client.get(f"/api/engagements/{eid}").json()["presented_snapshot_id"]
    snaps = client.get(f"/api/engagements/{eid}/snapshots").json()
    assert [(s["id"], s["is_presented"], s["is_baseline"]) for s in snaps] == [(first, True, True)]
    # A second PDF becomes the baseline; the first stays on record as presented.
    client.post(f"/api/engagements/{eid}/customer-report.pdf")
    snaps = client.get(f"/api/engagements/{eid}/snapshots").json()
    assert sum(s["is_baseline"] for s in snaps) == 1 and all(s["is_presented"] for s in snaps)
    assert next(s for s in snaps if s["id"] == first)["is_baseline"] is False


def test_customer_pdf_builds_for_an_empty_engagement(client):
    eid = _eng(client, "Empty Co")
    r = client.post(f"/api/engagements/{eid}/customer-report.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_customer_pdf_skips_an_unreadable_logo(client):
    eid = _eng(client, "Svg Logo Co")
    svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode()
    client.patch(f"/api/engagements/{eid}", json={"brand_logo_data_url": svg})
    assert client.post(f"/api/engagements/{eid}/customer-report.pdf").status_code == 200
