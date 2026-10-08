"""The unknown-licence flow (TARGET_SCHEMA §4.4 "Reading a line", D23; §3.4, D24;
WALKTHROUGH step 3): every Microsoft licence line is read or set aside. A line the
library can't read keeps its cost but leaves its groups' capability changes out,
loudly, until it is answered once per name on Other tools."""

import io

import pytest
from sqlalchemy import delete, select

from app import models
from app.db import SessionLocal
from app.services import ai, bundles as bundles_service, compute

UNKNOWN = "Legacy Mail Plan X"


def _bundle_ids(client):
    return {b["key"]: b["id"] for b in client.get("/api/catalog/bundles").json()}


def _outcome_ids(client, eid):
    return {o["seed_key"]: o["id"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}


def _engagement(client, name, sku=UNKNOWN, target="Microsoft 365 F1", price=27):
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    pid = client.post(f"/api/engagements/{eid}/personas",
                      json={"name": "Contractors", "headcount": 100}).json()["id"]
    lid = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": sku, "quantity_purchased": 100, "quantity_assigned": 100,
        "unit_price_paid_annual": 96, "persona_ids": [pid]}).json()["id"]
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": pid, "target_sku_reference": target,
        "target_unit_price_annual": price, "in_scope": True})
    return eid, pid, lid


def _names(client, eid):
    return {i["sku_reference"]: i for i in client.get(f"/api/engagements/{eid}/licence-names").json()}


def _answer(client, eid, answer, sku=UNKNOWN, **kw):
    return client.put(f"/api/engagements/{eid}/licence-names/answer",
                      json={"sku_reference": sku, "answer": answer, **kw})


def _tick(client, eid, outcome_id, action="add", sku=UNKNOWN):
    return client.put(f"/api/engagements/{eid}/licence-names/outcome",
                      json={"sku_reference": sku, "outcome_id": outcome_id, "action": action})


def _readings(eid):
    with SessionLocal() as db:
        eng = db.get(models.Engagement, eid)
        return {lid: r for lid, r in compute.licence_readings(db, eng).items()}


# ---- Unread: money counts, capability changes are left out, loudly ----------

def test_an_unread_licence_keeps_its_money_and_leaves_capability_changes_out(client):
    eid, pid, lid = _engagement(client, "Unread Co")
    assert _readings(eid)[lid].state == "unread"
    item = _names(client, eid)[UNKNOWN]
    assert item["state"] == "unread" and item["groups"] == ["Contractors"] and item["seats"] == 100
    assert item["library_offer"] is None and item["resolved_bundle_id"] is None

    readout = client.post(f"/api/engagements/{eid}/compute").json()
    # The money counts: today's spend includes the line.
    assert readout["rollup"]["existing_microsoft_annual"] == pytest.approx(9600)
    story = next(n for n in readout["new_outcomes"] if n["persona_id"] == pid)
    assert story["empty_reason"] == "licence_unread" and story["outcomes"] == []
    assert UNKNOWN in story["empty_reason_text"]
    assert readout["dropped_capability"] == []

    rv = client.get(f"/api/engagements/{eid}/review").json()
    unread = [c for c in rv["checks"] if c["code"] == "license_unread"]
    assert len(unread) == 1 and unread[0]["step"] == "tools" and "Contractors" in unread[0]["message"]
    assert any(l["what"] == "Capability changes for Contractors" and l["step"] == "tools"
               for l in rv["left_out"])


def test_one_question_per_name_whatever_the_spelling(client):
    eid, pid, lid = _engagement(client, "Spelling Co")
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "legacy  mail plan x", "quantity_purchased": 5,
        "quantity_assigned": 5, "persona_ids": [pid]})
    items = client.get(f"/api/engagements/{eid}/licence-names").json()
    assert len(items) == 1 and len(items[0]["line_ids"]) == 2 and items[0]["seats"] == 105
    rv = client.get(f"/api/engagements/{eid}/review").json()
    assert len([c for c in rv["checks"] if c["code"] == "license_unread"]) == 1


def test_the_pdf_says_capability_changes_are_left_out_never_already_in_place(client):
    pypdf = pytest.importorskip("pypdf")
    eid, pid, lid = _engagement(client, "Report Co")
    pdf = client.post(f"/api/engagements/{eid}/customer-report.pdf").content
    text = " ".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    text = " ".join(text.split())
    assert f"we couldn't confirm what {UNKNOWN} includes" in text
    assert "already in place today" not in text
    assert f"capability changes of the groups holding them are left out (their cost is counted): " \
           f"{UNKNOWN} (Contractors)" in text


# ---- The answers ---------------------------------------------------------------

