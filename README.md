# M365 TCO Tool

A quantitative Microsoft 365 Total Cost of Ownership tool for a Microsoft partner
practice. It compares a customer's current licensing spend against a
target-state M365 licensing spend, persona by persona, and rolls the scenarios
into a single hard-dollar story: current spend, target spend, net delta,
third-party tools eliminated, and renewal cycles removed.

The Net TCO delta uses a **cost-change convention** — `delta = new − old`, so a
**negative** number is a saving (shown green) and a **positive** number is a
cost increase (shown neutrally, for the added capabilities). The readout also
surfaces **Quick wins** (third-party tools the customer's *current* licensing
already duplicates — droppable today) and an advisory **AI sanity check** and
**per-persona business narratives**.

> v1 is a **pure licensing TCO** — tooling cost for tooling cost. It does not
> model managed-services, migration/PS, Microsoft funding, Azure consumption, or
> soft savings. Those are deferred; the schema is built to accept them later as
> overlays without a rebuild.

## Architecture

Three hard-separated layers (PRD Section 4):

| Layer | Where | Notes |
| --- | --- | --- |
| **Calculation engine** | `backend/tco_engine/` | Pure functions, no I/O, no framework imports. The asset that survives a platform change. Specified language-neutrally in [`docs/ENGINE_SPEC.md`](docs/ENGINE_SPEC.md). |
| **Data layer** | `backend/app/models.py` (SQLAlchemy) | Relational. SQLite for v1; swap to Postgres via `TCO_DATABASE_URL` only. |
| **Presentation / integration** | `backend/app/` (FastAPI) + `frontend/` (React/Vite) | REST API, CSV import, price-sheet sync, OpenRouter client, HTML/xlsx export. |

A SharePoint / Power Platform port is a new front end + a Dataverse/list
rendering of the Section 5 model executing the same engine spec. The model and
algorithm port; the code does not.

**Authoritative references:**
- [`docs/DATA_ARCHITECTURE.md`](docs/DATA_ARCHITECTURE.md) — the data architecture
  law: everything is a first-class object; minimize data that lives outside one.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — every first-class data set, the
  relationships between them, field ownership, and the repeatable CRUD module
  contract that keeps new data from becoming a snowflake.
- [`docs/ENGINE_SPEC.md`](docs/ENGINE_SPEC.md) — the language-neutral calculation
  algorithm.
- [`docs/PRICE_SYNC.md`](docs/PRICE_SYNC.md) — Partner Center price-sheet
  acquisition (interactive login, no stored token) and local freshness monitoring.
