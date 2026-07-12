"""Customer-facing HTML readout: no internal QA data, conditional sections, and
the computed math matches the /compute result the web app shows."""


def _outcome(client, eid, seed_key):
    return next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
                if o["seed_key"] == seed_key)


def test_readout_minimal_omits_internal_and_inapplicable_sections(client):
    """A plain engagement (no third-party tools, no managed split, no eliminations):
    the population check is gone and the tool/elimination/appendix sections that
    don't apply are omitted rather than printed as 'None'."""
    eng = client.post("/api/engagements", json={"customer_name": "Clean Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "Knowledge Worker", "headcount": 100}).json()
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    html = client.get(f"/api/engagements/{eid}/readout.html").text

    # Internal / QA data is not in the customer document (the population-check
    # metric and its headcount/tool-seat counts are gone).
    assert "Population check" not in html
    assert "tool-seats" not in html
    assert "distinct-people count" not in html
    # Sections that don't apply are omitted (not shown as "None").
    assert "Third-party dispositions" not in html   # no third-party tools
    assert "What this retires" not in html           # nothing eliminated
    assert "Tooling split" not in html               # no managed tools
    assert "Assumed full elimination" not in html
    assert ">None<" not in html
    # The customer-facing core is present, and the persona/math pulled in.
    assert "Per-persona scenarios" in html
    assert "Knowledge Worker" in html


def test_readout_conditionals_show_when_managed_and_eliminated(client):
    """A managed third-party tool that the target fully eliminates: the dispositions,
    'what this retires', and tooling-split sections all appear, and the readout's net
    delta matches the /compute result (the same math the web app renders)."""
    eng = client.post("/api/engagements", json={"customer_name": "Displace Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 100}).json()
    identity = _outcome(client, eid, "identity-sso")
    # A MANAGED IdP tool with a non-default tooling split (0.5 vs the 0.30 default).
    tool = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 50000, "covered_count": 100,
        "is_managed": True, "tooling_pct": 0.5}).json()
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": identity["id"], "product_kind": "ThirdParty",
        "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True})
    # E3 covers identity-sso → Okta is fully eliminated.
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "Population check" not in html
    assert "Third-party dispositions" in html
    assert "What this retires" in html
    assert "Tools fully eliminated" in html and "Okta" in html
    assert "Tooling split" in html          # a managed tool exists
    assert "50%" in html                     # the per-line tooling override is surfaced

    # The math from the web pulls into the readout: the net delta the /compute
    # endpoint returns appears verbatim in the HTML.
    result = client.post(f"/api/engagements/{eid}/compute").json()
    delta = result["rollup"]["net_tco_delta_annual"]
    usd = f"${abs(delta):,.0f}" if delta >= 0 else f"-${abs(delta):,.0f}"
    assert usd in html


def test_readout_renders_business_narrative_when_present(client, monkeypatch):
    """When per-persona narratives are attached to the result, the readout renders
    'The business case' section (advisory; generation is wired separately)."""
    from app.services import exporter
    from app import models

    eng = models.Engagement(customer_name="Story Co")

    class _Rollup(dict):
        pass

    result = {
        "rollup": {
            "net_tco_delta_annual": -1000, "population_check": {},
            "existing_microsoft_annual": 0, "target_microsoft_annual": 0,
            "freed_third_party": [], "fully_eliminated_tools": [],
            "eliminated_renewal_cycles": [], "residual_third_party_cost_annual": 0,
            "quick_wins": [], "quick_win_savings_annual": 0,
        },
        "scenarios": [], "dispositions": [],
        "narratives": [
            {"persona": "Knowledge Worker", "today": "On E3 + Okta.",
             "whats_new": "E5 consolidates identity.", "value": "Saves $45k/yr."}
        ],
    }
    html = exporter.build_html(eng, result)
    assert "The business case" in html
    assert "Knowledge Worker" in html and "Saves $45k/yr." in html
    assert "Population check" not in html
