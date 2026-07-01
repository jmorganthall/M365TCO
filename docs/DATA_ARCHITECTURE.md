# Data Architecture Law: Everything Is a First-Class Object

> Bad data architecture is what happens when data lives *outside* of first-class
> objects. Minimize that data as aggressively as you can. This document is the law;
> [`DATA_MODEL.md`](DATA_MODEL.md) is the detailed model that obeys it.

## The principle

A **first-class object** is a named entity in the data model that has all five of:

1. **Identity** — a stable primary key (`uuid`).
2. **Scope / owner** — it belongs to an aggregate (almost always an Engagement).
3. **Schema** — typed, named columns; not a bag of strings.
4. **A CRUD module** — one uniform create/read/update/delete surface.
5. **Provenance** — a `source_tag` and/or ratify state, so its values are traceable.

If a piece of data does not live inside something with those five properties, it is
**second-class data**, and second-class data is where the rot starts. We treat the
amount of second-class data in the system as a metric to **drive toward zero**.

## Why second-class data is corrosive

| Missing property | What you lose | How it bites later |
| --- | --- | --- |
| No identity | Can't reference, link, or de-duplicate it | Two copies drift; nothing can point at it |
| No scope/owner | No cascade, no lifecycle | Orphans; "who deletes this?" |
| No schema | No validation, no types | Silent format drift; parsing snowflakes |
| No CRUD module | Every touch is bespoke | A new code path each time = snowflakes |
| No provenance | Can't tell hard from soft | The number doesn't survive a CFO |

## What "data outside first-class objects" looks like (avoid)

These are the patterns to refuse in review:

- **Structured meaning hidden in free text.** Putting a price, a date, or a list
  into a `notes`/description field instead of a typed column or related row.
- **Delimited strings / spreadsheets-in-a-cell.** `"Okta;Mimecast;Rapid7"` instead
  of rows. A comma is not a schema.
- **JSON blobs as live state.** Stuffing a domain object into a `payload`/`config`
  text column so you can avoid modeling it. (Immutable, opaque snapshots are the
  one exception — see below.)
- **Domain data in environment variables or config files.** Env vars are for *how
  the app runs*, never for *what the customer's data is*.
- **Magic literals in code.** Enumerable domain values hard-coded as string literals
  scattered across modules instead of one declared set.
- **Ad-hoc derived values.** Recomputing the same number a different way in two
  places instead of deriving it once, on write, on the owning object.
- **Shadow representations.** A second, parallel shape for a concept that already has
  a first-class object. There is one shape per data set (see `DATA_MODEL.md` §1).
- **In-flight-only data.** Values that live in a request payload or session and never
  land in a typed, persisted object.

## The rules

1. **If it is referenced more than once, has a lifecycle, or needs provenance, it
   must be a first-class object** (or a typed field on one).
2. **Prefer the stronger structure every time:** a related table over a delimited
   string; a typed column over a JSON blob; one declared enum/constant set over
   scattered literals; derive-on-write over recompute-on-read.
3. **Model live data; never blob it.** A blob is allowed only as the *opaque,
   immutable payload of a first-class object*, never as a way to dodge modeling
   something that still changes.
4. **Seed, then own.** Reference libraries are versioned seed files that are *copied
   into* first-class engagement-scoped objects on use — we never run on shared
   mutable global state.

## The only sanctioned second-class data

These are deliberate, bounded exceptions. Anything not on this list must be modeled.

| Exception | Why it's allowed | Guardrail |
| --- | --- | --- |
| **Operational config** (`config.py` / env: `TCO_DATABASE_URL`, ports, `TCO_DATA_DIR`, CORS) | It's about *running* the app, not the domain | Never put domain data here |
| **Secrets** (OpenRouter key) | Must be out of the relational store by nature | Behind the secret-store contract; write-only; swappable for Key Vault. Price-sheet sync stores no token. |
| **`EngagementSnapshot.payload_json`** | A frozen, immutable record for reproducibility | It is itself a first-class object; the blob is opaque and never edited as live state |
| **Seed files** (`outcomes.json`, `coverage.json`) | Versioned *source* for first-class objects | Copied into scoped objects on engagement creation; never read at runtime as live data |

That is the complete list. `payload_json` is the single live blob in the system, and
it earns its place only because it is an immutable snapshot, not editable state.

## The checklist (apply before adding any data)

Before you persist, pass, or hard-code a new piece of data, ask:

- Does it have **identity**? If it's referenced, it needs one.
- Does it have an **owner/scope** that cascades?
- Does it have a **typed schema**, not a string or blob?
- Does it go through a **CRUD module**, not a bespoke path?
- Does it carry **provenance**?

If the answer to the first four is "no," you are about to add second-class data.
Model it as (or onto) a first-class object instead — see the Entity Module Contract
in [`DATA_MODEL.md`](DATA_MODEL.md) §2.
