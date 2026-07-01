"""Price-sheet fetch (PRD §6.2). One API call, streamed to disk.

Uses the access token once and discards it. Streams the response to a temp file
(sheets can be tens of MB), detects zipped vs plain CSV, then hands the staged
CSV to storage.commit_sheet for atomic publish. Never overwrites the last good
sheet on failure.

The Authorization header is never logged (SEC-5).
"""

from __future__ import annotations

import os
import zipfile
from datetime import datetime, timezone
from typing import Optional

import httpx

from .config import PRICE_SHEET_ENDPOINT, PriceSyncConfig
from . import storage


class FetchError(RuntimeError):
    """Carries a user-facing, actionable message (no stack trace to the UI)."""


def _friendly_status_error(status: int) -> str:
    if status == 400:
        return (
            "Bad request (400). The price sheet endpoint requires app+user "
            "authentication — app-only (client credentials) is not supported. "
            "Sign in interactively and retry."
        )
    if status in (401, 403):
        return (
            f"Not authorized ({status}). Your token may have expired, or the "
            "sign-in account lacks the Admin Agent or Sales Agent role. Re-login "
            "and confirm the account's Partner Center agent role."
        )
    if status == 404:
        return (
            "Not found (404). The requested PricesheetView may be unavailable to "
            "this account, or future pricing was requested where none exists."
        )
    return f"Price sheet request failed with HTTP {status}."


def _data_month(cfg: PriceSyncConfig) -> str:
    # For the 'current' timeline the data month is the current calendar month.
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _mfa_compliant_from(resp: httpx.Response) -> Optional[bool]:
    # Partner Center echoes MFA compliance when ValidateMfa is requested. It may
    # arrive as a header; capture it best-effort. Interactive sign-in satisfies MFA.
    for key in ("isMfaCompliant", "IsMfaCompliant", "X-IsMfaCompliant"):
        if key in resp.headers:
            return resp.headers[key].strip().lower() == "true"
    return None


def fetch_and_store(cfg: PriceSyncConfig, access_token: str, validate_mfa: bool = True) -> dict:
    """Fetch the price sheet with the given token and store it. Returns metadata."""
    url = PRICE_SHEET_ENDPOINT.format(market=cfg.market, view=cfg.pricesheet_view)
    headers = {
        "Authorization": f"Bearer {access_token}",  # never logged
        "Accept-Encoding": "deflate",
        "Accept": "application/octet-stream",
    }
    if validate_mfa:
        # Diagnostic path (FR-AUTH-6): ask Partner Center to validate MFA.
        headers["ValidateMfa"] = "true"

    os.makedirs(cfg.data_dir, exist_ok=True)
    staged_raw = os.path.join(cfg.data_dir, ".staging.download")

    last_err: Optional[Exception] = None
    for attempt in range(2):  # retry once on network/timeout (FR-FETCH-7)
        try:
            with httpx.stream(
                "GET", url, params={"timeline": cfg.timeline}, headers=headers,
                timeout=300, follow_redirects=True,
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise FetchError(_friendly_status_error(resp.status_code))
                compressed = resp.headers.get("Content-Encoding", "").lower() == "deflate"
                mfa = _mfa_compliant_from(resp)
                with open(staged_raw, "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        fh.write(chunk)
            break
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_err = exc
            if attempt == 0:
                continue
            _cleanup(staged_raw)
            raise FetchError(
                "Network error fetching the price sheet after a retry. The previous "
                "sheet was left intact."
            ) from exc
        except FetchError:
            _cleanup(staged_raw)
            raise

    # Detect zipped CSV vs plain CSV and stage a decompressed .csv temp file.
    staged_csv = os.path.join(cfg.data_dir, ".staging.csv")
    try:
        with open(staged_raw, "rb") as fh:
            head = fh.read(2)
        if head == b"PK":  # zip archive
            with zipfile.ZipFile(staged_raw) as zf:
                name = zf.namelist()[0]
                with zf.open(name) as src, open(staged_csv, "wb") as dst:
                    for chunk in iter(lambda: src.read(1024 * 256), b""):
                        dst.write(chunk)
            compressed = True
            os.remove(staged_raw)
        else:
            os.replace(staged_raw, staged_csv)
    except zipfile.BadZipFile as exc:
        _cleanup(staged_raw, staged_csv)
        raise FetchError("Downloaded file was not valid CSV or zip.") from exc

    metadata = storage.commit_sheet(
        cfg, staged_csv, data_month=_data_month(cfg),
        compressed_on_wire=compressed, mfa_compliant=mfa,
    )
    return metadata


def _cleanup(*paths: str) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass
