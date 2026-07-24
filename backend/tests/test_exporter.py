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
    assert "what happens to each" not in html       # no third-party tools
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
        "name": "Okta", "raw_cost": 50000, "covered_count_override": 100,
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
    assert "Third-party tools — what happens to each" in html
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


def test_readout_breaks_bridge_down_per_persona(client):
    """Two in-scope personas: the headline subtext tells the move story per persona
    ("we save you $X by moving Sales to E5 and Engineering to E3"), and the spend
    bridge gains one column per persona plus a Total — every line, including the
    per-tool freed-up sub-rows, split by persona from the per-scenario numbers."""
    eng = client.post("/api/engagements", json={"customer_name": "Split Co"}).json()
    eid = eng["id"]
    a = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "Sales", "headcount": 100}).json()
    b = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "Engineering", "headcount": 50}).json()
    identity = _outcome(client, eid, "identity-sso")
    tool = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 30000, "covered_count_override": 150}).json()
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": identity["id"], "product_kind": "ThirdParty",
        "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": a["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 600, "in_scope": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": b["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    html = client.get(f"/api/engagements/{eid}/readout.html").text

    # The hero: a 36-month headline (annual delta × default 3-year horizon) over
    # one compact move line per persona with its own signed annual delta. This
    # engagement has no current Microsoft spend, so the move is honestly a cost
    # increase: Sales +40k/yr (60k target − 20k freed Okta), Engineering +10k.
    assert "added cost over 36 months" in html
    assert "$150,000" in html                        # 3 × 50,000, unsigned + words
    assert "$50,000 per year" in html
    # Hero figures share one horizon (3yr): components sum to the headline.
    # Finance notation: added expense in parentheses, black; savings plain green.
    assert "($120,000)</span><span class='move-desc'><b>Sales</b> (100) → <b>Microsoft 365 E5</b></span>" in html
    assert "($30,000)</span><span class='move-desc'><b>Engineering</b> (50) → <b>Microsoft 365 E3</b></span>" in html
    # The bridge is a matrix: a column head per persona (→ its target) + Total.
    assert "Sales <small>→ Microsoft 365 E5</small>" in html
    assert "Engineering <small>→ Microsoft 365 E3</small>" in html
    assert "<th class='num'>Total</th>" in html
    # The freed-up tool sub-row splits per persona: Okta's per-unit cost is
    # 30000/150 = $200, credited 100 and 50 seats → −$20k + −$10k = −$30k total.
    assert "−$20,000.00" in html and "−$10,000.00" in html and "−$30,000.00" in html
    # New outcomes: the targets cover far more than Okta (identity-sso) delivers
    # today, so both personas get a per-persona block of newly-lit capabilities.
    assert "<h2>New outcomes</h2>" in html
    assert "<h3>Sales <span class='muted'>(100) → Microsoft 365 E5</span></h3>" in html
    assert "<h3>Engineering <span class='muted'>(50) → Microsoft 365 E3</span></h3>" in html
    # Each capability is a chip (the GUI's pill treatment, not a text run),
    # with the outcome's description as hover text.
    assert "class='chip'" in html
    # The engagement OWNS its outcome copy: editing a description in the GUI
    # (PATCH /outcomes) flows straight into the chip tooltip.
    edr = next(o for o in client.get(f"/api/engagements/{eid}/outcomes").json()
               if o["seed_key"] == "endpoint-edr")
    client.patch(f"/api/engagements/{eid}/outcomes/{edr['id']}",
                 json={"description": "Custom EDR wording for this customer"})
    html2 = client.get(f"/api/engagements/{eid}/readout.html").text
    assert 'title="Custom EDR wording for this customer"' in html2
    # Okta's outcome is delivered today, so it is NOT listed as new.
    outcomes = {o["seed_key"]: o["name"]
                for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    new_section = html.split("<h2>New outcomes</h2>")[1].split("</section>")[0]
    assert outcomes["identity-sso"] not in new_section


def test_readout_renders_business_narrative_when_present(client, monkeypatch):
    """When per-persona narratives are attached to the result, the readout renders
    'The business case' section (advisory; generation is wired separately)."""
    from app.services import exporter
    from app import models

    eng = models.Engagement(customer_name="Story Co")

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


def test_readout_disclosures_soft_inputs_and_honest_currency(client):
    """Provenance (§9) reaches the customer document: inputs tagged as
    assumptions are listed in Assumptions & sources, hard inputs are not, and
    the header states the engagement's actual currency instead of a hard-coded
    'USD'. An all-hard engagement omits the assumptions block entirely."""
    eng = client.post("/api/engagements", json={"customer_name": "Prov Co"}).json()
    eid = eng["id"]
    p = client.post(f"/api/engagements/{eid}/personas",
                    json={"name": "KW", "headcount": 10, "source_tag": "Estimate"}).json()
    client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 1000, "source_tag": "CustomerStated"})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "Inputs carried as assumptions" in html
    assert "KW <span class='muted'>(persona)</span>: estimate" in html
    assert "Okta <span" not in html          # hard-tagged input is not disclosed
    assert "annualized USD" in html          # engagement currency, printed live

    # All-hard engagement: the assumptions block is omitted, never a placeholder.
    eng2 = client.post("/api/engagements", json={"customer_name": "Hard Co"}).json()
    p2 = client.post(f"/api/engagements/{eng2['id']}/personas",
                     json={"name": "KW", "headcount": 5}).json()
    client.post(f"/api/engagements/{eng2['id']}/scenarios", json={
        "persona_id": p2["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})
    html2 = client.get(f"/api/engagements/{eng2['id']}/readout.html").text
    assert "Inputs carried as assumptions" not in html2


def test_engagement_currency_and_market_are_validated(client):
    """The engine never converts currency, so market/currency are validated soft
    refs against the loaded catalog (or the configured defaults with no
    catalog): unset values inherit the accepted pair, an explicit mismatch is
    rejected with a clear error instead of silently printing a readout header
    that contradicts its own numbers."""
    # An engagement created without market/currency inherits the accepted pair.
    eng = client.post("/api/engagements", json={"customer_name": "Ok Co"}).json()
    ok_market, ok_currency = eng["market"], eng["currency"]

    r = client.post("/api/engagements",
                    json={"customer_name": "Euro Co", "currency": ok_currency + "X"})
    assert r.status_code == 422
    assert "never converted" in r.json()["detail"]

    r = client.patch(f"/api/engagements/{eng['id']}", json={"market": ok_market + "X"})
    assert r.status_code == 422
    # Matching values round-trip.
    r = client.patch(f"/api/engagements/{eng['id']}",
                     json={"market": ok_market, "currency": ok_currency})
    assert r.status_code == 200
