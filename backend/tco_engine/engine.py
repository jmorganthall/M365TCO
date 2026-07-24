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
    """One displaced third-party product credited against a persona scenario.

    Credits are CAPPED at the product's covered population (Section 6.3a): a
    move can only retire seats the product actually covers, so when the
    displacing personas' combined headcount exceeds covered_count each
    scenario's credited units scale pro-rata by headcount (hence Decimal).
    The credit further splits into the portion that is redundant TODAY (the
    quick-win seats — current licensing already covers them) and the portion
    the move itself unlocks, so readouts reconcile exactly with Quick Wins."""

    third_party_product_id: str
    third_party_product_name: str
    per_unit_annual_cost: Decimal
    credited_units: Decimal
    credited_offset_annual: Decimal
    redundant_today_annual: Decimal = Decimal("0")
    move_unlocked_annual: Decimal = Decimal("0")


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
    # The move's OWN value (Section 6.3a): delta_annual with the quick-win
    # (redundant-today) credit added back — what changing this persona's
    # licensing contributes beyond savings available with no move at all.
    move_incremental_delta_annual: Decimal = Decimal("0")
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
    credits — the "we retired SentinelOne, saving $X" line — capped at the
    product's covered population (6.3a). When `credited_annual` is 0 the
    product delivers a covered outcome but its `covered_count` (and thus
    per-unit cost) is 0, so no dollars are credited.

    `credited_annual = redundant_today_annual + move_unlocked_annual`:
    the first is the quick-win portion (seats whose CURRENT licensing already
    duplicates the tool — retirable with no move), the second is what the move
    itself unlocks. The split lets a readout show "free today" with exactly
    the Quick Wins total and never label move-dependent dollars as free."""

    third_party_product_id: str
    third_party_product_name: str
    credited_annual: Decimal
    # True when the customer's CURRENT licensing already covers this product's
    # outcomes — i.e. it's a quick win (redundant today), not value the move adds.
    already_covered: bool = False
    redundant_today_annual: Decimal = Decimal("0")
    move_unlocked_annual: Decimal = Decimal("0")


@dataclass
class QuickWin:
    """A third-party product whose every delivered outcome is ALREADY covered by
    the customer's current Microsoft licensing — a duplicate they can drop today,
    with no scenario move. `credited_annual` is the saving on the overlapping
    population (min of the product's covered_count and the seats that already hold
    a covering license), on the effective (managed-split) cost basis."""

    third_party_product_id: str
    third_party_product_name: str
    duplicated_outcome_ids: list[str]
    covered_count: int
    displaced_today: int
    residual_today: int
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
    # over the IN-SCOPE set, so the readout can show the new target Microsoft cost
    # minus the existing spend it retires → the net change. By construction, with
    # the cost-change convention (delta = new − old):
    #   net_tco_delta_annual = target_microsoft_annual
    #                        - existing_microsoft_annual
    #                        - existing_third_party_annual
    existing_microsoft_annual: Decimal
    existing_third_party_annual: Decimal
    target_microsoft_annual: Decimal
    freed_third_party: list[FreedThirdParty]
    # Quick wins: third-party spend already duplicated by the CURRENT licensing,
    # savable today without any move. quick_win_savings_annual is the "save $X
    # today" headline; quick_wins lists the redundant products.
    quick_win_savings_annual: Decimal
    quick_wins: list[QuickWin]
    # Headline decomposition (Section 6.8a): the quick-win portion of the freed
    # credit (seats redundant today — no move required) and the moves' OWN
    # value with that portion added back. By construction:
    #   net_tco_delta_annual = move_incremental_delta_annual
    #                        - freed_redundant_today_annual
    # so "retire today" + "the moves" never double-count a dollar.
    freed_redundant_today_annual: Decimal = Decimal("0")
    move_incremental_delta_annual: Decimal = Decimal("0")


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
            # The move doesn't touch this tool — the customer keeps paying it.
            # Its carrying cost is real residual spend (Section 6.4): $0 here
            # would understate what remains and inflate the savings story.
            disposition = Disposition.UNCHANGED
            residual_annual_cost = _money(Decimal(residual_count) * per_unit)
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

    # ----- Quick wins: duplicates the CURRENT licensing already covers (6.10) -----
    # Scenario-independent: outcomes the existing Microsoft licensing already
    # delivers, and — per product — the seats that already hold a license covering
    # all its outcomes. A product fully covered today is a duplicate the customer
    # can drop now ("save $X today without doing anything").
    current_covered_all: set[str] = set()
    for line in engagement.current_licenses:
        current_covered_all |= set(line.covered_outcome_ids)

    quick_wins: list[QuickWin] = []
    for product in engagement.third_party_products:
        outs = product.delivered_outcome_ids
        if not outs or not (set(outs) <= current_covered_all):
            continue
        # Seats already holding a current license that covers ALL this product's
        # outcomes — the overlapping (redundant) population.
        covered_pop = sum(
            line.quantity_assigned
            for line in engagement.current_licenses
            if set(outs) <= set(line.covered_outcome_ids)
        )
        displaced_today = min(product.covered_count, covered_pop)
        credited = _money(Decimal(displaced_today) * product.per_unit_annual_cost)
        if credited <= 0:
            continue
        quick_wins.append(
            QuickWin(
                third_party_product_id=product.id,
                third_party_product_name=product.name,
                duplicated_outcome_ids=sorted(outs),
                covered_count=product.covered_count,
                displaced_today=displaced_today,
                residual_today=max(product.covered_count - displaced_today, 0),
                credited_annual=credited,
            )
        )
    quick_win_ids = {q.third_party_product_id for q in quick_wins}
    quick_win_savings = _money(
        sum((q.credited_annual for q in quick_wins), Decimal("0"))
    )

    # ----- Displacement credit allocation (Section 6.3a) -----
    # A move can only retire seats the product actually covers, so a product's
    # total credit caps at covered_count × per_unit (never more than the tool
    # costs). When the displacing personas' combined headcount exceeds the
    # covered population, each scenario's units scale pro-rata by headcount.
    # Each credit also splits into the portion redundant TODAY (the quick-win
    # seats) vs what the move unlocks, allocated cumulatively so every
    # product's parts sum exactly to its capped total (no rounding drift).
    qw_today = {q.third_party_product_id: q.displaced_today for q in quick_wins}
    offset_alloc: dict[tuple[str, str], tuple[Decimal, Decimal, Decimal]] = {}
    for product in engagement.third_party_products:
        displacing = displacing_scenarios_by_product[product.id]
        if not displacing:
            continue
        per_unit = product.per_unit_annual_cost
        covered = Decimal(product.covered_count)
        total_hc = sum(personas[s.persona_id].headcount for s in displacing)
        if product.covered_count == 0 or total_hc == 0:
            scale = Decimal(0)
        elif total_hc <= product.covered_count:
            scale = Decimal(1)
        else:
            scale = covered / Decimal(total_hc)
        effective_units = min(Decimal(total_hc), covered)
        today_units = min(Decimal(qw_today.get(product.id, 0)), effective_units)
        cum_units = Decimal(0)
        assigned_credit = Decimal(0)
        assigned_today = Decimal(0)
        for s in displacing:
            hc = Decimal(personas[s.persona_id].headcount)
            cum_units += hc * scale
            credit_target = _money(cum_units * per_unit)
            credit = credit_target - assigned_credit
            assigned_credit = credit_target
            if effective_units > 0:
                today_target = _money(
                    today_units * cum_units / effective_units * per_unit
                )
            else:
                today_target = Decimal("0.00")
            today = today_target - assigned_today
            assigned_today = today_target
            offset_alloc[(product.id, s.id)] = (
                (hc * scale).quantize(Decimal("0.0001")),
                credit,
                today,
            )

    # ----- Per-persona scenario math (Section 6.2 / 6.3) -----
    # Personas that have a scenario (appear in the readout). An UNTAGGED current
    # license — one the operator entered without attributing to a persona — is
    # treated as an org-wide pool distributed across these, so it still counts as
    # current spend and is retired when they move, instead of silently vanishing.
    scenario_persona_ids = {
        s.persona_id for s in engagement.scenarios if s.persona_id in personas
    }

    scenario_results: list[ScenarioResult] = []
    for scenario in engagement.scenarios:
        persona = personas.get(scenario.persona_id)
        if persona is None:
            continue

        # Each current license applies to one or more personas; its total cost is
        # distributed across their combined headcount, so this persona's share is
        # (its headcount / the pool's total headcount). A single-persona line
        # therefore counts in full, and a shared line is never double-counted
        # across personas (Section 6.2). An untagged line's pool is every persona
        # with a scenario (org-wide), so it is attributed and retired rather than
        # dropped.
        current_ms = Decimal("0")
        for line in engagement.current_licenses:
            if line.persona_ids:
                if persona.id not in line.persona_ids:
                    continue
                pool = [pid for pid in line.persona_ids if pid in personas]
            else:  # untagged -> org-wide pool of scenario personas
                if persona.id not in scenario_persona_ids:
                    continue
                pool = list(scenario_persona_ids)
            line_total = Decimal(line.quantity_assigned) * line.unit_price_paid_annual
            pool_hc = sum(personas[pid].headcount for pid in pool)
            if pool_hc > 0:
                share = Decimal(persona.headcount) / Decimal(pool_hc)
            else:  # personas with no headcount → even split so cost isn't lost
                share = Decimal(1) / Decimal(len(pool) or 1)
            current_ms += line_total * share

        # Linear-by-user offset, capped at the covered population (Sections
        # 6.3 / 6.3a): each product this scenario displaces credits its
        # allocated units × per_unit_annual_cost. In-scope scenarios use the
        # pro-rata allocation precomputed above; an out-of-scope what-if row
        # caps individually at covered_count (it has no share of the pool).
        offsets: list[OffsetDetail] = []
        offset_total = Decimal("0")
        for product in engagement.third_party_products:
            if _scenario_displaces_product(scenario, product):
                per_unit = product.per_unit_annual_cost
                alloc = offset_alloc.get((product.id, scenario.id))
                if alloc is not None:
                    units, credited, today = alloc
                else:
                    units = Decimal(min(persona.headcount, product.covered_count))
                    credited = _money(units * per_unit)
                    today = Decimal("0.00")
                offset_total += credited
                offsets.append(
                    OffsetDetail(
                        third_party_product_id=product.id,
                        third_party_product_name=product.name,
                        per_unit_annual_cost=per_unit,
                        credited_units=units,
                        credited_offset_annual=credited,
                        redundant_today_annual=today,
                        move_unlocked_annual=_money(credited - today),
                    )
                )

        current_spend = _money(current_ms + offset_total)
        target_spend = _money(
            Decimal(persona.headcount) * scenario.target_unit_price_annual
        )
        # Cost-change convention (Section 6.7): delta = new − old.
        #   delta > 0  → spending MORE (a cost increase)
        #   delta < 0  → spending LESS (a hard-dollar saving)
        # Saving money is the good outcome, so a negative delta is the win.
        delta = _money(target_spend - current_spend)

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
                move_incremental_delta_annual=_money(
                    delta + sum((o.redundant_today_annual for o in offsets), Decimal("0"))
                ),
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

    # What the customer keeps paying after the move: partial residuals PLUS the
    # carrying cost of tools the move doesn't touch (Unchanged). Excluding the
    # untouched tools would understate remaining spend (Section 6.8).
    residual_cost = _money(
        sum(
            (
                d.residual_annual_cost
                for d in dispositions
                if d.disposition
                in (Disposition.PARTIALLY_REDUCED, Disposition.UNCHANGED)
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
    # target Microsoft − existing Microsoft − existing third-party (the credited
    # offset). Summing the same per-scenario numbers the rollup already sums keeps
    # the bridge exactly consistent with net_tco_delta_annual (delta = new − old).
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
                    already_covered=o.third_party_product_id in quick_win_ids,
                    redundant_today_annual=o.redundant_today_annual,
                    move_unlocked_annual=o.move_unlocked_annual,
                )
            else:
                entry.credited_annual = _money(
                    entry.credited_annual + o.credited_offset_annual
                )
                entry.redundant_today_annual = _money(
                    entry.redundant_today_annual + o.redundant_today_annual
                )
                entry.move_unlocked_annual = _money(
                    entry.move_unlocked_annual + o.move_unlocked_annual
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
        quick_win_savings_annual=quick_win_savings,
        quick_wins=quick_wins,
        freed_redundant_today_annual=_money(
            sum((f.redundant_today_annual for f in freed_third_party), Decimal("0"))
        ),
        move_incremental_delta_annual=_money(
            net_delta
            + sum((f.redundant_today_annual for f in freed_third_party), Decimal("0"))
        ),
    )

    return EngineResult(
        scenarios=scenario_results,
        dispositions=dispositions,
        rollup=rollup,
    )
