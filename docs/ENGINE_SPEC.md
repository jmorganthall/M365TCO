# Reconciliation Engine Specification (language-neutral)

This is the authoritative, platform-independent specification of the M365 TCO
reconciliation engine (PRD Section 6). The Python implementation in
`backend/tco_engine/` is one rendering of it. A SharePoint / Power Platform /
Dataverse port reimplements **this algorithm** against the Section 5 data model
and must reproduce identical numbers. The code does not port; the model and the
algorithm port.

All money is annualized USD. Period normalization (monthly → annual) happens at
the data layer on input, never inside the engine.

## Inputs (hydrated)

- **Personas**: `{id, name, headcount}`.
- **CurrentLicenseLines**: `{quantity_assigned, unit_price_paid_annual, persona_ids}`.
  A line may apply to several personas; its cost is distributed across their
  combined headcount (see 6.2).
- **ThirdPartyProducts**: `{id, name, annual_cost, covered_count, is_managed,
  tooling_pct, renewal_date, delivered_outcome_ids, override, override_reason,
  residual_intent}`. `delivered_outcome_ids` are **ratified** coverage outcomes
  only.
- **PersonaScenarios**: `{id, persona_id, target_sku_reference,
  target_unit_price_annual, in_scope, target_covered_outcome_ids}`.
  `target_covered_outcome_ids` are the **ratified** outcomes the target SKU covers.
  Coverage is **binary** — an outcome is covered or it is not (there is no partial
  coverage); membership in this set is the whole signal.

> Ratified-only rule (6.6 / 5.7): unratified AI suggestions must be excluded by
> the caller before hydration. They never reach the engine, so they can never
> affect the math.

