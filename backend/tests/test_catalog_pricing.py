"""Deterministic catalog price resolution (services/bundles.catalog_price_row).

The NCE license-based price list ships every SKU as many priced variants —
segment (Commercial / Education / Charity), term (P1M / P1Y / P3Y), billing plan
(Annual / Monthly / Triennial), plus '(no Teams)' / trial / suite superstring
titles. The customer-facing baseline recommend-a-path must quote is the
requested segment's P1Y term billed annually, from the plain variant — and a
ratified SKU → Bundle mapping outranks any title fuzz. Variant shapes and the
ERP relationships (whole-term prices; ~+5% monthly-billed, ~+20% month-to-month)
mirror the real July sheet."""

from decimal import Decimal

import pytest

_VERSION = "test-pricing"


def _sku(name, erp_year, *, term="P1Y", billing="Annual", segment="Commercial",
         sku_id="0001", product_id=None, bundle_id=None, product_title=""):
    from app import models
    return models.MicrosoftSku(
        product_id=product_id or f"PID-{name}-{term}-{billing}-{segment}",
        sku_id=sku_id, product_title=product_title or name, sku_title=name,
        term_duration=term, billing_plan=billing, segment=segment,
        annual_erp_price=Decimal(str(erp_year)), annual_unit_price=Decimal(str(erp_year)) * Decimal("0.8"),
        market="UM", currency="USD", catalog_version=_VERSION, bundle_id=bundle_id,
    )


@pytest.fixture()
def db(client):  # client fixture boots the app (tables + seeded bundles)
    from app.db import SessionLocal
    from app import models
    session = SessionLocal()
    # Snapshot-and-clear the catalog: other suites import their own fixture rows
    # (e.g. a short-titled 'M365 E3'), which would tie-break these deterministic
    # resolution tests. Restored afterward so those suites are unaffected.
    cols = [c.name for c in models.MicrosoftSku.__table__.columns]
    saved = [
        {c: getattr(r, c) for c in cols}
        for r in session.query(models.MicrosoftSku).all()
    ]
    session.query(models.MicrosoftSku).delete()
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.query(models.MicrosoftSku).delete()
        for data in saved:
            session.add(models.MicrosoftSku(**data))
        session.commit()
        session.close()


def test_prefers_commercial_p1y_monthly_plain_variant(db):
    """Among the full real-sheet variant spread, the plain Commercial 1-year-commit
    pay-monthly row (the typical customer case) prices the bundle by default —
    not Charity, P3Y, month-to-month, '(no Teams)', or trial rows (which the old
    first-ILIKE-match could return). An explicit basis picks that variant."""
    from app.services import bundles as bsvc
    db.add_all([
        _sku("Microsoft 365 E3", 491.4, billing="Monthly"),               # the default basis
        _sku("Microsoft 365 E3", 468),                                    # annual-billed
        _sku("Microsoft 365 E3", 561.6, term="P1M", billing="Monthly"),   # +20% month-to-month
        _sku("Microsoft 365 E3", 468, term="P3Y", billing="Triennial"),
        _sku("Microsoft 365 E3 (Non-Profit Pricing)", 117, segment="Charity"),
        _sku("Microsoft 365 E3 (Education Pricing)", 200, segment="Education"),
        _sku("Microsoft 365 E3 (no Teams)", 383.9, billing="Monthly"),
        _sku("Microsoft 365 E3 Trial", 0, term="P1M", billing="None"),
    ])
    db.commit()

    row = bsvc.catalog_price_row(db, "Microsoft 365 E3")
    assert (row.sku_title, row.term_duration, row.billing_plan, row.segment) == \
        ("Microsoft 365 E3", "P1Y", "Monthly", "Commercial")
    assert bsvc.catalog_annual_erp(db, "Microsoft 365 E3") == Decimal("491.4")
    # An explicit basis (the engagement/scenario hierarchy) picks that variant.
    assert bsvc.catalog_annual_erp(db, "Microsoft 365 E3", billing="Annual") == Decimal("468")
    assert bsvc.catalog_annual_erp(db, "Microsoft 365 E3", term="P1M") == Decimal("561.6")
    assert bsvc.catalog_annual_erp(db, "Microsoft 365 E3", segment="Charity") == Decimal("117")


def test_reverse_title_match_when_sheet_drops_the_prefix(db):
    """The sheet titles some products without the 'Microsoft' prefix ('Power BI
    Pro'); a bundle named 'Microsoft Power BI Pro' must still resolve."""
    from app.services import bundles as bsvc
    db.add_all([
        _sku("Power BI Pro", 168),
        _sku("Power BI Pro (Education Faculty Pricing)", 50, segment="Education"),
    ])
    db.commit()
    assert bsvc.catalog_annual_erp(db, "Microsoft Power BI Pro") == Decimal("168")


def test_stripped_prefix_fallback_is_startswith_only(db):
    """'Microsoft Defender for Office 365 P2' resolves via the stripped-prefix
    tier to 'Defender for Office 365 P2 Add On' — but the same tier must NOT let
    'Microsoft 365 E3' (stripped: '365 E3') hit an Office 365 E3 row."""
    from app.services import bundles as bsvc
    db.add_all([
        _sku("Defender for Office 365 P2 Add On", 67.2),
        _sku("Office 365 E3", 346.8),
    ])
    db.commit()
    assert bsvc.catalog_annual_erp(db, "Microsoft Defender for Office 365 P2") == Decimal("67.2")
    # No Microsoft 365 E3 rows exist here: better an honest $0 than O365 pricing.
    assert bsvc.catalog_annual_erp(db, "Microsoft 365 E3") == Decimal("0")


