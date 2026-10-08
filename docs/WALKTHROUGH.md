# Guided walkthrough — the agreed design

> **Status: agreed design, built on today's data model.** This document describes how
> the TCO workshop becomes a guided walkthrough that an account executive can run live
> with a customer. It was agreed in a design review of the current GUI in October 2026,
> and is implemented on the current schema ahead of the TARGET_SCHEMA rebuild, using
> exactly the shapes §12 added to that contract.
>
> - The screens are built as described here. Field-level detail is in the code and in
>   [`DATA_MODEL.md`](DATA_MODEL.md).
> - The data this design needs is specified by [`TARGET_SCHEMA.md`](TARGET_SCHEMA.md),
>   which carries the additions in §12 (decisions D19–D24 and the items marked
>   *walkthrough*).
> - A follow-up review the same month added licences the library doesn't know, out-of-
>   scope licence lines, library updates and the run-rate headline (W7, W8, W12–W14).
>   Those parts are being built in the order given in §11.
> - The calculation changes in §4 are specified in [`ENGINE_SPEC.md`](ENGINE_SPEC.md)
>   §6.11 and covered by the engine's unit tests, as `CLAUDE.md` requires.

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
| W7 | **The yearly run rate is the headline, then the ramp.** Three sub-lines: duplicate spend today, consolidation, over-licensing. The ramp shows how the run rate is reached, year by year. | §4 |
| W8 | **Savings start when a contract allows it.** Each saving is timed by the renewal that unlocks it. No renewal date means month-to-month with no lock-in: it counts from day one. | §4 |
| W9 | **The PDF is the leave-behind.** | §7 |
| W10 | **AI is optional.** The workshop calculates and produces the PDF with AI off. | §8 |
| W11 | **Help text is written for the customer to read**, and one file feeds both the tooltips and the PDF's method page. | §9 |
| W12 | **Every Microsoft licence is read or set aside.** A licence the library doesn't know is answered on the Other tools step: the same as a library plan, the outcomes it delivers, or out of scope. Until then its group's capability changes are left out. Nothing is asked twice. | §3, §5 |
| W13 | **Library updates are reviewed, never automatic.** When the shared library changes after an engagement was created, the engagement shows each difference; nothing changes without a click, and "not for this customer" is remembered. | §3 |
| W14 | **The library learns from the workshop.** Licence names AEs had to answer by hand are listed in Settings for an admin to add to the library. | §3 |

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
  - **out of scope for this workshop**: for licences no plan includes, such as Visio,
    Project or Teams Rooms. The line stays visible, greyed, and is in no number;
  - the group's required capabilities (the default is everything it has today).
- A licence the library doesn't know is flagged on its row, with a link to the card on
  step 3 where it is answered. Nothing about it is asked here.

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

#### Microsoft licences we can't read yet

A separate card on this step, beside the tools, because the question is the same one:
"what does it deliver?". It lists each licence name from step 2 that the library
doesn't know, or that this engagement has no coverage for, once per name, with the
groups that hold it. Each takes one answer, written to every line with that name:

1. **It's the same as** a library plan, picked from a list. The customer's name for
   the line is kept; only this engagement records the link.
2. **It delivers these outcomes:** ticked by hand, or suggested by AI and confirmed,
   with the same controls as a tool's uses.
3. **Use the library's list:** offered when the library knows the plan but this
   engagement was created before it did.
4. **Out of scope for this workshop:** the same answer as on the licence line (step 2).

Until a licence is answered, its cost still counts, but its group's capability changes
are left out (§5) and the card, Coverage check and Review all say so.

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
- **Every item has a fix.** Each warning on this step is answered in place or links to
  the step where it is fixed: a licence the library doesn't know links to its card on
  step 3, a future plan with no capabilities to step 4, licences counted org-wide to
  step 2. The unknown-licence warning is shown first, and loudly.
- **Library updates** (W13): a card listing each difference between the shared
  library and this engagement's copy (TARGET_SCHEMA §4.7). For example:
  - "Microsoft 365 E3 now includes Endpoint Privilege Management";
  - "The library no longer lists X for Y";
  - "New outcome: Email & Calendar (in E1, E3, E5, …)";
  - "The library now has Exchange Online (Plan 2): this engagement lists A and B,
    the library lists A, B and C".

  Each has **Apply** and **Not for this customer**, and there is an **Apply all**.
  Nothing changes until one is clicked; a declined item doesn't come back. A notice
  when the engagement is opened says how many are waiting. Applying moves the
  numbers; a PDF already presented keeps its own snapshot.
