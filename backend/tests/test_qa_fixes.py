"""Regressions from the licensing-variation QA pass: deleting a group, tags that
point at a deleted group, today's licences over a seat cap, and a tool given only
to an empty group."""

from tco_engine.interpret import RawEngagement, RawLicenseLine, RawLimit, RawPersona, RawTool, interpret
from decimal import Decimal as D


def _eng(client, name):
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    return f"/api/engagements/{eid}"


def test_deleting_a_group_cleans_up_or_refuses(client):
    b = _eng(client, "Delete Group Co")
    keep = client.post(f"{b}/personas", json={"name": "Keep", "headcount": 900}).json()
    gone = client.post(f"{b}/personas", json={"name": "Contractors", "headcount": 100}).json()
    client.post(f"{b}/current-licenses", json={"sku_reference": "Office 365 E1", "quantity_assigned": 100,
                                               "unit_price_paid_annual": 126, "persona_ids": [gone["id"]]})
    shared = client.post(f"{b}/third-party", json={"name": "Shared", "raw_cost": 10000,
                                                    "persona_ids": [keep["id"], gone["id"]]}).json()
    # Refused while a licence belongs only to that group: it would become everyone's.
    r = client.delete(f"{b}/personas/{gone['id']}")
    assert r.status_code == 409 and "Office 365 E1" in r.json()["detail"]
    line = client.get(f"{b}/current-licenses").json()[0]
    client.patch(f"{b}/current-licenses/{line['id']}", json={"persona_ids": [keep["id"]]})
    client.post(f"{b}/scenarios", json={"persona_id": gone["id"], "target_sku_reference": "Microsoft 365 E3",
                                        "target_unit_price_annual": 400, "in_scope": True})
    assert client.delete(f"{b}/personas/{gone['id']}").status_code == 204
    tool = next(t for t in client.get(f"{b}/third-party").json() if t["id"] == shared["id"])
    assert tool["persona_ids"] == [keep["id"]]
    assert client.get(f"{b}/scenarios").json() == []


def test_a_licence_tagged_only_to_a_deleted_group_is_flagged():
    raw = RawEngagement(id="e", personas=(RawPersona("a", "A", 10),), tools=(
        RawTool(id="t", name="Orphan tool", raw_cost=D("100"), cost_period="Annual", is_managed=False,
                tooling_pct=None, covered_count_override=None, persona_ids=("gone",)),),
        licence_lines=(RawLicenseLine(id="l", sku_reference="Office 365 E1", quantity_assigned=5,
                                      quantity_purchased=5, list_price_annual=D("126"), price_override=False,
                                      overridden_price_annual=None, persona_ids=("gone",)),),
        scenarios=())
    codes = [(f.code, f.licence_id or f.tool_id) for f in interpret(raw).findings]
    assert ("tagged_to_missing_group", "l") in codes and ("tagged_to_missing_group", "t") in codes
    assert not [c for c in codes if c[0] == "tool_incomplete"]


def test_todays_licences_over_a_seat_cap_are_flagged():
    cap = RawLimit(key="business", name="Microsoft 365 Business", limit_type="max_total_seats",
                   max_quantity=300, member_bundle_ids=frozenset({"bs"}))
    line = RawLicenseLine(id="l", sku_reference="Business Standard", quantity_assigned=1500,
                          quantity_purchased=1500, list_price_annual=D("150"), price_override=False,
                          overridden_price_annual=None, persona_ids=(), resolved_bundle_id="bs")
    raw = RawEngagement(id="e", personas=(RawPersona("a", "A", 1500),), licence_lines=(line,), tools=(),
                        scenarios=(), licence_limits=(cap,))
    [f] = [f for f in interpret(raw).findings if f.code == "current_over_cap"]
    assert f.facts == {"cap_name": "Microsoft 365 Business", "cap": 300, "seats": 1500}


def test_a_tool_given_only_to_an_empty_group_names_the_group(client):
    b = _eng(client, "Empty Group Co")
    ghost = client.post(f"{b}/personas", json={"name": "Ghost", "headcount": 0}).json()
    client.post(f"{b}/third-party", json={"name": "GhostTool", "raw_cost": 12000, "persona_ids": [ghost["id"]]})
    [c] = [c for c in client.get(f"{b}/review").json()["checks"] if c["code"] == "tool_incomplete"]
    assert "no people in Ghost" in c["message"]
