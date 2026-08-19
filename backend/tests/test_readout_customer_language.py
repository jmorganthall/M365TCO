"""The HTML readout is handed to the customer, so its language has to hold up in
every direction the numbers can go: they save, they consolidate, they pay more and
get more, or they pay more and get nothing new.

Two rules are enforced here for all of them. Nothing written for the operator ever
reaches the page (no "set covers", no "tag them on the Current licensing tab", no
engine enums), and no line contradicts itself or reads as a placeholder ("0
retirement targets", "$0.00 remains", a share above 100%).
"""

import re

# Phrases that are ours, not the customer's — instructions to the operator, engine
# vocabulary, or QA shorthand. None may appear in a document we hand over.
OPERATOR_ONLY = [
    "set covers", "Staple bundles", "Coverage map", "Current licensing tab",
    "persona tag", "operator-entered", "FullyEliminated", "PartiallyReduced",
    "AISuggestedUnconfirmed", "ListPrice", "net TCO delta", "Population check",
]


def _no_operator_language(html):
    return [p for p in OPERATOR_ONLY if p in html]


def _persona(client, eid, name, headcount):
    return client.post(f"/api/engagements/{eid}/personas",
                       json={"name": name, "headcount": headcount}).json()


def _licence(client, eid, sku, qty, price, pid):
    client.post(f"/api/engagements/{eid}/current-licenses",
                json={"sku_reference": sku, "quantity_assigned": qty,
                      "unit_price_paid_annual": price, "persona_ids": [pid]})


def _scenario(client, eid, pid, target, price):
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": pid, "target_sku_reference": target,
                      "target_unit_price_annual": price, "in_scope": True})


