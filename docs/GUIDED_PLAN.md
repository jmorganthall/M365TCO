# Persona-guided walkthrough — the plan

The agreed design for a simpler, persona-first walkthrough that an account executive
or the SA team runs with a customer, and the order it is built in. It refines
[`WALKTHROUGH.md`](WALKTHROUGH.md); where the two differ, this document wins and the
change is recorded below. The data contract changes are in
[`TARGET_SCHEMA.md`](TARGET_SCHEMA.md) §17 and [`ENGINE_SPEC.md`](ENGINE_SPEC.md) §6.0a.

## 1. The idea in one paragraph

A persona is **a number of people**. A third-party tool is **a cost that covers a
number of people and delivers some outcomes**, and it is always given to personas.
From that, every persona has one all-in **cost per user per month today**: its share
of its Microsoft licences plus its share of every tool it holds. That is the baseline.
The future state is modelled per persona against it, like outcome for like outcome:
if a persona's future Microsoft plan delivers every outcome a tool delivers, that
persona's share of the tool goes away; if it doesn't, the share stays. Nothing about
the future is asked of the customer except one thing: when a tool that is being
replaced covers **more seats than the personas who hold it**, are the extra seats
just extra (the whole cost goes) or people who will stay on the tool?

## 2. Decisions

| # | Decision |
| --- | --- |
| G1 | **Persona-first.** Every licence and every tool is answered in terms of the personas defined first. "Everyone" is stored as Org-wide (`applies_to = OrgWide`, shown as "All groups"), so a group added later is included. |
| G2 | **One splitting rule.** A tool's cost lands on the personas that hold it, by headcount, exactly as a licence line's does. The only exception is the answer in G4. Its seat count no longer silently leaves part of the cost on nobody. |
| G3 | **A baseline per persona.** The interpreter returns, for every persona and with no future state needed: Microsoft annual, tools annual, total annual and cost per user per month. Screens and the PDF read it; none computes it. |
| G4 | **The seat-mismatch question** is the only future-facing question. It is asked once per tool, only when the tool covers more seats than its personas hold **and** at least one persona's future plan replaces it. Answers: *extra seats* (spread the cost over the personas; the whole cost can go) or *people who stay* (cost per seat on the full count; those seats' cost stays). Unanswered is read as *people who stay*, so a saving is never overstated, and is listed in Review. The answer describes today, so it is stored on the tool. |
| G5 | **Fewer seats than people** (a tool covering 1,000 tagged to a group of 1,200) is not asked. The cost is spread over the group, and Review notes that the figure follows the cost-per-user method but the tool's seat count does not match the group. |
| G6 | **A tool with no seat count** covers its personas (all personas when Org-wide). The seat count is a confirmation the AE gives, never written back by code. |
| G7 | **No keep-or-retire decision.** Displacement is like for like. The per-tool forced retirement and intended residual (`product_dispositions`) are retired; G4 replaces the one real question they asked. A tool that does something the customer doesn't use is corrected by unticking that outcome on the tool. |
| G8 | **Headcount from the groups.** The walkthrough asks first whether everyone gets the same licensing: yes asks how many people and creates one group; no collects the groups and shows their sum. `engagements.supported_headcount` stays as an optional cross-check on the detail view's Customer card; when set, a mismatch is a Review finding, never fixed by code. |
| G9 | **Outcome areas.** Library outcomes carry an `area` ("Email security", "Endpoint", …) so the walkthrough asks broad questions first and the specific outcomes beneath them. |
| G10 | **Outcome-first tool collection, then group by group.** The walk shows, per outcome, the Microsoft groups that have it (from the licences entered), the tools named for it and a box to add one; when no Microsoft licence delivers it, *Not currently delivered* confirms every group nothing delivers it to as not having it (`coverage_gap_answers` NotDeliveredToday). A tool's outcomes build up as it is picked again. After pricing (which sets who holds each tool), a **group-by-group review** lists, per group, the outcomes neither its Microsoft licences nor its tools deliver, each with: a tool it does have (gives the group the tool and ticks the outcome on it), a new tool, *covered at no extra cost* (CoveredOutsideInventory), or *not currently delivered*; answers can be undone. |
| G11 | **Price after naming.** Tools are named during the outcome walk; cost, period, seats, renewal and managed-service yes/no are asked afterwards, one tool at a time. |
| G12 | **Contracts and cost lines.** A vendor contract (one invoice, one renewal) can be broken down into cost lines, each with its own cost, seats, personas and outcomes. Displacement is tested per line. What the lines don't account for is kept as a "not broken down" line that carries all of the contract's outcomes. A contract that isn't broken down simply has one line. |
| G13 | **Practice services.** The practice's own services (for example a managed detection and response service or a Teams calling service) are a library object an admin enters in Settings: a name, a kind, a price per user per month and the outcomes they deliver. **Names are runtime data and are never seeded or hard-coded (CLAUDE.md white-label rule).** A service is added to a persona's future state beside the Microsoft plan, and its outcomes count in the replacement test. |
| G13a | **A service quote per customer.** The practice delivering a service quotes a cost and the users it covers; it is entered on Future state beside the groups that get the service (`service_quotes`). The backend spreads the quote over the people in those groups (G2), so the per-user price is derived, never typed. Without a quote the Settings price per user applies. A "Replacing your MDR" panel (Future state and PDF) sets today's managed tools, and what the move retires of each, against the managed security services that replace them. |
| G14 | **Managed tools count in full today.** The software share (tooling %) is what Microsoft can replace; the rest is a line of its own that only a practice managed-security service replaces (ENGINE_SPEC 6.5). A service's price is locked into the engagement when it's added; a later change in Settings is offered with "apply the new price". |
| G15 | **Three result sections**: Microsoft 365 consolidation, managed security, calling, each with its own number, plus a combined total. |
| G16 | **One question per screen.** Large type, Enter to continue, Back always available, a side rail with the groups and their cost per user per month (from the backend). The existing steps stay as the detail view. Uses the practice branding record. |
| G17 | **AI is optional.** The walkthrough works fully with AI off. |
| G18 | **Recommendations at list price.** The recommender prices plans from the catalog at list; the existing scenario discount and price override stay available in the detail view. |
| G20 | **A licence covers the whole group.** In the guided flow every Microsoft licence line is for all of the group's people (seats = headcount); people who get something extra are a separate group. The detail view still takes a typed seat count (bought but unassigned seats, or an Org-wide line), and Review flags any mismatch. |
| G21 | **The guided flow only collects data.** It ends with "Check the data": Review's checks for groups, licences and tools that the customer answers, each with a button to the screen that fixes it (the tool's own pricing screen for a tool), then "Continue to recommendations". Checks about reading the data (what a licence includes, what's set aside) are for the SA team on the detail view's Review; the guided step only counts them. The recommendation, "What's new", the seats question, practice services and the summary live in the detail view (Future state, Summary & PDF). |
| G19 | **Later**: a carve-out linked to its parent that inherits everything except what it adds ("this sub-group gets Visio too"), so shared licensing is entered once; a phased recommendation driven by tool renewal dates; customer self-service; a session file the customer downloads and uploads; custom outcomes in the walk. |

## 3. Changes to the agreed walkthrough

- **W4** ("one set of screens, two depths, no separate expert mode") is replaced by
  G16: a guided flow plus today's steps as the detail view, over the same data.
- **Step 4's keep-or-retire question** is removed (G7). Its place is taken by the
  seat-mismatch question (G4).
- **Step 3** collects tools outcome-first (G10, G11) in the guided flow; the detail
  view keeps the tool table.

## 4. The guided steps

1. Customer: name, workshop date, and how they buy (segment, term, payment; blank
   = the install defaults in Settings, options from the loaded price list).
2. (Removed: the headcount comes from the groups, G8.)
3. Groups: does everyone get the same Microsoft licensing? Yes → one group and its
   size. No → name each group and its size; the screen shows their sum.
4. Microsoft licensing per group: pick the plan, confirm the seats.
5. Outcome walk, area by area (G10).
6. Price your tools, with "Break this down" for contracts (G11, G12).
6a. Group by group: what each group doesn't get today, corrected or confirmed (G10).
7. Today: each group's people × cost per user per month (G3).
8. Check the data: what's missing or doesn't line up, with a way back to fix it (G21).

The recommendation and everything after it (per-group change, what's new, seat
mismatches, managed security, calling, summary and PDF) are on the detail view.

## 5. As built (phase 5)

- Route `#/e/<id>/guide/<step>/<n>`; opening or creating an engagement lands on the
  guided flow, and the header's **Guided / Detail** switch moves between the two.
- The outcome walk is **one screen per area**, with each outcome in the area as a
  row: the Microsoft groups that already have it (from the persona × outcome
  table), the tools named for it, a button to add a tool already named, and a field
  for a new one. A new tool starts on **all groups**; who has it is set on the
  pricing screen. The per-group follow-up is the group-by-group review.
- Every number shown comes from the backend: the interpreter's `baselines` and
  `summary` (count-down, totals, standing seats) and the result's
  `views.personas` (today, after and change per user per month).
- Each capability in the outcome walk has the "+ Add a tool" box and, only when no
  Microsoft licence entered delivers it, a "Not currently delivered" button: it
  confirms, for every group nothing delivers it to, that they don't have it
  (`coverage_gap_answers` NotDeliveredToday), so a plan that adds it counts it as
  gained; ↺ undoes. Hidden once a tool is picked. Groups that differ are corrected on the group-by-group
  review, one screen per group, after pricing.
- The Groups step asks what each group needs whatever plan it's on: Full-Size Mailbox,
  Full-Size Cloud Storage and Desktop Software (library outcomes by key, saved as the
  persona's required outcomes). A new group starts with all three ticked; untick one
  only if the group doesn't need it. The recommender keeps them.
- The Microsoft step takes line items per group: the main plan, then "+ Add another
  licence" for add-ons and products, each with its own seats and price; each adds to
  the group's cost per user; every line covers the whole group (G20). It uses the same searchable plan picker as the detail view (catalog
  pick = name and list price from the server). When a group's size no longer matches
  its licence's seats, the step offers "Set seats to N" (one click, never automatic).
- Review also flags a group holding two full plans (`two_base_plans`), and skips the
  "not confirmed" question for out-of-scope plans and empty groups. Disposition
  tables leave out $0 lines (`views.dispositions_shown`).
- "+ New customer" in the top bar opens `#/new`; "‹ All customers" goes to the search.
- "What's new" comes after the Recommendation (not before Today): one block per group
  listing only what its new plan adds that nothing delivers today, with "These are all
  new for <group>" and a ✕ for one they already have another way. Only these answers
  change a number (a confirmed one counts as gained).
- The Recommendation table shows each group's change per user per month, per year and
  over the engagement's modelling horizon (full yearly rate), with a total row from
  the backend (`views.persona_totals`).
- The Recommendation screen shows Review's future-state checks (for example a
  Business seat cap) beside the plans.

## 6. Build order

Each phase is one pull request to `main`, with its tests.

| Phase | What | Status |
| --- | --- | --- |
| 1 | This plan and the contract changes | done |
| 2 | Interpretation: one splitting rule (G2, G5, G6), the seat-mismatch answer (G4), the persona baseline (G3), total headcount (G8) | done |
| 3 | Retire `product_dispositions` (G7) | done |
| 4 | Outcome areas (G9) | done |
| 5 | The guided walkthrough GUI, steps 1–10 and 13 (G10, G11, G16) | done |
| 6 | Contracts and cost lines (G12) | done |
| 7 | Practice services and result sections, steps 11–12 (G13–G15) | done |
| 8 | Service quotes and "Replacing your MDR" (G13a) | done |
