"""Business Premium swap: engagement-inherited, capability-eligible, cap-counted."""

_CSV = (
    "ProductTitle,ProductId,SkuId,SkuTitle,TermDuration,BillingPlan,Market,"
    "Currency,UnitPrice,EffectiveStartDate,EffectiveEndDate,ERP Price,Segment\n"
    "Microsoft 365 E3,P1,001,M365 E3,P1Y,Annual,US,USD,384,2026-01-01,2026-12-31,432,Commercial\n"
    "Microsoft 365 Business Premium,P2,002,M365 BP,P1Y,Annual,US,USD,264,2026-01-01,2026-12-31,264,Commercial\n"
)


def _setup(client, headcount=100):
    client.post("/api/catalog/import-csv", files={"file": ("p.csv", _CSV, "text/csv")})
    eng = client.post("/api/engagements", json={"customer_name": "Swap Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": headcount}).json()
    # Currently on E3 → required outcomes = E3 coverage (all covered by BP).
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_assigned": headcount,
        "unit_price_paid_annual": 432, "persona_ids": [p["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 432, "in_scope": True})
    return eid, p


def _biz_seats(result):
    return next(l["target_seats"] for l in result["license_limits"]
               if l["key"] == "m365-business-seat-cap")


def test_swap_off_by_default(client):
    eid, _ = _setup(client)
    r = client.post(f"/api/engagements/{eid}/compute").json()
    assert r["bp_swap"]["enabled"] is False
    assert r["scenarios"][0]["target_sku_reference"] == "Microsoft 365 E3"
    assert r["bp_swap"]["swapped_count"] == 0


def test_swap_inherited_when_enabled_and_eligible(client):
    eid, _ = _setup(client)
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    s = r["scenarios"][0]
    # Effective target substituted with Business Premium; target spend uses BP price.
    assert s["target_sku_reference"] == "Microsoft 365 Business Premium"
    assert s["target_spend_annual"] == 26400.0        # 100 * 264
    # Delta convention (main): target - current, so a saving is NEGATIVE.
    assert s["delta_annual"] == -16800.0              # 26400 target - 43200 current
    sw = r["bp_swap"]
    assert sw["eligible_count"] == 1 and sw["swapped_count"] == 1
    assert sw["swapped_users"] == 100 and sw["swap_delta_annual"] == -16800.0
    # The swapped seats count against the Business 300-seat cap.
    assert _biz_seats(r) == 100


def test_persona_can_opt_out_of_inherited_swap(client):
    eid, _ = _setup(client)
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})
    sid = client.get(f"/api/engagements/{eid}/scenarios").json()[0]["id"]
    client.patch(f"/api/engagements/{eid}/scenarios/{sid}", json={"bp_swap_optout": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    # Reverts to the persona's own target; not counted against the cap.
    assert r["scenarios"][0]["target_sku_reference"] == "Microsoft 365 E3"
    assert r["bp_swap"]["swapped_count"] == 0
    row = next(x for x in r["bp_swap"]["scenarios"] if x["scenario_id"] == sid)
    assert row["eligible"] is True and row["opted_out"] is True and row["applied"] is False
    assert _biz_seats(r) == 0


def test_swap_eligibility_requires_capability_match(client):
    """A persona whose required outcomes Business Premium does NOT cover (Business
    Premium lacks PSTN dial-tone) is ineligible — the swap never drops a capability."""
    eid, p = _setup(client)
    outs = {o["name"]: o["id"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    # Require PSTN Dial-Tone — Business Premium doesn't cover it.
    client.patch(f"/api/engagements/{eid}/personas/{p['id']}",
                 json={"required_outcome_ids": [outs["PSTN Dial-Tone"]]})
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    assert r["scenarios"][0]["target_sku_reference"] == "Microsoft 365 E3"  # not swapped
    row = r["bp_swap"]["scenarios"][0]
    assert row["eligible"] is False and row["applied"] is False


def test_swap_capability_check_respects_orgwide_current_license(client):
    """An UNTAGGED current license is org-wide (engine §6.2): every persona holds it
    today. If it delivers an outcome Business Premium lacks (here PSTN dial-tone via a
    Calling Plan), swapping any persona to Business Premium would silently drop that
    capability — so the swap must find them ineligible even though their own tagged
    licenses are fully BP-covered."""
    eid, p = _setup(client)
    # Org-wide (no persona_ids) Calling Plan → delivers PSTN Dial-Tone, which BP lacks.
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft Teams Calling Plan", "quantity_assigned": 100,
        "unit_price_paid_annual": 120})
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    # The org-wide PSTN capability blocks the swap — persona keeps its own target.
    assert r["scenarios"][0]["target_sku_reference"] == "Microsoft 365 E3"
    row = r["bp_swap"]["scenarios"][0]
    assert row["eligible"] is False and row["applied"] is False


def test_swap_fills_up_to_cap_and_never_breaches(client):
    """Two personas totaling 340 seats can't both fit under the 300 Business Premium
    cap. The swap fills up to the limit — the larger group (200) swaps, the 140 that
    won't fit stays on its own target (reported `capped`) — so the future plan never
    proposes an unbuyable 340-seat state."""
    client.post("/api/catalog/import-csv", files={"file": ("p.csv", _CSV, "text/csv")})
    eng = client.post("/api/engagements", json={"customer_name": "Cap+Swap"}).json()
    eid = eng["id"]
    p1 = client.post(f"/api/engagements/{eid}/personas", json={"name": "A", "headcount": 200}).json()
    p2 = client.post(f"/api/engagements/{eid}/personas", json={"name": "B", "headcount": 140}).json()
    for p in (p1, p2):
        client.post(f"/api/engagements/{eid}/current-licenses", json={
            "sku_reference": "Office 365 E3", "quantity_assigned": p["headcount"],
            "unit_price_paid_annual": 432, "persona_ids": [p["id"]]})
        client.post(f"/api/engagements/{eid}/scenarios", json={
            "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
            "target_unit_price_annual": 432, "in_scope": True})
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})

    r = client.post(f"/api/engagements/{eid}/compute").json()
    biz = next(l for l in r["license_limits"] if l["key"] == "m365-business-seat-cap")
    # Filled to 200, not 340 — the cap is respected, not merely flagged.
    assert biz["target_seats"] == 200 and biz["violated"] is False
    sw = r["bp_swap"]
    assert sw["swapped_count"] == 1 and sw["swapped_users"] == 200 and sw["capped_count"] == 1
    capped = next(x for x in sw["scenarios"] if x["persona_id"] == p2["id"])
    assert capped["eligible"] is True and capped["applied"] is False and capped["reason"] == "capped"

    # Free the room instead: opt the larger group out → the 140 now fits and swaps.
    sid = next(s["id"] for s in client.get(f"/api/engagements/{eid}/scenarios").json()
               if s["persona_id"] == p1["id"])
    client.patch(f"/api/engagements/{eid}/scenarios/{sid}", json={"bp_swap_optout": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    biz = next(l for l in r["license_limits"] if l["key"] == "m365-business-seat-cap")
    assert biz["target_seats"] == 140 and biz["violated"] is False
    assert r["bp_swap"]["swapped_users"] == 140 and r["bp_swap"]["capped_count"] == 0


def test_swap_skips_when_no_saving(client):
    """An eligible persona whose own target already costs less than Business Premium
    is not swapped — the swap only moves seats that actually save (`no_savings`)."""
    eid, p = _setup(client)
    sid = client.get(f"/api/engagements/{eid}/scenarios").json()[0]["id"]
    # Own target priced BELOW Business Premium's $264 → swapping would cost more.
    client.patch(f"/api/engagements/{eid}/scenarios/{sid}",
                 json={"target_unit_price_annual": 200})
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    assert r["scenarios"][0]["target_sku_reference"] == "Microsoft 365 E3"  # not swapped
    row = r["bp_swap"]["scenarios"][0]
    assert row["eligible"] is True and row["applied"] is False and row["reason"] == "no_savings"


def test_swap_says_why_it_did_nothing_when_every_persona_exceeds_the_cap(client):
    """The reported symptom: at real scale the box is ticked and the TCO doesn't
    move. The swap moves WHOLE personas, so a persona larger than the 300-seat cap
    can never fit — and with every persona over the cap, an enabled swap is inert.
    That must be stated, with the seats it turned away and the seats left unused,
    not left for the operator to infer from an unchanged total."""
    eid, _ = _setup(client, headcount=2518)
    client.post(f"/api/engagements/{eid}/personas", json={"name": "Corp", "headcount": 632})
    p2 = client.get(f"/api/engagements/{eid}/personas").json()[-1]
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_assigned": 632,
        "unit_price_paid_annual": 432, "persona_ids": [p2["id"]]})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p2["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 432, "in_scope": True})

    before = client.post(f"/api/engagements/{eid}/compute").json()
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})
    after = client.post(f"/api/engagements/{eid}/compute").json()

    # The TCO genuinely does not move — the honest outcome at this scale.
    assert (after["rollup"]["net_tco_delta_annual"]
            == before["rollup"]["net_tco_delta_annual"])
    sw = after["bp_swap"]
    assert sw["enabled"] is True and sw["swapped_count"] == 0
    # ...and the app says so, in terms the operator can act on.
    assert sw["inert_reason"] == "all_capped"
    assert sw["stranded_seats"] == 3150            # 2518 + 632 turned away
    assert sw["cap"]["headroom_remaining"] == 300  # all of it unused
    assert sw["capped_count"] == 2


