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
    disposition = Unchanged;        residual_cost = 0
elif residual_count == 0:
    disposition = FullyEliminated;  residual_cost = 0
else:
    disposition = PartiallyReduced
    residual_cost = residual_count * per_unit_annual_cost
    if residual_intent == None: requires_residual_classification = true
```

## Per-persona scenario math (6.2 / 6.3)

```
# A current license line applies to one or more personas. Its total annual cost
# (quantity_assigned * unit_price_paid_annual) is distributed across the combined
# headcount of its tagged personas, so this persona's share is its headcount over
# the tagged total. A single-persona line therefore counts in full, and a shared
# line is never double-counted across personas. (If the tagged personas have zero
# total headcount, the line is split evenly by count so cost is not lost.)
current_microsoft = Σ over lines tagged with this persona of
      line_total * (persona.headcount / Σ headcount of the line's tagged personas)
    where line_total = line.quantity_assigned * line.unit_price_paid_annual

# Linear-by-user offset: for each product this scenario displaces, credit
# headcount * per_unit_annual_cost. This is the persona's allocated share of
# third-party cost it consumes today.
offset = Σ (persona.headcount * product.per_unit_annual_cost)
           over products where displaces(scenario, product)

current_spend_annual = current_microsoft + offset
# Composed target (base bundle + add-ons): the hydrator unions the covered
# outcomes across the base + add-on bundles, sums their list prices, and applies
# the scenario discount to yield the net target_unit_price_annual the engine uses:
#   net = (base_list + Σ addon_list) * (1 - target_discount_pct)
# target_covered_outcome_ids is the union across base + add-ons.
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
> applicable when it is à-la-carte or its base link matches the base. The candidate's
> covered set is the union (base ∪ chosen add-ons) and its price is the sum, so a
> recommendation reads as "E3 + E5 Security" rather than a single line. The
> displacement test and linear-by-user offset are unchanged and applied to the
> composed covered set. The **required** set (what a candidate must cover or show a
> gap) is the union of the outcomes the persona's current Microsoft licenses deliver
> **and** the persona's declared required capabilities (`PersonaRequirement`, the
> Personas tab) — so a needed capability with no current license still forces a gap.

## Rollup (6.8) with integrity rules (6.9)

```
net_tco_delta_annual = Σ delta_annual over IN-SCOPE scenarios
fully_eliminated_tools = products with disposition == FullyEliminated

# Rule 1 — renewal-elimination gating: a renewal cycle is eliminated ONLY when
# its product is FullyEliminated. Any residual users → the product still renews.
eliminated_renewal_cycles = renewal entries of FullyEliminated products only

residual_third_party_cost_annual = Σ residual_cost over PartiallyReduced products

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
# readout can name each displaced tool and the dollars it frees ("we freed up
# SentinelOne, saving $X"). Sums to existing_third_party_annual. A product with
# covered_count 0 has per-unit cost 0, so its credited_annual is 0 even when it
# is displaced/eliminated — the freed dollars are 0 until a covered population
# is entered.
freed_third_party = [ {product, credited_annual = Σ its in-scope offsets} ]
```

> Rule 2 — override disclosure: a ForceFullElimination override asserts savings
> on users the data did not displace; it is permitted, requires a reason, and the
> reason prints on the readout. An intended residual (e.g. Okta users who are not
> M365 users) is recorded as `residual_intent = IntendedOutOfScope` and is **not**
> an override. When a residual exists the tool must force the operator to choose
> which case applies (`requires_residual_classification`).

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
