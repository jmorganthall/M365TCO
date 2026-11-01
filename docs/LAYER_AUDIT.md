# Layer audit — where the app strays from raw → interpreted → calculated

`TARGET_SCHEMA.md` §1 is the agreed model: raw data only people write, one pure
interpreter, an engine that calculates, and screens that only display. This audit
lists where today's code departs from that and the order the gaps are being closed.
It is a work tracker, not a contract. `TARGET_SCHEMA.md` stays the contract.

## Phase 0 — trust breakers (done)

| Finding | Fix |
| --- | --- |
| Opening `readout.html` / `.xlsx` ran `compute_and_persist`, which wrote spend caches and disposition outputs and could erase an operator's decision | `services/results.current_result` is the one pure read path. The caches are retired. A decision with no current effect is flagged `decision_not_applicable` and kept (§1.2.3) |
| No write recomputed; the GUI called `POST /compute` on some tabs. Edits on Licences, Tools, Personas, Coverage and scenario create/delete didn't refresh the numbers | `GET …/result` is calculated on read. `api.js` refetches after every write (`useResult`) and the Recompute button is gone |
| Several fields saved on every keystroke | They save on Enter or blur |
| The Readout licence-limit card and Review / split offer counted Business seats differently | `limits._future_seat_usage` is the one count. Caps match by bundle id |
| Untagged licence lines: the engine split them over personas with a scenario, the recommender ignored them, everything else read them as everyone | Org-wide = every persona, everywhere (D12) |
| Reading the practice branding created its row | Reading returns an unsaved blank record |

## Phase 1 — `interpret()` on today's schema (done)

A pure `backend/tco_engine/interpret.py` that returns, in memory, the shapes in
§6.1–6.4. It folds today's copies of each rule into one definition.

**1a (done):**
- `services/raw.py` loads what people entered.
- `interpret()` reads it into: licence lines (read state, effective price and
  source, applies-to, assumed); licence and tool cost shares; tools (cost,
  tooling %, covers, cost per person, each with its source); scenarios
  (composition, net, coverage, complete).
- `to_engine()` builds the engine's input from those values. `compute.hydrate`
  is now just that.
