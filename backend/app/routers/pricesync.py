"""Price-sheet sync endpoints (PRD §6.5).

- GET  /api/pricesync/status     freshness state; no auth, no API call.
- POST /api/pricesync/login-url  begin interactive login; returns the auth URL.
- GET  /auth/callback            OAuth redirect; exchanges code, fetches, stores.
- POST /api/pricesync/import-latest  parse the stored sheet into the SKU catalog.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..pricesync import auth, config, fetch, freshness, notify, storage
from ..services import pricesheet

router = APIRouter(tags=["pricesync"])


def _missing_config(cfg: config.PriceSyncConfig) -> list[str]:
    """Which required config fields are absent (for actionable UI guidance)."""
    required = {
        "TENANT_ID": cfg.tenant_id,
        "CLIENT_ID": cfg.client_id,
        "REDIRECT_URI": cfg.redirect_uri,
        "PRICESHEET_VIEW": cfg.pricesheet_view,
        "CLIENT_CERT_PATH or CLIENT_SECRET": cfg.client_cert_path or cfg.client_secret,
    }
    return [name for name, value in required.items() if not value]


def _status_payload() -> dict:
    cfg = config.load_config()
    meta = storage.read_latest(cfg)
    fr = freshness.classify(
        meta.get("fetched_at") if meta else None,
        meta.get("data_month") if meta else None,
        aging_days=cfg.aging_days, stale_days=cfg.stale_days,
        use_month_rule=cfg.use_month_rule,
    )
    return {
        "configured": cfg.auth_configured,
        "missing": _missing_config(cfg),
        "credential_kind": cfg.credential_kind,
        "market": cfg.market,
        "pricesheet_view": cfg.pricesheet_view,
        "state": fr.state,
        "age_days": fr.age_days,
        "data_month": fr.data_month,
        "current_month": fr.current_month,
        "reasons": fr.reasons,
        "thresholds": {"aging_days": cfg.aging_days, "stale_days": cfg.stale_days,
                       "use_month_rule": cfg.use_month_rule},
        "latest": meta,
    }


@router.get("/api/pricesync/status")
def status():
    """Freshness — automatic, local, no auth, no API call (PRD §6.4)."""
    return _status_payload()


@router.post("/api/pricesync/login-url")
def login_url():
    cfg = config.load_config()
    if not cfg.auth_configured:
        raise HTTPException(400, "Price sync is not configured. Set TENANT_ID, "
                                 "CLIENT_ID, REDIRECT_URI, PRICESHEET_VIEW and a credential.")
    try:
        return {"auth_url": auth.begin_login(cfg)}
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))
    except ImportError:
        raise HTTPException(500, "msal is not installed in this image.")


@router.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(request: Request):
    """OAuth redirect target. Exchanges the code, fetches one sheet, stores it,
    and discards the token. Errors render as a friendly page, not a stack trace."""
    cfg = config.load_config()
    params = dict(request.query_params)
    if "error" in params:
        return _page(False, params.get("error_description", params["error"]))
    try:
        token = auth.redeem_code(cfg, params)
        meta = fetch.fetch_and_store(cfg, token)
        del token  # used once, discarded (SEC-3)
        return _page(
            True,
            f"Price sheet {meta['file_name']} stored "
            f"({meta['file_bytes']:,} bytes, MFA compliant: {meta.get('mfa_compliant')}).",
        )
    except (auth.AuthError, fetch.FetchError) as exc:
        return _page(False, str(exc))


@router.post("/api/pricesync/import-latest")
def import_latest(catalog_version: str = "", db: Session = Depends(get_db)):
    """Parse the most recent stored sheet into the Microsoft SKU catalog using
    the existing header-mapped parser (keeps acquisition and parsing separate)."""
    cfg = config.load_config()
    path = storage.latest_csv_path(cfg)
    if not path:
        raise HTTPException(404, "No stored price sheet to import.")
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    meta = storage.read_latest(cfg) or {}
    version = catalog_version or meta.get("data_month", "pricesync")
    try:
        return pricesheet.import_price_sheet(db, text, version)
    except pricesheet.PriceSheetError as exc:
        raise HTTPException(422, str(exc))


@router.post("/api/pricesync/check-notify")
def check_notify():
    """Run the local age check and, if Stale, post one webhook. No API call."""
    cfg = config.load_config()
    meta = storage.read_latest(cfg)
    fr = freshness.classify(
        meta.get("fetched_at") if meta else None,
        meta.get("data_month") if meta else None,
        aging_days=cfg.aging_days, stale_days=cfg.stale_days,
        use_month_rule=cfg.use_month_rule,
    )
    sent = notify.notify_if_stale(cfg, fr)
    return {"state": fr.state, "notified": sent}


def _page(ok: bool, message: str) -> HTMLResponse:
    color = "#127436" if ok else "#b00020"
    title = "Pricing refreshed" if ok else "Refresh failed"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1226;
color:#e8ebff;display:flex;min-height:100vh;align-items:center;justify-content:center}}
.card{{background:#1a1f3c;border:1px solid #2c3566;border-radius:12px;padding:2rem;max-width:520px}}
h1{{color:{color};margin-top:0}} a{{color:#5b7cff}}</style></head>
<body><div class="card"><h1>{title}</h1><p>{message}</p>
<p><a href="/">Return to the app</a></p></div></body></html>"""
    )
