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

## Phase 1 — `interpret()` on today's schema (next)

A pure `backend/tco_engine/interpret.py` that returns, in memory, the shapes in
§6.1–6.4. It folds today's copies of each rule into one definition:

| Interpreted output | Folded from |
| --- | --- |
| licence lines: read state, effective price and its source, applies-to (Org-wide `assumed`) | `compute.licence_readings`, `PersonaScenario`/`CurrentMicrosoftLicense` price properties, the engine's pool |
| licence and tool cost shares | `engine.py` per-scenario loop, the recommender's copy in `compute.analyze_persona_bundles` |
| tools: annual cost, effective tooling %, people covered, cost per person | `routers/entities._normalize_third_party` (writes these onto the raw row today) |
| scenarios: base + add-ons by bundle id, composed list, net, complete/incomplete | `compute.hydrate`, `compute._scenario_cover`, `lineage.py`, `limits` |
| persona × outcome today/after, tool retired, quick win | `persona_coverage_gaps`, `new_outcomes`, `dropped_capability`, and the five "tool retired" checks (`engine._scenario_displaces_product`, `compute._retired`, `compute._plan_fitting`, `lineage._plan_retires`, the readout's kept-tools filter) |
| seat caps | `limits._future_seat_usage` |
| findings | `services/review.py` |

Gate: the engine's output from `interpret()` equals today's output on every test
fixture and on a `tools/baseline.py` export.

## Phase 2 — every consumer reads interpret/engine output

One change per consumer; each deletes its private copy:

1. The recommender (`compute.analyze_persona_bundles`), keyed by bundle id, not name.
2. Coverage and Review: `persona_coverage_gaps`, `new_outcomes`, `dropped_capability`, `review.py`, `lineage.py`, `seat_cap.py`.
3. Readout and exports: `readout.py`, `exporter.py`, `narrative.py`. Narratives join on `persona_id`, not name.
4. The GUI's own arithmetic moves to the backend:
   - net/effective prices and % off list (`Scenarios.jsx`, `CurrentLicensing.jsx`);
   - unused-seat cost;
   - the Readout bridge group totals and per-persona sums;
   - Partly replaced residual seats and dollars;
   - the tool "left out" badge (`ThirdParty.jsx` vs `review.py`);
   - carve-out seat sums.

   Licence lines are requoted on the server, as scenarios already are. Unsaved price edits in the best-bundle panel are saved or removed.

## Phase 3 — no derived value written onto a raw row; references by key

- Retire the `ThirdPartyProduct` derived columns (`annual_cost`, `covered_count`, `per_unit_annual_cost`, `effective_annual_cost`). `_normalize_third_party` also writes the resolved `tooling_pct` onto the raw column, and an engagement-default change never reaches existing tools.
- Store "use the default" as NULL (tooling %, defaults copied at engagement creation).
- A carve-out keeps the parent's entered headcount; the effective headcount is interpreted.
- Scenario and licence targets carry a `bundle_id` set by the picker. Name and substring resolution (`bundles.resolve_bundle`, which ends in `ILIKE %ref%`) is only for unlinked rows, which are flagged unread.
- List prices follow the live catalog; a typed price is an explicit override (D9).

## Phase 4 — trace any number back to what was entered

Each calculated figure carries the interpreted rows it used, and each of those the
raw rows behind them. The Data inspector gets a Trace panel:
Calculated → Interpreted (derived, read-only) → Raw (editable, linked to its step),
with the rule applied at each hop.
