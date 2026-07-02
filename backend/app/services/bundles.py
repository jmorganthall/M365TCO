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
