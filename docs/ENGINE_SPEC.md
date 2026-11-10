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
- **CurrentLicenseLines**: `{id, quantity_assigned, quantity_purchased,
  unit_price_paid_annual, persona_ids, coverage_scope, covered_outcome_ids,
  renewal_date, unused_seats_answer}`.
  A line may apply to several personas; its cost is distributed across their
  combined headcount (see 6.2). `covered_outcome_ids` is how the hydrator reads
  the line (DATA_MODEL §4.5): its linked plan, its answered outcomes, or its
  name's plan; empty for a line nobody can read yet. Out-of-scope lines are not
  hydrated. `quantity_purchased`, `renewal_date` and
  `unused_seats_answer` (`Intended` | `NotNeeded` | none) feed only the timed
  headline (6.11).
- **Engagement timing**: `{workshop_date, microsoft_renewal_date, horizon_years}`,
  read only by the timed headline (6.11).
- **ThirdPartyProducts**: `{id, name, annual_cost, covered_count, is_managed,
  tooling_pct, renewal_date, delivered_outcome_ids}`. `delivered_outcome_ids` are **ratified** coverage outcomes
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

## Interpretation — raw data read through the rules (6.0)

The engine's inputs above are built by one pure function, `interpret(raw) →
interpreted` (`tco_engine/interpret.py`, TARGET_SCHEMA §6), then adapted by
`to_engine`. The data layer only loads what people entered (`services/raw.py`)
plus two catalog facts the interpreter can't look up itself: which plan a typed
name resolves to, and the engagement's ratified coverage. Every rule below is
defined once, here, and every consumer reads its result (`GET …/interpreted`).

```
# Licence line (D23) — how it is read, first match wins:
read_state = out_of_scope   if line.out_of_scope                  (delivers nothing, in no number)
           | linked / unread if line.linked_bundle_id             (its plan's coverage; unread if none)
           | mapped          if a person ticked outcomes          (those outcomes)
           | named           if coverage exists for the exact typed name (legacy)
           | resolved        if the typed name resolves to a plan with coverage
           | unread          otherwise                           (cost counts; outcomes unknown)
effective_price = overridden_price  if price_override and overridden_price is set   (source "override")
                | list_price                                                         (source "list")
applies_to      = persona_ids, or EVERY persona when untagged (assumed = true, D12)
annual_cost     = quantity_assigned × effective_price
share(line, p)  = annual_cost × headcount(p) / Σ headcount(applies_to)
                  (even split when that total is 0; out-of-scope lines have no shares)

# Tool
annual_cost     = raw_cost × 12 if cost_period = Monthly, else raw_cost
tooling_pct     = the tool's own value, else the engagement default          (source)
effective_cost  = 6.5 below;  cost_per_person = 6.3 below on people_covered
people_covered  = covered_count_override if set (source "override"),
                  else Σ headcount of its tagged personas (source "personas")
applies_to      = persona_ids, or every persona when untagged (assumed)
share(tool, p)  = cost_per_person × headcount(p) for p in applies_to
                  (a covers override above the personas' headcount leaves a
                  remainder that is nobody's share)

# Scenario
covered         = coverage(resolved base plan, else the typed name) ∪ coverage(each add-on)
composed_list   = base list + Σ add-on list
net             = overridden_price if price_override and set (source "override")
                | composed_list × (1 − discount_pct)              (source "list")
complete        = has a target name and (an override or net > 0)
```

```
# Persona × outcome tables (TARGET_SCHEMA §6.3) — per persona p
delivered_today(o) = an in-scope line applying to p delivers o (by_microsoft)
                   or a tool applying to p delivers o            (by_tool)
                   or p's gap answer for o is CoveredOutsideInventory (by_outside)
needed(o)          = o ∈ p's declared requirements, or — with none declared —
                     delivered_today(o)                      (needed_source)
retired(tool, p)   = p has a plan and (the plan covers every outcome the tool
                     delivers [6.6, tco_engine/rules.covers_tool] or the operator
                     forced it); the tool applies to p [6.3a, rules.applies_to]
quick_win(tool, p) = p's current Microsoft lines deliver every outcome the tool does
delivered_after(o) = with no plan: delivered_today(o); else the plan covers o, or
                     a tool p keeps delivers o, or o is covered outside the inventory
status(o)          = Pending          if p's plan is incomplete
                   | MovedToMicrosoft if today only by tools and the plan covers o
                   | Kept             if delivered today and after
                   | Gained           if new after and p's answer is NotDeliveredToday
                   | Unconfirmed      if new after and unanswered (never claimed, D21)
                   | Lost             if delivered today, not after
                   | Gap              if needed and delivered neither
outcomes_unknown(p) = an unread line applies to p (D23)
```

```
# Seat caps (D27) — per licensing limit L, over its member plans
current_seats(L)  = Σ assigned seats on in-scope lines whose typed name resolves to a member
usage(L, p)       = headcount(p)                 if p's in-scope plan (base or any add-on) is a member
                  | Σ over member lines applying to p of assigned × headcount(p) / Σ headcount(line's personas)
                                                 if p has no in-scope plan
unattributed(L)   = assigned seats on member lines whose personas have no headcount
future_seats(L)   = round(Σ usage + unattributed);  seats_left(L, p) = cap − round(Σ usage of others + unattributed)
seat_cap_exceeded(p) when p's in-scope plan is under a cap and headcount(p) > seats_left
                  (the tightest cap decides; kind no_seats | nested | split)

# Findings (TARGET_SCHEMA §6.4) — code, step, subject, facts; worded by Review
no_groups, headcount_vs_employees, licence_untagged, seats_vs_headcount,
unused_seats_unanswered, renewal_date_missing, license_out_of_scope, tool_incomplete,
group_without_plan, seat_cap_exceeded, license_unread (one per normalized name),
capability_changes_unknown, target_unmapped, capability_dropped, gap_unanswered.
(residual_unanswered and library_update_pending are added by Review: they need
the engine result and the shared library.)
```

Retirement here is the engine's displacement rule exactly: the parity test checks
every persona's retired tools against the engine's credited offsets. (TARGET_SCHEMA
definition 4 narrows it to the outcomes the persona *needs*; adopting that is a
number change, left for its own decision.)

The engine's `CurrentLicenseLine.unit_price_paid_annual` is the line's effective
price; its `ThirdPartyProduct.annual_cost` / `covered_count` / `tooling_pct` are the
interpreted values above (no longer the values stored on the tool row);
`target_unit_price_annual` is the scenario's `net`. The 6.5 and 6.3 formulas are one
function each (`tco_engine/money.py`), used by both the interpreter and the engine.
The 6.2 headcount split and the "applies to" rule are likewise one function each
(`tco_engine/rules.py`: `headcount_shares`, `applies_to`), used by the interpreter,
the engine's per-scenario math and the headline timing.

## People covered by a tool — the one splitting rule (6.0a)

Agreed in `GUIDED_PLAN.md` (G2, G4–G6). It replaces the tool's `people_covered`
line in 6.0. A tool's cost lands on the personas that hold it, by headcount, the
same way a licence line's does, unless the customer says its extra seats belong to
people who stay on it.

```
people_held     = Σ headcount(p) for p in applies_to        (every persona when untagged)
stated_seats    = covered_count_override                     (NULL = not given)
people_covered  = people_held     if stated_seats is NULL                 (source "personas")
                | people_held     if stated_seats < people_held           (source "personas"; finding tool_seats_short)
                | people_held     if stated_seats > people_held
                                     and extra_seats_answer = ExtraSeats  (source "extra_seats")
                | stated_seats    otherwise                               (source "override")
standing_seats  = people_covered − people_held                (≥ 0: seats outside the personas that stay)
cost_per_person = effective_cost / people_covered
share(tool, p)  = cost_per_person × headcount(p)
```

So Σ shares + standing_seats × cost_per_person = effective_cost: the cost is never
lost. The engine's `covered_count` is `people_covered`; nothing else in 6.3–6.4
changes. `extra_seats_unanswered` is raised when stated_seats > people_held, the
answer is NULL and at least one in-scope scenario retires the tool for a persona.

**The persona baseline (G3).** For every persona, with or without a scenario:

```
microsoft_annual = Σ share(line, p) over in-scope licence lines
tools_annual     = Σ share(tool, p)
total_annual     = microsoft_annual + tools_annual
per_user_monthly = total_annual / headcount(p) / 12      (0 when headcount is 0)
```

The baseline needs no future state. A scenario's current spend (6.2) is unchanged:
a tool the persona keeps would appear on both sides of a baseline comparison and
cancel, so `target − (microsoft + displaced tools)` equals
`(target + kept tools) − baseline`.

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

### Persona-scoped displacement (6.3a)

A move can only retire a tool for a persona that actually **holds** it. A product
carries the personas it is used by (the `ThirdPartyPersona` tags); displacement is
gated on that membership, so credit never leaks to a persona whose target merely
covers the outcomes:

```
applies(product, persona) =
    product.persona_ids is empty            # untagged → org-wide (applies to all)
    OR persona ∈ product.persona_ids        # tagged → only those personas hold it

# both gates, or no displacement / no offset credit:
displaces_for(scenario, product) =
    applies(product, persona(scenario)) AND displaces(scenario, product)
```

An **untagged** product (no persona tags) is org-wide, which preserves the
headcount spread across all displacing scenarios. A **tagged** product is
displaced only by its tagged personas' in-scope moves — so a tool tagged to a
persona who does not move stays `Unchanged` with its full carrying cost, never
credited as reduced by an unrelated persona's move.

## Per-product disposition (6.4), in-scope set only

```
displacing = in-scope scenarios s where displaces_for(s, product)   # persona-scoped (6.3a)
displaced_users = Σ headcount(persona(s)) for s in displacing
residual_count  = max(covered_count - displaced_users, 0)

# Like for like (GUIDED_PLAN G7): no operator override. Seats beyond the personas
# are answered once as the tool's extra_seats_answer, which sets covered_count (6.0a).
if displaced_users == 0:
    disposition = Unchanged;        residual_cost = residual_count * per_unit  # carrying cost — the customer keeps paying it
elif residual_count == 0:
    disposition = FullyEliminated;  residual_cost = 0
else:
    disposition = PartiallyReduced
    residual_cost = residual_count * per_unit_annual_cost
```

### Who the covered population is (6.4a)

Each disposition also reports, persona by persona, where the product's covered
population sits, so the residual can be traced to the people it comes from. It is
explanatory only and feeds no money:

```
for each persona p where applies(product, p):
    status(p) = Replaced    if p has a scenario in `displacing`
              = PlanGap     elif p has an in-scope scenario s
                            (missing_outcome_ids = product.delivered_outcome_ids
                                                   − s.target_covered_outcome_ids)
              = OutOfScope  elif p has a scenario (none in scope)
              = NoPlan      otherwise
    population += (p, headcount(p), status(p))

unassigned_count = covered_count − Σ headcount over population   # may be negative

# The same, summed, for the keep-or-retire question (shown, never re-derived):
uncovered_count         = max(covered_count − displaced_users, 0)
uncovered_annual        = money(uncovered_count × per_unit_annual_cost)
remaining_persona_count = Σ headcount over non-Replaced population
replaced_count          = Σ headcount over Replaced population
held_count              = covered_count − unassigned_count
over_licensed_only      = remaining_persona_count = 0 and unassigned_count > 0
                          and uncovered_count > 0
```

Because carve-outs take their seats out of the parent, persona headcounts are
disjoint, so `Σ headcount over non-Replaced population + unassigned_count =
covered_count − displaced_users` — exactly the pre-clamp residual (and, before any
override, `residual_count` whenever that is positive). A negative
`unassigned_count` means the product counts fewer users than the personas holding
it.

## Per-persona scenario math (6.2 / 6.3)

```
# A current license line applies to one or more personas (its `persona_ids`). Its
# total annual cost (quantity_assigned * unit_price_paid_annual) is distributed
# across the combined headcount of that pool, so this persona's share is its
# headcount over the pool total. A single-persona line therefore counts in full,
# and a shared line is never double-counted across personas. (If the pool has zero
# total headcount, the line is split evenly by count so cost is not lost.)
#
# UNTAGGED lines (empty persona_ids) are ORG-WIDE (TARGET_SCHEMA D12): the pool is
# EVERY persona, distributed by headcount — the same reading the quick-win
# coverage (6.10), the seat cap (6.13), the recommender and Review use. A persona
# with a scenario carries its share as current spend and retires it when it moves;
# a persona without one keeps its share (it isn't moving), so that share is in no
# scenario and in no delta. (The pool used to be only personas with a scenario,
# which credited the movers with retiring seats held by people who stay.)
current_microsoft = Σ over lines whose pool includes this persona of
      line_total * (persona.headcount / Σ headcount of the line's pool)
    where line_total = line.quantity_assigned * effective price (Interpretation, 6.0)
      and pool = line.persona_ids if set, else all personas with a scenario
# effective price = the line's explicit price override when set
# (price_override + overridden_price_annual), else the catalog list baseline
# (unit_price_paid_annual). An override is a negotiated rate on the SAME SKU, so it
# lowers current spend without changing coverage.

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
# An explicit price override (price_override + overridden_price_annual) SUPERSEDES
# this: net = overridden_price_annual — a negotiated net rate on the same composed
# target (the discount is ignored while the override is on).
# target_covered_outcome_ids is the union across base + add-ons.
# There is NO target substitution in the data layer: what the engine costs for a
# scenario is the target on that scenario, which is the target the operator sees on
# the row. A persona only PART of which should move to a different plan is carved
# into a child persona with its own scenario (Persona.parent_persona_id,
# DATA_MODEL 4.2b) — the seats move, the child inherits the parent's licences, tools
# and requirements, and this math is unchanged because a carve-out is just another
# persona. (This replaced an automatic Business Premium swap that substituted the
# effective target for whole personas under the 300-seat Business cap; because a
# persona is the licensing unit, any persona over 300 could never move, so on a real
# engagement the feature was inert.)
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
> composed covered set — **including the engine's limits on that offset**: credit
> goes only to tools this persona HOLDS (`persona_ids`, §6.3a) and only for the
> seats the tool covers (`min(headcount, covered_count) × per_unit`). A
> recommendation is a promise about what the engine will compute once the operator
> applies it, so any credit the engine would not repeat is a number that evaporates
> on contact. (Both limits were missing until the service sweep caught them:
> unbounded `headcount × per_unit` credit made a bundle read as saving $181,120
> where applying it yielded $91,120.) The **required** set (what a candidate must cover or show a
> gap) is the union of the outcomes the persona's current Microsoft licenses deliver
> and the persona's declared required capabilities (`PersonaRequirement`, the
> Personas tab) — so a needed capability with no current license still forces a gap.
> A third-party tool's outcomes are kept the cheapest way (TARGET_SCHEMA D28): by the
> tool, which stays and keeps costing, or by Microsoft when the candidate covers
> everything the tool delivers. After the gap-closing add-ons, each base takes the
> set of further priced add-ons whose tool credit (the same capped credit as above)
> most exceeds `headcount × their price`, or none when nothing pays; each add-on
> reports what it closes or retires, and each candidate the tools it keeps. Only
> **priced** add-ons are composed, and only onto a priced base: an unpriced one would
> join at $0 and quote a plan no one can buy.
>
> Carve-out family (TARGET_SCHEMA D29): a persona with carve-outs and those carve-outs
> decide each tool together. Over every set *R* of the family's tools that some priced
> plan could cover, each member takes its cheapest priced, gap-free, not-cap-limited
> plan that retires exactly the tools of *R* it holds (base + cheapest add-ons covering
> its required gaps and every outcome of *R*); the *R* with the lowest family net cost
> (Σ headcount × price − capped credit of the retired tools) wins, ties to the larger
> *R*. That plan is the member's recommendation (`family_choice`); its D28 plan is kept
> and tagged `cheaper_alone` (with `alone_saves_annual`) when cheaper. No *R* that works
> for every member → each falls back to D28 (`family.fallback`). The seat-cap split's
> other side takes *R* = the tools the capped side's chosen plan retires.
>
> Seat cap (TARGET_SCHEMA D27): candidates in a seat-capped bundle family (a
> `LicenseLimit` of type `max_total_seats`, e.g. Microsoft 365 Business ≤ 300 seats per
> tenant) carry the seats left for that family, measured in the **future state** by the
> data layer (`services/limits.seat_cap_context`), excluding the persona analysed:
>
> ```
> consumed = Σ over other personas with an in-scope scenario touching the family: headcount
>          + Σ over current member lines: assigned × (headcount of the personas it applies
>            to that have no in-scope scenario, excluding this one) / (headcount of all the
>            personas it applies to)          # untagged lines apply to every persona
> seats_left = max(0, cap − consumed)
> ```
>
> A persona that moves never counts its current lines: the move replaces them.
>
> - `seats_left = 0` → the candidate is `cap_limited`: shown, never recommended; the
>   recommendation falls to the next-best plan.
> - `0 < seats_left < headcount` → the candidate stays eligible and carries
>   `over_cap_by = headcount − seats_left`. The caller offers a split
>   (`services/seat_cap.split_offer`): `seats_left` on the capped plan, the rest on the
>   best plan the optimizer finds for that remainder with every capped candidate removed;
>   the larger side is the parent. Nothing is split until a person confirms it.
>
> There is no setting: the pure optimizer stays cap-agnostic unless the caller supplies
> seats left, so the language-neutral math is unchanged.

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

# 6.8b ECIF projected funding — the net uplift in annual Microsoft spend and the
# ONE-TIME fund Microsoft may co-invest (ECIF) against it. Uplift is the pure
# Microsoft increase (target − existing Microsoft), EXCLUDING the third-party
# offset: ECIF tracks incremental Microsoft revenue, not net customer TCO. The
# uplift is ANNUAL; the funding is a ONE-TIME amount (no per-year component), sized
# at ~1/N of the annual uplift per standard Microsoft investment ROI (N:1 ratios).
# It funds pre-sales (assessment, discovery, design) and post-sales (implementation,
# migration) acceleration. A non-positive uplift, or a non-positive ratio, yields
# zero funding. (The funding fields drop the `_annual` suffix — they are not annual.)
msft_uplift_annual  = target_microsoft_annual − existing_microsoft_annual
fundable            = max(msft_uplift_annual, 0)
ecif_funding_low    = fundable / ecif_roi_conservative   # e.g. 10:1 → 1/10 (less), one-time
ecif_funding_high   = fundable / ecif_roi_generous       # e.g.  5:1 → 1/5  (more), one-time
# ecif_roi_conservative / ecif_roi_generous are engagement policy inputs (default
# 10 and 5). Surfaced as an advisory co-investment note under the readout's next
# steps only when the engagement is flagged a managed Microsoft account (a
# presentation gate; the math is unconditional). Advisory — actual ECIF is scoped
# with the Microsoft account team.
```

> Rule 2 (retired, GUIDED_PLAN G7) — there is no override. A saving is claimed
> only where a persona's plan delivers every outcome a tool delivers. Seats beyond
> the personas are answered once per tool (`extra_seats_answer`, 6.0a); unanswered
> they stay, and Review asks (`extra_seats_unanswered`).

## Quick wins — duplicates the current licensing already covers (6.10)

Scenario-independent. A tool is redundant TODAY only where the **same persona**
both USES the tool and already HOLDS a current license covering all its outcomes
(persona-scoped, §6.3a). Crediting a tool because a *different* persona holds
covering licensing would assert savings the customer can't realize — retiring it
would strand the personas who actually use it.

**Seats are finite.** A covering line credits only the seats it actually
entitles, per its `coverage_scope`:

- `PerUser` (the default) — a per-seat purchase entitling exactly
  `quantity_assigned` seats. Leaving such a line **untagged** means the operator
  did not attribute those seats to a persona; it does **not** mean every user
  holds one. Its seats are one finite pool the tool's personas draw from, never
  replicated to each. (46 untagged Microsoft 365 E3 seats cannot make 3,150
  people redundant for a third-party MFA tool.)
- `TenantWide` — a tenant-level entitlement covering the whole population it
  applies to (its tagged personas, or everyone when untagged), whatever the seat
  count on the line. Only a tenant-wide line can make a whole persona redundant.

Seats are therefore **allocated**, not summed. Entitled seats are matched
against the headcount of the personas that actually use the tool, and each seat
is spent once — a line shared by several personas is one pool, not a full
allowance for each.

```
covering_lines(product) = current lines L whose covered_outcome_ids ⊇ product.delivered_outcome_ids
population(product)     = product.persona_ids, or every persona when untagged (org-wide)
applies(L, product)     = L.persona_ids ∩ population, or population when L is untagged

for product with delivered_outcome_ids ≠ ∅ and covering_lines non-empty:
    unmet(P) = P.headcount for each P in population(product)

    # 1. Tenant-wide entitlements cover their whole population outright.
    for L in covering_lines where L.coverage_scope = TenantWide:
        unmet(P) = 0 for each P in applies(L, product)

    # 2. Per-user seats are finite pools, MOST SPECIFIC FIRST: order by
    #    (|L.persona_ids|, sorted L.persona_ids), untagged lines last.
    for L in covering_lines where L.coverage_scope = PerUser, in that order:
        seats = max(L.quantity_assigned, 0)
        for P in sorted(applies(L, product)):
            take   = min(seats, unmet(P));  unmet(P) −= take;  seats −= take

    displaced_today = min(product.covered_count,
                          Σ (P.headcount − unmet(P)) over P in population(product))
    credited_annual = displaced_today * product.per_unit_annual_cost   # effective basis
    residual_today  = covered_count - displaced_today
quick_win_savings_annual = Σ credited_annual over quick-win products (credited > 0)
```

Each quick win also reports the allocation behind `displaced_today` as
`covered_by`: one `(persona, licence sku_reference, seats)` entry per seat grant
above — a tenant-wide line granting a persona's remaining unmet headcount, a
per-user pool granting its `take`. It is explanatory only (it feeds no money) and
answers "which personas already have this, through which licence". Its seats sum
to `Σ (P.headcount − unmet(P))`, i.e. to `displaced_today` unless the
`covered_count` cap applies.

Ordering the pools most-specific-first means a persona's own dedicated line
fills it before an org-wide pool is drawn on, leaving the pool for personas that
have nothing of their own; sorting by persona id makes the result independent of
input order. Where tag sets overlap without nesting, this greedy pass can leave
a seat unplaced that a perfect matching would use — it never places a seat that
does not exist, so the error is always toward **under**-crediting.

> There is one path for tagged and untagged tools alike. An untagged (org-wide)
> tool is scored against every persona, exactly as if tagged to all of them —
> never against the raw seat count of covering lines, which would credit a
> persona that holds nothing because a different line has spare seats.

> `coverage_scope` governs **seat counts**, not which personas can see a
> capability. The capability views (persona coverage gaps) and
> §6.2's org-wide cost pool still treat an untagged
> line as org-wide — those guards are deliberately conservative: over-counting
> there withholds a move, while over-counting seats here would assert savings the
> customer cannot realize.

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
> in sync. The readout **headline** is no longer a presentation multiplication:
> it is the timed headline of §6.11, computed once here. Every other engine
> quantity remains annualized.

## The headline: the run rate, then the ramp (6.11)

One headline, made of three sub-lines. It **leads with the full run rate over the
horizon** (`run_rate_horizon_amount`, "over 36 months at full run rate"), with the
**run rate** beside it: what the customer saves (or invests) each year once every
contract has renewed. Then the
**ramp**: each amount timed by the contract that unlocks it and summed over the
modelling horizon, year by year. Amounts here are **savings-positive** (a positive
amount is money saved, a negative one money added) — the opposite sign of `delta`,
because this is the number a customer reads.

```
H = horizon_years × 12                      # months in the headline
month 0 = workshop_date

start(d):                                   # the month an amount timed by date d starts
    if d is missing or workshop_date is missing:  return 0, no date
                                            # month-to-month with no lock-in: day one
    while d < workshop_date:  d = d + 12 months     # a past renewal: next anniversary
    return the smallest whole m with workshop_date + m months ≥ d
           (month arithmetic clamps the day to the month's length)

item(annual, s):  months = max(0, H − s);  amount = round_cents(annual × months / 12)
                                            # round_cents: half a cent rounds away from zero

# 1. Duplicate spend today — each quick win (6.10), from the tool's renewal.
for q in quick_wins:
    item(q.credited_annual, start(tool(q).renewal_date))

# 2. Consolidation — each IN-SCOPE scenario r (its move value, 6.8a, split by when
#    each part can happen).
for r in in-scope scenarios:
    for o in r.offsets:                                     # the move's own tool credit
        item(o.move_unlocked_annual, start(tool(o).renewal_date))
    ms_change = r.target_spend_annual − r.current_microsoft_annual
    if ms_change > 0:    # an INCREASE: Microsoft lets a customer add or upgrade mid-term,
                         # so it starts when there is something only the move can
                         # retire — the first tool whose credit the move itself
                         # unlocks (a quick win retires without it) — else on day one
        item(−ms_change, min(start(tool(o).renewal_date)
                             for o in r.offsets if o.move_unlocked_annual > 0) or 0)
    if ms_change < 0:    # a REDUCTION waits for every line it touches to renew
        lines = the licence lines that apply to r's persona (6.2's rule: tagged to it,
                or untagged = Org-wide, every persona)
        item(−ms_change, max(start(L.renewal_date or microsoft_renewal_date) for L in lines))

# 3. Over-licensing — unused seats the customer confirmed are not needed.
for L in current lines with unused = quantity_purchased − quantity_assigned > 0:
    list L (unused, unused × unit_price_paid_annual, its answer)   # shown either way
    if L.unused_seats_answer = NotNeeded:
        item(unused × L.unit_price_paid_annual, start(L.renewal_date or microsoft_renewal_date))
    # Intended (kept on purpose) and unanswered seats are never counted.

# The headline: the run rate.
run_rate_annual = Σ item annual amounts      # each year once everything has renewed
duplicate_spend_annual, consolidation_annual, overlicensing_annual = Σ item annual amounts per sub-line
direction = saved | added | none by the sign of run_rate_annual

# The headline figure: the run rate over the whole horizon.
run_rate_horizon_amount = round_cents(run_rate_annual × H / 12)

# Each saving sub-line's share of the savings, in whole percents.
pool = Σ sub-line annual amounts that are > 0       # never the net
share[s] = for each sub-line s with annual > 0: floor(100 × annual[s] / pool),
           then +1 to the largest remainders (ties: duplicate spend, consolidation,
           over-licensing order) until the shares sum to 100
           # a cost or zero sub-line has no share; no savings → no shares

# The ramp: the same items over the horizon.
duplicate_spend_amount, consolidation_amount, overlicensing_amount = Σ item amounts per sub-line
headline_amount = their sum
through(i, m) = round_cents(i.annual × min(max(m − i.start, 0), i.months) / 12)
for y in 1 … horizon_years:                  # each modelled year
    year[y] = Σ over items (through(i, 12y) − through(i, 12(y − 1)));  cumulative[y] = Σ year[1..y]
full_run_rate_month = max(i.start over items), or none when there are no items
```

**The ramp reconciles.** Each item's years add up to its own rounded amount
(`through(i, H) = i.amount`), so the years add up to `headline_amount` and the last
cumulative equals it. Every year that begins at or after `full_run_rate_month`
equals `run_rate_annual` exactly: a whole year adds a whole-cent amount of the same
sign, which never changes how half a cent rounds. When `full_run_rate_month` is at
or beyond the horizon, the full run rate is reached after the modelled years.

**Two horizon figures, never confused.** `run_rate_horizon_amount` is the run rate's
value over the horizon once at full rate — the headline. `headline_amount` is what
the timing actually lands inside the horizon — shown with the ramp. A tool that
renews in month 30 counts 12 months in the first and 6 in the second; with every
amount starting at month 0 the two are equal.

**Run-rate identity.** With no unused-seat answers, the run rate is today's untimed
total opportunity: `run_rate_annual = quick_win_savings_annual −
move_incremental_delta_annual`. With every date at the workshop, or no dates at all
(month-to-month), every item starts at month 0 and
`headline_amount = run_rate_annual × horizon_years`. Duplicate spend is exactly
the quick wins, consolidation exactly the moves' own value (6.8a), so timing never
counts a dollar in two sub-lines; it only decides from which month each dollar
counts.

Every item is returned (sub-line, kind, label, annual amount, start month, the
date it was timed by, whether no date was given, months counted, amount), so a
readout can show exactly how the headline was built and which amounts count from
day one for want of a date. An item that starts after the horizon is listed with
`months = 0`.

**Out-of-scope licence lines** (TARGET_SCHEMA D23) are not inputs to the engine:
the hydrator leaves them out, so they are in no number: not current spend, not
retired by a move, delivering nothing, and not over-licensing.

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

## Invariants — how the math is verified (not by examples)

Worked examples check the cases someone thought of. The failure mode they miss
is structural: an over-credit that only appears when a particular tagging shape
meets a particular seat count. `backend/tests/sweep.py` enumerates that space —
1 to 3 personas; licence lines tagged to **any** subset (including none =
org-wide), at seat counts below / at / above the population they apply to,
per-user or tenant-wide, covering or not; tools tagged to any subset, at covered
counts below / at / above their population, managed or not; and each persona's
scenario absent / in-scope displacing / in-scope non-displacing / out-of-scope.
Every case has a workshop date, and the renewal dates of the tool, the licence lines
and the agreement are cycled across cases (none, at the workshop, mid-horizon, past,
beyond the horizon) so the timing and the ramp see every shape. Then it asserts the
properties below on every result. ~1.27M engagements:

    cd backend && python -m tests.sweep            # the full space (~10 min)
    cd backend && python -m tests.sweep --level ci # the slice every test run does

| Invariant | Claim |
| --- | --- |
| `bridge` | `net = target_MS − existing_MS − existing_3P` (§6.8) |
| `headline-decomposition` | `net = move value − free-today` (§6.8a) |
| `delta-sum` | the in-scope scenario deltas sum to the net |
| `quick-win-total` | the credited quick wins sum to the headline |
| `qw-seat-bounds` / `qw-residual` | `0 ≤ displaced ≤ covered`; residual is the remainder |
| `qw-population` | never more redundant seats than people who use the tool |
| `qw-entitlement` | never more redundant seats than the covering licences entitle |
| `qw-credit-cap` / `freed-cap` | credit never exceeds what the tool costs |
| `freed-split` | free-today + move-unlocked = credited, both ≥ 0 |
| `freed-free-today` | the "free today" portion never exceeds the quick win |
| `offset-units` / `offset-dollars` | allocated units/dollars stay within the covered population and cost |
| `disposition-displaced` | displaced users = the displacing personas' headcount |
| `current-ms-conservation` | attributed Microsoft spend never exceeds actual licence spend |
| `order-dependence` | shuffling the input lists changes no output |
| `timed-runrate` | untimed, the headline is the quick wins plus the moves' own value (§6.11) |
| `timed-sum` | the headline is the sum of its sub-lines, each the sum of its timed items |
| `timed-bounds` | timing only shortens a saving or a cost: never longer than the horizon, never a flipped sign |
| `timed-nodate` | an item with no date starts at month 0 and names no date (month-to-month) |
| `runrate-parts` | the run rate is its three sub-lines' yearly amounts |
| `runrate-direction` | the headline's direction is the sign of the run rate |
| `ramp-years` | the ramp has one entry per modelled year |
| `ramp-sum` | the years add up to the horizon total, and the last running total equals it |
| `ramp-full-month` | full run rate starts at the latest item start month |
| `ramp-full-year` | every year that begins at or after that month is exactly the run rate |

The two decision surfaces above the engine — recommend-a-path and the Business
carve-out — get the same treatment in `backend/tests/sweep_services.py`, built
through the HTTP API so a violation is one a user can hit:

    cd backend && python -m tests.sweep_services            # the full space
    cd backend && python -m tests.sweep_services --level ci # the slice every test run does

| Invariant | Claim |
| --- | --- |
| `opt-engine-agreement` | applying a recommendation yields the delta it advertised |
| `opt-offset-cap` / `opt-offset-phantom` | never credit more than a tool costs, nor credit nothing displaced |
| `opt-persona-attribution` | only tools this persona holds |
| `opt-recommend-eligible` / `opt-recommend-best` | the recommendation is eligible, and the best eligible |
| `carve-population-conserved` | carving moves seats, it never mints or loses them |
| `carve-parent-reduced` / `carve-seats-moved` | the parent loses exactly what the child gains |
| `carve-current-spend-conserved` / `carve-covers-conserved` | today's bill and tool coverage are untouched |
| `carve-inherits-associations` | the child holds the licences, tools and requirements those people already held |
| `carve-spend-splits` | ...so today's spend splits between them, rather than the carved seats reading as holding nothing |
| `carve-lineage` / `carve-has-scenario` / `carve-target-applied` | the child records its origin and has a real, costed future state |
| `carve-undo-restores` / `carve-bounded` | deleting the child puts the seats back; carving a whole persona is refused |

> Why a carve-out and not an automatic swap: the engagement used to carry a
> "swap eligible personas to Business Premium" toggle that substituted the
> effective target for whole personas, filling the 300-seat Business cap. A persona
> is the licensing unit, so a persona larger than the cap could never move — and at
> enterprise scale every persona exceeds it, making the toggle inert on exactly the
> engagements it was meant to help, silently. Carving says the same thing as data
> the engine already costs: 300 seats become a child persona targeting Business
> Premium, the seats move out of the parent, and the cap counts them like any other
> scenario. The invariants above are conservation laws over that operation, and each
> one is mutation-tested — the guard has been shown to fail when the behaviour it
> protects is removed.

These are claims about reality, not about the code: when one fails, the engine is
wrong until proven otherwise. Two over-credits were found this way and fixed in
§6.10 — a per-user line tagged to several personas granting each of them its full
`quantity_assigned`, and an untagged tool credited from raw covering-seat counts
regardless of who held them. Any change to the math must leave the sweep clean;
`backend/tests/test_engine_invariants.py` fails the build otherwise.
