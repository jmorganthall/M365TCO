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
| G8 | **Total headcount.** The walkthrough asks "how many people does IT support?" first and stores it (`engagements.supported_headcount`). Groups count down to it; a mismatch is a Review finding, never fixed by code. |
| G9 | **Outcome areas.** Library outcomes carry an `area` ("Email security", "Endpoint", …) so the walkthrough asks broad questions first and the specific outcomes beneath them. |
| G10 | **Outcome-first tool collection.** "How do you deliver *Endpoint protection* today?" Answers: Microsoft (offered when a group's current plan includes it), a tool already named, a new tool, or skip. A tool's outcomes build up as it is picked again. After the walk, a gap sweep asks about outcomes nothing was collected for: not delivered today, or covered by something not paid for separately (`coverage_gap_answers`). |
| G11 | **Price after naming.** Tools are named during the outcome walk; cost, period, seats, renewal and managed-service yes/no are asked afterwards, one tool at a time. |
| G12 | **Contracts and cost lines.** A vendor contract (one invoice, one renewal) can be broken down into cost lines, each with its own cost, seats, personas and outcomes. Displacement is tested per line. What the lines don't account for is kept as a "not broken down" line that carries all of the contract's outcomes. A contract that isn't broken down simply has one line. |
| G13 | **Practice services.** The practice's own services (for example a managed detection and response service or a Teams calling service) are a library object an admin enters in Settings: a name, a kind, a price per user per month and the outcomes they deliver. **Names are runtime data and are never seeded or hard-coded (CLAUDE.md white-label rule).** A service is added to a persona's future state beside the Microsoft plan, and its outcomes count in the replacement test. |
| G14 | **Managed security is compared on the managed share.** A managed tool counts at its tooling % against Microsoft (ENGINE_SPEC 6.5). A practice managed-security service is compared against the other share, the managed part. |
| G15 | **Three result sections**: Microsoft 365 consolidation, managed security, calling, each with its own number, plus a combined total. |
| G16 | **One question per screen.** Large type, Enter to continue, Back always available, a side rail with the groups and their cost per user per month (from the backend). The existing steps stay as the detail view. Uses the practice branding record. |
| G17 | **AI is optional.** The walkthrough works fully with AI off. |
| G18 | **Recommendations at list price.** The recommender prices plans from the catalog at list; the existing scenario discount and price override stay available in the detail view. |
| G19 | **Later**: a phased recommendation driven by tool renewal dates; customer self-service; a session file the customer downloads and uploads; custom outcomes in the walk. |

## 3. Changes to the agreed walkthrough

- **W4** ("one set of screens, two depths, no separate expert mode") is replaced by
  G16: a guided flow plus today's steps as the detail view, over the same data.
- **Step 4's keep-or-retire question** is removed (G7). Its place is taken by the
  seat-mismatch question (G4).
- **Step 3** collects tools outcome-first (G10, G11) in the guided flow; the detail
  view keeps the tool table.

## 4. The guided steps

1. Customer: name and workshop date.
2. Headcount: how many people IT supports (G8).
3. Groups: does everyone get the same Microsoft licensing? Yes → one group. No → name
   each group and its size, counting down to the total.
4. Microsoft licensing per group: pick the plan, confirm the seats.
5. Outcome walk, area by area (G10).
6. Price your tools, with "Break this down" for contracts (G11, G12).
7. Gap sweep (G10).
8. Today: each group's people × cost per user per month (G3).
9. Recommendation: each group's Microsoft plan, its cost per user and what changes.
10. Seat mismatches, only when needed (G4).
11. Managed security (G13, G14).
12. Calling (G13).
13. Summary and PDF (G15).

## 5. As built (phase 5)

- Route `#/e/<id>/guide/<step>/<n>`; opening or creating an engagement lands on the
  guided flow, and the header's **Guided / Detail** switch moves between the two.
- The outcome walk is **one screen per area**, with each outcome in the area as a
  row: the Microsoft groups that already have it (from the persona × outcome
  table), the tools named for it, a button to add a tool already named, and a field
  for a new one. A new tool starts on **all groups**; who has it is set on the
  pricing screen. The per-group follow-up ("And Frontline?") is the gap sweep.
- Every number shown comes from the backend: the interpreter's `baselines` and
  `summary` (count-down, totals, standing seats) and the result's
  `views.personas` (today, after and change per user per month).
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
| 6 | Contracts and cost lines (G12) | — |
| 7 | Practice services and result sections, steps 11–12 (G13–G15) | — |
