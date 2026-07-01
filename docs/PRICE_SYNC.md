# Price Sheet Sync and Freshness

Acquires the Microsoft Partner Center price sheet through an interactive login,
stores it on the persistent volume with metadata, and flags staleness locally.
Two behaviours are decoupled: the **age check** is automatic, local, and makes no
API call; the **fetch** is on demand, one interactive login, one API call.

Code: `backend/app/pricesync/` (`config`, `auth`, `fetch`, `storage`,
`freshness`, `notify`) + `backend/app/routers/pricesync.py`.

## Fixed technical facts (hard constraints)
- Endpoint: `GET https://api.partner.microsoft.com/v1.0/sales/pricesheets(Market='{market}',PricesheetView='{view}')/$value`
- Auth: **app + user only** (app-only is not supported). Authorization code flow
  with **PKCE**, confidential client. Certificate credential preferred; client
  secret is a fallback. Scope `https://api.partner.microsoft.com/.default`.
- **No device code flow, no ROPC.** MFA is enforced; interactive sign-in satisfies it.
- Login account needs the **Admin Agent or Sales Agent** role.
- Response is a CSV file stream, or zipped CSV when compressed.
- **No user refresh token is ever persisted.** The access token is used once and discarded.

## Endpoints
| Method | Path | Notes |
| --- | --- | --- |
| GET/PUT | `/api/pricesync/config` | Non-secret settings (GUI editor). Secrets never returned. |
| PUT/DELETE | `/api/pricesync/credential` | Set/clear the certificate PEM or client secret (encrypted store). |
| GET | `/api/pricesync/status` | Freshness state. No auth, no API call. |
| POST | `/api/pricesync/login-url` | Begins the interactive flow; returns the Microsoft authorization URL. |
| GET | `/auth/callback` | OAuth redirect. Exchanges the code, fetches one sheet, stores it, discards the token. |
| POST | `/api/pricesync/import-latest` | Parses the stored sheet into the SKU catalog (existing parser). |
| POST | `/api/pricesync/check-notify` | Runs the local age check and posts one webhook if Stale. No API call. |

## Near one-click sign-in
The only value the operator must enter is the **Client (application) ID** plus a
credential. Everything else is handled automatically:
- **Tenant ID** — sign-in starts against the `organizations` authority, and the
  tenant is read from the token's `tid` claim and persisted on first success.
- **Redirect URI** — auto-derived from the app's own request origin (honoring
  `X-Forwarded-Proto`/`-Host` behind a reverse proxy). Editable for edge cases;
  it must still be registered on the app in Azure.
- **Price sheet view** — defaults to `updatedlicensebased`.
- The signed-in account is captured from the token claims and shown in Settings.

## Configuration (in-app GUI — no environment variables)
Everything is configured in **Settings › Pricing sync**, not via env vars:
- **Non-secret settings** — tenant id, client id, redirect URI, price sheet view,
  market, aging/stale thresholds, month rule, retention, notify webhook — persist
  in the first-class `PriceSyncSettings` singleton (`GET/PUT /api/pricesync/config`).
- **Credential** — a certificate PEM (key + cert, preferred) or a client secret —
  is stored in the encrypted secret store (`PUT/DELETE /api/pricesync/credential`),
  encrypted at rest and never returned by the API. Requires `TCO_MASTER_SECRET`.
- Only `DATA_DIR` (the storage path on the volume, default `/data/pricesheets`) is
  infrastructure; it may come from the environment.

## Freshness rules
- **Day rule**: Fresh < `AGE_AGING_DAYS` ≤ Aging < `AGE_STALE_DAYS` ≤ Stale (or no sheet → Stale).
- **Month rule** (optional): if the sheet's data month ≠ current calendar month, classify at least Aging.
- When both run, the **stricter** state wins.

## Storage
Sheets and a JSON metadata sidecar (schema in the PRD) are written to `DATA_DIR`
with atomic temp-then-rename, a SHA-256, a `latest.json` pointer, and retention of
the newest `RETENTION_COUNT` sheets. A failed fetch never corrupts the last good sheet.

## Human checks (environment facts the code can't establish)
- The operator is an **owner** of the App Management app (so the redirect URI can be set).
- `REDIRECT_URI` is registered on the app and matches config.
- The login account holds **Admin Agent or Sales Agent** (not only Account Admin).
- MFA compliance confirmed (the fetch sends `ValidateMfa` and records
  `mfa_compliant` in metadata).
- `PRICESHEET_VIEW` confirmed to return data for this account (some views, e.g.
  `updatedlicensebased`, have had preview gating).

## Validation status
Offline logic — freshness classification, atomic storage, retention, hashing —
is unit-tested (`backend/tests/test_pricesync.py`). The live interactive login and
real Partner Center fetch require a partner tenant with the roles above and must
be validated in the target environment.
