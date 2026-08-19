"""An empty New-outcomes story must never be silent.

"No new capabilities for this persona" has three very different causes — the
target maps to no capability at all, the current licensing was counted org-wide
because it carries no persona tag, or the target genuinely adds nothing. Omitting
the persona (the old behaviour) made all three look identical to a lost section,
and the first two also invented a full-capability "drop". Every surface that
shows the story must name the reason instead.
"""

import io

from openpyxl import load_workbook


def _engagement(client, name, *, target, tag_licence=True):
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    kw = client.post(f"/api/engagements/{eid}/personas",
                     json={"name": "Knowledge Workers", "headcount": 1000}).json()
    client.post(f"/api/engagements/{eid}/current-licenses",
                json={"sku_reference": "Microsoft 365 E5", "quantity_assigned": 1000,
                      "unit_price_paid_annual": 690,
                      "persona_ids": [kw["id"]] if tag_licence else []})
    client.post(f"/api/engagements/{eid}/scenarios",
                json={"persona_id": kw["id"], "target_sku_reference": target,
                      "target_unit_price_annual": 690, "in_scope": True})
    return eid, kw["id"]


def _entry(result, pid):
    return next(n for n in result["new_outcomes"] if n["persona_id"] == pid)


def test_in_scope_persona_with_nothing_new_is_still_listed_with_a_reason(client):
    """Target = what they already hold: a real finding, stated as one."""
    eid, pid = _engagement(client, "Nothing New Co", target="Microsoft 365 E5")
    result = client.post(f"/api/engagements/{eid}/compute").json()
    entry = _entry(result, pid)
    assert entry["outcomes"] == []
    assert entry["empty_reason"] == "covered_today"
    assert "Nothing new" in entry["empty_reason_text"]
    assert result["dropped_capability"] == []

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "<h2>New outcomes</h2>" in html          # the section survives
    assert "Knowledge Workers" in html
    assert "Nothing new" in html


def test_unmapped_target_is_reported_as_a_data_gap_not_a_capability_wipe(client):
    """A target with no ratified coverage can neither add nor drop anything —
    the old behaviour showed no new outcomes AND dropped the persona's whole
    current capability, a phantom trade-off on the readout."""
    eid, pid = _engagement(client, "Unmapped Target Co", target="Microsoft 365 E5 (bespoke)")
    result = client.post(f"/api/engagements/{eid}/compute").json()
    entry = _entry(result, pid)
    assert entry["outcomes"] == []
    assert entry["empty_reason"] == "target_unmapped"
    assert "Microsoft 365 E5 (bespoke)" in entry["empty_reason_text"]
    # No phantom drop, and no Capability trade-offs section built on one.
    assert result["dropped_capability"] == []
    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "no ratified coverage" in html
    assert "Capability trade-offs" not in html

    gaps = client.get(f"/api/engagements/{eid}/coverage-gaps").json()
    gap = next(g for g in gaps["personas"] if g["persona_id"] == pid)
    assert gap["target_unmapped"] is True
    assert gap["dropped_outcomes"] == []
    assert [u["reference"] for u in gap["unmapped_target"]] == ["Microsoft 365 E5 (bespoke)"]


def test_untagged_current_licensing_is_named_as_the_reason(client):
    """An untagged licence line counts for EVERY persona (deliberately
    conservative), so it can make a target look redundant. Say so — with the
    line named — instead of printing nothing."""
    eid, pid = _engagement(client, "Untagged Co", target="Microsoft 365 E3",
                           tag_licence=False)
    result = client.post(f"/api/engagements/{eid}/compute").json()
    entry = _entry(result, pid)
    assert entry["outcomes"] == []
    assert entry["empty_reason"] == "covered_org_wide"
    assert "Microsoft 365 E5" in entry["empty_reason_text"]

    html = client.get(f"/api/engagements/{eid}/readout.html").text
    assert "no persona tag" in html

    gaps = client.get(f"/api/engagements/{eid}/coverage-gaps").json()
    gap = next(g for g in gaps["personas"] if g["persona_id"] == pid)
    assert gap["org_wide_current_licenses"] == ["Microsoft 365 E5"]


def test_tagged_licensing_does_not_raise_the_org_wide_guard(client):
    """The guard fires on untagged lines only — no false alarm on a tagged one."""
    eid, pid = _engagement(client, "Tagged Co", target="Microsoft 365 E3")
    client.post(f"/api/engagements/{eid}/compute")
    gaps = client.get(f"/api/engagements/{eid}/coverage-gaps").json()
    gap = next(g for g in gaps["personas"] if g["persona_id"] == pid)
    assert gap["org_wide_current_licenses"] == []
    assert gap["target_unmapped"] is False


def test_xlsx_accounts_for_a_persona_that_gains_nothing(client):
    eid, _ = _engagement(client, "Xlsx Nothing New Co", target="Microsoft 365 E5")
    data = client.get(f"/api/engagements/{eid}/readout.xlsx").content
    rows = list(load_workbook(io.BytesIO(data))["Capability changes"].iter_rows(values_only=True))
    none_rows = [r for r in rows[1:] if r[2] == "None"]
    assert len(none_rows) == 1
    assert none_rows[0][0] == "Knowledge Workers"
    assert "Nothing new" in none_rows[0][3]


def test_gaining_persona_carries_no_reason(client):
    eid, pid = _engagement(client, "Gain Co", target="Microsoft 365 E5")
    # Swap the persona onto a smaller current licence so the target adds capability.
    lics = client.get(f"/api/engagements/{eid}/current-licenses").json()
    client.patch(f"/api/engagements/{eid}/current-licenses/{lics[0]['id']}",
                 json={"sku_reference": "Microsoft 365 E3"})
    result = client.post(f"/api/engagements/{eid}/compute").json()
    entry = _entry(result, pid)
    assert entry["outcomes"]
    assert entry["empty_reason"] is None
