"""Staple Microsoft bundle library — the SKU → Bundle → Outcomes spine.

Global, editable, seeded from seeds/bundles.json. Bundles are the stable
identities that coverage, scenarios, and licenses resolve to; the many priced
catalog SKUs collapse onto a bundle via MicrosoftSku.bundle_id.
"""

from __future__ import annotations

import functools
import json
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds")


@functools.lru_cache(maxsize=None)
def _seed() -> dict:
    with open(os.path.join(SEED_DIR, "bundles.json"), encoding="utf-8") as fh:
        return json.load(fh)


def seed_bundles(db: Session) -> None:
    """Insert any seed bundle whose key isn't present yet, then reconcile the
    add-on → base links. Never overwrites operator edits to name/kind. Idempotent."""
    by_key = {b.key: b for b in db.execute(select(models.Bundle)).scalars().all()}
    changed = False
    for b in _seed()["bundles"]:
        if b["key"] not in by_key:
            row = models.Bundle(
                key=b["key"], name=b["name"], kind=b.get("kind", "bundle"),
                sort_order=b.get("sort_order", 0),
            )
            db.add(row)
            by_key[b["key"]] = row
            changed = True
    if changed:
        db.flush()
    # Resolve add-on base links now that every bundle row exists.
    for b in _seed()["bundles"]:
        base_key = b.get("base")
        row = by_key.get(b["key"])
        if base_key and row is not None and row.base_bundle_id is None:
            base = by_key.get(base_key)
            if base is not None:
                row.base_bundle_id = base.id
                changed = True
    if changed:
        db.commit()


def list_bundles(db: Session) -> list[models.Bundle]:
    seed_bundles(db)
    return db.execute(
        select(models.Bundle).order_by(models.Bundle.sort_order, models.Bundle.name)
    ).scalars().all()


def _norm(s: str) -> str:
    return (s or "").lower().replace(" ", " ").replace("  ", " ").strip()


# Legacy shortcodes / common aliases → bundle key, so existing scenario targets
# and current-license references still resolve after the re-key.
_ALIASES = {
    "f1": "m365-f1", "f3": "m365-f3", "e3": "m365-e3", "e5": "m365-e5",
    "e7": "m365-e7", "business premium": "m365-business-premium",
    "entra id p2": "entra-id-p2", "defender for endpoint p2": "defender-endpoint-p2",
    "defender for office 365 p2": "defender-office-p2", "sentinel": "sentinel",
    "teams phone": "teams-phone", "power bi pro": "power-bi-pro",
    "power automate premium": "power-automate-premium",
}


def resolve_bundle(db: Session, ref: str) -> str | None:
    """Resolve a free-text SKU/bundle reference to a Bundle id, or None. Tiered:
    exact key, legacy alias, exact bundle name, then a mapped catalog SKU whose
    title matches. Read-only (assumes bundles are seeded)."""
    if not ref:
        return None
    r = _norm(ref)
    rows = db.execute(select(models.Bundle)).scalars().all()
    by_key = {b.key: b.id for b in rows}
    if r in by_key:
        return by_key[r]
    if r in _ALIASES and _ALIASES[r] in by_key:
        return by_key[_ALIASES[r]]
    for b in rows:
        if _norm(b.name) == r:
            return b.id
    like = f"%{ref}%"
    row = db.execute(
        select(models.MicrosoftSku).where(
            models.MicrosoftSku.bundle_id.isnot(None),
            (models.MicrosoftSku.sku_title.ilike(like))
            | (models.MicrosoftSku.product_title.ilike(like)),
        )
    ).scalars().first()
    return row.bundle_id if row else None