> Coverage key (SKU → Bundle → Outcomes): Microsoft SKU coverage is keyed by a
> **Bundle**, not an ambiguous SKU shortcode. The hydrator resolves a scenario's
> `target_sku_reference` (and a current license's `sku_reference`) to a bundle and
> looks up that bundle's covered outcomes, so a target and its coverage always
> speak the same language (fixes "target names a SKU the coverage map doesn't").

## Derived per-product cost (6.5 managed split)

```
effective_annual_cost = is_managed ? annual_cost * tooling_pct : annual_cost
per_unit_annual_cost  = covered_count > 0 ? effective_annual_cost / covered_count : 0
```

`tooling_pct` defaults to 0.30 and applies only when `is_managed`.

## Displacement test (6.6)

A scenario displaces a product **iff** the product delivers at least one outcome
**and** every outcome the product delivers is in the scenario's target covered
set:

```
displaces(scenario, product) =
    product.delivered_outcome_ids is non-empty
    AND product.delivered_outcome_ids ⊆ scenario.target_covered_outcome_ids
```

A product that delivers an outcome the SKU does not cover is **not** displaced by
that scenario.

## Per-product disposition (6.4), in-scope set only

```
displacing = in-scope scenarios s where displaces(s, product)
displaced_users = Σ headcount(persona(s)) for s in displacing
residual_count  = max(covered_count - displaced_users, 0)

if override == ForceFullElimination:
    disposition = FullyEliminated;  residual_count = 0;  residual_cost = 0
    (override_reason is required and prints on the readout)
elif displaced_users == 0:
    disposition = Unchanged;        residual_cost = residual_count * per_unit  # carrying cost — the customer keeps paying it
elif residual_count == 0:
    disposition = FullyEliminated;  residual_cost = 0
else:
    disposition = PartiallyReduced
    residual_cost = residual_count * per_unit_annual_cost
    if residual_intent == None: requires_residual_classification = true
```

## Per-persona scenario math (6.2 / 6.3)

```
# A current license line applies to one or more personas (its `persona_ids`). Its
# total annual cost (quantity_assigned * unit_price_paid_annual) is distributed
# across the combined headcount of that pool, so this persona's share is its
# headcount over the pool total. A single-persona line therefore counts in full,
# and a shared line is never double-counted across personas. (If the pool has zero
# total headcount, the line is split evenly by count so cost is not lost.)
#
# UNTAGGED lines (empty persona_ids) are treated as an ORG-WIDE pool: the pool is
# every persona that has a scenario, distributed by headcount. This means a
# current license the operator entered without attributing it to a persona (e.g.
# "255 Business Premium") still counts as current spend and is retired when those
# personas move to a target — rather than silently vanishing from the TCO.
current_microsoft = Σ over lines whose pool includes this persona of
      line_total * (persona.headcount / Σ headcount of the line's pool)
    where line_total = line.quantity_assigned * line.unit_price_paid_annual
      and pool = line.persona_ids if set, else all personas with a scenario

# Linear-by-user offset, CAPPED at the covered population (6.3a): for each
# product this scenario displaces, credit allocated_units * per_unit_annual_cost.
# A move can only retire seats the product actually covers, so a product's
# total credit never exceeds covered_count * per_unit (never more than the
# tool costs). When Σ headcount over the in-scope displacing scenarios exceeds
# covered_count, each scenario's units scale pro-rata by headcount
# (units = headcount * covered_count / Σ headcount — fractional units are
# expected); otherwise units = headcount. An out-of-scope scenario caps
# individually at covered_count. Money is allocated cumulatively across the
# displacing scenarios so per-product parts sum exactly (no rounding drift).
#
# Each credit also splits into redundant_today_annual — the quick-win portion,
# min(displaced_today from 6.10, total allocated units) allocated pro-rata —
# and move_unlocked_annual (the remainder), so readouts can label "free
# today" vs "unlocked by the move" without double-counting (see 6.8a).
offset = Σ (allocated_units * product.per_unit_annual_cost)
           over products where displaces(scenario, product)

current_spend_annual = current_microsoft + offset
# Composed target (base bundle + add-ons): the hydrator unions the covered
# outcomes across the base + add-on bundles, sums their list prices, and applies
# the scenario discount to yield the net target_unit_price_annual the engine uses:
#   net = (base_list + Σ addon_list) * (1 - target_discount_pct)
# target_covered_outcome_ids is the union across base + add-ons.
# Business Premium swap (data layer): when the engagement's BP swap is active for a
# scenario (inherited, not opted out, Business Premium covers every outcome the persona
# requires, the swap actually saves, and the seat fits under the 300-seat Business cap
# — services/swap fills up to the limit, most-saving personas first), the hydrator
# substitutes the EFFECTIVE target with Business Premium (its covered outcomes + catalog
# price × (1 - discount)) before the engine runs. The engine math below is unchanged —
# it consumes whichever target the data layer resolved.
target_spend_annual  = persona.headcount * scenario.target_unit_price_annual
delta_annual         = target_spend_annual - current_spend_annual   # +cost / -saving
```

Cost-change convention (`delta = new − old`): a **positive** delta means the move
costs MORE (a cost increase); a **negative** delta means it costs LESS (a
hard-dollar saving). Saving money is the good outcome, so readouts show negative
deltas in green and positive deltas neutrally — spending more is shown honestly,
not as an error. The M365 uplift can exceed the third-party cost it offsets, which
is a legitimate positive (cost-increase) delta.

> Recommend-a-path (best-bundle optimizer): the optimizer (`tco_engine/optimizer.py`)
> evaluates *composed* candidates, not raw SKUs. The hydrator builds one candidate
> per staple base bundle by adding the **cheapest add-ons that close that base's
> capability gaps** (required outcomes the base does not cover), where an add-on is
> applicable when it is **eligible for the base** — à-la-carte add-ons (no eligibility
> rows) apply to any base; otherwise the base must be in the add-on's eligibility set
> (`AddonEligibility`, e.g. E5 Security → E3). The candidate's
> covered set is the union (base ∪ chosen add-ons) and its price is the sum, so a
> recommendation reads as "E3 + E5 Security" rather than a single line. The
> displacement test and linear-by-user offset are unchanged and applied to the
> composed covered set. The **required** set (what a candidate must cover or show a
> gap) is the union of the outcomes the persona's current Microsoft licenses deliver
> **and** the persona's declared required capabilities (`PersonaRequirement`, the
> Personas tab) — so a needed capability with no current license still forces a gap.
>
> Seat-cap gate: candidates in a seat-capped bundle family (a `LicenseLimit` of type
> `max_total_seats`, e.g. Microsoft 365 Business ≤ 300 seats/tenant) are gated by the
> remaining headroom. The data layer (`services/limits.seat_cap_context`) computes the
> seats already committed to that family — current-license seats **plus** the headcount
> of every OTHER in-scope persona scenario whose target is in the family — and passes
> `cap_headroom = cap − consumed` per capped reference. A candidate whose persona
> headcount exceeds its headroom is flagged `cap_limited` and, like a gapped or unpriced
> candidate, is returned/shown but never recommended; the recommendation falls to the
> next-best bundle that is not seat-capped. The gate is applied only when the engagement
> opts in (`Engagement.business_cap_enabled`); the pure optimizer stays cap-agnostic
> unless the caller supplies headroom, so the language-neutral math is unchanged.

## Rollup (6.8) with integrity rules (6.9)

