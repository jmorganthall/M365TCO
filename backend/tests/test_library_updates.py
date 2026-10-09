"""The Library updates review (TARGET_SCHEMA §4.7, D8 revised; WALKTHROUGH W13):
an engagement keeps its own copy of the library; a later library change reaches
it only when a person applies it, and "not for this customer" is remembered."""

from sqlalchemy import delete, select

from app import models
from app.db import SessionLocal
from app.services import bundles as bundles_service

URL = "/api/engagements/{}/library-updates"


def _bundle_ids(client):
    return {b["key"]: b["id"] for b in client.get("/api/catalog/bundles").json()}


def _outcomes(client, eid):
    return {o["seed_key"]: o for o in client.get(f"/api/engagements/{eid}/outcomes").json()}


def _updates(client, eid):
    return client.get(URL.format(eid)).json()


def _items(client, eid, kind=None):
    return [i for i in _updates(client, eid)["items"] if kind is None or i["kind"] == kind]


def _post(client, eid, action, **body):
    return client.post(f"{URL.format(eid)}/{action}", json=body)


def _older_engagement(client, name):
    """An engagement as it was before the library learned the email outcomes,
    Exchange Online (Plan 2), and Defender for Office P1 in E3."""
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    b = _bundle_ids(client)
    with SessionLocal() as db:
        old = [o.id for o in db.execute(select(models.Outcome).where(
            models.Outcome.engagement_id == eid,
            models.Outcome.seed_key.in_(("email-calendar", "mailbox-full")))).scalars()]
        atp = db.execute(select(models.Outcome.id).where(
            models.Outcome.engagement_id == eid, models.Outcome.seed_key == "email-atp")).scalar()
        db.execute(delete(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == eid,
            models.CoverageMapEntry.outcome_id.in_(old)
            | (models.CoverageMapEntry.bundle_id == b["exo-p2"])
            | ((models.CoverageMapEntry.bundle_id == b["m365-e3"])
               & (models.CoverageMapEntry.outcome_id == atp))))
        db.execute(delete(models.Outcome).where(models.Outcome.id.in_(old)))
        db.commit()
    return eid


def test_a_new_engagement_has_nothing_waiting(client):
    eid = client.post("/api/engagements", json={"customer_name": "Fresh Co"}).json()["id"]
    assert _updates(client, eid) == {"items": [], "declined": []}
    rv = client.get(f"/api/engagements/{eid}/review").json()
    assert not [c for c in rv["checks"] if c["code"] == "library_update_pending"]


def test_an_older_engagement_sees_each_difference_and_nothing_changes_until_applied(client):
    eid = _older_engagement(client, "Older Library Co")
    b = _bundle_ids(client)
    before = client.get(f"/api/engagements/{eid}/coverage").json()

    added = {i["outcome_key"] for i in _items(client, eid, "outcome_added")}
    assert added == {"email-calendar", "mailbox-full"}
    e3 = next(i for i in _items(client, eid, "coverage_added") if i["bundle_id"] == b["m365-e3"])
    assert e3["outcomes"] == ["Advanced Email Threat Protection"] and not e3["new_plan"]
    exo = next(i for i in _items(client, eid, "coverage_added") if i["bundle_id"] == b["exo-p2"])
    assert exo["new_plan"] and "Information Governance / eDiscovery / Records Management" in exo["outcomes"]
    rv = client.get(f"/api/engagements/{eid}/review").json()
    note = next(c for c in rv["checks"] if c["code"] == "library_update_pending")
    assert note["step"] == "gaps" and note["severity"] == "info"
    # Reading changed nothing.
    assert client.get(f"/api/engagements/{eid}/coverage").json() == before

    # Apply one: the outcome arrives with the library's coverage of it on every plan.
    assert _post(client, eid, "apply", kind="outcome_added", outcome_key="email-calendar").status_code == 204
    o = _outcomes(client, eid)
    rows = {c["bundle_id"] for c in client.get(f"/api/engagements/{eid}/coverage").json()
            if c["outcome_id"] == o["email-calendar"]["id"]}
    assert {b["m365-e3"], b["m365-f3"], b["exo-kiosk"]} <= rows and b["m365-f1"] not in rows
    assert all(c["source"] == "library" for c in client.get(f"/api/engagements/{eid}/coverage").json()
               if c["outcome_id"] == o["email-calendar"]["id"])
    # Applying twice: it isn't waiting any more.
    assert _post(client, eid, "apply", kind="outcome_added", outcome_key="email-calendar").status_code == 404

    # Apply all clears the rest.
    assert client.post(f"{URL.format(eid)}/apply-all").json()["applied"] >= 3
    assert _items(client, eid) == []
    e3_rows = {c["outcome_id"] for c in client.get(f"/api/engagements/{eid}/coverage").json()
               if c["bundle_id"] == b["m365-e3"]}
    assert _outcomes(client, eid)["email-atp"]["id"] in e3_rows


