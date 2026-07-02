"""AI instruction templates + third-party paste parser (offline parts).

The live OpenRouter call needs a key and is not exercised here; we test the pure
model-output normalizer, the editable-prompt CRUD, and the disabled-AI guard.
"""

from app.services import ai


def test_normalize_parsed_products_from_customer_table():
    # Shape a model would return for a pasted budget table.
    data = {"products": [
        {"name": "SentinelONE", "raw_cost": " $102,000 ", "cost_period": "Annual"},
        {"name": "Rapid7 - Managed Threat", "vendor": "Rapid7", "raw_cost": "$175,000", "is_managed": "true"},
        {"name": "Okta", "raw_cost": 215000, "cost_period": "Monthly", "covered_count": "500"},
        {"name": "FileVault (MAC)", "raw_cost": ""},   # no amount -> 0
        {"name": "", "raw_cost": "999"},               # no name -> dropped
        {"raw_cost": "12"},                             # no name -> dropped
    ]}
    rows = ai.normalize_parsed_products(data)
    assert len(rows) == 4
    by_name = {r["name"]: r for r in rows}
    assert by_name["SentinelONE"]["raw_cost"] == 102000.0
    assert by_name["SentinelONE"]["cost_period"] == "Annual"
    assert by_name["Rapid7 - Managed Threat"]["vendor"] == "Rapid7"
    assert by_name["Rapid7 - Managed Threat"]["cost_period"] == "Annual"  # default
    assert by_name["Rapid7 - Managed Threat"]["is_managed"] is True
    assert by_name["Okta"]["cost_period"] == "Monthly"
    assert by_name["Okta"]["covered_count"] == 500
    assert by_name["Okta"]["is_managed"] is False   # "false" string / absent -> False
    assert by_name["FileVault (MAC)"]["raw_cost"] == 0.0


def test_normalize_clamps_bad_period_and_missing_products():
    assert ai.normalize_parsed_products({}) == []
    rows = ai.normalize_parsed_products({"products": [{"name": "X", "cost_period": "weekly"}]})
    assert rows[0]["cost_period"] == "Annual"


def test_normalize_parsed_licenses_periods_and_scope():
    data = {"licenses": [
        # Per-seat monthly: 32 * 12 = 384 annual per seat.
        {"product_description": "Microsoft 365 E3", "license_quantity": "250",
         "price": "$32.00", "price_period": "Monthly", "price_scope": "PerSeat"},
        # Per-seat quarterly: 30 * 4 = 120 annual per seat.
        {"product_description": "Defender P2", "license_quantity": 100,
         "price": 30, "price_period": "Quarterly"},
        # Line total annual: 120000 / 200 = 600 annual per seat.
        {"product_description": "Microsoft 365 E5", "license_quantity": 200,
         "price": "120,000", "price_period": "Annual", "price_scope": "Total"},
        # Unknown period -> Annual; no name -> dropped.
        {"product_description": "Visio", "price": 60, "price_period": "weekly"},
        {"product_description": "", "price": 5},
    ]}
    rows = ai.normalize_parsed_licenses(data)
    assert len(rows) == 4
    by = {r["product_description"]: r for r in rows}
    assert by["Microsoft 365 E3"]["unit_price_paid_annual"] == 384.0
    assert by["Microsoft 365 E3"]["license_quantity"] == 250
    assert by["Defender P2"]["price_scope"] == "PerSeat"   # default
    assert by["Defender P2"]["unit_price_paid_annual"] == 120.0
    assert by["Microsoft 365 E5"]["unit_price_paid_annual"] == 600.0
    assert by["Visio"]["price_period"] == "Annual"         # bad period clamped


def test_parse_current_licenses_requires_ai_enabled(client):
    eng = client.post("/api/engagements", json={"customer_name": "License Co"}).json()
    r = client.post(f"/api/admin/engagements/{eng['id']}/ai/parse-current-licenses",
                    json={"raw_text": "Microsoft 365 E3  250  $32/mo"})
    assert r.status_code == 400


def test_ai_prompts_seeded_and_editable(client):
    prompts = client.get("/api/admin/ai/prompts").json()["prompts"]
    keys = {p["key"] for p in prompts}
    assert {"coverage_suggest", "third_party_parse", "current_license_parse"} <= keys
    assert all(p["is_default"] for p in prompts)  # freshly seeded

    # Edit one, then it is no longer flagged default.
    r = client.patch("/api/admin/ai/prompts/third_party_parse",
                     json={"instructions": "Custom instructions."})
    assert r.status_code == 200
    body = r.json()
    assert body["instructions"] == "Custom instructions."
    assert body["is_default"] is False

    # Reset restores the seeded default.
    r = client.post("/api/admin/ai/prompts/third_party_parse/reset")
    assert r.status_code == 200
    assert r.json()["is_default"] is True


def test_update_unknown_prompt_404(client):
    r = client.patch("/api/admin/ai/prompts/nope", json={"instructions": "x"})
    assert r.status_code == 404


def test_seed_refreshes_unedited_but_protects_edits(monkeypatch):
    from app.services import ai_prompts
    from app.db import SessionLocal

    def seed(version, text):
        return {"version": version, "prompts": [
            {"key": "__test__", "label": "T", "description": "d", "instructions": text}
        ]}

    db = SessionLocal()
    try:
        monkeypatch.setattr(ai_prompts, "_seed", lambda: seed("t1", "v1 text"))
        ai_prompts.seed_defaults(db)
        assert ai_prompts.get_instructions(db, "__test__") == "v1 text"

        # Unedited row picks up an improved default.
        monkeypatch.setattr(ai_prompts, "_seed", lambda: seed("t2", "v2 text"))
        ai_prompts.seed_defaults(db)
        assert ai_prompts.get_instructions(db, "__test__") == "v2 text"

        # After an operator edit, a newer default no longer overwrites it.
        ai_prompts.update_instructions(db, "__test__", "operator wording")
        monkeypatch.setattr(ai_prompts, "_seed", lambda: seed("t3", "v3 text"))
        ai_prompts.seed_defaults(db)
        assert ai_prompts.get_instructions(db, "__test__") == "operator wording"
    finally:
        db.close()


def test_parse_third_party_requires_ai_enabled(client):
    # No OpenRouter key in the test env, so AI assist is disabled.
    eng = client.post("/api/engagements", json={"customer_name": "Paste Co"}).json()
    r = client.post(f"/api/admin/engagements/{eng['id']}/ai/parse-third-party",
                    json={"raw_text": "Okta  $215,000"})
    assert r.status_code == 400
