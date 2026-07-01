"""Partner Center pricing API client (PRD Section 8.2) — phase two.

Same target table and parser as the CSV path (8.1); this just automates the
fetch. Authentication is App+User via the Secure Application Model: a stored
refresh token (in the encrypted secret store) is exchanged for an access token
with audience https://api.partner.microsoft.com, then the price-sheet endpoint
is called with the bearer token. The response is a CSV (optionally zip) stream
fed to the same parser as the CSV import.

The pricing host (api.partner.microsoft.com) differs from the rest of the
Partner Center API (api.partnercenter.microsoft.com).

MFA: from Oct 2025 the APIs check the MFA claim; from Apr 1 2026 MFA is enforced
for App+User. A token obtained through SAM with an MFA-enabled service account
satisfies this. Refresh tokens can expire/revoke — re-consent via the operator
flow (admin endpoint) restores the stored token.
"""

from __future__ import annotations

import io
import zipfile

import httpx

from . import secrets

PRICING_HOST = "https://api.partner.microsoft.com"
PRICING_AUDIENCE = "https://api.partner.microsoft.com"
TOKEN_ENDPOINT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


class PartnerCenterNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    store = secrets.get_store()
    if not store.enabled:
        return False
    return all(
        store.get(k)
        for k in (
            secrets.PARTNER_CENTER_REFRESH_TOKEN,
            secrets.PARTNER_CENTER_APP_ID,
            secrets.PARTNER_CENTER_TENANT_ID,
        )
    )


def _exchange_refresh_token() -> str:
    store = secrets.get_store()
    if not is_configured():
        raise PartnerCenterNotConfigured(
            "Partner Center not configured. Complete the operator consent flow to "
            "store a refresh token, app id, and tenant id."
        )
    tenant = store.get(secrets.PARTNER_CENTER_TENANT_ID)
    data = {
        "client_id": store.get(secrets.PARTNER_CENTER_APP_ID),
        "grant_type": "refresh_token",
        "refresh_token": store.get(secrets.PARTNER_CENTER_REFRESH_TOKEN),
        "scope": f"{PRICING_AUDIENCE}/.default",
    }
    secret = store.get(secrets.PARTNER_CENTER_APP_SECRET)
    if secret:
        data["client_secret"] = secret

    resp = httpx.post(TOKEN_ENDPOINT.format(tenant=tenant), data=data, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    # Rotate the refresh token if a new one is returned.
    if payload.get("refresh_token"):
        store.set(secrets.PARTNER_CENTER_REFRESH_TOKEN, payload["refresh_token"])
    return payload["access_token"]


def fetch_price_sheet(market: str = "US", timeline: str = "current", month: str | None = None) -> str:
    """Fetch the new-commerce license-based price sheet and return CSV text.

    timeline: current | future | history. month=YYYYMM required for history.
    A 404 on future means no change is coming (8.2).
    """
    token = _exchange_refresh_token()
    view = "updatedlicensebased"
    url = (
        f"{PRICING_HOST}/v1.0/sales/pricesheets"
        f"(Market='{market}',PricesheetView='{view}')/$value"
    )
    params = {"timeline": timeline}
    if timeline == "history" and month:
        params["Month"] = month

    resp = httpx.get(
        url,
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            # Documented MFA validation header pattern (8.2). The SAM token already
            # carries the MFA claim; this header signals MFA-aware client behavior.
            "X-MS-PartnerCenter-Application": "M365-TCO-Tool",
        },
        timeout=300,
        follow_redirects=True,
    )
    if resp.status_code == 404 and timeline == "future":
        return ""  # no upcoming change
    resp.raise_for_status()

    content = resp.content
    # Response may be CSV or zip-compressed CSV (8.2).
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            name = zf.namelist()[0]
            return zf.read(name).decode("utf-8-sig")
    return content.decode("utf-8-sig")