def test_not_for_this_customer_is_remembered_and_can_be_undone(client):
    eid = _older_engagement(client, "Declining Co")
    b = _bundle_ids(client)
    assert _post(client, eid, "decline", kind="coverage_added", bundle_id=b["m365-e3"]).status_code == 204
    assert not [i for i in _items(client, eid, "coverage_added") if i["bundle_id"] == b["m365-e3"]]
    declined = _updates(client, eid)["declined"]
    assert declined[0]["label"] == "Microsoft 365 E3 includes Advanced Email Threat Protection"
    # Apply all leaves a declined item alone.
    client.post(f"{URL.format(eid)}/apply-all")
    e3_rows = {c["outcome_id"] for c in client.get(f"/api/engagements/{eid}/coverage").json()
               if c["bundle_id"] == b["m365-e3"]}
    assert _outcomes(client, eid)["email-atp"]["id"] not in e3_rows
    # Undo brings it back.
    assert client.delete(f"{URL.format(eid)}/decisions/{declined[0]['id']}").status_code == 204
    assert [i for i in _items(client, eid, "coverage_added") if i["bundle_id"] == b["m365-e3"]]


def test_a_row_the_library_no_longer_lists_is_offered_for_removal_unless_added_here(client):
    eid = client.post("/api/engagements", json={"customer_name": "Removed Co"}).json()["id"]
    b = _bundle_ids(client)
    o = _outcomes(client, eid)
    with SessionLocal() as db:
        # F1 has no desktop apps in the library: one row as if the library once said
        # so, one from before origins were recorded.
        for key, source in (("desktop-software", "library"), ("email-atp", None)):
            db.add(models.CoverageMapEntry(
                engagement_id=eid, outcome_id=o[key]["id"], product_kind="MicrosoftSku",
                bundle_id=b["m365-f1"], microsoft_sku_reference="Microsoft 365 F1",
                coverage="Full", ratified=True, source=source))
        db.commit()
    # One a person adds here is the engagement's own: never offered.
    r = client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": o["ai-assistant"]["id"], "product_kind": "MicrosoftSku",
        "bundle_id": b["m365-f1"], "ratified": True})
    assert r.json()["source"] == "engagement"

    item = next(i for i in _items(client, eid, "coverage_removed") if i["bundle_id"] == b["m365-f1"])
    assert item["outcomes"] == ["Advanced Email Threat Protection", "Desktop Software"]
    # A row of unknown origin may be this customer's own: Apply all leaves it.
    assert item["origin_unknown"]
    client.post(f"{URL.format(eid)}/apply-all")
    assert [i for i in _items(client, eid, "coverage_removed") if i["bundle_id"] == b["m365-f1"]]
    assert _post(client, eid, "apply", kind="coverage_removed", bundle_id=b["m365-f1"]).status_code == 204
    f1 = {c["outcome_id"] for c in client.get(f"/api/engagements/{eid}/coverage").json()
          if c["bundle_id"] == b["m365-f1"]}
    assert o["desktop-software"]["id"] not in f1 and o["email-atp"]["id"] not in f1
    assert o["ai-assistant"]["id"] in f1