def _tool(client, eid, name, cost, covers, pid, outcome_name):
    tp = client.post(f"/api/engagements/{eid}/third-party",
                     json={"name": name, "raw_cost": cost, "cost_period": "Annual",
                           "covered_count_override": covers, "persona_ids": [pid]}).json()
    outs = {o["name"]: o["id"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    oid = next(v for k, v in outs.items() if outcome_name in k)
    client.post(f"/api/engagements/{eid}/coverage",
                json={"product_kind": "ThirdParty", "third_party_product_id": tp["id"],
                      "outcome_id": oid, "coverage": "Full", "ratified": True})
    return tp


def test_saving_move_that_consolidates_tools_names_the_consolidation(client):
    """Savings + vendor consolidation: the dollars are only half the story — the
    contracts, integrations and audit scope leaving the estate are the other half,
    and the readout has to say so."""
    eid = client.post("/api/engagements", json={"customer_name": "Consolidate Co"}).json()["id"]
    kw = _persona(client, eid, "Knowledge Workers", 1000)
    _licence(client, eid, "Microsoft 365 E3", 1000, 420, kw["id"])
    _tool(client, eid, "Acme SSO", 300000, 1000, kw["id"], "Identity Provider")
    _tool(client, eid, "Acme Phish", 90000, 1000, kw["id"], "Security Awareness")
    # A tool whose covered population was never captured: its disposition note is
    # the place the readout most easily slips into telling the CUSTOMER to go and
    # fix OUR input, so keep one in the sample.
    # (No persona tag and no covers override → covered_count 0.)
    zero = client.post(f"/api/engagements/{eid}/third-party",
                       json={"name": "Acme Vault", "raw_cost": 20000,
                             "cost_period": "Annual"}).json()
    assert zero["covered_count"] == 0
    outs = {o["name"]: o["id"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    client.post(f"/api/engagements/{eid}/coverage",
                json={"product_kind": "ThirdParty", "third_party_product_id": zero["id"],
                      "outcome_id": next(v for k, v in outs.items() if "Drive Encryption" in k),
                      "coverage": "Full", "ratified": True})
    _scenario(client, eid, kw["id"], "Microsoft 365 E5", 660)

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert _no_operator_language(html) == []
    assert "Covered user count not yet provided" in html
    assert "saved over 36 months" in html
    assert "Tools retired in full" in html
    # The consolidation, spelled out — the value that never shows in the dollar line.
    assert "fewer tools to renew, integrate, secure and audit each year" in html
    assert "No third-party spend remains after the move" in html


def test_increase_that_buys_capability_reads_as_an_investment(client):
    """Paying more, getting more: the headline says invested (not "added cost"),
    the framing label agrees, and the caveat names what the money buys."""
    eid = client.post("/api/engagements", json={"customer_name": "Uplift Co"}).json()["id"]
    kw = _persona(client, eid, "Knowledge Workers", 500)
    _licence(client, eid, "Office 365 E3", 500, 300, kw["id"])
    _scenario(client, eid, kw["id"], "Microsoft 365 E5", 660)

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert _no_operator_language(html) == []
    assert "invested over 36 months" in html
    assert "Net licensing investment" in html
    assert "buys the capabilities listed under New outcomes below" in html
    assert "added cost" not in html
    # An increase is never red; red is reserved for nothing on this page.
    assert "headline neg" not in html
    # "Full savings from day one" is the wrong promise over an increase.
    assert "assume full savings from day one" not in html
    assert "assume the full-year effect from day one" in html


def test_increase_that_buys_nothing_is_stated_without_inventing_value(client):
    """Paying more for the same thing: no "investment" word it hasn't earned, and
    the next move is put as a conversation, not as an internal instruction."""
    eid = client.post("/api/engagements", json={"customer_name": "Flat Co"}).json()["id"]
    kw = _persona(client, eid, "Knowledge Workers", 200)
    _licence(client, eid, "Microsoft 365 E5", 200, 600, kw["id"])
    _scenario(client, eid, kw["id"], "Microsoft 365 E5", 720)

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert _no_operator_language(html) == []
    assert "added over 36 months" in html
    assert "invested over" not in html
    assert "Net change in licensing spend" in html
    assert "one to confirm together before it lands in the plan" in html
    # The operator's version of that sentence keeps the blunt recommendation.
    result = client.post(f"/api/engagements/{eid}/compute").json()
    entry = next(n for n in result["new_outcomes"] if n["persona_id"] == kw["id"])
    assert "keeping this persona out of scope" in entry["empty_reason_text"]
    assert "keeping this persona out of scope" not in html


def test_right_sizing_that_sheds_capability_stays_plural_and_honest(client):
    """A saving that gives something up: the caveat rides with the headline, and
    the trade-off section does not call the figures above "savings" — they may not
    be, on another engagement."""
    eid = client.post("/api/engagements", json={"customer_name": "Rightsize Co"}).json()["id"]
    p = _persona(client, eid, "Back Office", 700)
    for sku in ("Office 365 E3", "Enterprise Mobility + Security E3"):
        _licence(client, eid, sku, 700, 300, p["id"])
    _scenario(client, eid, p["id"], "Office 365 E3", 300)

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert _no_operator_language(html) == []
    assert "This includes capability trade-offs for 1 persona" in html
    assert "acceptable to give up" in html
    assert "a larger base bundle or an add-on brings it back" in html


def test_nothing_retires_produces_no_zero_placeholder_next_steps(client):
    """An engagement with no third-party tools must not tell the customer to pull
    contract end dates for "the 0 retirement targets"."""
    eid = client.post("/api/engagements", json={"customer_name": "No Tools Co"}).json()["id"]
    kw = _persona(client, eid, "Knowledge Workers", 100)
    _licence(client, eid, "Microsoft 365 E3", 100, 400, kw["id"])
    _scenario(client, eid, kw["id"], "Microsoft 365 E5", 600)

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "0 retirement target" not in html and "0 tools being retired" not in html
    assert "Confirm current licensing counts and the prices actually paid" in html
    assert "Time each persona's move to its licensing anniversary" in html


def test_hero_shares_are_hidden_when_they_would_read_as_nonsense(client):
    """Quick wins bigger than the total (the moves are a net investment) make the
    two shares 625% / (525%) — arithmetically consistent, unreadable to a human.
    They are suppressed; the legend explains how the cards reconcile instead."""
    eid = client.post("/api/engagements", json={"customer_name": "Mixed Co"}).json()["id"]
    kw = _persona(client, eid, "Knowledge Workers", 1000)
    fl = _persona(client, eid, "Frontline", 3000)
    _licence(client, eid, "Microsoft 365 E3", 1000, 420, kw["id"])
    _licence(client, eid, "Microsoft 365 F1", 3000, 28, fl["id"])
    _tool(client, eid, "Acme SSO", 300000, 1000, kw["id"], "Identity Provider")
    _scenario(client, eid, kw["id"], "Microsoft 365 E5", 660)
    _scenario(client, eid, fl["id"], "Microsoft 365 F3", 96)   # a big net increase

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert _no_operator_language(html) == []
    pcts = re.findall(r"<div class='part-pct'>\(?(\d+)%\)?</div>", html)
    assert all(int(p) <= 100 for p in pcts), pcts
    assert "① less these gives the headline figure" in html