def test_ticking_outcomes_maps_every_line_and_the_move_is_compared(client):
    eid, pid, lid = _engagement(client, "Mapped Co")
    o = _outcome_ids(client, eid)
    assert _tick(client, eid, o["email-calendar"]).status_code == 204
    assert _tick(client, eid, o["mailbox-full"]).status_code == 204
    reading = _readings(eid)[lid]
    assert reading.state == "mapped" and reading.outcomes == {o["email-calendar"], o["mailbox-full"]}
    item = _names(client, eid)[UNKNOWN]
    assert item["state"] == "mapped"
    assert {t["name"] for t in item["outcomes"]} == {"Email & Calendar", "Full-Size Mailbox"}
    assert all(t["ratified"] and not t["ai_suggested"] for t in item["outcomes"])

    readout = client.post(f"/api/engagements/{eid}/compute").json()
    lost = {x["name"] for d in readout["dropped_capability"] for x in d["outcomes"]}
    assert {"Email & Calendar", "Full-Size Mailbox"} <= lost          # F1 has no mailbox
    rv = client.get(f"/api/engagements/{eid}/review").json()
    assert not [c for c in rv["checks"] if c["code"] == "license_unread"]

    # Unticking one leaves the other.
    _tick(client, eid, o["mailbox-full"], "remove")
    assert _readings(eid)[lid].outcomes == {o["email-calendar"]}


def test_a_mapping_keeps_counting_after_the_library_learns_the_name(client):
    """The silent-switch fix: an answer a person gave wins over resolving the name,
    so teaching the library the name later doesn't quietly change the numbers."""
    eid, pid, lid = _engagement(client, "Learned Co")
    o = _outcome_ids(client, eid)
    _tick(client, eid, o["email-calendar"])
    exo1 = _bundle_ids(client)["exo-p1"]
    r = client.post("/api/admin/licence-names/alias", json={"name": UNKNOWN, "bundle_id": exo1})
    assert r.status_code == 200
    try:
        with SessionLocal() as db:
            assert bundles_service.resolve_bundle(db, UNKNOWN) == exo1
        reading = _readings(eid)[lid]
        assert reading.state == "mapped" and reading.outcomes == {o["email-calendar"]}
    finally:
        with SessionLocal() as db:
            db.execute(delete(models.BundleAlias).where(
                models.BundleAlias.alias == bundles_service.normalize_alias(UNKNOWN)))
            db.commit()


def test_same_as_a_plan_links_every_line_and_replaces_ticks(client):
    eid, pid, lid = _engagement(client, "Linked Co")
    o = _outcome_ids(client, eid)
    _tick(client, eid, o["chat-meetings"])
    exo2 = _bundle_ids(client)["exo-p2"]
    r = _answer(client, eid, "same_as", bundle_id=exo2)
    assert r.status_code == 200 and r.json()["coverage_added"] == 0   # already has it
    reading = _readings(eid)[lid]
    assert reading.state == "linked" and o["mailbox-full"] in reading.outcomes
    assert o["chat-meetings"] not in reading.outcomes
    item = _names(client, eid)[UNKNOWN]
    assert item["bundle_name"] == "Exchange Online (Plan 2)" and item["outcomes"] == []
    lic = next(l for l in client.get(f"/api/engagements/{eid}/current-licenses").json() if l["id"] == lid)
    assert lic["bundle_id"] == exo2 and lic["sku_reference"] == UNKNOWN   # the name is kept

    assert _answer(client, eid, "same_as").status_code == 422             # no plan picked
    assert _answer(client, eid, "same_as", sku="No Such Line", bundle_id=exo2).status_code == 404


def test_out_of_scope_and_clear(client):
    eid, pid, lid = _engagement(client, "Aside Co")
    assert _answer(client, eid, "out_of_scope").status_code == 200
    assert _readings(eid)[lid].state == "out_of_scope"
    assert _names(client, eid)[UNKNOWN]["state"] == "out_of_scope"
    readout = client.post(f"/api/engagements/{eid}/compute").json()
    assert readout["rollup"]["existing_microsoft_annual"] == 0                # in no number
    assert _answer(client, eid, "clear").status_code == 200
    assert _readings(eid)[lid].state == "unread"