def test_removing_a_library_row_by_hand_is_not_offered_back(client):
    eid = client.post("/api/engagements", json={"customer_name": "Tuned Co"}).json()["id"]
    b = _bundle_ids(client)
    edr = _outcomes(client, eid)["endpoint-edr"]["id"]
    row = next(c for c in client.get(f"/api/engagements/{eid}/coverage").json()
               if c["bundle_id"] == b["m365-e5"] and c["outcome_id"] == edr)
    assert client.delete(f"/api/engagements/{eid}/coverage/{row['id']}").status_code == 204
    assert _items(client, eid) == []
    declined = _updates(client, eid)["declined"]
    assert declined[0]["reason"] == "Removed from this engagement's plan coverage by hand"
    assert "Microsoft 365 E5 includes" in declined[0]["label"]


def test_deleting_a_library_outcome_by_hand_is_not_offered_back(client):
    eid = client.post("/api/engagements", json={"customer_name": "Trimmed Co"}).json()["id"]
    ai = _outcomes(client, eid)["ai-assistant"]
    assert client.delete(f"/api/engagements/{eid}/outcomes/{ai['id']}").status_code == 204
    assert _items(client, eid) == []
    d = _updates(client, eid)["declined"][0]
    assert d["kind"] == "outcome_added" and d["reason"] == "Removed from this engagement by hand"
    # Undo offers it again; applying brings it back with its library coverage.
    client.delete(f"{URL.format(eid)}/decisions/{d['id']}")
    assert _post(client, eid, "apply", kind="outcome_added", outcome_key="ai-assistant").status_code == 204
    assert "ai-assistant" in _outcomes(client, eid)


def test_a_licence_the_library_learned_is_offered_as_a_link(client):
    name = "Legacy Mail Plan Y"
    eid = client.post("/api/engagements", json={"customer_name": "Learned Library Co"}).json()["id"]
    pid = client.post(f"/api/engagements/{eid}/personas", json={"name": "Staff", "headcount": 10}).json()["id"]
    lid = client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": name, "quantity_purchased": 10, "quantity_assigned": 10,
        "persona_ids": [pid]}).json()["id"]
    o = _outcomes(client, eid)
    client.put(f"/api/engagements/{eid}/licence-names/outcome", json={
        "sku_reference": name, "outcome_id": o["email-calendar"]["id"], "action": "add"})
    assert _items(client, eid) == []
    b = _bundle_ids(client)
    client.post("/api/admin/licence-names/alias", json={"name": name, "bundle_id": b["exo-p1"]})
    try:
        item = _items(client, eid, "licence_in_library")[0]
        assert item["bundle_name"] == "Exchange Online (Plan 1)" and item["line_ids"] == [lid]
        assert item["line_outcomes"] == ["Email & Calendar"]
        assert "Full-Size Mailbox" in item["plan_outcomes"]
        # Declining is remembered per line; undoing and applying links the line.
        _post(client, eid, "decline", kind="licence_in_library", sku_reference=name)
        assert _items(client, eid, "licence_in_library") == []
        dup = client.post(f"/api/engagements/{eid}/duplicate").json()["id"]
        assert [d["kind"] for d in _updates(client, dup)["declined"]] == ["licence_in_library"]
        decision = _updates(client, eid)["declined"][0]
        client.delete(f"{URL.format(eid)}/decisions/{decision['id']}")
        assert _post(client, eid, "apply", kind="licence_in_library", sku_reference=name).status_code == 204
        lic = next(l for l in client.get(f"/api/engagements/{eid}/current-licenses").json() if l["id"] == lid)
        assert lic["bundle_id"] == b["exo-p1"]
        # Deleting a line removes its decisions.
        _post(client, dup, "decline", kind="licence_in_library", sku_reference=name)
        dup_line = client.get(f"/api/engagements/{dup}/current-licenses").json()[0]["id"]
        client.delete(f"/api/engagements/{dup}/current-licenses/{dup_line}")
        with SessionLocal() as db:
            assert db.execute(select(models.LibraryUpdateDecision).where(
                models.LibraryUpdateDecision.license_id == dup_line)).first() is None
    finally:
        with SessionLocal() as db:
            db.execute(delete(models.BundleAlias).where(
                models.BundleAlias.alias == bundles_service.normalize_alias(name)))
            db.commit()
