"""Workshop delivered + the Workshop value page (TARGET_SCHEMA §7.2, D30)."""

import json

from app.services.workshop_value import _stronger, summarize, workshop_figures


def _w(savings, current=1000.0, users=10, personas=2, keys=(), dup=0.0, opt=None):
    outcomes = {k: k.upper() for k in keys}
    return {
        "savings": savings, "current_spend": current, "users": users, "personas": personas,
        "duplicate_spend": dup, "optimization": savings - dup if opt is None else opt,
        "new_outcomes": len(outcomes), "outcomes": outcomes,
    }


def test_stronger_picks_the_higher_of_mean_and_median():
    assert _stronger([]) is None
    # One big customer lifts the mean above the median.
    assert _stronger([1, 2, 30]) == {"value": 11, "statistic": "mean"}
    # One small customer drags the mean below the median.
    assert _stronger([2, 30, 31]) == {"value": 30, "statistic": "median"}


def test_savings_count_only_workshops_that_save_outcomes_count_all():
    ws = [
        _w(100, current=1000, users=10, keys={"edr", "mfa"}, dup=40),
        _w(300, current=1000, users=30, keys={"edr", "dlp", "mfa", "sso"}, dup=60),
        _w(-500, current=2000, users=50, keys={"dlp", "mfa", "custom:kiosk"}),  # adds cost
    ]
    out = summarize(ws, {"edr": "EDR", "dlp": "Data loss prevention", "mfa": "MFA", "sso": "SSO"},
                    years=3)
    assert out["workshops"] == 3 and out["saving_workshops"] == 2
    t = out["totals"]
    # Money over 36 months: the yearly figures × 3.
    assert t["savings"] == 1200 and t["savings_annual"] == 400 and t["current_spend"] == 6000
    assert t["optimized_spend"] == 4800 and t["savings_pct"] == 0.2
    assert t["duplicate_spend"] == 300 and t["optimization"] == 900
    assert t["duplicate_share"] == 0.25 and t["optimization_share"] == 0.75
    assert t["users"] == 90 and t["personas"] == 6 and t["new_outcomes"] == 9
    h = out["headline"]
    assert h["savings_per_customer"]["value"] == 600
    assert h["savings_per_user"]["value"] == 30
    assert h["new_outcomes_per_customer"]["value"] == 3
    # Per-customer shares are 10% and 30% (mean 20%), the same as the pooled 20%.
    assert h["savings_pct"]["value"] == 0.2
    assert out["savings_per_user_spread"] == [30, 30]
    lit = [(r["name"], r["count"], round(r["share"], 3)) for r in out["outcomes_lit"]]
    assert lit == [("MFA", 3, 0.333), ("Data loss prevention", 2, 0.222), ("EDR", 2, 0.222),
                   ("CUSTOM:KIOSK", 1, 0.111), ("SSO", 1, 0.111)]
    assert abs(sum(r["share"] for r in out["outcomes_lit"]) - 1) < 1e-9


def test_loud_percent_takes_the_pooled_share_when_it_is_higher():
    # One big customer saving a lot lifts the pooled share above both the
    # per-customer mean (30%) and median (30%).
    ws = [_w(10, current=100), _w(30, current=100), _w(5000, current=10000)]
    pct = summarize(ws, {})["headline"]["savings_pct"]
    assert pct["statistic"] == "overall" and round(pct["value"], 4) == round(5040 / 10200, 4)


def test_workshop_figures_read_the_headline_and_count_outcomes_once():
    result = {
        "scenarios": [{"in_scope": True}, {"in_scope": False}, {"in_scope": True}],
        # Every tool's cost counts, including one whose users weren't entered
        # (credited 0, residual 0): 300 + 100 + 250.
        "dispositions": [{"effective_annual_cost": 300}, {"effective_annual_cost": 100},
                         {"effective_annual_cost": 250}],
        "rollup": {
            "existing_microsoft_annual": 600, "existing_third_party_annual": 300,
            "residual_third_party_cost_annual": 100,
            "population_check": {"in_scope_persona_headcount": 40},
            "headline": {"run_rate_annual": 250, "duplicate_spend_annual": 50,
                         "consolidation_annual": 180, "overlicensing_annual": 20},
        },
        "new_outcomes": [
            {"outcomes": [{"id": "o1", "seed_key": "edr"}, {"id": "o2"}]},
            {"outcomes": [{"id": "o1", "seed_key": "edr"}, {"id": "o3"}]},
        ],
    }
    result["new_outcomes"][0]["outcomes"][1]["name"] = "Data loss prevention"
    result["new_outcomes"][1]["outcomes"][1]["name"] = "Kiosk mode"
    f = workshop_figures(result, {"o2": "dlp", "o3": None})
    assert f["savings"] == 250 and f["current_spend"] == 1250
    assert f["duplicate_spend"] == 50 and f["optimization"] == 200
    assert f["users"] == 40 and f["personas"] == 2
    assert f["new_outcomes"] == 3  # o1 counted once
    # o3 has no library key, so it is matched by name.
    assert f["outcomes"] == {"edr": "edr", "dlp": "Data loss prevention",
                             "custom:kiosk mode": "Kiosk mode"}


def _engagement(client, name):
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "Staff", "headcount": 100}).json()
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})
    return eid


def test_mark_delivered_freezes_a_snapshot_and_clearing_keeps_it(client):
    eid = _engagement(client, "Delivered Mark Co")
    assert client.post(f"/api/engagements/{eid}/delivered",
                       json={"delivered_by": "  "}).status_code == 422

    eng = client.post(f"/api/engagements/{eid}/delivered", json={"delivered_by": " Sam "}).json()
    assert eng["delivered_by"] == "Sam" and eng["delivered_at"]
    snaps = client.get(f"/api/engagements/{eid}/snapshots").json()
    assert [s["id"] for s in snaps if s["label"] == "Workshop delivered"] == [eng["delivered_snapshot_id"]]

    eng = client.delete(f"/api/engagements/{eid}/delivered").json()
    assert eng["delivered_at"] is None and eng["delivered_by"] is None
    assert eng["delivered_snapshot_id"] is None
    assert len(client.get(f"/api/engagements/{eid}/snapshots").json()) == len(snaps)


def test_workshop_value_names_no_customer_and_writes_nothing(client):
    from app import models
    from app.db import SessionLocal

    before = client.get("/api/workshop-value").json()["delivered"]
    ids = []
    for i in range(2):
        eid = _engagement(client, f"Secret Customer {i}")
        client.post(f"/api/engagements/{eid}/delivered", json={"delivered_by": "Sam"})
        ids.append(eid)

    def state():
        db = SessionLocal()
        try:
            return (db.query(models.EngagementSnapshot).count(),
                    sorted((e.id, e.updated_at) for e in db.query(models.Engagement)))
        finally:
            db.close()

    snapshot_state = state()
    res = client.get("/api/workshop-value")
    assert state() == snapshot_state  # viewing never writes (D18)

    data = res.json()
    assert data["delivered"] == before + 2
    assert data["lead"] in ("as_completed", "today")
    assert data["bases"]["as_completed"]["workshops"] == data["delivered"]
    assert data["bases"]["today"]["workshops"] == data["delivered"]
    text = res.text
    assert "Secret Customer" not in text and "Sam" not in text
    assert not any(eid in text for eid in ids)
    json.dumps(data)  # plain JSON throughout

    for eid in ids:
        client.delete(f"/api/engagements/{eid}/delivered")
