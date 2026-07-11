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


def _seed_base_keys(entry: dict) -> list[str]:
    """The base keys an add-on seed entry declares — `bases: [...]` (multi) unioned
    with the single-`base` sugar. Empty for à-la-carte add-ons and base bundles."""
    keys = list(entry.get("bases") or [])
    if entry.get("base"):
        keys.append(entry["base"])
    # De-dup, preserve order.
    seen: set[str] = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


def seed_bundles(db: Session) -> None:
    """Insert any seed bundle whose key isn't present yet, then reconcile the
    add-on → base primary link and the M:N AddonEligibility set. Never overwrites
    operator edits to name/kind. Idempotent."""
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
    # Resolve the primary add-on → base link now that every bundle row exists.
    for b in _seed()["bundles"]:
        base_keys = _seed_base_keys(b)
        row = by_key.get(b["key"])
        if base_keys and row is not None and row.base_bundle_id is None:
            base = by_key.get(base_keys[0])
            if base is not None:
                row.base_bundle_id = base.id
                changed = True
    if changed:
        db.flush()
    # Reconcile the eligibility set: ensure a row for every declared base of every
    # seeded add-on. Additive — never removes operator-added eligibilities.
    have = {
        (e.addon_bundle_id, e.base_bundle_id)
        for e in db.execute(select(models.AddonEligibility)).scalars().all()
    }
    for b in _seed()["bundles"]:
        row = by_key.get(b["key"])
        if row is None:
            continue
        for base_key in _seed_base_keys(b):
            base = by_key.get(base_key)
            if base is not None and (row.id, base.id) not in have:
                db.add(models.AddonEligibility(addon_bundle_id=row.id, base_bundle_id=base.id))
                have.add((row.id, base.id))
                changed = True
    if changed:
        db.commit()


def eligibility_map(db: Session) -> dict[str, set[str]]:
    """`{addon_bundle_id: {eligible base_bundle_id, …}}` for every add-on that has
    at least one eligibility row. An add-on ABSENT from this map is à-la-carte
    (eligible for any base) — see AddonEligibility."""
    out: dict[str, set[str]] = {}
    for e in db.execute(select(models.AddonEligibility)).scalars().all():
        out.setdefault(e.addon_bundle_id, set()).add(e.base_bundle_id)
    return out


def addon_applies(addon_id: str, base_id: str, elig_map: dict[str, set[str]]) -> bool:
    """True iff an add-on may layer onto a base: à-la-carte (no eligibility rows) →
    any base; otherwise the base must be in the add-on's eligibility set."""
    allowed = elig_map.get(addon_id)
    return allowed is None or base_id in allowed


def eligible_base_ids(db: Session, addon_id: str) -> list[str]:
    return [
        e.base_bundle_id
        for e in db.execute(
            select(models.AddonEligibility).where(
                models.AddonEligibility.addon_bundle_id == addon_id
            )
        ).scalars().all()
    ]


def set_addon_eligibility(db: Session, addon_id: str, base_ids: list[str]) -> list[str]:
    """Replace an add-on's eligible-base set (à-la-carte when empty). Returns the
    resulting base id list. Assumes caller validated the ids."""
    existing = db.execute(
        select(models.AddonEligibility).where(
            models.AddonEligibility.addon_bundle_id == addon_id
        )
    ).scalars().all()
    want = set(base_ids)
    have = {e.base_bundle_id: e for e in existing}
    for bid, row in have.items():
        if bid not in want:
            db.delete(row)
    for bid in want:
        if bid not in have:
            db.add(models.AddonEligibility(addon_bundle_id=addon_id, base_bundle_id=bid))
    db.commit()
    return eligible_base_ids(db, addon_id)


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