```
net_tco_delta_annual = Σ delta_annual over IN-SCOPE scenarios
fully_eliminated_tools = products with disposition == FullyEliminated

# Rule 1 — renewal-elimination gating: a renewal cycle is eliminated ONLY when
# its product is FullyEliminated. Any residual users → the product still renews.
eliminated_renewal_cycles = renewal entries of FullyEliminated products only

residual_third_party_cost_annual = Σ residual_cost over PartiallyReduced AND
                                   Unchanged products   # what the customer keeps paying

population_check = {
    in_scope_persona_headcount: Σ headcount of in-scope scenarios' personas,
    third_party_covered_population: Σ covered_count over all products
}

# Spend bridge — the components that build to net_tco_delta_annual over the
# IN-SCOPE set, so a readout can show existing spend (Microsoft + third-party)
# → target Microsoft → net delta. These are the same per-scenario numbers the
# net delta already sums, regrouped; the identity below holds by construction.
existing_microsoft_annual   = Σ current_microsoft_annual   over in-scope scenarios
existing_third_party_annual = Σ current_third_party_offset over in-scope scenarios
target_microsoft_annual     = Σ target_spend_annual        over in-scope scenarios

net_tco_delta_annual = target_microsoft_annual
                     - existing_microsoft_annual
                     - existing_third_party_annual

# Per-product freed-up savings: the in-scope offsets aggregated by product, so a
# readout can name each displaced tool and the dollars it frees ("we retired
# SentinelOne, saving $X"). Sums to existing_third_party_annual. A product with
# covered_count 0 has per-unit cost 0, so its credited_annual is 0 even when it
# is displaced/eliminated — the freed dollars are 0 until a covered population
# is entered.
freed_third_party = [ {product,
                       credited_annual        = Σ its in-scope offsets (capped, 6.3a),
                       redundant_today_annual = the quick-win portion (6.3a),
                       move_unlocked_annual   = credited − redundant_today} ]

# 6.8a Headline decomposition — "retire today" vs "the moves", never
# double-counting a dollar:
freed_redundant_today_annual  = Σ redundant_today_annual over freed_third_party
move_incremental_delta_annual = net_tco_delta_annual + freed_redundant_today_annual
# (per scenario: move_incremental_delta_annual = delta_annual + Σ its offsets'
#  redundant_today_annual). The identity holds by construction:
#  net = move_incremental − freed_redundant_today.
```

> Rule 2 — override disclosure: a ForceFullElimination override asserts savings
> on users the data did not displace; it is permitted, requires a reason, and the
> reason prints on the readout. An intended residual (e.g. Okta users who are not
> M365 users) is recorded as `residual_intent = IntendedOutOfScope` and is **not**
> an override. When a residual exists the tool must force the operator to choose
> which case applies (`requires_residual_classification`).

## Quick wins — duplicates the current licensing already covers (6.10)

```
# Scenario-independent. current_covered = union of covered_outcome_ids across all
# current license lines (each line's bundle's ratified Microsoft coverage). A
# third-party product is a QUICK WIN when it delivers ≥1 outcome and ALL of its
# delivered outcomes are already in current_covered — the customer is paying twice
# and can drop it TODAY, with no move ("save $X today without doing anything").
for product where delivered_outcome_ids ⊆ current_covered and delivered ≠ ∅:
    covered_pop      = Σ quantity_assigned of current lines whose covered_outcome_ids
                       ⊇ product.delivered_outcome_ids            # seats already covered
    displaced_today  = min(product.covered_count, covered_pop)
    credited_annual  = displaced_today * product.per_unit_annual_cost   # effective basis
    residual_today   = covered_count - displaced_today
quick_win_savings_annual = Σ credited_annual over quick-win products (credited > 0)
```

Quick wins are surfaced as their own readout section and as the "save today"
headline. In the spend bridge, each freed third-party product is tagged
`already_covered` (it's a quick win) so the "existing third-party freed up" line
splits into *already covered by current licensing* vs *additionally freed by the
move* — the two sum to the same total, so the bridge still builds to the net delta.

> Presentation note (no engine math): the readout surfaces render the bridge
> **per persona** — one column per in-scope scenario plus a Total — by regrouping
> the same per-scenario values the rollup already sums (`target_spend_annual`,
> `current_microsoft_annual`, the per-product `offsets`, `delta_annual`). The
> bridge identity therefore holds per column as well as in total; the engine
> emits no separate per-persona bridge structure, so there is nothing to keep
> in sync. The readout **headline** is `net_tco_delta_annual ×
> engagement.modeling_horizon_years` (e.g. "36-month savings") — a presentation
> multiplication; every engine quantity remains annualized.

## Recompute is total, not incremental (6.7)

Toggling a scenario in/out of scope recomputes **all** dispositions from scratch.
A product can flip FullyEliminated → PartiallyReduced when a persona is removed.

## Worked example (Okta 500-vs-450)

Okta covers 500 at effective per-unit cost $100/yr (`$50,000 / 500`). 450
Knowledge Workers displace it.

- `displaced_users = 450`, `residual_count = 50`, disposition `PartiallyReduced`,
  `residual_cost = 50 * 100 = $5,000`.
- Persona offset credited = `450 * 100 = $45,000`.
- Renewal **not** eliminated (partial). This case is covered by the unit tests in
  `backend/tests/test_engine.py`.
