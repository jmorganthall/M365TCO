"""The spreadsheet readout (PRD Section 11.5).

The xlsx export keeps the full QA detail (including the population check) for the
operator. The customer document — the HTML readout and the PDF, one design — is
services/readout (TARGET_SCHEMA D26).
"""

from __future__ import annotations

import io
from decimal import Decimal

from openpyxl import Workbook

from .. import models


def _fmt_ratio(value) -> str:
    """Render an ECIF ROI ratio without trailing zeros: 10.00 → '10', 7.50 → '7.5'."""
    d = Decimal(str(value or 0))
    d = d.to_integral_value() if d == d.to_integral_value() else d.normalize()
    return format(d, "f")


def build_xlsx(engagement: models.Engagement, result: dict) -> bytes:
    wb = Workbook()

    ws = wb.active
    ws.title = "Scenarios"
    ws.append(
        ["Persona", "Target SKU", "Headcount", "In scope", "Current spend",
         "Target spend", "Delta", "Current MS", "Third-party offset"]
    )
    for s in result["scenarios"]:
        ws.append([
            s["persona_name"], s.get("target_label") or s["target_sku_reference"], s["headcount"],
            s["in_scope"], s["current_spend_annual"], s["target_spend_annual"],
            s["delta_annual"], s["current_microsoft_annual"],
            s["current_third_party_offset_annual"],
        ])

    # Current licensing — the from-state, with the list baseline, the effective
    # (paid) price, and any negotiated override made explicit.
    wc = wb.create_sheet("Current licensing")
    wc.append(
        ["SKU", "Seats assigned", "List $/seat/yr", "Effective $/seat/yr",
         "Price override", "% off list", "Annual", "Out of scope (in no number)"]
    )
    for lic in engagement.current_licenses:
        list_base = float(lic.list_unit_price_annual)
        effective = float(lic.effective_unit_price_annual)
        off = (list_base - effective) / list_base if (lic.price_override and list_base) else 0
        wc.append([
            lic.sku_reference, lic.quantity_assigned, list_base, effective,
            "yes" if lic.price_override else "no",
            round(off, 4), effective * (lic.quantity_assigned or 0),
            "yes" if lic.out_of_scope else "no",
        ])

    wd = wb.create_sheet("Dispositions")
    wd.append(
        ["Product", "Disposition", "Covered", "Displaced", "Residual count",
         "Residual cost", "Per-unit", "Effective cost", "Managed", "Tooling %",
         "Override", "Override reason", "Residual intent", "Renewal date"]
    )
    for d in result["dispositions"]:
        wd.append([
            d["third_party_product_name"], d["disposition"], d["covered_count"],
            d["displaced_users"], d["residual_count"], d["residual_annual_cost"],
            d["per_unit_annual_cost"], d["effective_annual_cost"], d["is_managed"],
            d["tooling_pct"], d["override"], d["override_reason"],
            d["residual_intent"], d["renewal_date"],
        ])

    wr = wb.create_sheet("Rollup")
    rollup = result["rollup"]
    pop = rollup["population_check"]
    wr.append(["Metric", "Value"])
    wr.append(["Existing Microsoft licensing (annual, in scope)", rollup["existing_microsoft_annual"]])
    wr.append(["Existing third-party freed up (annual, in scope)", rollup["existing_third_party_annual"]])
    for f in rollup["freed_third_party"]:
        wr.append([f"  freed up: {f['third_party_product_name']}", f["credited_annual"]])
    wr.append([
        "Total existing spend (annual, in scope)",
        rollup["existing_microsoft_annual"] + rollup["existing_third_party_annual"],
    ])
    wr.append(["Target Microsoft licensing (annual, in scope)", rollup["target_microsoft_annual"]])
    wr.append(["Net TCO delta (annual) — negative = saving", rollup["net_tco_delta_annual"]])
    wr.append(["  of which: quick wins (retirable today, no move)",
               -(rollup.get("freed_redundant_today_annual", 0) or 0)])
    wr.append(["  of which: the moves' own value (excl. quick wins)",
               rollup.get("move_incremental_delta_annual", 0) or 0])
    # The headline (ENGINE_SPEC 6.11) — the run rate first, then the ramp.
    # Savings-positive, unlike the cost-change deltas above.
    h = rollup.get("headline") or {}
    hm = h.get("horizon_months") or 0
    wr.append([f"Headline: over {hm} months at full run rate (positive = saving)",
               h.get("run_rate_horizon_amount", 0)])
    wr.append(["Run rate: per year once every contract has renewed (positive = saving)",
               h.get("run_rate_annual", 0)])
    wr.append(["  of which: duplicate spend today (quick wins)", h.get("duplicate_spend_annual", 0)])
    wr.append(["  of which: consolidation (the moves)", h.get("consolidation_annual", 0)])
    wr.append(["  of which: over-licensing (unused seats not needed)",
               h.get("overlicensing_annual", 0)])
    for key, label in (("duplicate_spend", "duplicate spend"), ("consolidation", "consolidation"),
                       ("overlicensing", "over-licensing")):
        if key in (h.get("savings_shares") or {}):
            wr.append([f"  share of the savings: {label} (%)", h["savings_shares"][key]])
    wr.append([f"Over {hm} months, each from its renewal (positive = saving)", h.get("amount", 0)])
    for y in h.get("years") or []:
        wr.append([f"  year {y['year']} (running total {y['cumulative']})", y["amount"]])
    fm = h.get("full_run_rate_month")
    wr.append(["Full run rate from month", "—" if fm is None else fm])
    if engagement.managed_ms_account:
        cons = _fmt_ratio(engagement.ecif_roi_conservative)
        gen = _fmt_ratio(engagement.ecif_roi_generous)
        wr.append(["Net annual uplift in Microsoft spend (target − current)",
                   rollup.get("msft_uplift_annual", 0)])
        wr.append([f"Projected ECIF funding — {cons}:1 (one-time)",
                   rollup.get("ecif_funding_low", 0)])
        wr.append([f"Projected ECIF funding — {gen}:1 (one-time)",
                   rollup.get("ecif_funding_high", 0)])
    wr.append(["Residual third-party cost (annual)", rollup["residual_third_party_cost_annual"]])
    wr.append(["In-scope persona headcount", pop["in_scope_persona_headcount"]])
    wr.append(["Third-party covered population", pop["third_party_covered_population"]])
    wr.append(["Fully eliminated tools", ", ".join(rollup["fully_eliminated_tools"])])
    wr.append([
        "Eliminated renewal cycles",
        ", ".join(r["third_party_product_name"] for r in rollup["eliminated_renewal_cycles"]),
    ])

    # Capability changes — the honest counterpart to the dollar savings: what each
    # in-scope persona GAINS (new) and GIVES UP (dropped) with its target licensing,
    # so the spreadsheet reconciles the number with the capability trade-offs.
    wcap = wb.create_sheet("Capability changes")
    wcap.append(["Persona", "Headcount", "Change", "Outcome"])
    for n in result.get("new_outcomes") or []:
        for o in n.get("outcomes", []):
            wcap.append([n["persona_name"], n["headcount"], "Gained", o["name"]])
        if not n.get("outcomes"):
            # A persona that gains nothing still gets a row carrying WHY — the
            # sheet accounts for every in-scope persona, blanks explain nothing.
            wcap.append([n["persona_name"], n["headcount"], "None",
                         n.get("empty_reason_text") or ""])
    for d in result.get("dropped_capability") or []:
        for o in d.get("outcomes", []):
            wcap.append([d["persona_name"], d["headcount"], "Dropped", o["name"]])

    # How the headline is timed — every timed amount behind it.
    wt = wb.create_sheet("Headline timing")
    wt.append(["Sub-line", "Item", "Kind", "Starts", "No date (counted from today)",
               "Months counted", "Per year (positive = saving)", "Over the horizon"])
    for i in h.get("items") or []:
        wt.append([i["sub_line"], i["label"], i["kind"], i["timed_by"] or "",
                   "yes" if i["date_missing"] else "no", i["months_counted"],
                   i["annual_amount"], i["amount"]])
    wu = wb.create_sheet("Unused licences")
    wu.append(["Licence", "Unused seats", "Per year", "Customer's answer"])
    for u in h.get("unused_seat_lines") or []:
        wu.append([u["sku_reference"], u["unused_seats"], u["annual_value"],
                   u.get("answer") or "not answered"])

    # The business case — the same stored narratives the HTML readout prints.
    narratives = result.get("narratives") or []
    if narratives:
        wn = wb.create_sheet("Business case")
        wn.append(["Persona", "Today", "What's new", "Value"])
        for n in narratives:
            wn.append([n.get("persona", ""), n.get("today", ""),
                       n.get("whats_new", ""), n.get("value", "")])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