- **Expander:** this engagement's Microsoft coverage, its own copy of the library
  (TARGET_SCHEMA §4.7). Library edits stay in Settings.
- **Settings** lists the licence names AEs answered by hand (W14), with how many
  engagements answered each way and never which customers, so an admin can add a plan
  or a name alias to the library. The "Update outcomes" button, which overwrote an
  engagement's outcomes and deleted its custom ones, is removed.

### Step 6 — Review

- Runs the checks in §6, plus the AI sanity check when AI is on.
- For each finding, the AE asks the customer to clarify. Anything that can't be
  resolved is left out (§5), and the review lists exactly what is being left out.

### Step 7 — Summary and PDF

- **Show:**
  - the run rate and its three sub-lines, then the ramp (§4);
  - the number of years to model (the default is 3).
- **Expander:** branding colours; the partner funding (ECIF) note.
- **Produce the PDF** (§7). Producing it saves a Presented snapshot (TARGET_SCHEMA §8),
  so later edits show as "changed since workshop".

---

## 4. The headline

The headline is the **yearly run rate**: what the customer saves each year once every
contract has renewed. It is made of three sub-lines, each also a yearly amount:

| Sub-line | What it is | Today |
| --- | --- | --- |
| **Duplicate spend today** | Tools whose every use is already covered by the customer's current Microsoft licences. No licensing change. | Exists: "Retire duplicate tools today" (`frontend/src/components/Readout.jsx`) |
| **Consolidation** | Moving each group to its future plan and retiring the tools it replaces. | Exists: "Move each persona to right-sized licensing" |
| **Over-licensing** | Unused seats the customer confirmed are not needed. | New |

- Seats the customer says are intended are noted on the group's page and are not
  counted anywhere.
- Unanswered unused seats are left out.
- When the run rate is a cost (an uplevel), the headline reads "Invest $X per year to
  gain N new capabilities" (W6), counting the capabilities the customer confirmed are
  new; with none confirmed, it reads "$X per year added".

### The ramp

After the run rate, the readout and the PDF show how it is reached:

- **a bar per modelled year** with that year's amount and the running total, ending at
  the total over the modelled years;
- **"full run rate from month N"**: the month the last saving starts. When that is
  beyond the modelled years, it says so;
- the **renewal table** below: each amount, the date it is timed by, and the months it
  counts.

The run rate is never multiplied by the modelled years: a tool that renews in month 30
saves only 6 months inside a 36-month window.

### Timing