def test_inert_reason_names_the_single_actionable_cause():
    """`inert_reason` is what the GUI shows when an enabled swap changes nothing.

    The price-unknown case is real but only reachable before any pricing catalog
    is imported (the lookup deliberately falls back across bases once one is
    loaded), so the decision function is exercised directly here rather than
    through a session whose catalog other tests have already populated.
    """
    from decimal import Decimal

    from app.services.swap import _inert_reason

    class _Eng:
        pass

    def reason(rows, bp=object(), price=Decimal("264")):
        return _inert_reason(_Eng(), {"bp": bp, "bp_price": price}, rows)

    applied = [{"applied": True, "reason": "applied"}]
    capped = [{"applied": False, "reason": "capped"}]
    mixed = [{"applied": False, "reason": "capped"},
             {"applied": False, "reason": "ineligible"}]

    # Nothing to explain when the swap actually did something.
    assert reason(applied) == ""
    # The feature cannot run at all — reported ahead of any per-persona reason.
    assert reason(capped, bp=None) == "no_bp_bundle"
    assert reason(capped, price=Decimal("0")) == "price_unknown"
    assert reason([]) == "no_scenarios"
    assert reason([{"applied": False, "reason": "out_of_scope"}]) == "no_scenarios"
    # Per-persona causes, most actionable first: a capped persona can be split or
    # opted out today; "ineligible" is a capability fact, not a lever.
    assert reason(capped) == "all_capped"
    assert reason(mixed) == "all_capped"
    assert reason([{"applied": False, "reason": "no_savings"}]) == "all_no_savings"
    assert reason([{"applied": False, "reason": "opted_out"}]) == "all_opted_out"
    assert reason([{"applied": False, "reason": "ineligible"}]) == "all_ineligible"
