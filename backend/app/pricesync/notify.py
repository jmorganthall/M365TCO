"""Optional unattended notification (PRD §6.5 FR-UI-4).

When the daily local check finds Stale, post one message to a webhook (Teams-style
or generic). This uses no API call and does not fetch. A small marker file
prevents repeat notifications for the same day.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from .config import PriceSyncConfig
from .freshness import Freshness


def _marker_path(cfg: PriceSyncConfig) -> str:
    return os.path.join(cfg.data_dir, ".notified")


def _already_notified_today(cfg: PriceSyncConfig, day: str) -> bool:
    path = _marker_path(cfg)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() == day
    except OSError:
        return False


def _record_notified(cfg: PriceSyncConfig, day: str) -> None:
    try:
        os.makedirs(cfg.data_dir, exist_ok=True)
        with open(_marker_path(cfg), "w", encoding="utf-8") as fh:
            fh.write(day)
    except OSError:
        pass


def notify_if_stale(cfg: PriceSyncConfig, freshness: Freshness) -> bool:
    """Post one webhook message if Stale and not already notified today.
    Returns True if a notification was sent. Never calls the price sheet API."""
    if not cfg.notify_webhook_url or not freshness.is_stale:
        return False
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _already_notified_today(cfg, day):
        return False

    text = (
        "M365 TCO pricing is STALE. "
        + " ".join(freshness.reasons)
        + " Sign in to the app and refresh pricing."
    )
    try:
        # Teams-style card; also works as a generic JSON webhook body.
        httpx.post(cfg.notify_webhook_url, json={"text": text}, timeout=30)
    except httpx.HTTPError:
        return False
    _record_notified(cfg, day)
    return True