- The engine no longer reads the tool-cost columns stored on the raw row.
- The 6.3/6.5 formulas are one function each (`tco_engine/money.py`).
- `GET …/interpreted` shows the layer.
- A parity test (removed with Phase 2's cleanup) checked that the engine input
  was identical to the old hydrator's for every engagement the suite built.

**1b (done):**
- The persona × outcome tables: today, after the move, each status, and what
  delivers each outcome.
- Each tool's retirement and quick win per persona.
- Which personas' outcome changes are unknown (an unread line).
- The 6.3a/6.6 rules are one function each (`tco_engine/rules.py`), shared with
  the engine.
- The parity test checks the tables against the Coverage check and the
  engine's credited tools for every engagement the suite builds.

**1c (done):**
- The seat-cap count moved into the interpreter (`seat_caps`).
  `limits.evaluate` and `seat_cap_context` only read it.
- Review's checks are the interpreter's `findings`, structured (code, step,
  subject, facts); `services/review.py` only words them. Two checks come from
  outside the interpreter: a partly replaced tool with no keep-or-retire answer
  (an engine result) and pending library updates.
- Licence-name normalization is one function.
- Parity: frozen copies of the old Review and seat-cap count must match for
  every engagement the suite builds.

Phase 1 is complete: every rule in the table below is now defined in the
interpreter. Phase 2 moves each remaining consumer onto it and deletes its
private copy, then deletes the frozen copies.

| Interpreted output | Folded from |
| --- | --- |
| licence lines: read state, effective price and its source, applies-to (Org-wide `assumed`) | `compute.licence_readings`, `PersonaScenario`/`CurrentMicrosoftLicense` price properties, the engine's pool |
| licence and tool cost shares | `engine.py` per-scenario loop, the recommender's copy in `compute.analyze_persona_bundles` |
| tools: annual cost, effective tooling %, people covered, cost per person | `routers/entities._normalize_third_party` (writes these onto the raw row today) |
| scenarios: base + add-ons by bundle id, composed list, net, complete/incomplete | `compute.hydrate`, `compute._scenario_cover`, `lineage.py`, `limits` |
| persona × outcome today/after, tool retired, quick win | `persona_coverage_gaps`, `new_outcomes`, `dropped_capability`, and the five "tool retired" checks (`engine._scenario_displaces_product`, `compute._retired`, `compute._plan_fitting`, `lineage._plan_retires`, the readout's kept-tools filter) |
| seat caps | `limits._future_seat_usage` (done in 1c) |
| findings | `services/review.py` |

Gate: the engine's output from `interpret()` equals today's output on every test
fixture (done for 1a) and on a `tools/baseline.py` export of a live instance.

## Phase 2 — every consumer reads interpret/engine output (done)

Each consumer dropped its private reading of raw data and was checked against a
frozen copy of its old code for every engagement the suite builds. The frozen
copies, the old helpers (`compute.licence_readings`, `_ratified_*`, `_cover_key`)
and the ORM price properties are now deleted.

- **Backend:** the Coverage check, the recommender (including the split's "people
  over the cap"), the split offer, carve-out drift, the unknown-licence card,
  library updates, the Readout/PDF/Excel and the AI narrative input.
- **GUI:**
  - prices, % off list and unused seats come from `useInterpreted`;
  - the tool "left out" badge, unread licences and personas without a plan are
    findings;
  - carved-out seats come from the interpreter;
  - Partly replaced uses the engine's population summary (6.4a);
  - the Summary's sums come from `services/results.views`, which the HTML
    readout shares.

**Still open:**
- The engine splits licence cost over personas itself. That repeats the
  interpreter's `licence_shares`, and they agree by construction. Making the
  engine read the shares is part of storing the calculated layer.
- Picking a SKU still looks its catalog price up in the browser
  (`matchSku`, CurrentLicensing and Scenarios). This goes away with live list
  prices in Phase 3: no list price is stored, so there is nothing to requote.
- The best-bundle panel's price edits are what-if inputs sent to the
  recommender, not derived figures. They stay.

## Phase 3 — no derived value written onto a raw row; references by key

**3a (done):**
- A tool row stores only what was entered. The four derived columns are
  dropped, and a value typed into the old covers column is kept as the override
  first.
- `_normalize_third_party` is gone. `tooling_pct` is nullable, and NULL means
  inherit the engagement default, so a change to that default now reaches its
  tools.
- The tool API returns the interpreter's figures read-only.

**3b (done):**
- The engagement's six inheritable settings are nullable (TARGET_SCHEMA §4.1):
  tooling %, segment, term, billing and the two ECIF ratios.
- A new engagement no longer copies the install settings in; NULL means inherit.
- Every reader resolves the settings through `Engagement.effective_settings`.
  The GUI shows "Default (…)" and can return a setting to inheriting.
- Existing values are kept as explicit values.
- This also removes a snowflake: the engagement's built-in billing default
  ("Annual") differed from the install's ("Monthly").

**3c (done):**
- A scenario stores the plan a person picked (`bundle_id`, TARGET_SCHEMA §5.2),
  set by every picker. Name resolution is used only when nothing was picked, and
  Future state says which: picked, matched by name, or not a library plan.
- Re-pricing and add-on eligibility decide the base plan in one place
  (`_scenario_base`), with the same rule as the interpreter.
- Licence lines already had their link (the unknown-licence card's "same as").

**Still to do:**
- A carve-out keeps the parent's entered headcount; the effective headcount is interpreted.
- List prices follow the live catalog; a typed price is an explicit override (D9).

The carve-out headcount and live list prices both change what existing
engagements have stored (a parent's headcount; a stored list price becoming
"follow the catalog"). CLAUDE.md forbids migrating existing engagement data
outside the planned move to the target schema, so they land with that
migration (TARGET_SCHEMA §12), with its before/after report.

## Phase 4 — trace any number back to what was entered

Each calculated figure carries the interpreted rows it used, and each of those the
raw rows behind them. The Data inspector gets a Trace panel:
Calculated → Interpreted (derived, read-only) → Raw (editable, linked to its step),
with the rule applied at each hop.
