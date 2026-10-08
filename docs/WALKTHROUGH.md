# Guided walkthrough — the agreed design (not yet built)

> **Status: agreed design, implementation pending.** This document describes how the
> TCO workshop becomes a guided walkthrough that an account executive can run live
> with a customer. It was agreed in a design review of the current GUI in October 2026.
>
> - The **current** screens are described by the code and by
>   [`DATA_MODEL.md`](DATA_MODEL.md). They stay as they are until each part below lands.
> - The data this design needs is specified by [`TARGET_SCHEMA.md`](TARGET_SCHEMA.md).
>   The additions in §12 are **not yet in that document**. They land there first, in
>   their own reviewed PR, before any code that depends on them.
> - The calculation changes in §4 land in [`ENGINE_SPEC.md`](ENGINE_SPEC.md) and the
>   engine's unit tests before any code uses them, as `CLAUDE.md` requires.

---

## 0. The decisions this design encodes

| # | Decision | Where |
| --- | --- | --- |
| W1 | **One pass, run by the account executive.** The AE collects the data live on a shared screen. A review step at the end catches what doesn't line up. No hand-off to the SA team is required. | §1 |
| W2 | **Unanswered means left out.** Anything the customer can't answer is left out of the numbers and the PDF. There are no escalation flags. | §5 |
| W3 | **Never show another customer.** Nothing on the working screen lists or names other engagements. | §2 |
| W4 | **One set of screens, two depths.** The basics are visible; unusual cases use each card's expander. There is no separate expert mode. | §3, §10 |
| W5 | **Groups are defined by the licences they receive.** Each group has one future state: one scenario per persona per proposal, as in TARGET_SCHEMA §5.2. | §3 |
| W6 | **Savings first.** Each group leads with what consolidation saves. Where a group can't save, it leads with the capabilities it gains. Every group shows both. | §3, §7 |
| W7 | **One headline, three sub-lines:** duplicate spend today, consolidation, over-licensing. | §4 |
| W8 | **Savings start when a contract allows it.** Each saving is timed by the renewal that unlocks it. | §4 |
| W9 | **The PDF is the leave-behind.** | §7 |
| W10 | **AI is optional.** The workshop calculates and produces the PDF with AI off. | §8 |
| W11 | **Help text is written for the customer to read**, and one file feeds both the tooltips and the PDF's method page. | §9 |

---

## 1. Who runs it, and how

- The **account executive** runs the workshop with the customer on a shared screen.
  They are not expected to know licensing detail. The tooltips say what is being asked
  and how the answer is used.
- **One pass.** The AE collects what is needed, in order. The review step (§6) lists
  what doesn't line up, and the AE asks the customer to clarify on the spot. Anything
  that still can't be answered is left out (§5). There is no flag-for-review step and
  no second pass.
- The SA team uses the same screens. The expanders hold the detail needed for unusual
  cases.

---

## 2. Never showing another customer

Today three things expose other customers:

- The left sidebar lists every engagement by customer name and is always visible on
  desktop (`frontend/src/components/Sidebar.jsx`).
- A reload always returns to that list, because navigation lives only in React state
  (`frontend/src/App.jsx`).
- `GET /api/engagements` returns every customer's name, profile, notes and logo to
  anyone who can reach the app (`backend/app/routers/engagements.py`).

**Now** (front end and the list endpoint only, no data-model change):

- The engagement list leaves the working screen. Engagements are opened from an
  **Open engagement** page that shows a search box and no list until something is
  typed.
- Each engagement has its own address, so a reload returns to it instead of the list.
- Inside an engagement, nothing lists or names other engagements. Settings, the Data
  inspector and the update and pricing banners are not on the presenting screen.
- The list endpoint stops returning notes and logos.

**With the rebuild:** sign-in and engagement membership (TARGET_SCHEMA §9) stop one AE
from reaching another AE's customers at all.

---

## 3. The steps

For each step: what the AE asks, the basics shown by default, and what sits in the
card's expander.

### Step 1 — Customer

