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
# Business Premium swap (data layer): when the engagement's BP swap is active for a
# scenario (inherited, not opted out, and Business Premium covers every outcome the
# persona requires), the hydrator substitutes the EFFECTIVE target with Business
# Premium (its covered outcomes + catalog price × (1 - discount)) before the engine
# runs. The engine math below is unchanged — it consumes whichever target the data
# layer resolved.
target_spend_annual  = persona.headcount * scenario.target_unit_price_annual
delta_annual         = current_spend_annual - target_spend_annual   # +saving / -cost
```

A negative delta is shown honestly as a cost; the M365 uplift can exceed the
third-party cost it offsets.

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
