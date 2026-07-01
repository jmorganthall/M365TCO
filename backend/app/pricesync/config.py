"""Configuration for the price-sheet sync module (Cloud Solution Provider auth).

CSP / Secure Application Model: a one-time interactive partner consent (MFA,
dedicated service account holding Admin Agent / Sales Agent) yields a refresh
token, which is exchanged for access tokens on the partner's behalf — no per-
fetch browser redirect.

Configuration is GUI-managed and persisted, NOT in environment variables:
  - Non-secret settings (tenant, client id, view, market, freshness thresholds,
    webhook) live in the first-class PriceSyncSettings singleton.
  - The app credential (client secret or certificate PEM) AND the refresh token
    live in the encrypted secret store.
Only DATA_DIR (the storage path on the persistent volume) is infrastructure and
may come from the environment; it defaults to <TCO_DATA_DIR>/pricesheets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import settings
from ..services import secrets

# Fixed technical facts (do not deviate).
PRICE_SHEET_ENDPOINT = (
    "https://api.partner.microsoft.com/v1.0/sales/pricesheets"
    "(Market='{market}',PricesheetView='{view}')/$value"
)
TOKEN_SCOPE = "https://api.partner.microsoft.com/.default"
AUTHORITY = "https://login.microsoftonline.com/{tenant_id}"

VALID_VIEWS = (
    "azure_consumption",
    "azure_reservations",
    "updatedlicensebased",
    "licensebasedest",
    "licensebasedeos",
    "marketplace",
    "software",
)


@dataclass
class PriceSyncConfig:
    tenant_id: str
    client_id: str
    client_cert_pem: str
    client_secret: str
    refresh_token: str
    market: str
    pricesheet_view: str
    timeline: str
    data_dir: str
    aging_days: int
    stale_days: int
    use_month_rule: bool
    retention_count: int
    notify_webhook_url: str

    @property
    def auth_configured(self) -> bool:
        # CSP: partner tenant + app id + app credential + the consent refresh
        # token. No redirect URI (token exchange is server-side).
        return bool(
            self.tenant_id and self.client_id and self.pricesheet_view
            and (self.client_cert_pem or self.client_secret)
            and self.refresh_token
        )

    @property
    def credential_kind(self) -> str:
        if self.client_cert_pem:
            return "certificate"
        if self.client_secret:
            return "secret"
        return "none"


def get_or_create_settings(db: Session):
    from .. import models

    row = db.get(models.PriceSyncSettings, "singleton")
    if row is None:
        row = models.PriceSyncSettings(id="singleton")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def load_config(db: Session) -> PriceSyncConfig:
    row = get_or_create_settings(db)
    store = secrets.get_store()
    cert_pem = store.get(secrets.PRICESYNC_CLIENT_CERT_PEM) if store.enabled else None
    client_secret = store.get(secrets.PRICESYNC_CLIENT_SECRET) if store.enabled else None
    refresh_token = store.get(secrets.PRICESYNC_REFRESH_TOKEN) if store.enabled else None

    default_data_dir = os.path.join(settings.data_dir, "pricesheets")
    return PriceSyncConfig(
        tenant_id=row.tenant_id,
        client_id=row.client_id,
        client_cert_pem=cert_pem or "",
        client_secret=client_secret or "",
        refresh_token=refresh_token or "",
        market=row.market or "US",
        pricesheet_view=row.pricesheet_view,
        timeline=row.timeline or "current",
        data_dir=os.environ.get("DATA_DIR", default_data_dir).strip() or default_data_dir,
        aging_days=row.aging_days,
        stale_days=row.stale_days,
        use_month_rule=row.use_month_rule,
        retention_count=row.retention_count,
        notify_webhook_url=row.notify_webhook_url,
    )
