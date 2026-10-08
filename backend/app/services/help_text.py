"""The walkthrough's help text (docs/WALKTHROUGH.md §9): one reviewed file,
`app/content/help_text.json`, read by the GUI tooltips (via /api/help-text) and by
the customer PDF's "How we calculated this" page — so the screen and the paper
can never disagree. Content, not domain data: it changes through a reviewed PR,
never in Settings."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "content" / "help_text.json"


@lru_cache(maxsize=1)
def load() -> dict:
    with _PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    data.pop("_about", None)
    return data


def field(key: str) -> dict:
    return load()["fields"].get(key, {})


def method() -> list[dict]:
    return load()["method"]
