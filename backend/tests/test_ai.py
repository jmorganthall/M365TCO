"""AI instruction templates + third-party paste parser (offline parts).

The live OpenRouter call needs a key and is not exercised here; we test the pure
model-output normalizer, the editable-prompt CRUD, and the disabled-AI guard.
"""

from app.services import ai


def test_normalize_parsed_products_from_customer_table():
    # Shape a model would return for a pasted budget table.
    data = {"products": [
        {"name": "SentinelONE", "raw_cost": " $102,000 ", "cost_period": "Annual"},
        {"name": "Rapid7 - Managed Threat", "vendor": "Rapid7", "raw_cost": "$175,000"},
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
    assert by_name["Okta"]["cost_period"] == "Monthly"
    assert by_name["Okta"]["covered_count"] == 500
    assert by_name["FileVault (MAC)"]["raw_cost"] == 0.0


def test_normalize_clamps_bad_period_and_missing_products():
    assert ai.normalize_parsed_products({}) == []
    rows = ai.normalize_parsed_products({"products": [{"name": "X", "cost_period": "weekly"}]})
    assert rows[0]["cost_period"] == "Annual"


def test_ai_prompts_seeded_and_editable(client):
    prompts = client.get("/api/admin/ai/prompts").json()["prompts"]
    keys = {p["key"] for p in prompts}
    assert {"coverage_suggest", "third_party_parse"} <= keys
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


def test_parse_third_party_requires_ai_enabled(client):
    # No OpenRouter key in the test env, so AI assist is disabled.
    eng = client.post("/api/engagements", json={"customer_name": "Paste Co"}).json()
    r = client.post(f"/api/admin/engagements/{eng['id']}/ai/parse-third-party",
                    json={"raw_text": "Okta  $215,000"})
    assert r.status_code == 400
