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

1. **Personas & headcounts.**
2. **Current Microsoft licensing** — model on *assigned*, enter the real price paid.
   A **pricing basis** (segment / commit term / purchase term) is inherited
   `Global default → Engagement → line item`, so a picked SKU seeds the right
   priced variant (e.g. a Nonprofit customer's Business Premium price).
3. **Third-party products** — cost, term, unit basis, count, renewal, managed flag, tooling split.
4. **Coverage map** — confirm/extend; AI-assist proposes third-party coverage
   (human-ratified). Microsoft bundle coverage (the reference map) is collapsed
   by default so the tab leads with third-party coverage.
5. **Scenarios** — a base target bundle **+** composable add-ons per persona.
6. **Coverage Check** — per-persona validation, scoped to the outcomes the
   persona's **proposed target scenario** would deliver (the *new-outcome*
   candidates) that aren't delivered today by their current licensing or a
   mapped third party (tagged or org-wide, so existing coverage-map mappings
   count). Resolve each: map a third party that actually delivers it, mark it
   *covered elsewhere / out of scope* (recorded as a $0 sentinel, so it's kept
   out of cost and the new-outcome story), add a third party, or leave it as a
   genuine new outcome the target lights up. Reads existing relationships only.
7. **Readout & export** — the Net TCO delta, the Quick-wins "save today" story,
   the spend bridge, per-persona scenarios, third-party dispositions, and rollup;
   plus advisory **AI sanity check** + **business narratives**, per-engagement
   **readout branding** (logo + theme colors), and HTML / xlsx export.

The **in/out-of-scope** toggle on a scenario recomputes everything.

**Settings** is a dedicated page (top-bar ⚙ gear) with a left-hand section nav —
General/defaults, AI assist, Pricing sync, SKU catalog, Staple bundles, Default
coverage, Default outcomes, and Secrets.

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
  suggestions. AI never writes a final number.

## Seed libraries

`backend/app/seeds/outcomes.json` and `coverage.json` are versioned seed files.
The shipped content is a starter set — the practice's final libraries replace
these files (bump the `version`). On engagement creation the defaults are copied
into engagement-scoped rows so edits never mutate the global library.

## Security

No secrets in config files. The OpenRouter key, the pricing app credential
(certificate/secret), and the CSP consent refresh token live in an
encrypted-at-rest local store (Fernet + PBKDF2) unlocked by `TCO_MASTER_SECRET`;
values are write-only over the API. Azure Key Vault is the
documented alternative.
