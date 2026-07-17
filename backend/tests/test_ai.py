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


def test_normalize_bundle_suggestions_validates_ids_and_keys():
    valid_skus = {"sku-1", "sku-2", "sku-3"}
    valid_bundles = {"m365-e3", "m365-e5"}
    data = {"mappings": [
        {"sku_id": "sku-1", "bundle_key": "m365-e3", "reason": "E3 title"},
        {"sku_id": "sku-2", "bundle_key": "made-up", "reason": "unknown -> null"},
        {"sku_id": "sku-3", "bundle_key": None, "reason": "no staple"},
        {"sku_id": "sku-1", "bundle_key": "m365-e5", "reason": "dup dropped"},
        {"sku_id": "ghost", "bundle_key": "m365-e3", "reason": "unknown sku dropped"},
    ]}
    rows = ai.normalize_bundle_suggestions(data, valid_skus, valid_bundles)
    by = {r["sku_id"]: r for r in rows}
    assert set(by) == {"sku-1", "sku-2", "sku-3"}      # dup + ghost dropped
    assert by["sku-1"]["bundle_key"] == "m365-e3"
    assert by["sku-2"]["bundle_key"] is None            # unknown key -> null (no match)
    assert by["sku-3"]["bundle_key"] is None            # explicit null preserved
    assert ai.normalize_bundle_suggestions({}, valid_skus, valid_bundles) == []


def test_suggest_bundles_requires_ai_enabled(client):
    # No OpenRouter key in the test env, so AI assist is disabled.
    r = client.post("/api/catalog/skus/suggest-bundles")
    assert r.status_code == 400


def test_parse_current_licenses_requires_ai_enabled(client):
    eng = client.post("/api/engagements", json={"customer_name": "License Co"}).json()
    r = client.post(f"/api/admin/engagements/{eng['id']}/ai/parse-current-licenses",
                    json={"raw_text": "Microsoft 365 E3  250  $32/mo"})
    assert r.status_code == 400


def test_suggest_coverage_all_requires_ai_enabled(client):
    eng = client.post("/api/engagements", json={"customer_name": "Bulk Co"}).json()
    r = client.post(f"/api/admin/engagements/{eng['id']}/ai/suggest-coverage-all")
    assert r.status_code == 400


def test_suggest_coverage_all_unknown_engagement_404(client):
    r = client.post("/api/admin/engagements/nope/ai/suggest-coverage-all")
    assert r.status_code == 404


def test_normalize_findings_clamps_and_orders():
    from app.services import sanity
    data = {"findings": [
        {"severity": "info", "field": "price", "message": "Looks fine."},
        {"severity": "nope", "field": "x", "message": "Bad severity -> info."},
        {"severity": "error", "field": "E5", "message": "$10/seat/yr is implausible."},
        {"severity": "warn", "message": "  "},   # empty message -> dropped
        {"field": "y"},                            # no message -> dropped
    ]}
    out = sanity.normalize_findings(data)
    assert len(out) == 3
    assert out[0]["severity"] == "error"      # most severe first
    assert out[-1]["severity"] == "info"
    assert any(f["severity"] == "info" and f["field"] == "x" for f in out)  # clamped


def test_build_sanity_payload_shape():
    from app.services import sanity

    class L:
        sku_reference = "M365 E5"; quantity_purchased = 10; quantity_assigned = 10
        unit_price_paid_annual = 684; segment = None

    class Eng:
        customer_name = "Acme"; market = "US"; currency = "USD"
        default_segment = "Commercial"
        personas = []; current_licenses = [L()]; third_party_products = []

    result = {"rollup": {"net_tco_delta_annual": 1000,
                         "population_check": {"in_scope_persona_headcount": 10,
                                              "third_party_covered_population": 0}},
              "scenarios": []}
    payload = sanity.build_sanity_payload(Eng(), result)
    assert payload["current_licenses"][0]["segment"] == "Commercial"  # inherited
    assert payload["current_licenses"][0]["unit_price_annual"] == 684.0
    assert payload["rollup"]["net_tco_delta_annual"] == 1000


def test_normalize_narratives_filters_and_validates():
    from app.services import narrative
    data = {"narratives": [
        {"persona": "Knowledge Worker", "today": "E3 today", "whats_new": "E5 security", "value": "Consolidates Okta; $45k/yr."},
        {"persona": "Ghost", "value": "not a real persona"},        # unknown -> dropped
        {"persona": "Frontline", "today": "F1", "whats_new": "x", "value": "  "},  # empty value -> dropped
        {"persona": "Knowledge Worker", "value": "dupe"},           # duplicate -> dropped
    ]}
    out = narrative.normalize_narratives(data, ["Knowledge Worker", "Frontline"])
    assert len(out) == 1
    assert out[0]["persona"] == "Knowledge Worker"
    assert out[0]["whats_new"] == "E5 security"


def test_narrative_requires_ai_enabled(client):
    eng = client.post("/api/engagements", json={"customer_name": "Story Co"}).json()
    r = client.post(f"/api/engagements/{eng['id']}/narrative")
    assert r.status_code == 400  # no OpenRouter key in the test env


def test_sanity_check_requires_ai_enabled(client):
    eng = client.post("/api/engagements", json={"customer_name": "Sanity Co"}).json()
    r = client.post(f"/api/engagements/{eng['id']}/sanity-check")
    assert r.status_code == 400  # no OpenRouter key in the test env


