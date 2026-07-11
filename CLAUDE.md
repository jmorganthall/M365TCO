# Project conventions (read before contributing)

## Branding: white-label only — NO client or company names

This tool is operated by a Microsoft partner practice, but **the operating
company's identity must never appear in the repository, the data, the seed
libraries, the UI, the exports, the docs, or commit/PR text.** Specifically:

- **Never** write the operating company's name, nor any client or partner name,
  anywhere. Do not reintroduce a previously-removed brand name either.
- Keep all wording generic and white-label. Use neutral phrasing such as
  "the Microsoft Practice", "the practice", "a Microsoft partner practice",
  "the SA team", or "default library".
- This applies to: source code, comments, JSX/HTML/titles, seed JSON, generated
  HTML/xlsx readouts, Markdown docs, environment/config, commit messages, PR
  titles/bodies, and workflow files.
- If a customer or operator name is genuinely needed at runtime, it belongs in
  **engagement data entered by the user** (e.g. `Engagement.customer_name`),
  never hard-coded anywhere in the repo.

Before opening a PR, grep the repo for the operating company's name (and common
misspellings) and confirm there are zero hits. Do not commit the name into this
file to build that check.

## Data architecture (the law)

Everything is a first-class object; minimize data that lives outside one. Read
and obey `docs/DATA_ARCHITECTURE.md` and `docs/DATA_MODEL.md`:

- New data is a new first-class object (or a typed field on one) with identity,
  owner/scope, schema, a uniform CRUD module, and provenance — not a free-text
  blob, delimited string, magic literal, or shadow representation.
- Domain data never lives in env vars or config files; those are for operational
  settings only. Secrets live in the encrypted secret store.
- **No hidden data — everything is visible in the GUI.** Every persisted domain
  field must have a GUI surface: user-owned fields are viewable **and** editable;
  system-derived fields are shown read-only and labelled as derived. A change that
  adds or alters a field must add or update its GUI surface in the same change —
  a field must never live only in code/DB, inaccessible to the user. Keep the data
  model inspectable and its movement (input → engine → readout) traceable in the
  app, not just in the code. Prefer the line-item form: core fields shown up top,
  an expander for the non-standard/detail fields.
- Relationships are explicit and referential: model many-to-many as a first-class
  association object (not a delimited string or a duplicated row), and validate
  soft references (e.g. a SKU string) against their source in the UI.

## Engine

The calculation engine (`backend/tco_engine/`) is pure and I/O-free. It
implements `docs/ENGINE_SPEC.md` exactly and must stay framework-free so it can
be ported. Any change to the math requires updating the spec and the unit tests
in `backend/tests/test_engine.py`.

## Branch / trunk

`main` is the trunk **and** the repository default branch. Branch new work from
`main` and open PRs **against `main`**; a PR targeting anything else won't publish
`:latest`. Don't trust a session's reported "default HEAD" if it isn't `main` —
verify against `main` before building (a `claude/*` branch is a feature branch,
not the trunk).

## Seeds, defaults & migrations

Seed files (`seeds/*.json`) are the versioned source for the global, editable
tables (`DefaultOutcome`, `DefaultBundleCoverage`, `Bundle` + `AddonEligibility`,
`LicenseLimit`, `AiPrompt`). On engagement creation they are **copied** into
engagement-scoped rows — an engagement owns its copy for life.

- **Never mutate an existing engagement's data in a migration.** Existing
  engagements keep the taxonomy/coverage they were created with; to adopt a change
  the operator recreates the engagement (or edits it in the GUI). Migrations touch
  only the global tables.
- **Adding a seeded default → add an additive startup backfill** (populate-if-empty
  seeds a fresh DB; an idempotent `_backfill_*` inserts the new rows on an
  already-seeded DB).
- **Splitting or retiring a seeded default → also add a targeted retirement
  migration** that removes the retired keys from the global template by an
  *explicit key list* (so operator-added customs are never touched). Additive
  backfills alone leave the old keys behind, so **new** engagements keep inheriting
  the stale ones.
- Reference an object by its **stable key/id**, never a mutable display name (the
  API exposes `seed_key` for exactly this).

## Container image

Published to `ghcr.io/jmorganthall/m365tco` by `.github/workflows/docker-publish.yml`
on pushes to `main` and `v*` tags. `:latest` tracks `main`, so merges to `main`
update the image `docker-compose.yml` pulls.