- [`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md) — the agreed design (not yet built)
  for the guided walkthrough an account executive runs live with a customer.

## Quick start (Docker — Unraid / local)

The image is published to GHCR (`ghcr.io/jmorganthall/m365tco:latest`) by the
`docker-publish` workflow on every push to `main` and on `v*` tags.

```bash
cp .env.example .env          # set TCO_MASTER_SECRET to a long random value

# Pull and run the published image:
docker compose up -d          # http://localhost:8080 (this host only — see below)

# …or build the same image locally instead of pulling:
docker compose up --build -d
```

The single image bundles the API and the built UI; all state (SQLite DB +
encrypted `secrets.enc`) persists under `/data`.

**Network exposure.** The app has **no sign-in yet**, so by default the port is
published on the Docker host's loopback only (`127.0.0.1`) — reachable from the
host itself and nowhere else. To reach it from other machines, set
`M365TCO_BIND_ADDRESS` in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `M365TCO_BIND_ADDRESS` | `127.0.0.1` | Host address the port is published on. `0.0.0.0` (all interfaces) or a single host IP exposes the app to the network — **only do this on a trusted internal network**, since anyone who can reach the port can use the app. |
| `M365TCO_WEB_PORT` | `8080` | Host port mapped to the container's `8000`. |
| `TCO_CORS_ORIGINS` | *(empty)* | Comma-separated origins allowed to call the API cross-origin. Empty adds no CORS headers, which is right for normal use: the UI is served by the app itself (same origin) and the dev server proxies `/api`. Set it only for a front end hosted on a different origin; a `*` wildcard is honoured but never with credentials. |

Upgrading an existing install that was reachable from the LAN: set
`M365TCO_BIND_ADDRESS=0.0.0.0` (or the host's LAN IP) and `docker compose up -d`
again, or it will only answer on the host itself.

**Unraid:** deploy via the Compose Manager plugin, or run the published image with
host `${M365TCO_BIND_ADDRESS:-127.0.0.1}:${M365TCO_WEB_PORT:-8080}` → container
`8000` (see **Network exposure** above) and the appdata volume
`${M365TCO_DATA_DIR:-/mnt/cache/appdata/m365tco}` → `/data`. All tunables are env
vars with sane defaults (see `.env.example`); only `TCO_MASTER_SECRET` is required.

### One-click updates (optional)

The app can only *detect* a new image from inside its container — it can't recreate
itself — so a `watchtower` instance does the pull + restart. The bundled sidecar
watches only the labeled `m365tco` container; you can also point the app at a
shared/central Watchtower you already run. To enable the in-app **Update now** button:

1. Set `WATCHTOWER_HTTP_API_TOKEN` on the Watchtower container to a long random value
   and `up -d`.
2. Give the app the **same** token, either way:
   - **In the app** — paste it under **Settings › Secrets › "Watchtower update API
     token"** (encrypted; preferred), or
   - **Via env** — set `WATCHTOWER_TOKEN` in the deploy environment (useful when a
     shared token is managed centrally). The secret store wins if both are set.
3. Point `WATCHTOWER_URL` at your Watchtower (defaults to the internal
   `http://watchtower:8080`; set e.g. `http://host:8998` for a shared one). The
   legacy `TCO_WATCHTOWER_URL` / `TCO_WATCHTOWER_TOKEN` names still work.

The button appears on the "update available" banner and in Settings › Version;
clicking it triggers Watchtower, which pulls the latest image and recreates the
container, so the UI reconnects after a few seconds. Watchtower also auto-checks on
a schedule (`WATCHTOWER_POLL_INTERVAL`, default 24h; set `0` for on-demand only).
Delete the `watchtower` service from `docker-compose.yml` to opt out of the bundled
sidecar (e.g. when using a shared one) and update by pulling the image yourself.

### Azure Container Apps (future rehost)

The same image runs unchanged. Set `TCO_DATABASE_URL` to a managed Postgres
(`postgresql+psycopg://…`), mount durable storage at `TCO_DATA_DIR`, and
optionally back the secret store with Azure Key Vault.

### Before/after export

Before an upgrade that could move the numbers (a data-model change, an engine
change, a catalog refresh), export every engagement's engine inputs, engine outputs
and headline; after the upgrade, export again and diff the two. The export is
strictly read-only (it opens the database read-only and refuses any write) and
reads one consistent snapshot in a few seconds, so run it while nobody is editing.
The file contains customer data — keep it with the instance's other data.

```bash
# Before the upgrade: export inside the running container and copy the file out
# (recreating the container discards its /tmp, so copy it out first).
docker compose exec m365tco python -m app.tools.baseline export --out /tmp/before.json
docker compose cp m365tco:/tmp/before.json ./before.json

# After the upgrade:
docker compose exec m365tco python -m app.tools.baseline export --out /tmp/after.json
docker compose cp m365tco:/tmp/after.json ./after.json

# Diff, inside the container...
docker compose cp ./before.json m365tco:/tmp/before.json
docker compose exec m365tco python -m app.tools.baseline diff /tmp/before.json /tmp/after.json --out /tmp/report.md
docker compose cp m365tco:/tmp/report.md ./report.md
# ...or from backend/ in a checkout (diff needs only the Python standard library):
python -m app.tools.baseline diff ../before.json ../after.json --out ../report.md
```

The report opens with a per-engagement summary — headline before → after, how many
numbers changed, whether the engagement's inputs changed, and a cause hint (input,
catalog or calculation change) — then lists every changed number by JSON path with
before, after and delta. Add `--fail-on-change` to exit 1 when anything changed.

## Local development

Backend:
```bash
cd backend
pip install -r requirements.txt
export TCO_DATABASE_URL=sqlite:///./tco.db TCO_DATA_DIR=. TCO_MASTER_SECRET=dev
uvicorn app.main:app --reload          # http://localhost:8000
pytest -q                              # engine + API tests
```

