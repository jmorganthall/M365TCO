"""Trace a persona's numbers back to what was entered (docs/LAYER_AUDIT.md Phase 4).

For one persona, every figure the readout shows for it — Microsoft licences today,
the tools its move retires, its recommended plan, the change — as three layers:

  calculated   the engine's figure (services/results.engine_result)
  interpreted  the derived values it was built from (tco_engine.interpret),
               read-only and labelled derived
  raw          the entries behind those, with the walkthrough step that edits them

and the rule applied at each hop, in the words of ENGINE_SPEC. Each figure's parts
add up to it. Reads only.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from .. import models
from . import raw, results, review

RULES = {
    "today_microsoft": (
        "Each Microsoft licence line that applies to the persona — tagged to it, or "
        "untagged, which means every persona — is split across the personas it applies "
        "to by headcount. Its cost is assigned seats × the effective price: the "
        "negotiated price when one is entered, else the list price. (ENGINE_SPEC 6.2)"),
    "tools_retired": (
        "A tool retires for the persona when the persona holds it and the plan covers "
        "every outcome the tool delivers. The credit is the persona's seats × the "
        "tool's cost per person, never more seats than the tool covers. Cost per "
        "person is the effective cost (a managed service counts only its software "
        "share) ÷ the people it covers. (ENGINE_SPEC 6.3a, 6.5, 6.6)"),
    "target": (
        "The plan costs headcount × the net price per seat. Net = (base + add-ons) × "
        "(1 − discount), or the negotiated net price when one is entered. "
        "(ENGINE_SPEC 6.2, Interpretation 6.0)"),
    "change": (
        "Change = the plan − (Microsoft today + the tools the move retires). Negative "
        "is a saving. (ENGINE_SPEC 6.2)"),
}


def _f(v) -> float:
    return float(v) if isinstance(v, Decimal) else (v or 0.0)


def persona_trace(db: Session, engagement_id: str, persona_id: str) -> dict:
    eng = db.get(models.Engagement, engagement_id)
    persona = db.get(models.Persona, persona_id)
    if eng is None or persona is None or persona.engagement_id != engagement_id:
        raise ValueError("Persona not found")

    i = raw.interpreted(db, engagement_id)
    result = results.engine_result(db, engagement_id)
    sr = next((s for s in result.scenarios if s.persona_id == persona_id), None)
    names = {p.id: p.name for p in i.personas}
    headcount = {p.id: p.headcount for p in i.personas}
    bundle_name = {b.id: b.name for b in db.query(models.Bundle).all()}
    lines = {l.id: l for l in eng.current_licenses}
    tools = {t.id: t for t in eng.third_party_products}

    # ---- Microsoft licences today ------------------------------------------
    licence_parts = []
    for line in i.lines_for(persona_id):
        row = lines[line.id]
        pool = sum(headcount.get(p, 0) for p in line.applies_to)
        licence_parts.append({
            "label": row.sku_reference or "A licence line",
            "value": _f(i.licence_shares.get((line.id, persona_id), 0)),
            "interpreted": {
                "annual_cost": _f(line.annual_cost),
                "effective_price_annual": _f(line.effective_price_annual),
                "price_source": line.price_source,
                "applies_to": [names.get(p, p) for p in line.applies_to],
                "applies_to_assumed": line.assumed,
                "share": f"{headcount.get(persona_id, 0):,} of {pool:,} people",
                "read_state": line.read_state,
            },
            "raw": {
                "id": row.id, "step": "groups", "sku_reference": row.sku_reference,
                "quantity_assigned": row.quantity_assigned,
                "list_price_annual": _f(row.unit_price_paid_annual),
                "price_override": bool(row.price_override),
                "overridden_price_annual": (None if row.overridden_price_annual is None
                                            else _f(row.overridden_price_annual)),
                "personas": [names.get(p, p) for p in row.persona_ids] or ["(untagged)"],
            },
        })

    figures = [{
        "key": "today_microsoft", "label": "Microsoft licences today",
        "value": _f(sr.current_microsoft_annual) if sr else sum(p["value"] for p in licence_parts),
        "rule": RULES["today_microsoft"], "parts": licence_parts,
    }]

    if sr is not None:
        # ---- Tools the move retires --------------------------------------
        tool_parts = []
        for o in sr.offsets:
            it = i.tool(o.third_party_product_id)
            row = tools[o.third_party_product_id]
            tool_parts.append({
                "label": o.third_party_product_name,
                "value": _f(o.credited_offset_annual),
                "interpreted": {
                    "credited_people": _f(o.credited_units),
                    "cost_per_person": _f(it.cost_per_person),
                    "annual_cost": _f(it.annual_cost),
                    "effective_annual_cost": _f(it.effective_annual_cost),
                    "tooling_pct": _f(it.tooling_pct), "tooling_source": it.tooling_source,
                    "people_covered": it.people_covered, "covered_source": it.covered_source,
                    "redundant_today_annual": _f(o.redundant_today_annual),
                },
                "raw": {
                    "id": row.id, "step": "tools", "name": row.name,
                    "raw_cost": _f(row.raw_cost), "cost_period": row.cost_period,
                    "is_managed": bool(row.is_managed),
                    "tooling_pct": None if row.tooling_pct is None else _f(row.tooling_pct),
                    "covered_count_override": row.covered_count_override,
                    "personas": [names.get(p, p) for p in row.persona_ids] or ["(untagged)"],
                },
            })
        figures.append({
            "key": "tools_retired", "label": "Tools the move retires",
            "value": _f(sr.current_third_party_offset_annual),
            "rule": RULES["tools_retired"], "parts": tool_parts,
        })

        # ---- The recommended plan ----------------------------------------
        isc = next(s for s in i.scenarios if s.id == sr.scenario_id)
        srow = db.get(models.PersonaScenario, sr.scenario_id)
        figures.append({
            "key": "target", "label": "Recommended plan",
            "value": _f(sr.target_spend_annual), "rule": RULES["target"],
            "parts": [{
                "label": f"{sr.headcount:,} people × net per seat",
                "value": _f(sr.target_spend_annual),
                "interpreted": {
                    "headcount": sr.headcount,
                    "net_annual": _f(isc.net_annual), "price_source": isc.price_source,
                    "composed_list_annual": _f(isc.composed_list_annual),
                    "pct_off_list": _f(isc.pct_off_list),
                    "plan": bundle_name.get(isc.base_bundle_id, isc.target_sku_reference),
                    "plan_source": isc.plan_source,
                    "add_ons": [bundle_name.get(b, b) for b in isc.addon_bundle_ids],
                    "in_scope": isc.in_scope,
                },
                "raw": {
                    "id": srow.id, "step": "future",
                    "target_sku_reference": srow.target_sku_reference,
                    "picked_plan": bundle_name.get(srow.bundle_id) if srow.bundle_id else None,
                    "list_price_annual": _f(srow.target_unit_price_annual),
                    "add_on_list_prices": {bundle_name.get(a.bundle_id, a.bundle_id): _f(a.unit_price_annual)
                                           for a in srow.addons},
                    "discount_pct": None if srow.target_discount_pct is None else _f(srow.target_discount_pct),
                    "price_override": bool(srow.price_override),
                    "overridden_price_annual": (None if srow.overridden_price_annual is None
                                                else _f(srow.overridden_price_annual)),
                },
            }],
        })
        figures.append({
            "key": "change", "label": "Change", "value": _f(sr.delta_annual),
            "rule": RULES["change"], "parts": [],
        })

    # ---- Open questions that touch this persona (Review's wording) -------
    # A finding about the persona itself, or about a licence or tool it holds.
    its_lines = {l.id for l in i.lines_for(persona_id)}
    its_tools = {t for (t, p) in i.tool_shares if p == persona_id}
    questions = [
        {"code": c["code"], "step": c["step"], "severity": c["severity"], "message": c["message"]}
        for c in review.review(db, engagement_id)["checks"]
        if c.get("persona_id") == persona_id or persona_id in (c.get("persona_ids") or ())
        or c.get("licence_id") in its_lines or c.get("tool_id") in its_tools
    ]

    headline = result.rollup.headline
    items = [{
        "label": t.label, "sub_line": t.sub_line, "annual_amount": _f(t.annual_amount),
        "start_month": t.start_month, "timed_by": t.timed_by, "date_missing": t.date_missing,
        "amount_over_horizon": _f(t.amount),
    } for t in (headline.items if headline else []) if t.persona_id == persona_id]

    return {
        "persona": {"id": persona.id, "name": persona.name, "headcount": persona.headcount,
                    "step": "groups", "has_plan": sr is not None,
                    "in_scope": bool(sr.in_scope) if sr else False},
        "figures": figures,
        "headline_items": items,
        "questions": questions,
    }
