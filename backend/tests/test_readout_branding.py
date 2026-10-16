"""The practice brands the customer readout once (TARGET_SCHEMA §3.5, D25), and the
HTML readout and the PDF are one document (D26)."""

import base64
import re
from io import BytesIO

from pypdf import PdfReader

PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAQAAAACCAIAAADwyuo0AAAAEklEQVR4"
       "nGMUiTrBAANMDEgAAByqATrMLakTAAAAAElFTkSuQmCC")
SVG = "data:image/svg+xml;base64," + base64.b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
    b'<rect width="40" height="20" fill="#123456"/></svg>').decode()


def _text(pdf: bytes) -> str:
    return re.sub(r"\s+", " ", " ".join(p.extract_text() for p in PdfReader(BytesIO(pdf)).pages))


FIELDS = ("logo_data_url", "primary_color", "accent_color", "highlight_color", "tagline",
          "cover_subtitle", "closing_block", "footer_disclaimer")


def test_branding_defaults_are_null_and_resolved_at_render(client):
    # The install is shared across tests: clear every field back to its default.
    b = client.put("/api/admin/branding", json={k: "" for k in FIELDS}).json()
    # Nothing stored: every field NULL, the built-in defaults only resolved.
    assert all(b[k] is None for k in ("logo_data_url", "primary_color", "tagline",
                                      "closing_block", "footer_disclaimer"))
    assert b["resolved"]["primary_color"].startswith("#")
    assert b["resolved"]["cover_subtitle"] == "Microsoft 365 Total Cost of Ownership"
    # Reading never writes the defaults back.
    assert client.get("/api/admin/branding").json()["primary_color"] is None


def test_branding_is_validated_sanitized_and_cleared(client):
    r = client.put("/api/admin/branding", json={
        "primary_color": "#0e2841", "logo_data_url": SVG,
        "tagline": 'Build what <span class="hl" style="x">matters</span>.<script>alert(1)</script>',
        "closing_block": '<b>Your team</b><br><a href="javascript:alert(1)">x</a>'
                         '<a href="mailto:team@example.com" onclick="y">mail</a>'}).json()
    assert r["primary_color"] == "#0E2841"
    assert r["tagline"] == 'Build what <span class="hl">matters</span>.'
    assert r["closing_block"] == '<b>Your team</b><br>x<a href="mailto:team@example.com">mail</a>'
    assert client.put("/api/admin/branding", json={"primary_color": "blue"}).status_code == 422
    assert client.put("/api/admin/branding",
                      json={"logo_data_url": "https://example.com/x.png"}).status_code == 422
    # An empty value clears the field back to its default (NULL).
    r = client.put("/api/admin/branding", json={"tagline": "", "primary_color": ""}).json()
    assert r["tagline"] is None and r["primary_color"] is None
    assert r["logo_data_url"] == SVG             # untouched fields stay as entered


def test_readout_carries_the_practice_branding_and_customer_logo(client):
    client.put("/api/admin/branding", json={
        "logo_data_url": SVG, "accent_color": "#1578F8",
        "tagline": 'Build what <span class="hl">matters</span>.',
        "closing_block": "<b>Your account team</b>",
        "footer_disclaimer": "Indicative figures only."})
    eid = client.post("/api/engagements", json={"customer_name": "Logo Co"}).json()["id"]
    client.patch(f"/api/engagements/{eid}", json={"brand_logo_data_url": PNG})
    p = client.post(f"/api/engagements/{eid}/personas", json={"name": "Staff", "headcount": 10}).json()
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 400, "in_scope": True})

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert SVG[:60] in html and PNG[:60] in html     # both logos
    assert "--accent:#1578F8" in html
    assert 'Build what <span class="hl">matters</span>.' in html
    assert "<b>Your account team</b>" in html and "Indicative figures only." in html

    pdf = client.post(f"/api/engagements/{eid}/customer-report.pdf").content
    text = _text(pdf)
    assert "Logo Co" in text and "Build what matters." in text.replace(" .", ".")
    assert "added over 36 months at full run rate" in text
    assert "Indicative figures only." in text        # the running footer
    assert "Your account team" in text