def test_ai_prompts_seeded_and_editable(client):
    prompts = client.get("/api/admin/ai/prompts").json()["prompts"]
    keys = {p["key"] for p in prompts}
    assert {"coverage_suggest", "third_party_parse", "current_license_parse",
            "readout_sanity_check", "scenario_narrative"} <= keys
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


def test_web_search_plugin_attached_only_when_enabled(monkeypatch):
    """The OpenRouter web plugin is added to the request payload iff web_search is
    set, and JSON mode is preserved either way."""
    import app.services.ai as ai_mod

    captured = {}

    class FakeResp:
        status_code = 200
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["body"] = json
        return FakeResp()

    monkeypatch.setattr(ai_mod, "_api_key", lambda: "test-key")
    monkeypatch.setattr(ai_mod.httpx, "post", fake_post)

    # Off by default: no web plugin.
    ai_mod.parse_third_party("some text", "instructions", model="x/y")
    assert "plugins" not in captured["body"]
    assert captured["body"]["response_format"] == {"type": "json_object"}

    # Enabled: provider-agnostic web plugin attached, JSON mode kept.
    ai_mod.parse_third_party("some text", "instructions", model="x/y", web_search=True)
    assert captured["body"]["plugins"] == [{"id": "web"}]
    assert captured["body"]["response_format"] == {"type": "json_object"}

    # The web plugin also reaches customer research (the canonical web-search case).
    ai_mod.research_customer({"customer_name": "Acme"}, "instructions", model="x/y", web_search=True)
    assert captured["body"]["plugins"] == [{"id": "web"}]


def test_normalize_customer_research_pure():
    """The customer-research normalizer: keeps confident fields, strips a website
    scheme, coerces employee_count to a positive int, drops blanks — no HTTP."""
    from app.services import ai

    out = ai.normalize_customer_research({"customer": {
        "industry": "Manufacturing", "hq_location": "Austin, TX",
        "website": "https://acme.com/", "employee_count": "1,200 approx",
        "description": "  Makes widgets.  ", "unknown": "ignored"}})
    assert out == {"industry": "Manufacturing", "hq_location": "Austin, TX",
                   "website": "acme.com", "employee_count": 1200,
                   "description": "Makes widgets."}

    # Blanks / zero / missing are dropped (fill-empty-only relies on this).
    assert ai.normalize_customer_research({"industry": "", "employee_count": 0}) == {}
    # Tolerates a flat (no "customer" wrapper) object too.
    assert ai.normalize_customer_research({"website": "acme.io"}) == {"website": "acme.io"}


def test_research_customer_requires_name_and_ai(client):
    """The endpoint needs AI enabled; with it disabled in tests it 400s (a name
    check would 422 first if AI were on)."""
    eng = client.post("/api/engagements", json={"customer_name": "Acme"}).json()
    r = client.post(f"/api/admin/engagements/{eng['id']}/ai/research-customer", json={})
    assert r.status_code == 400  # AI disabled in the test env


def test_business_narratives_stored_on_engagement(client, monkeypatch):
    """Narratives are ENGAGEMENT-LEVEL data: generation stores them (they
    survive navigation via GET), regeneration replaces them, and the customer
    context from Customer Info reaches the model."""
    from app.routers import engagements as eng_router

    seen = {}

    def fake_narratives(scenarios, instructions, model=None, web_search=False, customer=None):
        seen["customer"] = customer
        return [{"persona": s["persona"], "today": "On Okta today.",
                 "whats_new": seen.get("tag", "Gains EPM."), "value": "Saves money."}
                for s in scenarios]

    monkeypatch.setattr(eng_router.ai, "is_enabled", lambda: True)
    monkeypatch.setattr(eng_router.ai, "scenario_narratives", fake_narratives)

    eng = client.post("/api/engagements", json={
        "customer_name": "Narrative Co", "notes": "mid-merger"}).json()
    eid = eng["id"]
    client.patch(f"/api/engagements/{eid}", json={"industry": "Healthcare"})
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 100}).json()
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": kw["id"], "target_sku_reference": "E3",
                      "target_unit_price_annual": 0, "in_scope": True})

    # Nothing stored yet.
    empty = client.get(f"/api/engagements/{eid}/narrative").json()
    assert empty == {"narratives": [], "generated_at": None}

    # Generate → stored, and the model saw the Customer Info context.
    res = client.post(f"/api/engagements/{eid}/narrative").json()
    assert res["narratives"][0]["persona"] == "KW"
    assert res["generated_at"] is not None
    assert seen["customer"]["name"] == "Narrative Co"
    assert seen["customer"]["industry"] == "Healthcare"
    assert seen["customer"]["notes"] == "mid-merger"

    # Survives navigation: GET returns the stored set.
    stored = client.get(f"/api/engagements/{eid}/narrative").json()
    assert stored["narratives"] == res["narratives"]

    # Regeneration REPLACES the stored set.
    seen["tag"] = "Now with Copilot."
    res2 = client.post(f"/api/engagements/{eid}/narrative").json()
    assert res2["narratives"][0]["whats_new"] == "Now with Copilot."
    stored2 = client.get(f"/api/engagements/{eid}/narrative").json()
    assert stored2["narratives"][0]["whats_new"] == "Now with Copilot."
    assert len(stored2["narratives"]) == 1
