"""Readout export (PRD Section 11.5): structured HTML + spreadsheet.

The HTML readout leads with the population check (11.2) and includes the
assumptions and source appendix (11.3): the tooling-split line with per-line
overrides, every ForceFullElimination reason, current-line price bases, and a
source legend. Deck/one-pager generation is a later AI-assisted feature.
"""

from __future__ import annotations

import html
import io
from decimal import Decimal

from openpyxl import Workbook

from .. import models


def _usd(value) -> str:
    return f"${float(value or 0):,.2f}"


def _pct(value) -> str:
    return f"{float(value or 0) * 100:.0f}%"


def build_html(engagement: models.Engagement, result: dict) -> str:
    rollup = result["rollup"]
    pop = rollup["population_check"]
    delta = rollup["net_tco_delta_annual"]
    delta_label = "Annual savings" if delta >= 0 else "Annual cost increase"

    rows_scenarios = "".join(
        f"<tr><td>{html.escape(s['persona_name'])}</td>"
        f"<td>{html.escape(s['target_sku_reference'])}</td>"
        f"<td>{s['headcount']}</td>"
        f"<td class='num'>{_usd(s['current_spend_annual'])}</td>"
        f"<td class='num'>{_usd(s['target_spend_annual'])}</td>"
        f"<td class='num {'pos' if s['delta_annual'] >= 0 else 'neg'}'>{_usd(s['delta_annual'])}</td>"
        f"<td>{'In scope' if s['in_scope'] else 'Excluded'}</td></tr>"
        for s in result["scenarios"]
    )

    rows_disp = "".join(
        f"<tr><td>{html.escape(d['third_party_product_name'])}</td>"
        f"<td>{d['disposition']}</td>"
        f"<td>{d['displaced_users']} / {d['covered_count']}</td>"
        f"<td>{d['residual_count']}</td>"
        f"<td class='num'>{_usd(d['residual_annual_cost'])}</td>"
        f"<td>{'managed @ ' + _pct(d['tooling_pct']) if d['is_managed'] else 'unmanaged'}</td>"
        f"<td>{html.escape(d['override_reason']) if d['override'] != 'None' else ''}</td></tr>"
        for d in result["dispositions"]
    )

    eliminated = "".join(
        f"<li>{html.escape(t)}</li>" for t in rollup["fully_eliminated_tools"]
    ) or "<li>None</li>"

    renewals = "".join(
        f"<li>{html.escape(r['third_party_product_name'])}"
        + (f" — renews {r['renewal_date']}" if r["renewal_date"] else "")
        + "</li>"
        for r in rollup["eliminated_renewal_cycles"]
    ) or "<li>None</li>"

    # Assumptions / source appendix
    overrides = [
        d for d in result["dispositions"] if d["override"] == "ForceFullElimination"
    ]
    override_lines = "".join(
        f"<li><b>{html.escape(d['third_party_product_name'])}</b>: "
        f"{html.escape(d['override_reason'])}</li>"
        for d in overrides
    ) or "<li>None</li>"

    tooling_overrides = [
        d
        for d in result["dispositions"]
        if d["is_managed"] and abs(d["tooling_pct"] - float(engagement.global_tooling_pct)) > 1e-9
    ]
    tooling_override_lines = "".join(
        f"<li>{html.escape(d['third_party_product_name'])}: {_pct(d['tooling_pct'])}</li>"
        for d in tooling_overrides
    ) or "<li>None</li>"

    price_basis_lines = "".join(
        f"<li>{html.escape(lic.sku_reference)}: {lic.price_basis}"
        + (f", {_pct(lic.discount_pct)} discount" if lic.discount_pct else "")
        + "</li>"
        for lic in engagement.current_licenses
    ) or "<li>None entered</li>"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>M365 TCO Readout — {html.escape(engagement.customer_name)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:2rem;color:#1a1a2e;max-width:1100px}}
 h1{{margin-bottom:0}} .sub{{color:#555}}
 .headline{{font-size:2.2rem;font-weight:700;margin:.5rem 0}}
 .pos{{color:#127436}} .neg{{color:#b00020}}
 .popcheck{{background:#f3f6ff;border:1px solid #c9d6ff;padding:.75rem 1rem;border-radius:8px;margin:1rem 0}}
 table{{border-collapse:collapse;width:100%;margin:1rem 0}}
 th,td{{border:1px solid #ddd;padding:.45rem .6rem;text-align:left;font-size:.92rem}}
 th{{background:#fafafa}} td.num{{text-align:right;font-variant-numeric:tabular-nums}}
 section{{margin:1.5rem 0}} ul{{margin:.3rem 0}}
 footer{{margin-top:2rem;color:#777;font-size:.8rem}}
</style></head><body>
<h1>M365 TCO Readout</h1>
<div class="sub">{html.escape(engagement.customer_name)} · {engagement.market}/{engagement.currency} · annualized USD</div>

<div class="headline {'pos' if delta >= 0 else 'neg'}">{_usd(delta)} <span style="font-size:1rem;font-weight:400">{delta_label}</span></div>

<div class="popcheck"><b>Population check.</b>
 In-scope persona headcount: <b>{pop['in_scope_persona_headcount']}</b> ·
 Third-party covered population: <b>{pop['third_party_covered_population']}</b>.
 Gaps between these surface as residuals below.</div>

<section><h2>Per-persona scenarios</h2>
<table><thead><tr><th>Persona</th><th>Target SKU</th><th>Headcount</th>
<th>Current</th><th>Target</th><th>Delta</th><th>Scope</th></tr></thead>
<tbody>{rows_scenarios}</tbody></table></section>

<section><h2>Third-party dispositions</h2>
<table><thead><tr><th>Product</th><th>Disposition</th><th>Displaced/Covered</th>
<th>Residual</th><th>Residual cost</th><th>Basis</th><th>Override reason</th></tr></thead>
<tbody>{rows_disp}</tbody></table></section>

<section><h2>Rollup</h2>
<p><b>Fully eliminated tools:</b></p><ul>{eliminated}</ul>
<p><b>Eliminated renewal cycles</b> (gated on full elimination):</p><ul>{renewals}</ul>
<p><b>Residual third-party cost:</b> {_usd(rollup['residual_third_party_cost_annual'])}</p>
</section>

<section><h2>Assumptions and source appendix</h2>
<p><b>Tooling split.</b> Managed third-party products count at their tooling
percentage only; management of M365 is presumed neutral or better. Engagement
default: {_pct(engagement.global_tooling_pct)}. Per-line overrides:</p>
<ul>{tooling_override_lines}</ul>
<p><b>ForceFullElimination overrides</b> (assert savings on undisplaced users):</p>
<ul>{override_lines}</ul>
<p><b>Current Microsoft line price bases:</b></p><ul>{price_basis_lines}</ul>
<p><b>Source legend.</b> Invoice / CustomerStated / ListPrice / Estimate /
AISuggestedUnconfirmed. Hard inputs (Invoice) are separable from soft ones.</p>
</section>

<footer>v1 pure licensing TCO. Excludes managed-services, migration/PS, Microsoft
funding, Azure consumption, and soft savings (deferred). Generated by the M365 TCO Tool.</footer>
</body></html>"""


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
            s["persona_name"], s["target_sku_reference"], s["headcount"],
            s["in_scope"], s["current_spend_annual"], s["target_spend_annual"],
            s["delta_annual"], s["current_microsoft_annual"],
            s["current_third_party_offset_annual"],
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
    wr.append(["Net TCO delta (annual)", rollup["net_tco_delta_annual"]])
    wr.append(["Residual third-party cost (annual)", rollup["residual_third_party_cost_annual"]])
    wr.append(["In-scope persona headcount", pop["in_scope_persona_headcount"]])
    wr.append(["Third-party covered population", pop["third_party_covered_population"]])
    wr.append(["Fully eliminated tools", ", ".join(rollup["fully_eliminated_tools"])])
    wr.append([
        "Eliminated renewal cycles",
        ", ".join(r["third_party_product_name"] for r in rollup["eliminated_renewal_cycles"]),
    ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