Frontend (dev server proxies `/api` to `:8000`):
```bash
cd frontend
npm install
npm run dev                            # http://localhost:5173
```

## Workshop flow (PRD Section 3)

The app opens on an **Open engagement** page: type part of a customer's name to
find an engagement, or create a new one. There is no list of customers on
screen, because the app is run on a shared screen with a customer
([`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md) §2). Each engagement has its own
address (`#/e/<id>/<step>`), so a reload returns to it.

An engagement is a **guided walkthrough** an account executive runs with the
customer, seven steps along a chevron stepper with **Back / Next** at the foot of
each ([`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md)). Each step shows the basics; the
unusual cases sit in each card's **▸ / details** expander. Every question carries an
**ⓘ** tooltip — *what we're asking* and *how it's used* — written for the customer
to read, from one reviewed file (`backend/app/content/help_text.json`) that the
PDF's method page also reads.

1. **Customer** — name, workshop date, logo. Optional details (industry, HQ,
   website, employee count, notes, ✨ AI research) and the **pricing basis**
   (segment / commit term / payment, inherited `Global default → Engagement → line`)
   are in expanders.
2. **Groups & licences** — the groups that get different licences (name, people,
   description; *must also include* capabilities in the expander), when the
   **Microsoft agreement renews**, and each Microsoft licence: product, bought, price
   (list unless known); in its expander the groups that get it, seats assigned, the
   **unused-seat answer** (kept on purpose / not needed), its own renewal date,
   per-user vs tenant-wide scope and price-basis overrides.
3. **Other tools** — each tool's cost, period, **renewal date**, **who uses it**
   (groups) and whether it's a **managed service**; vendor, the managed service's
   software share and a covers number in the expander. Below, **what each tool is
   used for** (✨ AI suggest pre-ticks; only confirmed uses count). A tool with no
   cost, users or uses is flagged *left out*.
4. **Future state** — **Fill in recommended plans** (the lowest-cost plan plus
   add-ons that keeps everything each group has today), a base bundle + eligible
   add-ons per group, **carve-out** for moving part of a group, and **partly
   replaced tools**: keep for the remaining users, or retire (with a reason).
5. **Coverage check** — for each capability a plan adds that nothing in the
   inventory delivers today, the customer's answer: *not delivered today* (a new
   outcome), *covered outside this inventory* (never costed or claimed), or map a
   tool that does it. Unanswered gaps are never claimed as new. Amber honesty
   guards flag unmapped licensing and capability a plan drops. The engagement's
   capability library sits in an advanced expander.
6. **Review** — every check that works without AI, each linking to the step that
   fixes it, and what is **left out** because it wasn't answered. The optional
   **AI sanity check** is here.
7. **Summary & PDF** — years to model and **Create customer PDF** (title page,
   overview, a page per group, *How we calculated this*), which records a
   **Presented** snapshot. Below it, the readout: the timed headline — ① duplicate
   tools retired with no licensing change, ② each group's move, ③ unused licences
   confirmed not needed, each from the renewal that unlocks it (ENGINE_SPEC 6.11),
   with *How the headline is timed* — quick wins, per-group scenarios, **New
   outcomes**, **Capability trade-offs**, the per-group spend bridge, dispositions,
   **License-limit** checks, AI **business narratives** (editable), and HTML / xlsx
   export. Report colours and Microsoft co-funding (ECIF) are under *Report options*.

The **in/out-of-scope** toggle on a scenario recomputes everything. A header
**🔧 Tools** menu holds engagement-specific tools outside the workshop flow — the
**Data inspector** (the live data-model view) lives there rather than as a step.

**Settings** is a dedicated page (top-bar ⚙ gear) with a left-hand section nav —
General/defaults, AI assist, Pricing sync, SKU catalog, Staple bundles, Default
coverage, License limits, Default outcomes, and Secrets. **Staple bundles** edits
the SKU → Bundle spine (each add-on's eligible bases, plus a "how catalog SKUs
bucket into bundles" rollup showing the priced variants that collapse onto each
staple); **License limits** edits the tenant caps and which bundles share each pool.

## The engine (the spine)

Deterministic, fully unit-tested (`backend/tests/test_engine.py`), including the
worked Okta 500-vs-450 case, the renewal-gating rule, and the override-disclosure
rule. See [`docs/ENGINE_SPEC.md`](docs/ENGINE_SPEC.md) for the algorithm.

Key rules:
- **Managed split** keeps management cost out of the comparison — managed products
  count at their tooling percentage (default 30%), unmanaged at 100%.
- **Linear-by-user displacement** — third-party cost allocated by headcount at the
  per-unit effective rate.
- **Ratified-only coverage** — unratified AI suggestions never feed the math.
- **Renewal gating** — a renewal is "eliminated" only when its product is fully
  eliminated.
- **Override disclosure** — forcing full elimination on undisplaced users requires
  a reason that prints on the readout; an intended residual is recorded separately.
- **Quick wins** — third-party products whose outcomes the customer's *current*
  licensing already delivers are flagged as droppable-today savings, separate
  from what the target move adds (spec §6.10).
- **Cost-change delta** — `delta = target − current` (negative = saving); the
  optimizer recommends the biggest-saving bundle.

## Catalog & integrations (PRD Sections 8–9)

- **Price-sheet CSV import** (permanent fallback): Settings → SKU catalog →
  import the new-commerce license-based price list. The parser maps by column
  name and tolerates Microsoft's column drift; **all segments** are ingested
  (Commercial, Education, Government, Nonprofit, …) and prices are annualized on
  import. The full catalog is searchable (no silent row cap), with fuzzy SKU
  matching ("O365 E5" → "Office 365 E5"). The raw uploaded file is retained so
  it can be **downloaded as-is** later.
- **Partner Center price-sheet sync** (automated acquisition): Cloud Solution
  Provider auth (Secure Application Model). A one-time partner consent yields a
  refresh token (stored encrypted) that the app exchanges for access tokens
  server-side — no per-fetch browser redirect, so it works over IP or hostname.
  A local age check flags staleness. See [`docs/PRICE_SYNC.md`](docs/PRICE_SYNC.md).
  "Import latest into catalog" then feeds the same parser.
- **OpenRouter AI assist**: proposes third-party → outcome coverage as *unratified*
  suggestions, parses pasted third-party/license text, drafts business narratives,
  runs the pre-readout sanity check, and — from the Customer Info tab — **researches
  customer info** (industry, HQ, website, employee count, a short description) from
  the company name to fill the empty fields for the operator to verify. AI is
  advisory: it never writes a final number, and every function's prompt is an
  editable `AiPrompt` (Settings → AI assist).

## Seed libraries

`backend/app/seeds/outcomes.json`, `coverage.json`, `bundles.json`, and
`license_limits.json` are versioned seed files (the starter source for the
globally-editable `DefaultOutcome`, `DefaultBundleCoverage`, `Bundle` /
`AddonEligibility`, and `LicenseLimit` tables). The shipped content is a starter
set — the practice's final libraries replace these files (bump the `version`). On
engagement creation the outcome + Microsoft-coverage defaults are copied into
engagement-scoped rows so edits never mutate the global library.

The default **outcomes** are split to the granularity at which Microsoft SKUs and
third-party tools actually differ — Endpoint = EPP vs EDR, Email = Hygiene vs
Advanced Threat Protection, Identity = Core vs Governance, plus CASB and two
telephony layers (Cloud PBX vs PSTN dial-tone, so Phone System vs dial-tone is
explicit without exploding a bundle into calling/non-calling variants). The
**bundles** are the staple SKU → Bundle spine the many priced catalog SKUs
collapse onto; add-ons carry an eligibility set (which bases they layer onto).
Bundle coverage sets may **overlap** — e.g. *Enterprise Mobility + Security E3/E5*
are add-ons (eligible for the Office 365 bases) whose identity/mobility/security
coverage overlaps a base's, so a persona holding *Office 365 E3 + EMS E3* is modelled
by the union rather than by decomposing *Microsoft 365 E3*.

## Security

No secrets in config files. The OpenRouter key, the pricing app credential
(certificate/secret), and the CSP consent refresh token live in an
encrypted-at-rest local store (Fernet + PBKDF2) unlocked by `TCO_MASTER_SECRET`;
values are write-only over the API. Azure Key Vault is the
documented alternative.
