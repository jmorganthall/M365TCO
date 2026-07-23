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

## Quick start (Docker — Unraid / local)

The image is published to GHCR (`ghcr.io/jmorganthall/m365tco:latest`) by the
`docker-publish` workflow on every push to `main` and on `v*` tags.

```bash
cp .env.example .env          # set TCO_MASTER_SECRET to a long random value

# Pull and run the published image:
docker compose up -d          # http://localhost:8080

# …or build the same image locally instead of pulling:
docker compose up --build -d
```

The single image bundles the API and the built UI; all state (SQLite DB +
encrypted `secrets.enc`) persists under `/data`.

**Unraid:** deploy via the Compose Manager plugin, or run the published image with
host `${M365TCO_WEB_PORT:-8080}` → container `8000` and the appdata volume
`${M365TCO_DATA_DIR:-/mnt/cache/appdata/m365tco}` → `/data`. All tunables are env
vars with sane defaults (see `.env.example`); only `TCO_MASTER_SECRET` is required.

### One-click updates (optional)

The app can only *detect* a new image from inside its container — it can't recreate
itself — so the bundled `watchtower` sidecar does the pull + restart. It watches
only the labeled `m365tco` container. To enable the in-app **Update now** button:

1. Set `WATCHTOWER_HTTP_API_TOKEN` in `.env` to a long random value and `up -d`.
2. Paste the **same** value into the app under **Settings › Secrets › "Watchtower
   update API token."** (`TCO_WATCHTOWER_URL` already defaults to the internal
   `http://watchtower:8080`.)

The button appears on the "update available" banner and in Settings › Version;
clicking it triggers Watchtower, so the container restarts and the UI reconnects
after a few seconds. Watchtower also auto-checks on a schedule
(`WATCHTOWER_POLL_INTERVAL`, default 24h; set `0` for on-demand only). Delete the
`watchtower` service from `docker-compose.yml` to opt out entirely and update by
pulling the image yourself.

### Azure Container Apps (future rehost)

The same image runs unchanged. Set `TCO_DATABASE_URL` to a managed Postgres
(`postgresql+psycopg://…`), mount durable storage at `TCO_DATA_DIR`, and
optionally back the secret store with Azure Key Vault.

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

The steps run along a chevron **progress stepper** at the top of an engagement:

1. **Baseline Data** — one tab, three sequential cards: **Customer Info** (the
   editable engagement/customer name + basic context — workshop date (defaults to
   today), industry, HQ location, website, employee count, notes — for display and
   later AI business-narrative grounding); **Personas & headcounts**; and **Current
   Microsoft licensing** (model on *assigned*, enter the real price paid — a
   **pricing basis** of segment / commit term / purchase term is inherited
   `Global default → Engagement → line item`, so a picked SKU seeds the right priced
   variant, e.g. a Nonprofit customer's Business Premium price).
2. **Third-party products** — cost, term, unit basis, count, renewal, managed flag, tooling split.
3. **Coverage map** — confirm/extend; AI-assist proposes third-party coverage
   (human-ratified). Microsoft bundle coverage (the reference map) is collapsed
   by default so the tab leads with third-party coverage.
4. **Scenarios** — a base target bundle **+** composable add-ons per persona.
   Add-ons are constrained to the bases they may layer onto (the composition
   **eligibility** rule — e.g. F5 Security only onto F3), so the picker only
   offers valid add-ons. An engagement-level **"swap eligible users to Business
   Premium to save"** toggle proposes moving every *capability-eligible* persona
   (Business Premium covers everything they require) onto Business Premium, with a
   per-persona opt-out — bounded by the Business seat cap below.
5. **Coverage Check** — per-persona validation, scoped to the outcomes the
   persona's **proposed target scenario** would deliver (the *new-outcome*
   candidates) that aren't delivered today by their current licensing or a
   mapped third party (tagged or org-wide, so existing coverage-map mappings
   count). Resolve each: map a third party that actually delivers it, mark it
   *covered elsewhere / out of scope* (recorded as a $0 sentinel, so it's kept
   out of cost and the new-outcome story), add a third party, or leave it as a
   genuine new outcome the target lights up. Reads existing relationships only.
6. **Readout & export** — the horizon headline (annual net delta × the
   engagement's modeling horizon, e.g. "36-month savings", annualized figure
   beneath) over one plain move line per persona ("Baseline (1000) → Microsoft
   365 E5 (−$246,560/yr)"), the Quick-wins "save today" story, the spend bridge
   (every line broken down per persona: one column per in-scope persona plus a
   Total), per-persona scenarios, third-party dispositions, and rollup;
   plus **License-limit** checks (Microsoft licensing caps evaluated tenant-wide —
   e.g. Microsoft 365 Business Basic/Standard/Premium share a 300-seat maximum,
   shown as an over/under badge across current + future state), the **Business
   Premium swap** savings line, advisory **AI sanity check** + **business
   narratives**, per-engagement **readout branding** (logo + theme colors), and
   HTML / xlsx export.

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

## Security

No secrets in config files. The OpenRouter key, the pricing app credential
(certificate/secret), and the CSP consent refresh token live in an
encrypted-at-rest local store (Fernet + PBKDF2) unlocked by `TCO_MASTER_SECRET`;
values are write-only over the API. Azure Key Vault is the
documented alternative.
