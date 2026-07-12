"""One-click container update via a Watchtower sidecar's HTTP API.

The app runs INSIDE the container it would update, so it can't pull a new image
and recreate itself — a process cannot outlive the recreation of its own
container. Instead a Watchtower sidecar (see docker-compose.yml) watches this
container and exposes an authenticated HTTP endpoint; the "Update now" button
POSTs to it and Watchtower does the pull + recreate. That is also why the update
briefly drops the connection: the container is replaced out from under the app.

Config split follows the app's law (docs/DATA_ARCHITECTURE.md): the Watchtower
base URL is operational settings (settings.watchtower_url, an env var), while the
bearer token is a secret in the encrypted store (secrets.WATCHTOWER_API_TOKEN).
"""

from __future__ import annotations

import httpx

from ..config import settings
from . import secrets


def _token() -> str | None:
    store = secrets.get_store()
    return store.get(secrets.WATCHTOWER_API_TOKEN) if store.enabled else None


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

    Returns `{ok, detail}`. Unlike the update *check* this is NOT fail-silent — it
    is an explicit operator action, so a misconfiguration or a Watchtower error is
    reported back to the UI rather than swallowed."""
    url = (settings.watchtower_url or "").rstrip("/")
    if not url:
        return {"ok": False, "detail": "No Watchtower URL configured — set TCO_WATCHTOWER_URL."}
    token = _token()
    if not token:
        return {"ok": False,
                "detail": "No Watchtower API token set — add it under Settings › Secrets."}
    try:
        resp = httpx.post(
            f"{url}/v1/update",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"Could not reach Watchtower: {exc}"}
    if resp.status_code == 401:
        return {"ok": False, "detail": "Watchtower rejected the token (401) — check it matches."}
    if resp.status_code >= 400:
        return {"ok": False, "detail": f"Watchtower returned HTTP {resp.status_code}."}
    return {"ok": True,
            "detail": "Update triggered — the container will pull the new image and restart shortly."}
