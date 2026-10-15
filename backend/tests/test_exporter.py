"""The customer readout (services/readout — the HTML readout and the PDF are one
document): no internal QA data, conditional sections, and the computed math
matches the /compute result the web app shows."""


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
    assert "What happens to each tool" not in html   # no third-party tools
    assert "Retired completely" not in html          # nothing eliminated
    assert "bought as a managed service" not in html  # no managed tools
    assert "assumed fully retired" not in html
    assert "Quick wins" not in html
    assert ">None<" not in html
    # The customer-facing core is present, and the persona/math pulled in: the
    # headline, the persona's own page and the bridge.
    assert "What this is worth" in html
    assert "<h1>Knowledge Worker</h1>" in html
    assert "How we get to the number" in html


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
    assert "What happens to each tool" in html
    assert "<span class='tag retire'>Retire</span>" in html
    assert "Retired completely" in html and "Okta" in html
    assert "bought as a managed service; 50% of its price" in html  # the tool's own split

    # The math from the web pulls into the readout: the net delta the /compute
    # endpoint returns appears verbatim in the HTML.
    result = client.post(f"/api/engagements/{eid}/compute").json()
    delta = result["rollup"]["net_tco_delta_annual"]
    usd = f"${abs(delta):,.0f}" if delta >= 0 else f"−${abs(delta):,.0f}"
    assert f"'>{usd}</td></tr>" in html                  # the bridge's net change


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
    # Okta renews at the workshop, so nothing waits and the timed headline equals
    # the untimed one (ENGINE_SPEC 6.11's run-rate identity).
    tool = client.post(f"/api/engagements/{eid}/third-party", json={
        "name": "Okta", "raw_cost": 30000, "covered_count_override": 150,
        "renewal_date": eng["workshop_date"]}).json()
    client.post(f"/api/engagements/{eid}/coverage", json={
        "outcome_id": identity["id"], "product_kind": "ThirdParty",
        "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": a["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 600, "in_scope": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": b["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})
    # The customer confirms nothing delivers the gaps today, so they are new (D21).
    for g in client.get(f"/api/engagements/{eid}/coverage-gaps").json()["personas"]:
        for o in g["uncovered_outcomes"]:
            client.put(f"/api/engagements/{eid}/coverage-gap-answers", json={
                "persona_id": g["persona_id"], "outcome_id": o["id"],
                "answer": "NotDeliveredToday"})

    html = client.get(f"/api/engagements/{eid}/readout.html").text

    # The hero: the run rate (per year once every contract has renewed) over one
    # compact move line per persona with its own annual amount. This engagement
    # has no current Microsoft spend, so the move is honestly a cost increase:
    # Sales +40k/yr (60k target − 20k freed Okta), Engineering +10k. With new
    # capabilities confirmed, the headline leads with what the investment buys.
    # 50,000 a year added × 3 years = 150,000 over the horizon at full run rate.
    assert "<div class='hero-figure added'>Invest $150,000</div>" in html
    assert "over 36 months at full run rate to gain" in html
    # Okta renews at the workshop, so the ramp lands the full run rate every year.
    assert "Within the first 36 months, $150,000 is added" in html
    # The consolidation source lists each persona's move with its own amount, per
    # year. Finance notation: added expense in parentheses; savings plain green.
    assert ("<td class='num'>($40,000)</td><td><b>Sales</b> <span class='muted'>(100)</span>"
            " → <b>Microsoft 365 E5</b></td>") in html
    assert ("<td class='num'>($10,000)</td><td><b>Engineering</b> <span class='muted'>(50)</span>"
            " → <b>Microsoft 365 E3</b></td>") in html
    # The bridge is a matrix: a column head per persona (→ its target) + Total.
    assert "Sales<div class='muted'>→ Microsoft 365 E5</div>" in html
    assert "Engineering<div class='muted'>→ Microsoft 365 E3</div>" in html
    assert "<th class='num'>Total</th>" in html
    # The freed-up tool sub-row splits per persona: Okta's per-unit cost is
    # 30000/150 = $200, credited 100 and 50 seats → −$20k + −$10k = −$30k total.
    assert "−$20,000" in html and "−$10,000" in html and "−$30,000" in html
    # New capabilities: the targets cover far more than Okta (identity-sso)
    # delivers today, so each persona's page lists its newly-lit capabilities.
    sales_page = html.split("<h1>Sales</h1>")[1].split("<section")[0]
    eng_page = html.split("<h1>Engineering</h1>")[1].split("<section")[0]
    assert "New capabilities" in sales_page and "New capabilities" in eng_page
    # Each capability is a chip, with the outcome's description as hover text.
    assert "class='chip new'" in html
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
    new_section = sales_page.split("New capabilities")[1].split("</div></div>")[0]
    assert outcomes["identity-sso"] not in new_section


def test_readout_renders_business_narrative_when_present(client, monkeypatch):
    """When per-persona narratives are attached to the result, each persona's page
    leads with its story (advisory; generation is wired separately)."""
    from app.services import branding, readout
    from app import models

    eng = models.Engagement(customer_name="Story Co")
    eng.personas, eng.third_party_products, eng.outcomes = [], [], []
    eng.coverage_entries, eng.current_licenses, eng.scenarios = [], [], []

    result = {
        "rollup": {
            "net_tco_delta_annual": -1000, "population_check": {},
            "existing_microsoft_annual": 0, "target_microsoft_annual": 0,
            "freed_third_party": [], "fully_eliminated_tools": [],
            "eliminated_renewal_cycles": [], "residual_third_party_cost_annual": 0,
            "quick_wins": [], "quick_win_savings_annual": 0,
        },
        "scenarios": [{
            "persona_id": "p1", "persona_name": "Knowledge Worker", "headcount": 10,
            "in_scope": True, "target_sku_reference": "Microsoft 365 E5",
            "target_spend_annual": 0, "current_microsoft_annual": 0, "delta_annual": 0,
            "move_incremental_delta_annual": 0, "offsets": []}],
        "dispositions": [],
        "narratives": [
            {"persona": "Knowledge Worker", "today": "On E3 + Okta.",
             "whats_new": "E5 consolidates identity.", "value": "Saves $45k/yr."}
        ],
    }
    html = readout.build_html(eng, result, [], branding.resolved(None))
    assert "<div class='narrative'>" in html
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
    assert "KW (persona: estimate)" in html
    assert "Okta (tool" not in html          # hard-tagged input is not disclosed
    assert "Figures in USD, US pricing" in html  # engagement market/currency, printed live

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