- **Ask:** who the customer is.
- **Basics:**
  - customer name;
  - workshop date (shown on the PDF title page);
  - logo (shown on the PDF title page);
  - optional: industry, headquarters, website and notes, which are context for AI;
  - optional: employee count, which is context for AI and feeds a review check.
- **Expander:**
  - pricing basis (segment, term, payment);
  - market and currency, shown but not editable (only the catalog's value is valid
    today).

### Step 2 — Groups and their licences

- **Ask:**
  - "Which groups of users get different licences today? How many are in each, and
    what do they get?"
  - "When does your Microsoft agreement renew?"
  - For each licence with unused seats: "Are these spare seats intended?"
- **Basics:**
  - each group: name, headcount, and a description (shown on the group's PDF page);
  - each licence: the product, the groups that receive it, the number bought, the
    number assigned, and an optional "what do you pay?" (the default is list price);
  - the Microsoft renewal date, one per engagement;
  - for unused seats (bought − assigned): *intended*, meaning kept on hand for quick
    onboarding, or *not needed*.
- **Expander:**
  - per-user or tenant-wide scope;
  - a licence's own renewal date, when it differs from the engagement's;
  - the licence's pricing basis and price-override detail;
  - the group's required capabilities (the default is everything it has today).

### Step 3 — Other tools

- **Ask:** "What else do you buy that overlaps with Microsoft 365? What does it cost,
  when does it renew, who uses it, and what do you use it for?"
- **Basics:**
  - name;
  - cost and period (monthly or yearly);
  - renewal date;
  - who uses it: whole groups, everyone, or a number of people;
  - whether it is bought directly or through a managed service (yes/no);
  - what it is used for: capabilities ticked by hand, or pre-ticked by AI and
    confirmed by the AE with the customer.
- **Expander:**
  - vendor;
  - tooling % for a managed service. The default is 30%: the share of a managed
    service's price that is software Microsoft 365 can replace (ENGINE_SPEC §6.5).
  - the number of people covered, when it differs from the groups' headcount.
- When every ticked use of a tool is covered by the customer's current Microsoft
  licences, the step says so right away: that tool is duplicate spend today (§4).
- A use the customer confirms counts at once. The PDF labels it "confirmed by the
  customer in the workshop". This meets TARGET_SCHEMA §1.4, because a person
  confirmed it.

### Step 4 — Future state per group

- **Show:** each group's recommended plan, pre-filled by the best-bundle recommender.
  It is the lowest-cost plan that keeps every capability the group has today, and the
  AE can change it.
- Each group shows its saving and the capabilities it gains. Where the best plan costs
  more, the group leads with the capabilities gained.
- **Ask**, for any tool only partly replaced: keep it for the remaining users, or
  retire it?
  - *Keep* maps to today's intended residual.
  - *Retire* maps to today's forced full elimination, and its reason is required.
  - Unanswered means the tool is assumed kept.
- **Expander:**
  - add-ons;
  - discount or a typed net price;
  - term and payment;
  - in scope;
  - the Business seat-cap switch;
  - splitting off a sub-group (carve-out).

### Step 5 — Coverage check

- **Ask**, for each capability the new plan would add that nothing delivers today:
  "Is that expected, or is it covered somehow outside this inventory?"
- **Answers:**
  - *Not delivered today:* it is shown as a new capability.
  - *Covered outside this inventory:* it is never claimed as new and never costed.
- This is today's Coverage Check (`frontend/src/components/CoverageCheck.jsx`), kept
  as it works today.
  - It comes after the future state because it asks only about what the new plan
    would add.
  - The "covered outside" answer moves from today's $0 placeholder tool to an answer
    stored on the gap (§12).
- **Expander:** this engagement's exceptions to the shared Microsoft coverage library
  (TARGET_SCHEMA §4.7). Library edits stay in Settings.

### Step 6 — Review

- Runs the checks in §6, plus the AI sanity check when AI is on.
- For each finding, the AE asks the customer to clarify. Anything that can't be
  resolved is left out (§5), and the review lists exactly what is being left out.

### Step 7 — Summary and PDF

- **Show:**
  - the headline and its three sub-lines (§4);
  - the number of years to model (the default is 3).
- **Expander:** branding colours; the partner funding (ECIF) note.
- **Produce the PDF** (§7). Producing it saves a Presented snapshot (TARGET_SCHEMA §8),
  so later edits show as "changed since workshop".

---

## 4. The headline

One headline, the total over the modelled years, made of three sub-lines:

| Sub-line | What it is | Today |
| --- | --- | --- |
| **Duplicate spend today** | Tools whose every use is already covered by the customer's current Microsoft licences. No licensing change. | Exists: "Retire duplicate tools today" (`frontend/src/components/Readout.jsx`) |
| **Consolidation** | Moving each group to its future plan and retiring the tools it replaces. | Exists: "Move each persona to right-sized licensing" |
| **Over-licensing** | Unused seats the customer confirmed are not needed. | New |

- Seats the customer says are intended are noted on the group's page and are not
  counted anywhere.
- Unanswered unused seats are left out.

### Timing

Today every saving counts from day one, and the readout says so: "Figures assume full
savings from day one" (`backend/app/services/exporter.py`). The walkthrough times
each saving by the contract that unlocks it:

- **A tool stops at its renewal.** A tool's saving, whether duplicate spend or
  consolidation, starts at the tool's renewal date.
- **Microsoft increases can start any time.** Microsoft lets a customer add seats or
  add-ons, or upgrade, mid-term. So a group's added Microsoft cost starts when the
  first tool it replaces renews, because that is when there is something to retire. A
  group that retires no tool starts its added cost on day one.
- **Microsoft reductions wait for the Microsoft renewal.** Removing unused seats
  (over-licensing), or moving a group to a cheaper plan, starts at the Microsoft
  renewal date. That is the licence's own date if one is set, otherwise the
  engagement's.
- **Missing dates.** A missing renewal date is assumed to be one year after the
  workshop date, and the PDF marks it as assumed. The review lists every missing date.

The headline is calculated month by month over the modelled years, once, in the
engine. The GUI and both exports display it; none of them calculates it (TARGET_SCHEMA
§7, D16 as changed in §12).

---

## 5. Left out when unanswered

Nothing is counted that the customer could not confirm.

| Unanswered | Effect |
| --- | --- |
| A tool's cost | The tool is left out of the numbers and the PDF. |
| Who uses a tool | Same. Today such a tool earns $0 and only the readout's "covers not set" badge shows why (`frontend/src/components/Readout.jsx`). |
| What a tool is used for | The tool can't be displaced, so it is left out. |
| A capability gap | It is not claimed as new. |
| Whether unused seats are intended | They are not counted. |
| Keep or retire a partly replaced tool | The tool is assumed kept. Its remaining cost stays in the future state. |
| A renewal date | Assumed to be one year after the workshop date, and marked as assumed (§4). |

The review (§6) lists everything being left out, so the AE can ask before producing
the PDF.

---

## 6. Review checks

These checks work without AI:

- group headcounts compared with the employee count;
- each licence's assigned seats compared with the headcount of the groups that
  receive it;
- tools with no cost, no users, or no uses ticked;
- missing renewal dates;
- unused seats not yet answered;
- capability gaps not yet answered;
- partly replaced tools with no keep-or-retire answer;
- the existing checks in `backend/app/services/compute.py` (`persona_coverage_gaps`,
  `dropped_capability`):
  - licensing not mapped to a product;
  - a future plan with no mapped capabilities;
  - licensing counted org-wide;
  - a capability the future plan drops.

With AI on, the AI sanity check adds its findings to the same list.

---

## 7. The PDF

The PDF is the leave-behind for the customer. It has four parts:

1. **Title page:** customer name, logo, workshop date.
2. **Overview:**
   - every group and its recommendation;
   - the headline and its three sub-lines;
   - a renewal timeline.
3. **One section per group:**
   - current state;
   - what is replaced: tools, with their renewal dates;
   - future state;
   - economic impact;
   - capability changes, with new capabilities highlighted;
   - AI narratives, when they have been generated.
4. **How we calculated this:**
   - the method, from the same text as the tooltips (§9);
   - the assumptions: list prices assumed, the managed-service share, assumed renewal
     dates.

Anything left out (§5) does not appear. The Excel export stays as it is today, for
the team.

---

## 8. AI is optional

- **With AI off, every step works by hand.**
  - Tool uses are ticked by hand from the capability list.
  - Licences and tools are typed in, because the paste import needs AI.
  - There is no customer research, no narratives and no sanity check.
- **With AI on:**
  - AI pre-ticks tool uses, which the AE confirms live;
  - it researches the customer and writes group narratives;
  - it runs the sanity check and parses pasted lists.
  - Customer data, including the name, may be sent to the configured AI service.
- **Rule:** nothing the engine or the PDF needs may depend on an AI call (§12).

---

## 9. Help text

- Every question and every figure on screen has a short tooltip that says **what we
  are asking for** and **how it is used** in the calculation. It is written for the
  customer to read.
- All help text lives in one file in the repository. It changes through a reviewed
  PR, in the same PR as any calculation change it describes. It is not editable in
  Settings.
- The PDF's method page is built from the same text, so the screen and the paper can't
  disagree.

---

## 10. Fields

The steps in §3 place every field the walkthrough uses: basics are shown by default,
everything else sits in the card's expander.

- **Removed:** third-party "unit basis". TARGET_SCHEMA already removes it (D13).
- **New:** see §12.
- **Fields that have no reader today gain one:**

  | Field | Its new reader |
  | --- | --- |
  | Workshop date | PDF title page |
  | Persona description | the group's PDF section |
  | Licence quantity purchased | over-licensing (§4) and the review (§6) |
  | Third-party vendor | the PDF's tool list and the AI coverage prompt |

---

## 11. Order of work

1. **Now, with no data-model change.** Each is its own PR:
   1. Customer names off the working screen (§2, "Now").
   2. Fix: business narratives never reach the HTML or Excel readout.
      `_computed_dict` in `backend/app/routers/engagements.py` never adds them, so
      the exporter's business-case section is always skipped.
   3. The help-text file, and tooltips on today's screens.
   4. Reshape today's cards into basics plus expander (§3), reusing the existing
      expanders.
2. **Documents.** First this file. Then the TARGET_SCHEMA additions in §12, in their
   own PR. Then the ENGINE_SPEC changes for §4, with tests.
3. **Build** the guided flow and the PDF on the new data model as the rebuild lands.

---

## 12. Contract changes this needs (not yet in TARGET_SCHEMA.md)

| Change | TARGET_SCHEMA section |
| --- | --- |
| `engagements`: Microsoft agreement renewal date (nullable). | §4.1 |
| `current_microsoft_licenses`: renewal date (nullable = the engagement's date). | §4.4 |
| `current_microsoft_licenses`: the answer for unused seats: *intended*, *not needed*, or unanswered. | §4.4 |
| A coverage-gap answer per persona and outcome: *not delivered today* or *covered outside this inventory*. It replaces the $0 "Covered elsewhere (out of scope)" placeholder tool that Coverage Check creates today. | new, beside §4.7 |
| The headline: the three sub-lines in §4, each timed by its renewal and summed month by month over the horizon. This replaces "net change × horizon" (D16). `c_proposal_results` holds each sub-line. | §0 D16, §7 |
| Rule: the engine and the PDF never depend on an AI call. | §13 |

No change is needed for:

- the keep-or-retire answer: it uses `tool_decisions`, §5.4;
- the Presented snapshot made with the PDF: §8;
- one scenario per persona: §5.2.

---

## 13. Later, or out of scope

- **An export for CSP quoting tools:** product, seat count, term and billing per
  group. It comes later, and the pricing-basis fields stay for it.
- **Side-by-side proposals** (TARGET_SCHEMA §5.1): not needed for the walkthrough.
- **A shared library of common third-party tools and their usual uses:** not now.
  Without AI, AEs tick each tool's uses by hand.

---

## 14. Points to confirm in review

These two rules were derived from the agreed principles but not discussed directly:

1. A missing renewal date is assumed to be one year after the workshop date (§4).
2. A group's added Microsoft cost starts when the first tool it replaces renews. A
   group that retires no tool starts its added cost on day one (§4).
