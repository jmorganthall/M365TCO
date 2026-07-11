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
        "sku_reference": "Microsoft 365 E3", "quantity_assigned": headcount,
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
    assert s["delta_annual"] == 16800.0               # 43200 current - 26400
    sw = r["bp_swap"]
    assert sw["eligible_count"] == 1 and sw["swapped_count"] == 1
    assert sw["swapped_users"] == 100 and sw["swap_delta_annual"] == 16800.0
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
    Premium lacks telephony) is ineligible — the swap never drops a capability."""
    eid, p = _setup(client)
    outs = {o["name"]: o["id"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    # Require Telephony — Business Premium doesn't cover it.
    client.patch(f"/api/engagements/{eid}/personas/{p['id']}",
                 json={"required_outcome_ids": [outs["Telephony"]]})
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    assert r["scenarios"][0]["target_sku_reference"] == "Microsoft 365 E3"  # not swapped
    row = r["bp_swap"]["scenarios"][0]
    assert row["eligible"] is False and row["applied"] is False


def test_swap_counts_toward_cap_and_can_breach_it(client):
    """Two personas totaling 340 seats swapped to Business Premium breach the 300
    cap — the guardrail (license_limits) flags it; opting one out clears it."""
    client.post("/api/catalog/import-csv", files={"file": ("p.csv", _CSV, "text/csv")})
    eng = client.post("/api/engagements", json={"customer_name": "Cap+Swap"}).json()
    eid = eng["id"]
    p1 = client.post(f"/api/engagements/{eid}/personas", json={"name": "A", "headcount": 200}).json()
    p2 = client.post(f"/api/engagements/{eid}/personas", json={"name": "B", "headcount": 140}).json()
    for p in (p1, p2):
        client.post(f"/api/engagements/{eid}/current-licenses", json={
            "sku_reference": "Microsoft 365 E3", "quantity_assigned": p["headcount"],
            "unit_price_paid_annual": 432, "persona_ids": [p["id"]]})
        client.post(f"/api/engagements/{eid}/scenarios", json={
            "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
            "target_unit_price_annual": 432, "in_scope": True})
    client.patch(f"/api/engagements/{eid}", json={"bp_swap_enabled": True})

    r = client.post(f"/api/engagements/{eid}/compute").json()
    biz = next(l for l in r["license_limits"] if l["key"] == "m365-business-seat-cap")
    assert biz["target_seats"] == 340 and biz["violated"] is True

    # Opt the smaller persona out → 200 seats, within the cap.
    sid = next(s["id"] for s in client.get(f"/api/engagements/{eid}/scenarios").json()
               if s["persona_id"] == p2["id"])
    client.patch(f"/api/engagements/{eid}/scenarios/{sid}", json={"bp_swap_optout": True})
    r = client.post(f"/api/engagements/{eid}/compute").json()
    biz = next(l for l in r["license_limits"] if l["key"] == "m365-business-seat-cap")
    assert biz["target_seats"] == 200 and biz["violated"] is False