def test_ratified_bundle_mapping_outranks_title_match(db):
    """Rows ratified onto the bundle (MicrosoftSku.bundle_id — the first-class
    SKU → Bundle spine) price it even when their titles wouldn't match, and win
    over title-matching unmapped rows."""
    from app.services import bundles as bsvc
    e3 = next(b for b in bsvc.list_bundles(db) if b.name == "Microsoft 365 E3")
    db.add_all([
        _sku("Microsoft 365 E3", 468),  # unmapped title match
        _sku("M365 Enterprise Plan 3 (promo)", 450, bundle_id=e3.id),
    ])
    db.commit()
    assert bsvc.catalog_annual_erp(db, e3.name, bundle_id=e3.id) == Decimal("450")


def test_recommend_a_path_prices_at_engagement_basis(client, db):
    """End-to-end: with a catalog loaded and no per-request overrides, the
    persona bundle analysis quotes the ERP at the engagement's pricing basis
    (default: Commercial, 1-year commit, pay monthly) — through the API, not
    just the service helper."""
    db.add_all([
        _sku("Microsoft 365 Business Premium", 277.2, billing="Monthly"),
        _sku("Microsoft 365 Business Premium", 264),
        _sku("Microsoft 365 Business Premium (Non-Profit Pricing)", 66, segment="Charity"),
    ])
    db.commit()

    eng = client.post("/api/engagements", json={"customer_name": "Pricing Co"}).json()
    eid = eng["id"]
    assert (eng["default_term_duration"], eng["default_billing_plan"]) == ("P1Y", "Monthly")
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 100}).json()
    res = client.post(f"/api/engagements/{eid}/personas/{kw['id']}/bundle-analysis").json()
    bp = next(b for b in res["bundles"] if b["sku_reference"] == "Microsoft 365 Business Premium")
    assert bp["base_price_annual"] == 277.2

    # Flip the engagement's payment default to Annual → the same analysis
    # requotes at the annual-billed variant (engagement level of the hierarchy).
    client.patch(f"/api/engagements/{eid}", json={"default_billing_plan": "Annual"})
    res = client.post(f"/api/engagements/{eid}/personas/{kw['id']}/bundle-analysis").json()
    bp = next(b for b in res["bundles"] if b["sku_reference"] == "Microsoft 365 Business Premium")
    assert bp["base_price_annual"] == 264.0
    client.delete(f"/api/engagements/{eid}")


def test_global_defaults_inherited_by_new_engagements(client, db):
    """Level 1 → 2 of the hierarchy: changing the global default term/billing
    retargets NEW engagements only."""
    orig = client.get("/api/admin/defaults").json()
    try:
        client.put("/api/admin/defaults",
                   json={"default_term_duration": "P3Y", "default_billing_plan": "Triennial"})
        eng = client.post("/api/engagements", json={"customer_name": "Inherit Co"}).json()
        assert (eng["default_term_duration"], eng["default_billing_plan"]) == ("P3Y", "Triennial")
        client.delete(f"/api/engagements/{eng['id']}")
    finally:
        client.put("/api/admin/defaults", json={
            "default_term_duration": orig["default_term_duration"],
            "default_billing_plan": orig["default_billing_plan"]})


def test_scenario_basis_change_requotes_target_and_addons(client, db):
    """Level 3 of the hierarchy: picking a different term/payment model on a
    scenario requotes the base bundle AND its add-ons from the catalog at that
    basis; clearing it requotes back at the engagement default. Prices remain
    hand-editable afterward."""
    db.add_all([
        _sku("Microsoft 365 E3", 491.4, billing="Monthly"),
        _sku("Microsoft 365 E3", 468),
        _sku("Microsoft 365 E5 Security", 144, billing="Monthly"),
        _sku("Microsoft 365 E5 Security", 137),
    ])
    db.commit()

    from app.services import bundles as bsvc
    e5sec = next(b for b in bsvc.list_bundles(db) if b.name == "Microsoft 365 E5 Security")

    eng = client.post("/api/engagements", json={"customer_name": "Requote Co"}).json()
    eid = eng["id"]
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "KW", "headcount": 100}).json()
    s = client.post(f"/api/engagements/{eid}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 491.4,
        "addons": [{"bundle_id": e5sec.id, "unit_price_annual": 144}],
    }).json()
    assert s["term_duration"] is None  # inherits the engagement default

    # Pick annual billing on the line → base and add-on requote to the Annual rows.
    s = client.patch(f"/api/engagements/{eid}/scenarios/{s['id']}",
                     json={"billing_plan": "Annual"}).json()
    assert float(s["target_unit_price_annual"]) == 468.0
    assert float(s["addons"][0]["unit_price_annual"]) == 137.0

    # A hand edit afterward sticks (no silent requote on unrelated patches).
    s = client.patch(f"/api/engagements/{eid}/scenarios/{s['id']}",
                     json={"target_unit_price_annual": 400}).json()
    assert float(s["target_unit_price_annual"]) == 400.0

    # Clearing the line override requotes back at the engagement default (Monthly).
    s = client.patch(f"/api/engagements/{eid}/scenarios/{s['id']}",
                     json={"billing_plan": None}).json()
    assert float(s["target_unit_price_annual"]) == 491.4
    assert float(s["addons"][0]["unit_price_annual"]) == 144.0
    client.delete(f"/api/engagements/{eid}")
