# Price Sheet Sync and Freshness (Cloud Solution Provider auth)

Acquires the Microsoft Partner Center price sheet using **Cloud Solution Provider
(CSP) authentication — the Secure Application Model** — stores it on the
persistent volume with metadata, and flags staleness locally. The age check is
automatic, local, and makes no API call; a fetch is a server-side token exchange
(no browser redirect).

Code: `backend/app/pricesync/` (`config`, `auth`, `fetch`, `storage`,
`freshness`, `notify`) + `backend/app/routers/pricesync.py`.

## How CSP auth works
1. **One-time partner consent** (done once, out of band): a dedicated,
   MFA-enabled service account holding **Admin Agent or Sales Agent** consents to
   the app and produces a **refresh token**. Because this is done once from
   wherever a browser is convenient, it is unaffected by how the app itself is
   reached (IP or hostname) — there is **no redirect URI on the app**.
2. The refresh token is pasted into the app (Settings) and stored **encrypted**.
3. On each refresh, the app exchanges the refresh token for a price-sheet API
   access token (`https://api.partner.microsoft.com/.default`), fetches the sheet,
   and discards the access token. A rotated refresh token, if returned, is stored.

## Fixed technical facts
- Endpoint: `GET https://api.partner.microsoft.com/v1.0/sales/pricesheets(Market='{market}',PricesheetView='{view}')/$value`
- Auth: **app + user** via the Secure Application Model (a stored refresh token).
  App-only is not supported for this endpoint. Certificate credential preferred;
  client secret fallback. Scope `https://api.partner.microsoft.com/.default`.
- Token endpoint: `https://login.microsoftonline.com/{tenantId}/oauth2/v2.0/token`,
  `grant_type=refresh_token`.
- MFA is enforced; the refresh token must come from an MFA-compliant consent.
- Login account role: **Admin Agent or Sales Agent**.
- Response is a CSV file stream, or zipped CSV when compressed.

## Endpoints
| Method | Path | Notes |
| --- | --- | --- |
| GET/PUT | `/api/pricesync/config` | Non-secret settings (GUI editor). Secrets never returned. |
| PUT/DELETE | `/api/pricesync/credential` | Set/clear the app credential (certificate PEM or client secret). |
| PUT/DELETE | `/api/pricesync/refresh-token` | Set/clear the consent refresh token (encrypted). |
| POST | `/api/pricesync/refresh` | Exchange the refresh token → fetch → store. Server-side, no redirect. |
| GET | `/api/pricesync/status` | Freshness state. No auth, no API call. |
| POST | `/api/pricesync/import-latest` | Parse the stored sheet into the SKU catalog. |
| POST | `/api/pricesync/check-notify` | Local age check + optional webhook. No API call. |

## Configuration (in-app GUI — no environment variables)
Everything is configured in **Settings › Pricing sync**, not via env vars:
- **Non-secret settings** — partner tenant id, client (app) id, price sheet view,
  market, aging/stale thresholds, month rule, retention, notify webhook — persist
  in the first-class `PriceSyncSettings` singleton.
- **App credential** (certificate PEM preferred, or client secret) and the
  **consent refresh token** live in the encrypted secret store; validated on save,
  never returned by the API. Requires `TCO_MASTER_SECRET`.
- Only `DATA_DIR` (the storage path, default `/data/pricesheets`) is infrastructure.

## Obtaining the refresh token (one-time)
**Settings › Pricing sync** has a "How do I get a refresh token?" helper that
prints the exact PowerShell, pre-filled with your tenant/app IDs. In brief, on any
machine with a browser, signed in as the MFA-enabled Admin Agent / Sales Agent
service account:

```powershell
Install-Module PartnerCenter -Scope CurrentUser -Force
$secret = ConvertTo-SecureString "<APP_SECRET>" -AsPlainText -Force
$cred   = New-Object System.Management.Automation.PSCredential("<CLIENT_ID>", $secret)
$token  = New-PartnerAccessToken -ApplicationId "<CLIENT_ID>" -Credential $cred `
  -Scopes "https://api.partner.microsoft.com/user_impersonation" `
  -Tenant "<TENANT_ID>" -UseAuthorizationCode
$token.RefreshToken   # paste into Settings
```

Certificate apps use `-CertificateThumbprint <thumbprint>` instead of the
credential. Ensure the app registration (Partner Center App Management) has the
pricing-API permission and the consent covered it, or the exchange to
`api.partner.microsoft.com` will fail.

## Freshness rules
- **Day rule**: Fresh < `AGE_AGING_DAYS` ≤ Aging < `AGE_STALE_DAYS` ≤ Stale (or no sheet → Stale).
- **Month rule** (optional): sheet data month ≠ current month → at least Aging.
- The stricter state wins.
- **Newest source wins**: freshness classifies the most recent *successful*
  pricing load across **both** paths — the price-sync sheet on disk and a manual
  CSV upload (each recorded as a `CatalogImport`, §4.4a of the data model). A
  mixed shop always reflects whichever path last succeeded.
- **CSV data month**: taken from the sheet's own `LastUpdatedDate` column, so an
  uploaded sheet ages by *when Microsoft last revised it*, not when it was
  uploaded. A pre-`LastUpdatedDate` sheet (no such column) falls back to the
  upload month, so it still reads Fresh on upload.

## Storage
Sheets + a JSON metadata sidecar are written to `DATA_DIR` with atomic
temp-then-rename, a SHA-256, a `latest.json` pointer, and retention of the newest
`RETENTION_COUNT` sheets. A failed fetch never corrupts the last good sheet.

## Validation status
Offline logic — freshness classification, atomic storage, retention, hashing, and
the CSP GUI config flow — is unit-tested (`backend/tests/test_pricesync.py`). The
live refresh-token exchange and real Partner Center fetch require a partner tenant
with the roles above and must be validated in the target environment.