Before the walkthrough, every saving counted from day one ("Figures assume full
savings from day one"). The walkthrough times each saving by the contract that
unlocks it:

- **A tool stops at its renewal.** A tool's saving, whether duplicate spend or
  consolidation, starts at the tool's renewal date.
- **Microsoft increases can start any time.** Microsoft lets a customer add seats or
  add-ons, or upgrade, mid-term. So a group's added Microsoft cost starts when the
  first tool only the move can retire renews, because that is when the upgrade is
  needed. A duplicate tool (duplicate spend today) retires without the move, so it
  does not pull the cost forward. A group whose move retires no such tool starts its
  added cost on day one.
- **Microsoft reductions wait for the Microsoft renewal.** Removing unused seats
  (over-licensing), or moving a group to a cheaper plan, starts at the Microsoft
  renewal date. That is the licence's own date if one is set, otherwise the
  engagement's.
- **Missing dates.** No renewal date means month-to-month with no lock-in: the amount
  counts from day one. The renewal table and the PDF say "no date — counted from
  today", and the review notes every missing date.

The run rate, the yearly ramp and the renewal table are calculated once, in the
engine. The GUI and both exports display them; none of them calculates them
(TARGET_SCHEMA §7, D16 as changed in §12).

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
| A renewal date | Treated as month-to-month: counted from day one, and marked "no date" (§4). |
| What a Microsoft licence delivers, when the library doesn't know it | Its cost still counts, but its group's capability changes are left out of the readout and the PDF (W12). |

The review (§6) lists everything being left out, so the AE can ask before producing
the PDF.

---

## 6. Review checks

These checks work without AI:

- group headcounts compared with the employee count;
- each licence's assigned seats compared with the headcount of the groups that
  receive it;
- tools with no cost, no users, or no uses ticked;
- Microsoft licences the library doesn't know and that aren't answered yet (W12);
- licences set aside as out of scope (a note);
- library updates not yet reviewed (a note);
- missing renewal dates (a note: they count from day one);
- unused seats not yet answered;
- capability gaps not yet answered;
- partly replaced tools with no keep-or-retire answer;
- the existing checks in `backend/app/services/compute.py` (`persona_coverage_gaps`,
  `dropped_capability`):
  - a future plan with no mapped capabilities;
  - licensing counted org-wide;
  - a capability the future plan drops.

With AI on, the AI sanity check adds its findings to the same list.

---

## 7. The PDF

The PDF is the leave-behind for the customer. It has four parts:

1. **Title page:** customer name, logo, workshop date.
2. **Overview:**
   - the run rate and its three sub-lines;
   - every group and its recommendation;
   - the ramp: the yearly bars, "full run rate from month N", and the renewal table.
3. **One section per group:**
   - current state;
   - what is replaced: tools, with their renewal dates;
   - future state;
   - economic impact;
   - capability changes, with new capabilities highlighted;
   - AI narratives, when they have been generated.
4. **How we calculated this:**
   - the method, from the same text as the tooltips (§9);
   - the assumptions: list prices assumed, the managed-service share, renewal dates
     not given (counted from day one);
   - "Not part of this workshop": the licences set aside as out of scope.

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
- **Removed:** the "Update outcomes" button (W13 replaces it).
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
4. **The follow-up review** (W7, W8, W12–W14), each its own PR:
   1. this file and TARGET_SCHEMA (D8 revised, D16, D19, D23, D24);
   2. the engine and the headline: no date counts from day one, out-of-scope lines,
      the run rate first and the yearly ramp (ENGINE_SPEC and tests in the same PR);
   3. library content: the email outcomes, Exchange Online, the "(no Teams)" suites,
      Teams Enterprise and the stand-alone licences, with aliases;
   4. the unknown-licence card, the warnings that link to it, and the Settings list;
   5. library updates, and removing "Update outcomes".

---

## 12. Contract changes this needs (now in TARGET_SCHEMA.md)

| Change | TARGET_SCHEMA section |
| --- | --- |
| `engagements`: Microsoft agreement renewal date (nullable). | §4.1 |
| `current_microsoft_licenses`: renewal date (nullable = the engagement's date). | §4.4 |
| `current_microsoft_licenses`: the answer for unused seats: *intended*, *not needed*, or unanswered. | §4.4 |
| A coverage-gap answer per persona and outcome: *not delivered today* or *covered outside this inventory*. It replaces the $0 "Covered elsewhere (out of scope)" placeholder tool that Coverage Check creates today. | new, beside §4.7 |
| The headline: the three sub-lines in §4, each timed by its renewal and summed month by month over the horizon. This replaces "net change × horizon" (D16). `c_proposal_results` holds each sub-line. | §0 D16, §7 |
| Rule: the engine and the PDF never depend on an AI call. | §13 |
| *Follow-up review:* the headline leads with the yearly run rate; the ramp is per year (`c_headline_years`, `full_run_rate_month`). | §0 D16, §7 |
| *Follow-up review:* a missing renewal date means month-to-month, counted from day one. | §0 D19, §4.1, §7.1 |
| *Follow-up review:* a licence line is linked, mapped to outcomes (`current_license_outcomes`), out of scope (`out_of_scope`), or unread; unread lines leave their personas' outcome changes out. | §0 D23, §4.4, §6.3 |
| *Follow-up review:* each engagement keeps its own copy of the library; updates arrive through the Library updates review (`engagement_outcomes`, `engagement_bundle_coverage`, `library_update_decisions`). This revises D8. | §0 D8, §4.7 |
| *Follow-up review:* bundle name aliases as data, and the Settings list of names answered by hand. | §0 D24, §3.1, §3.4 |

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
- **The recommender** ("creative bundles"): composing add-ons that retire tools,
  preferring an uplevel, and pricing new customers on the "(no Teams)" suites. Next,
  with its own design review (TARGET_SCHEMA §16).
- **Plans built from their component licences** (TARGET_SCHEMA §16): recorded as a
  possibility, not designed.

---

## 14. Points to confirm in review

This rule was derived from the agreed principles but not discussed directly:

- A group's added Microsoft cost starts when the first tool only the move can retire
  renews (a duplicate tool retires without the move, so it doesn't count). A group
  whose move retires no such tool starts its added cost on day one (§4).

The other rule once listed here, a missing renewal date, was settled in the follow-up
review: it means month-to-month, counted from day one (W8).