def test_use_the_librarys_list_on_an_engagement_made_before_the_library_knew_it(client):
    """An engagement created before Exchange Online and the email outcomes were in
    the library: its EXO P2 line is unread, the card offers the library's list, and
    taking it brings the plan's coverage and the new outcomes, with those outcomes'
    library coverage on every plan, so E3 doesn't look as though it drops email."""
    eid, pid, lid = _engagement(client, "Older Co", sku="Exchange Online (Plan 2)",
                                target="Microsoft 365 E3", price=432)
    exo2 = _bundle_ids(client)["exo-p2"]
    with SessionLocal() as db:
        old = [o.id for o in db.execute(select(models.Outcome).where(
            models.Outcome.engagement_id == eid,
            models.Outcome.seed_key.in_(("email-calendar", "mailbox-full")))).scalars()]
        db.execute(delete(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == eid,
            models.CoverageMapEntry.outcome_id.in_(old) | (models.CoverageMapEntry.bundle_id == exo2)))
        db.execute(delete(models.Outcome).where(models.Outcome.id.in_(old)))
        db.commit()
    item = _names(client, eid)["Exchange Online (Plan 2)"]
    assert item["state"] == "unread" and item["resolved_bundle_id"] == exo2
    assert item["library_offer"]["bundle_id"] == exo2
    assert "Full-Size Mailbox" in item["library_offer"]["outcomes"]

    r = _answer(client, eid, "library", sku="Exchange Online (Plan 2)")
    assert r.status_code == 200
    assert set(r.json()["outcomes_added"]) == {"Email & Calendar", "Full-Size Mailbox"}
    assert _readings(eid)[lid].state == "linked"
    assert _names(client, eid)["Exchange Online (Plan 2)"]["library_offer"] is None
    readout = client.post(f"/api/engagements/{eid}/compute").json()
    lost = {x["name"] for d in readout["dropped_capability"] for x in d["outcomes"]}
    assert not ({"Email & Calendar", "Full-Size Mailbox"} & lost)

    # "Use the library's list" needs a plan the library knows.
    eid2, _, _ = _engagement(client, "Unknown Co")
    assert _answer(client, eid2, "library").status_code == 422


# ---- AI suggestions: the same mechanism as a tool's uses -----------------------

def test_ai_suggestions_count_only_once_confirmed(client, monkeypatch):
    eid, pid, lid = _engagement(client, "Suggested Co")
    url = f"/api/admin/engagements/{eid}/ai/suggest-licence-outcomes"
    monkeypatch.setattr(ai, "is_enabled", lambda: False)
    assert client.post(url, json={"sku_reference": UNKNOWN}).status_code == 400

    o = _outcome_ids(client, eid)
    seen = {}

    def fake(product_name, outcomes, instructions, model=None, web_search=False):
        seen["product"], seen["instructions"] = product_name, instructions
        return [{"outcome_id": o["email-calendar"], "coverage": "Full", "rationale": "mail"}]

    monkeypatch.setattr(ai, "is_enabled", lambda: True)
    monkeypatch.setattr(ai, "suggest_coverage", fake)
    r = client.post(url, json={"sku_reference": UNKNOWN})
    assert r.status_code == 200 and r.json()["suggested"] == [o["email-calendar"]]
    assert UNKNOWN in seen["product"] and "Microsoft licence" in seen["instructions"]
    assert client.post(url, json={"sku_reference": "No Such Line"}).status_code == 404

    tick = _names(client, eid)[UNKNOWN]["outcomes"][0]
    assert tick["ai_suggested"] and not tick["ratified"]
    assert _readings(eid)[lid].state == "unread"                 # a suggestion isn't an answer
    _tick(client, eid, o["email-calendar"], "confirm")
    assert _readings(eid)[lid].state == "mapped"


# ---- Housekeeping --------------------------------------------------------------

def test_deleting_an_outcome_removes_its_ticks(client):
    eid, pid, lid = _engagement(client, "Tidy Co")
    o = _outcome_ids(client, eid)
    _tick(client, eid, o["email-calendar"])
    assert client.delete(f"/api/engagements/{eid}/outcomes/{o['email-calendar']}").status_code == 204
    with SessionLocal() as db:
        assert db.execute(select(models.CurrentLicenseOutcome).where(
            models.CurrentLicenseOutcome.license_id == lid)).first() is None


def test_duplicate_keeps_the_answers(client):
    eid, pid, lid = _engagement(client, "Copied Co")
    o = _outcome_ids(client, eid)
    _tick(client, eid, o["email-calendar"])
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Other Mail Plan", "quantity_purchased": 5, "quantity_assigned": 5,
        "persona_ids": [pid]})
    exo2 = _bundle_ids(client)["exo-p2"]
    _answer(client, eid, "same_as", sku="Other Mail Plan", bundle_id=exo2)
    copy = client.post(f"/api/engagements/{eid}/duplicate").json()["id"]
    names = _names(client, copy)
    assert names[UNKNOWN]["state"] == "mapped"
    assert [t["name"] for t in names[UNKNOWN]["outcomes"]] == ["Email & Calendar"]
    assert names["Other Mail Plan"]["state"] == "linked" and names["Other Mail Plan"]["bundle_id"] == exo2
    # The copy's ticks point at the copy's own outcomes.
    copy_outcomes = {x["id"] for x in client.get(f"/api/engagements/{copy}/outcomes").json()}
    assert names[UNKNOWN]["outcomes"][0]["outcome_id"] in copy_outcomes


