"""Configuration for the price-sheet sync module (PRD §7).

All configuration is via environment variables using the exact names from the
PRD, read directly from the environment (not the TCO_ settings prefix) so the
operator's env matches the spec. No secrets are committed; they are injected at
runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..config import settings

# Fixed technical facts (PRD §2) — do not deviate.
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


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (ValueError, AttributeError):
        return default


@dataclass
class PriceSyncConfig:
    tenant_id: str
    client_id: str
    redirect_uri: str
    client_cert_path: str
    client_secret: str
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
        """Enough to attempt an interactive login + fetch."""
        return bool(
            self.tenant_id and self.client_id and self.redirect_uri
            and self.pricesheet_view and (self.client_cert_path or self.client_secret)
        )

    @property
    def credential_kind(self) -> str:
        if self.client_cert_path:
            return "certificate"
        if self.client_secret:
            return "secret"
        return "none"


def load_config() -> PriceSyncConfig:
    default_data_dir = os.path.join(settings.data_dir, "pricesheets")
    return PriceSyncConfig(
        tenant_id=os.environ.get("TENANT_ID", "").strip(),
        client_id=os.environ.get("CLIENT_ID", "").strip(),
        redirect_uri=os.environ.get("REDIRECT_URI", "").strip(),
        client_cert_path=os.environ.get("CLIENT_CERT_PATH", "").strip(),
        client_secret=os.environ.get("CLIENT_SECRET", "").strip(),
        market=os.environ.get("MARKET", "US").strip() or "US",
        pricesheet_view=os.environ.get("PRICESHEET_VIEW", "").strip(),
        timeline=os.environ.get("PRICESHEET_TIMELINE", "current").strip() or "current",
        data_dir=os.environ.get("DATA_DIR", default_data_dir).strip() or default_data_dir,
        aging_days=_int("AGE_AGING_DAYS", 25),
        stale_days=_int("AGE_STALE_DAYS", 30),
        use_month_rule=_bool("AGE_USE_MONTH_RULE", True),
        retention_count=_int("RETENTION_COUNT", 2),
        notify_webhook_url=os.environ.get("NOTIFY_WEBHOOK_URL", "").strip(),
    )
