"""One-click container update via a Watchtower sidecar's HTTP API.

The app runs INSIDE the container it would update, so it can't pull a new image
and recreate itself — a process cannot outlive the recreation of its own
container. Instead a Watchtower sidecar (see docker-compose.yml) watches this
container and exposes an authenticated HTTP endpoint; the "Update now" button
POSTs to it and Watchtower does the pull + recreate. That is also why the update
briefly drops the connection: the container is replaced out from under the app.

Config split follows the app's law (docs/DATA_ARCHITECTURE.md): the Watchtower
base URL is operational settings (settings.watchtower_url, an env var). The bearer
token is preferentially a secret in the encrypted store (secrets.WATCHTOWER_API_TOKEN,
entered in Settings › Secrets); as a fallback it may be supplied from the deploy
environment (settings.watchtower_token / WATCHTOWER_TOKEN) so a shared, centrally
managed Watchtower can be wired once through env vars — the secret store wins when
both are present.
"""

from __future__ import annotations

import logging
import re

import httpx

from ..config import settings
from . import secrets

_log = logging.getLogger("m365tco.updater")

# Watchtower's on-demand /v1/update returns HTTP 200 even when it scans and does
# NOT recreate anything (no newer image, a pull it couldn't do, or an update
# already running). These markers in its response body mean "ran, changed
# nothing" — so we don't tell the operator a restart is coming that never will.
_NOOP_MARKERS = (
    re.compile(r"Updated=0\b"),          # "Session done: Failed=0 Scanned=1 Updated=0"
    re.compile(r"no new images", re.I),
    re.compile(r"already in progress", re.I),
)


def _token() -> str | None:
    """The Watchtower HTTP-API bearer token. Encrypted secret store first (it wins
    when both are set), then the operational env fallback (settings.watchtower_token
    / WATCHTOWER_TOKEN)."""
    store = secrets.get_store()
    if store.enabled:
        stored = store.get(secrets.WATCHTOWER_API_TOKEN)
        if stored:
            return stored
    return settings.watchtower_token or None


def status() -> dict:
    """Whether the one-click update action is available: a Watchtower URL is
    configured (operational) and its API token is set (secret store). The UI shows
    the button only when `configured` is true; otherwise it keeps the manual
    'pull the newest image' guidance."""
    url_set = bool(settings.watchtower_url)
    token_set = bool(_token())
    return {
        "configured": url_set and token_set,
        "url_set": url_set,
        "token_set": token_set,
    }


def trigger() -> dict:
    """POST Watchtower's `/v1/update` to pull + recreate this container now.

    Returns `{ok, no_op, detail}`. Unlike the update *check* this is NOT
    fail-silent — it is an explicit operator action, so a misconfiguration or a
    Watchtower error is reported back to the UI rather than swallowed. Because
    Watchtower answers 200 even for a no-op (nothing newer to pull, or a scan that
    recreated nothing), the outcome is also LOGGED server-side (visible in
    `docker logs m365tco`) and Watchtower's own response body is surfaced, so a
    button-press that changes nothing is diagnosable instead of silent."""
    url = (settings.watchtower_url or "").rstrip("/")
    if not url:
        _log.warning("Update now: no Watchtower URL configured")
        return {"ok": False, "no_op": False,
                "detail": "No Watchtower URL configured — set WATCHTOWER_URL (or TCO_WATCHTOWER_URL)."}
    token = _token()
    if not token:
        _log.warning("Update now: no Watchtower API token set")
        return {"ok": False, "no_op": False,
                "detail": "No Watchtower API token set — add it under Settings › Secrets, "
                          "or set WATCHTOWER_TOKEN in the environment."}
    endpoint = f"{url}/v1/update"
    _log.info("Update now: POST %s", endpoint)  # never log the bearer token
    try:
        resp = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        _log.warning("Update now: could not reach Watchtower at %s: %s", endpoint, exc)
        return {"ok": False, "no_op": False, "detail": f"Could not reach Watchtower: {exc}"}

    # Watchtower's update run writes its result into the response body — capture it
    # (trimmed) so the operator sees what it actually did. Truncate for the UI.
    body = (resp.text or "").strip()
    body_short = body if len(body) <= 500 else body[:500] + "…"
    _log.info("Update now: Watchtower HTTP %s%s", resp.status_code,
              f" — {body}" if body else " (empty body)")

    if resp.status_code == 401:
        return {"ok": False, "no_op": False,
                "detail": "Watchtower rejected the token (401) — check it matches "
                          "WATCHTOWER_HTTP_API_TOKEN on the sidecar."}
    if resp.status_code >= 400:
        return {"ok": False, "no_op": False,
                "detail": f"Watchtower returned HTTP {resp.status_code}."
                          + (f" {body_short}" if body else "")}

    no_op = any(m.search(body) for m in _NOOP_MARKERS)
    if no_op:
        _log.warning("Update now: Watchtower ran but updated nothing — %s", body or "(no detail)")
        return {"ok": True, "no_op": True,
                "detail": "Watchtower ran but found no image to update — the container "
                          "won't restart. Confirm a newer image is published and pullable "
                          "(check the sidecar's own log: docker logs m365tco-watchtower)."
                          + (f" Watchtower said: {body_short}" if body else "")}
    return {"ok": True, "no_op": False,
            "detail": "Update triggered — the container will pull the new image and restart shortly."
                      + (f" Watchtower: {body_short}" if body else "")}