def test_a_plan_a_line_is_linked_to_cannot_be_deleted(client):
    b = client.post("/api/catalog/bundles", json={"key": "linked-op-plan", "name": "Linked Op Plan"}).json()
    eid, pid, lid = _engagement(client, "Blocked Co")
    _answer(client, eid, "same_as", bundle_id=b["id"])
    r = client.delete(f"/api/catalog/bundles/{b['id']}")
    assert r.status_code == 409 and "licence line" in r.json()["detail"]
    _answer(client, eid, "clear")
    assert client.delete(f"/api/catalog/bundles/{b['id']}").status_code == 200


def test_the_inspector_shows_what_a_line_was_answered_as_delivering(client):
    eid, pid, lid = _engagement(client, "Inspected Co")
    o = _outcome_ids(client, eid)
    _tick(client, eid, o["email-calendar"])
    data = client.get(f"/api/engagements/{eid}/inspect").json()
    lic = next(x for x in data["objects"] if x["type"] == "CurrentMicrosoftLicense")
    assert lic["records"][0]["cells"]["outcome_ids"]["display"] == "Email & Calendar"


# ---- Settings: licence names answered by hand ----------------------------------

def test_settings_lists_names_answered_by_hand_without_customers(client):
    name = "Regional Mail Plan Z"
    exo2 = _bundle_ids(client)["exo-p2"]
    for customer in ("Hand One Co", "Hand Two Co"):
        eid, _, _ = _engagement(client, customer, sku=name)
        _answer(client, eid, "same_as", sku=name, bundle_id=exo2)
    eid3, _, _ = _engagement(client, "Hand Three Co", sku=name)
    _tick(client, eid3, _outcome_ids(client, eid3)["email-calendar"], sku=name)

    r = client.get("/api/admin/licence-names")
    assert r.status_code == 200
    assert not any(c in r.text for c in ("Hand One Co", "Hand Two Co", "Hand Three Co"))
    item = next(i for i in r.json() if i["name"] == name)
    assert item["engagements"] == 3 and item["library_reads_as"] is None
    linked, mapped = item["variants"]
    assert linked["kind"] == "linked" and linked["engagements"] == 2 and linked["can_add_alias"]
    assert mapped["kind"] == "mapped" and mapped["outcomes"] == ["Email & Calendar"]
    assert mapped["can_add_plan"]

    # Teach the library the name; it then reads every new line by itself.
    assert client.post("/api/admin/licence-names/alias",
                       json={"name": name, "bundle_id": exo2}).status_code == 200
    item = next(i for i in client.get("/api/admin/licence-names").json() if i["name"] == name)
    assert item["library_reads_as"] == "Exchange Online (Plan 2)"
    assert not item["variants"][0]["can_add_alias"]
    # A name another plan holds can't be added twice.
    e3 = _bundle_ids(client)["m365-e3"]
    assert client.post("/api/admin/licence-names/alias",
                       json={"name": name, "bundle_id": e3}).status_code == 409
    with SessionLocal() as db:
        db.execute(delete(models.BundleAlias).where(
            models.BundleAlias.alias == bundles_service.normalize_alias(name)))
        db.commit()


def test_settings_adds_a_name_as_a_new_plan(client):
    name = "Kiosk Mail Plan Q"
    r = client.post("/api/admin/licence-names/plan",
                    json={"name": name, "outcome_keys": ["email-calendar", "not-a-key"]})
    assert r.status_code == 201
    plan = r.json()
    try:
        with SessionLocal() as db:
            assert bundles_service.resolve_bundle(db, name) == plan["id"]
            assert [c.outcome_key for c in db.execute(select(models.DefaultBundleCoverage).where(
                models.DefaultBundleCoverage.bundle_key == plan["key"])).scalars()] == ["email-calendar"]
        # Already known now; and a plan needs a library outcome.
        assert client.post("/api/admin/licence-names/plan",
                           json={"name": name, "outcome_keys": ["email-calendar"]}).status_code == 422
        assert client.post("/api/admin/licence-names/plan",
                           json={"name": "Empty Plan", "outcome_keys": []}).status_code == 422
    finally:
        with SessionLocal() as db:
            db.execute(delete(models.DefaultBundleCoverage).where(
                models.DefaultBundleCoverage.bundle_key == plan["key"]))
            db.execute(delete(models.BundleAlias).where(models.BundleAlias.bundle_id == plan["id"]))
            db.execute(delete(models.Bundle).where(models.Bundle.id == plan["id"]))
            db.commit()
