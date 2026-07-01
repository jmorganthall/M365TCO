"""Price-sheet sync endpoints.

Configuration is GUI-managed (no env vars):
- GET/PUT /api/pricesync/config     non-secret settings (first-class singleton).
- PUT/DELETE /api/pricesync/credential  client secret or certificate PEM (encrypted store).
- GET  /api/pricesync/status        freshness state; no auth, no API call.
- POST /api/pricesync/login-url     begin interactive login; returns the auth URL.
- GET  /auth/callback               OAuth redirect; exchanges code, fetches, stores.
- POST /api/pricesync/import-latest  parse the stored sheet into the SKU catalog.
- POST /api/pricesync/check-notify   local age check + optional webhook. No API call.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..pricesync import auth, config, fetch, freshness, notify, storage
from ..services import pricesheet, secrets

router = APIRouter(tags=["pricesync"])


def _missing_config(cfg: config.PriceSyncConfig) -> list[str]:
    # Tenant ID (auto-discovered) and Redirect URI (auto-derived) are not required.
    required = {
        "Client (application) ID": cfg.client_id,
        "Price sheet view": cfg.pricesheet_view,
        "Credential (certificate or client secret)": cfg.client_cert_pem or cfg.client_secret,
    }
    return [name for name, value in required.items() if not value]


def _derive_redirect_uri(request: Request) -> str:
    """The app's own callback URL, honoring reverse-proxy forwarded headers so it
    matches the origin the browser actually used. uvicorn's proxy-headers handles
    X-Forwarded-Proto but not X-Forwarded-Host, so read both explicitly."""
    proto = (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        or request.url.scheme
    )
    host = (
        request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        or request.headers.get("host", "").strip()
        or request.url.netloc
    )
    return f"{proto}://{host}/auth/callback"


def _effective_redirect_uri(request: Request, row) -> str:
    return (row.redirect_uri or "").strip() or _derive_redirect_uri(request)


def _freshness_for(cfg: config.PriceSyncConfig) -> freshness.Freshness:
    meta = storage.read_latest(cfg)
    return freshness.classify(
        meta.get("fetched_at") if meta else None,
        meta.get("data_month") if meta else None,
        aging_days=cfg.aging_days, stale_days=cfg.stale_days,
        use_month_rule=cfg.use_month_rule,
    )


@router.get("/api/pricesync/status")
def status(db: Session = Depends(get_db)):
    """Freshness — automatic, local, no auth, no API call."""
    cfg = config.load_config(db)
    fr = _freshness_for(cfg)
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
        "latest": storage.read_latest(cfg),
    }


def _config_payload(request: Request, db: Session) -> dict:
    """Non-secret settings for the editor. Secrets are never returned — only
    whether a credential is set."""
    row = config.get_or_create_settings(db)
    store = secrets.get_store()
    return {
        "tenant_id": row.tenant_id,
        "client_id": row.client_id,
        "redirect_uri": row.redirect_uri,
        "effective_redirect_uri": _effective_redirect_uri(request, row),
        "suggested_redirect_uri": _derive_redirect_uri(request),
        "signed_in_user": row.signed_in_user,
        "pricesheet_view": row.pricesheet_view,
        "market": row.market,
        "timeline": row.timeline,
        "aging_days": row.aging_days,
        "stale_days": row.stale_days,
        "use_month_rule": row.use_month_rule,
        "retention_count": row.retention_count,
        "notify_webhook_url": row.notify_webhook_url,
        "valid_views": list(config.VALID_VIEWS),
        "secret_store_enabled": store.enabled,
        "credential_set": bool(
            store.enabled and (
                store.get(secrets.PRICESYNC_CLIENT_CERT_PEM)
                or store.get(secrets.PRICESYNC_CLIENT_SECRET)
            )
        ),
        "credential_kind": (
            "certificate" if store.enabled and store.get(secrets.PRICESYNC_CLIENT_CERT_PEM)
            else "secret" if store.enabled and store.get(secrets.PRICESYNC_CLIENT_SECRET)
            else "none"
        ),
    }


@router.get("/api/pricesync/config")
def get_config(request: Request, db: Session = Depends(get_db)):
    return _config_payload(request, db)


@router.put("/api/pricesync/config")
def update_config(
    payload: schemas.PriceSyncConfigUpdate, request: Request, db: Session = Depends(get_db)
):
    row = config.get_or_create_settings(db)
    data = payload.model_dump(exclude_unset=True)
    if "pricesheet_view" in data and data["pricesheet_view"] and data["pricesheet_view"] not in config.VALID_VIEWS:
        raise HTTPException(422, f"Invalid price sheet view. Valid: {', '.join(config.VALID_VIEWS)}")
    for k, v in data.items():
        if v is not None:
            setattr(row, k, v)
    db.commit()
    return _config_payload(request, db)


@router.put("/api/pricesync/credential")
def set_credential(payload: schemas.PriceSyncCredentialIn):
    """Store the client secret or certificate PEM in the encrypted store."""
    store = secrets.get_store()
    if not store.enabled:
        raise HTTPException(400, "Secret store disabled: set TCO_MASTER_SECRET to store credentials.")
    if payload.kind == "certificate":
        # Validate the PEM contains a usable private key + certificate.
        try:
            serialization.load_pem_private_key(payload.value.encode(), password=None)
            x509.load_pem_x509_certificate(payload.value.encode())
        except Exception:
            raise HTTPException(422, "Not a valid PEM containing a private key and certificate.")
        store.set(secrets.PRICESYNC_CLIENT_CERT_PEM, payload.value)
        store.delete(secrets.PRICESYNC_CLIENT_SECRET)  # cert supersedes secret
        return {"ok": True, "credential_kind": "certificate"}
    if payload.kind == "secret":
        if not payload.value.strip():
            raise HTTPException(422, "Client secret is empty.")
        store.set(secrets.PRICESYNC_CLIENT_SECRET, payload.value)
        store.delete(secrets.PRICESYNC_CLIENT_CERT_PEM)
        return {"ok": True, "credential_kind": "secret"}
    raise HTTPException(422, "kind must be 'certificate' or 'secret'.")


@router.delete("/api/pricesync/credential")
def clear_credential():
    store = secrets.get_store()
    if store.enabled:
        store.delete(secrets.PRICESYNC_CLIENT_SECRET)
        store.delete(secrets.PRICESYNC_CLIENT_CERT_PEM)
    return {"ok": True, "credential_kind": "none"}


@router.post("/api/pricesync/login-url")
def login_url(request: Request, db: Session = Depends(get_db)):
    cfg = config.load_config(db)
    if not cfg.auth_configured:
        raise HTTPException(400, "Price sync is not configured. Enter the Client ID and a credential.")
    row = config.get_or_create_settings(db)
    redirect_uri = _effective_redirect_uri(request, row)
    try:
        return {"auth_url": auth.begin_login(cfg, redirect_uri), "redirect_uri": redirect_uri}
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))
    except ImportError:
        raise HTTPException(500, "msal is not installed in this image.")


@router.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(request: Request, db: Session = Depends(get_db)):
    cfg = config.load_config(db)
    params = dict(request.query_params)
    if "error" in params:
        return _page(False, params.get("error_description", params["error"]))
    try:
        token, claims = auth.redeem_code(cfg, params)
        # Auto-capture the tenant (if not set yet) and the signed-in account.
        row = config.get_or_create_settings(db)
        tid = claims.get("tid")
        who = claims.get("preferred_username") or claims.get("name") or ""
        changed = False
        if tid and not row.tenant_id:
            row.tenant_id = tid
            changed = True
        if who and who != row.signed_in_user:
            row.signed_in_user = who
            changed = True
        if changed:
            db.commit()
        # Re-load config so the freshly captured tenant is used for the fetch.
        cfg = config.load_config(db)
        meta = fetch.fetch_and_store(cfg, token)
        del token  # used once, discarded (no refresh token stored)
        who_note = f" Signed in as {who}." if who else ""
        return _page(
            True,
            f"Price sheet {meta['file_name']} stored "
            f"({meta['file_bytes']:,} bytes, MFA compliant: {meta.get('mfa_compliant')}).{who_note}",
        )
    except (auth.AuthError, fetch.FetchError) as exc:
        return _page(False, str(exc))


@router.post("/api/pricesync/import-latest")
def import_latest(catalog_version: str = "", db: Session = Depends(get_db)):
    cfg = config.load_config(db)
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
def check_notify(db: Session = Depends(get_db)):
    cfg = config.load_config(db)
    fr = _freshness_for(cfg)
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
