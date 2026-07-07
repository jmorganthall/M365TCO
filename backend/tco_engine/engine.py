"""The reconciliation engine — PRD Section 6, implemented exactly.

Pure functions. No I/O. `compute(engagement)` returns an EngineResult.

The math is deterministic and total (not incremental): toggling a scenario in
or out triggers a full recompute so no stale dispositions survive (Section 6.7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .models import (
    Coverage,
    Disposition,
    Engagement,
    Override,
    Persona,
    PersonaScenario,
    ResidualIntent,
    ThirdPartyProduct,
)

CENTS = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENTS)


@dataclass
class OffsetDetail:
    """One displaced third-party product credited against a persona scenario."""

    third_party_product_id: str
    third_party_product_name: str
    per_unit_annual_cost: Decimal
    credited_units: int
    credited_offset_annual: Decimal


@dataclass
class ScenarioResult:
    scenario_id: str
    persona_id: str
    persona_name: str
    headcount: int
    target_sku_reference: str
    in_scope: bool
    current_spend_annual: Decimal
    target_spend_annual: Decimal
    delta_annual: Decimal
    current_microsoft_annual: Decimal
    current_third_party_offset_annual: Decimal
    offsets: list[OffsetDetail] = field(default_factory=list)


@dataclass
class ProductDispositionResult:
    third_party_product_id: str
    third_party_product_name: str
    covered_count: int
    displaced_users: int
    disposition: Disposition
    residual_count: int
    residual_annual_cost: Decimal
    per_unit_annual_cost: Decimal
    effective_annual_cost: Decimal
    is_managed: bool
    tooling_pct: Decimal
    override: Override
    override_reason: str
    residual_intent: ResidualIntent
    renewal_date: Optional[str]
    # True when a residual exists but the user has not classified it as either
    # an override or an intended out-of-scope residual (Section 6.9 rule 2).
    requires_residual_classification: bool


@dataclass
class EliminatedRenewal:
    third_party_product_id: str
    third_party_product_name: str
    renewal_date: Optional[str]


@dataclass
class FreedThirdParty:
    """A third-party product's credited savings, aggregated across the in-scope
    scenarios that displace it. This is the persona-allocated offset the engine
    credits today — the "we freed up SentinelOne, saving $X" line. When
    `credited_annual` is 0 the product delivers a covered outcome but its
    `covered_count` (and thus per-unit cost) is 0, so no dollars are freed."""

    third_party_product_id: str
    third_party_product_name: str
    credited_annual: Decimal


@dataclass
class RollupResult:
    net_tco_delta_annual: Decimal
    fully_eliminated_tools: list[str]
    eliminated_renewal_cycles: list[EliminatedRenewal]
    residual_third_party_cost_annual: Decimal
    in_scope_persona_headcount: int
    third_party_covered_population: int
    # Spend bridge (Section 6.8): the components that build to net_tco_delta_annual
    # over the IN-SCOPE set, so the readout can show existing spend (Microsoft +
    # third-party) → target Microsoft → net delta. By construction
    #   net_tco_delta_annual = existing_microsoft_annual
    #                        + existing_third_party_annual
    #                        - target_microsoft_annual
    existing_microsoft_annual: Decimal
    existing_third_party_annual: Decimal
    target_microsoft_annual: Decimal
    freed_third_party: list[FreedThirdParty]


@dataclass
class EngineResult:
    scenarios: list[ScenarioResult]
    dispositions: list[ProductDispositionResult]
    rollup: RollupResult


def _scenario_displaces_product(
    scenario: PersonaScenario, product: ThirdPartyProduct
) -> bool:
    """Section 6.6 displacement test.

    A target SKU displaces a product when, for every outcome the product
    delivers, the target SKU has a ratified Full or Partial coverage entry.
    Unratified AI suggestions are excluded by the caller (they never reach the
    hydrated sets). A product that delivers no outcomes is treated as NOT
    displaceable — there is nothing the SKU can be shown to cover.
    """
    if not product.delivered_outcome_ids:
        return False
    return product.delivered_outcome_ids.issubset(scenario.target_covered_outcome_ids)


def compute(engagement: Engagement) -> EngineResult:
    personas: dict[str, Persona] = {p.id: p for p in engagement.personas}

    # ----- Per-product dispositions (Section 6.4), in-scope set only -----
    # Precompute, per product, the in-scope scenarios that displace it.
    dispositions: list[ProductDispositionResult] = []
    displacing_scenarios_by_product: dict[str, list[PersonaScenario]] = {}

    for product in engagement.third_party_products:
        displacing = [
            s
            for s in engagement.scenarios
            if s.in_scope
            and s.persona_id in personas
            and _scenario_displaces_product(s, product)
        ]
        displacing_scenarios_by_product[product.id] = displacing

        displaced_users = sum(personas[s.persona_id].headcount for s in displacing)
        per_unit = product.per_unit_annual_cost

        override = product.override
        residual_count = max(product.covered_count - displaced_users, 0)
        requires_classification = False

        if override == Override.FORCE_FULL_ELIMINATION:
            disposition = Disposition.FULLY_ELIMINATED
            residual_count = 0
            residual_annual_cost = Decimal("0")
        elif displaced_users == 0:
            disposition = Disposition.UNCHANGED
            residual_annual_cost = Decimal("0")
        elif residual_count == 0:
            disposition = Disposition.FULLY_ELIMINATED
            residual_annual_cost = Decimal("0")
        else:
            disposition = Disposition.PARTIALLY_REDUCED
            residual_annual_cost = _money(Decimal(residual_count) * per_unit)
            # A residual exists. Section 6.9 rule 2: the user must classify it as
            # either an override (ForceFullElimination) or an intended residual.
            if product.residual_intent == ResidualIntent.NONE:
                requires_classification = True

        dispositions.append(
            ProductDispositionResult(
                third_party_product_id=product.id,
                third_party_product_name=product.name,
                covered_count=product.covered_count,
                displaced_users=displaced_users,
                disposition=disposition,
                residual_count=residual_count,
                residual_annual_cost=residual_annual_cost,
                per_unit_annual_cost=per_unit,
                effective_annual_cost=product.effective_annual_cost,
                is_managed=product.is_managed,
                tooling_pct=product.tooling_pct,
                override=override,
                override_reason=product.override_reason,
                residual_intent=product.residual_intent,
                renewal_date=product.renewal_date,
                requires_residual_classification=requires_classification,
            )
        )

    products_by_id = {p.id: p for p in engagement.third_party_products}

    # ----- Per-persona scenario math (Section 6.2 / 6.3) -----
    scenario_results: list[ScenarioResult] = []
    for scenario in engagement.scenarios:
        persona = personas.get(scenario.persona_id)
        if persona is None:
            continue

        # Each current license applies to one or more personas; its total cost is
        # distributed across their combined headcount, so this persona's share is
        # (its headcount / the tagged personas' total headcount). A single-persona
        # line therefore counts in full, and a shared line is never double-counted
        # across personas (Section 6.2).
        current_ms = Decimal("0")
        for line in engagement.current_licenses:
            if persona.id not in line.persona_ids:
                continue
            line_total = Decimal(line.quantity_assigned) * line.unit_price_paid_annual
            tagged = [pid for pid in line.persona_ids if pid in personas]
            tagged_hc = sum(personas[pid].headcount for pid in tagged)
            if tagged_hc > 0:
                share = Decimal(persona.headcount) / Decimal(tagged_hc)
            else:  # personas with no headcount → even split so cost isn't lost
                share = Decimal(1) / Decimal(len(tagged) or 1)
            current_ms += line_total * share

        # Linear-by-user offset: each product this scenario displaces credits
        # headcount * per_unit_annual_cost (Section 6.3). This is the persona's
        # allocated share of third-party cost it consumes today.
        offsets: list[OffsetDetail] = []
        offset_total = Decimal("0")
        for product in engagement.third_party_products:
            if _scenario_displaces_product(scenario, product):
                per_unit = product.per_unit_annual_cost
                credited = _money(Decimal(persona.headcount) * per_unit)
                offset_total += credited
                offsets.append(
                    OffsetDetail(
                        third_party_product_id=product.id,
                        third_party_product_name=product.name,
                        per_unit_annual_cost=per_unit,
                        credited_units=persona.headcount,
                        credited_offset_annual=credited,
                    )
                )

        current_spend = _money(current_ms + offset_total)
        target_spend = _money(
            Decimal(persona.headcount) * scenario.target_unit_price_annual
        )
        delta = _money(current_spend - target_spend)

        scenario_results.append(
            ScenarioResult(
                scenario_id=scenario.id,
                persona_id=persona.id,
                persona_name=persona.name,
                headcount=persona.headcount,
                target_sku_reference=scenario.target_sku_reference,
                in_scope=scenario.in_scope,
                current_spend_annual=current_spend,
                target_spend_annual=target_spend,
                delta_annual=delta,
                current_microsoft_annual=_money(current_ms),
                current_third_party_offset_annual=_money(offset_total),
                offsets=offsets,
            )
        )

    # ----- Rollup (Section 6.8) with integrity rules (Section 6.9) -----
    net_delta = _money(
        sum(
            (r.delta_annual for r in scenario_results if r.in_scope),
            Decimal("0"),
        )
    )

    fully_eliminated = [
        d for d in dispositions if d.disposition == Disposition.FULLY_ELIMINATED
    ]
    fully_eliminated_names = [d.third_party_product_name for d in fully_eliminated]

    # Rule 1: a renewal cycle is reported eliminated ONLY for fully-eliminated
    # products. A product with any residual still renews.
    eliminated_renewals = [
        EliminatedRenewal(
            third_party_product_id=d.third_party_product_id,
            third_party_product_name=d.third_party_product_name,
            renewal_date=d.renewal_date,
        )
        for d in fully_eliminated
        if d.renewal_date
    ]

    residual_cost = _money(
        sum(
            (
                d.residual_annual_cost
                for d in dispositions
                if d.disposition == Disposition.PARTIALLY_REDUCED
            ),
            Decimal("0"),
        )
    )

    in_scope_headcount = sum(
        personas[s.persona_id].headcount
        for s in engagement.scenarios
        if s.in_scope and s.persona_id in personas
    )
    covered_population = sum(
        p.covered_count for p in engagement.third_party_products
    )

    # Spend bridge over the in-scope set. The three totals build to net_delta:
    # existing Microsoft + existing third-party (the credited offset) − target
    # Microsoft. Summing the same per-scenario numbers the rollup already sums
    # keeps the bridge exactly consistent with net_tco_delta_annual.
    in_scope_results = [r for r in scenario_results if r.in_scope]
    existing_microsoft = _money(
        sum((r.current_microsoft_annual for r in in_scope_results), Decimal("0"))
    )
    existing_third_party = _money(
        sum((r.current_third_party_offset_annual for r in in_scope_results), Decimal("0"))
    )
    target_microsoft = _money(
        sum((r.target_spend_annual for r in in_scope_results), Decimal("0"))
    )

    # Aggregate the per-scenario offsets by product so the readout can name each
    # freed-up tool and its credited savings (summing to existing_third_party).
    freed_by_product: dict[str, FreedThirdParty] = {}
    for r in in_scope_results:
        for o in r.offsets:
            entry = freed_by_product.get(o.third_party_product_id)
            if entry is None:
                freed_by_product[o.third_party_product_id] = FreedThirdParty(
                    third_party_product_id=o.third_party_product_id,
                    third_party_product_name=o.third_party_product_name,
                    credited_annual=o.credited_offset_annual,
                )
            else:
                entry.credited_annual = _money(
                    entry.credited_annual + o.credited_offset_annual
                )
    freed_third_party = sorted(
        freed_by_product.values(),
        key=lambda f: (-f.credited_annual, f.third_party_product_name),
    )

    rollup = RollupResult(
        net_tco_delta_annual=net_delta,
        fully_eliminated_tools=fully_eliminated_names,
        eliminated_renewal_cycles=eliminated_renewals,
        residual_third_party_cost_annual=residual_cost,
        in_scope_persona_headcount=in_scope_headcount,
        third_party_covered_population=covered_population,
        existing_microsoft_annual=existing_microsoft,
        existing_third_party_annual=existing_third_party,
        target_microsoft_annual=target_microsoft,
        freed_third_party=freed_third_party,
    )

    return EngineResult(
        scenarios=scenario_results,
        dispositions=dispositions,
        rollup=rollup,
    )
