"""Serialize EngineResult dataclasses to JSON-friendly dicts for the API."""

from __future__ import annotations

from decimal import Decimal

from tco_engine.engine import EngineResult


def _num(value) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return value


def result_to_dict(result: EngineResult) -> dict:
    return {
        "scenarios": [
            {
                "scenario_id": s.scenario_id,
                "persona_id": s.persona_id,
                "persona_name": s.persona_name,
                "headcount": s.headcount,
                "target_sku_reference": s.target_sku_reference,
                "in_scope": s.in_scope,
                "current_spend_annual": _num(s.current_spend_annual),
                "target_spend_annual": _num(s.target_spend_annual),
                "delta_annual": _num(s.delta_annual),
                "current_microsoft_annual": _num(s.current_microsoft_annual),
                "current_third_party_offset_annual": _num(
                    s.current_third_party_offset_annual
                ),
                "offsets": [
                    {
                        "third_party_product_id": o.third_party_product_id,
                        "third_party_product_name": o.third_party_product_name,
                        "per_unit_annual_cost": _num(o.per_unit_annual_cost),
                        "credited_units": o.credited_units,
                        "credited_offset_annual": _num(o.credited_offset_annual),
                    }
                    for o in s.offsets
                ],
            }
            for s in result.scenarios
        ],
        "dispositions": [
            {
                "third_party_product_id": d.third_party_product_id,
                "third_party_product_name": d.third_party_product_name,
                "covered_count": d.covered_count,
                "displaced_users": d.displaced_users,
                "disposition": d.disposition.value,
                "residual_count": d.residual_count,
                "residual_annual_cost": _num(d.residual_annual_cost),
                "per_unit_annual_cost": _num(d.per_unit_annual_cost),
                "effective_annual_cost": _num(d.effective_annual_cost),
                "is_managed": d.is_managed,
                "tooling_pct": _num(d.tooling_pct),
                "override": d.override.value,
                "override_reason": d.override_reason,
                "residual_intent": d.residual_intent.value,
                "renewal_date": d.renewal_date,
                "requires_residual_classification": d.requires_residual_classification,
            }
            for d in result.dispositions
        ],
        "rollup": {
            "net_tco_delta_annual": _num(result.rollup.net_tco_delta_annual),
            "fully_eliminated_tools": result.rollup.fully_eliminated_tools,
            "eliminated_renewal_cycles": [
                {
                    "third_party_product_id": r.third_party_product_id,
                    "third_party_product_name": r.third_party_product_name,
                    "renewal_date": r.renewal_date,
                }
                for r in result.rollup.eliminated_renewal_cycles
            ],
            "residual_third_party_cost_annual": _num(
                result.rollup.residual_third_party_cost_annual
            ),
            "existing_microsoft_annual": _num(result.rollup.existing_microsoft_annual),
            "existing_third_party_annual": _num(
                result.rollup.existing_third_party_annual
            ),
            "target_microsoft_annual": _num(result.rollup.target_microsoft_annual),
            "freed_third_party": [
                {
                    "third_party_product_id": f.third_party_product_id,
                    "third_party_product_name": f.third_party_product_name,
                    "credited_annual": _num(f.credited_annual),
                }
                for f in result.rollup.freed_third_party
            ],
            "population_check": {
                "in_scope_persona_headcount": result.rollup.in_scope_persona_headcount,
                "third_party_covered_population": result.rollup.third_party_covered_population,
            },
        },
    }