def test_the_customer_name_stands_in_when_there_is_no_customer_logo(client):
    eid = client.post("/api/engagements", json={"customer_name": "Plain Co"}).json()["id"]
    html = client.get(f"/api/engagements/{eid}/readout.html").text
    # No customer logo: the name is the cover title; no logo slot stands empty.
    assert "<h1 class='cover-title'>Plain Co</h1>" in html


def test_third_party_spend_bar_reconciles_to_todays_spend(client):
    """Retired now + retired by the plans + remains on reduced tools + kept as is =
    today's counted third-party spend, from engine figures only."""
    eid = client.post("/api/engagements", json={"customer_name": "Bar Co"}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas", json={"name": "Staff", "headcount": 100}).json()
    for sku, price in [("Office 365 E3", 276), ("Enterprise Mobility + Security E3", 127)]:
        client.post(f"/api/engagements/{eid}/current-licenses", json={
            "sku_reference": sku, "quantity_purchased": 100, "quantity_assigned": 100,
            "unit_price_paid_annual": price, "persona_ids": [p["id"]]})
    outcomes = {o["seed_key"]: o["id"] for o in client.get(f"/api/engagements/{eid}/outcomes").json()}
    for name, cost, keys, covers in [("SSO Tool", 6000, ("identity-sso",), None),
                                     ("EDR Tool", 9000, ("endpoint-edr",), 150),
                                     ("Other Tool", 3000, ("analytics-bi",), None)]:
        body = {"name": name, "raw_cost": cost, "persona_ids": [p["id"]]}
        if covers:
            body["covered_count_override"] = covers
        t = client.post(f"/api/engagements/{eid}/third-party", json=body).json()
        for k in keys:
            client.post(f"/api/engagements/{eid}/coverage", json={
                "outcome_id": outcomes[k], "product_kind": "ThirdParty",
                "third_party_product_id": t["id"], "coverage": "Full", "ratified": True})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 684, "in_scope": True})

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    today = int(re.search(r"Today these tools cost <b>\$([\d,]+)</b>", html).group(1).replace(",", ""))
    legend = html.split("<div class='legend'>")[1].split("<table")[0]
    parts = [int(v.replace(",", "")) for v in re.findall(r"<b>\$([\d,]+)</b>", legend)]
    assert len(parts) >= 2 and sum(parts) == today


def test_engagement_brand_colours_are_retired(client):
    from sqlalchemy import inspect
    from app.db import engine
    cols = {c["name"] for c in inspect(engine).get_columns("engagements")}
    assert "brand_primary_color" not in cols and "brand_accent_color" not in cols
    assert "brand_logo_data_url" in cols             # kept: the customer's logo


def test_spend_bridge_ends_on_the_headline_run_rate(client):
    """The bridge reconciles to the headline: its last line is the run rate per year,
    in the same notation (savings plain, costs in brackets)."""
    eid = client.post("/api/engagements", json={"customer_name": "Bridge Co"}).json()["id"]
    p = client.post(f"/api/engagements/{eid}/personas", json={"name": "Staff", "headcount": 100}).json()
    client.post(f"/api/engagements/{eid}/current-licenses", json={
        "sku_reference": "Microsoft 365 E3", "quantity_purchased": 120, "quantity_assigned": 100,
        "unit_price_paid_annual": 432, "persona_ids": [p["id"]], "unused_seats_answer": "NotNeeded"})
    client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": p["id"], "target_sku_reference": "Microsoft 365 E5",
        "target_unit_price_annual": 684, "in_scope": True})
    rr = client.post(f"/api/engagements/{eid}/compute").json()["rollup"]["headline"]["run_rate_annual"]
    html = client.get(f"/api/engagements/{eid}/readout.html").text
    bridge = html.split("<h1>Spend Bridge</h1>")[1].split("</section>")[0]
    want = f"${abs(rr):,.0f}" if rr >= 0 else f"(${abs(rr):,.0f})"
    assert f"<td>Run rate per year</td><td class='num {'pos' if rr > 0 else ''}'>{want}</td>" in bridge
    assert "−$" not in bridge                       # one sign convention, no minus signs
