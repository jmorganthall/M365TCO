"""License-limit rules over the global Bundle spine (e.g. Microsoft 365 Business
≤ 300 seats in the tenant).

The limit DEFINITIONS are first-class, global, editable rows (LicenseLimit +
LicenseLimitMember), seeded from seeds/license_limits.json. The EVALUATION is a
pure, tenant-wide derived aggregate over an engagement's current licenses and
in-scope scenarios — it persists nothing (the same "don't create second-class
data" outcome as the best-bundle analysis). It runs at compute time and is
surfaced on the readout, so a violation is always visible in the GUI.
"""

from __future__ import annotations

import functools
import json
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import bundles as bundles_service
from . import raw

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds")


@functools.lru_cache(maxsize=None)
def _seed() -> dict:
    with open(os.path.join(SEED_DIR, "license_limits.json"), encoding="utf-8") as fh:
        return json.load(fh)


def seed_license_limits(db: Session) -> None:
    """Populate LicenseLimit + members from the seed file if the table is empty
    (populate-if-empty, the same pattern as DefaultOutcome). Resolves the seed's
    bundle keys to Bundle ids; runs after seed_bundles so the bundles exist."""
    if db.execute(select(models.LicenseLimit.id).limit(1)).first():
        return
    by_key = {b.key: b for b in bundles_service.list_bundles(db)}
    for item in _seed()["limits"]:
        limit = models.LicenseLimit(
            key=item["key"], name=item["name"],
            limit_type=item.get("limit_type", "max_total_seats"),
            max_quantity=item.get("max_quantity", 0),
            unit_basis=item.get("unit_basis", "Users"),
            scope=item.get("scope", "tenant"),
            sort_order=item.get("sort_order", 0),
        )
        db.add(limit)
        db.flush()  # assign id
        for bkey in item.get("bundles", []):
            bundle = by_key.get(bkey)
            if bundle is not None:
                db.add(models.LicenseLimitMember(
                    license_limit_id=limit.id, bundle_id=bundle.id))
    db.commit()


# Seeded limits that gained member bundles after they were first seeded: inserted
# on an already-seeded DB when missing, so a new plan that shares a cap counts
# toward it. An explicit list (the seed's other members are left exactly as the
# operator has them).
_MEMBER_ADDITIONS = (
    ("m365-business-seat-cap", "m365-business-basic-no-teams"),
    ("m365-business-seat-cap", "m365-business-standard-no-teams"),
    ("m365-business-seat-cap", "m365-business-premium-no-teams"),
)


def backfill_limit_members(db: Session) -> None:
    """Additive: add each _MEMBER_ADDITIONS pair whose limit and bundle exist but
    whose membership is missing. A no-op on a fresh DB (the seed already lists
    them) and idempotent."""
    limits = {l.key: l for l in db.execute(select(models.LicenseLimit)).scalars()}
    by_key = {b.key: b for b in bundles_service.list_bundles(db)}
    have = {(m.license_limit_id, m.bundle_id)
            for m in db.execute(select(models.LicenseLimitMember)).scalars()}
    changed = False
    for limit_key, bundle_key in _MEMBER_ADDITIONS:
        lim, b = limits.get(limit_key), by_key.get(bundle_key)
        if lim is not None and b is not None and (lim.id, b.id) not in have:
            db.add(models.LicenseLimitMember(license_limit_id=lim.id, bundle_id=b.id))
            have.add((lim.id, b.id))
            changed = True
    if changed:
        db.commit()


def _members_by_limit(db: Session) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for m in db.execute(select(models.LicenseLimitMember)).scalars().all():
        out.setdefault(m.license_limit_id, set()).add(m.bundle_id)
    return out


def member_bundle_ids(db: Session, limit_id: str) -> list[str]:
    return [
        m.bundle_id
        for m in db.execute(
            select(models.LicenseLimitMember).where(
                models.LicenseLimitMember.license_limit_id == limit_id
            )
        ).scalars().all()
    ]


def set_limit_members(db: Session, limit_id: str, bundle_ids: list[str]) -> list[str]:
    """Replace a limit's member-bundle set. Assumes caller validated the ids."""
    existing = db.execute(
        select(models.LicenseLimitMember).where(
            models.LicenseLimitMember.license_limit_id == limit_id
        )
    ).scalars().all()
    want = set(bundle_ids)
    have = {m.bundle_id: m for m in existing}
    for bid, row in have.items():
        if bid not in want:
            db.delete(row)
    for bid in want:
        if bid not in have:
            db.add(models.LicenseLimitMember(license_limit_id=limit_id, bundle_id=bid))
    db.commit()
    return member_bundle_ids(db, limit_id)


def evaluate(db: Session, engagement_id: str) -> list[dict]:
    """Every license limit against this engagement, tenant-wide: seats today on
    member licence lines and seats in the future state. Counted once, by the
    interpreter (tco_engine.interpret `seat_caps`, TARGET_SCHEMA D27) — the same
    count the recommender, the split offer and Review read. Reads only."""
    if db.get(models.Engagement, engagement_id) is None:
        return []
    caps = {c.key: c for c in raw.interpreted(db, engagement_id).seat_caps}
    bundle_name = {b.id: b.name for b in db.execute(select(models.Bundle)).scalars().all()}
    out: list[dict] = []
    for lim in db.execute(
            select(models.LicenseLimit).order_by(models.LicenseLimit.sort_order)).scalars():
        c = caps[lim.key]
        cap = c.max_quantity
        out.append({
            "id": lim.id, "key": lim.key, "name": lim.name,
            "limit_type": lim.limit_type, "max_quantity": cap,
            "unit_basis": lim.unit_basis, "scope": lim.scope,
            "member_bundle_names": sorted(
                bundle_name[i] for i in c.member_bundle_ids if i in bundle_name),
            "current_seats": c.current_seats,
            "target_seats": c.future_seats,
            "current_over_by": max(0, c.current_seats - cap),
            "target_over_by": max(0, c.future_seats - cap),
            # The actionable signal: the plan (future state) or the existing tenant
            # already exceeds the ceiling.
            "violated": c.current_seats > cap or c.future_seats > cap,
        })
    return out


def seat_cap_context(
    db: Session, engagement_id: str, exclude_persona_id: str | None = None,
    interpreted=None,
) -> list[dict]:
    """Seats left under each tenant seat cap (max_total_seats) in the FUTURE state,
    for one persona's decision (TARGET_SCHEMA §3.1, D27): the interpreter's count
    less that persona's own seats — they are what is being decided.

    Returns one dict per cap: {key, limit_id, name, cap, consumed, headroom,
    member_bundle_ids, member_bundle_names, member_references}; member_references
    are the member bundle NAMES (an optimizer candidate's sku_reference). Empty when
    no such limit is defined. Pass `interpreted` to reuse an interpretation the
    caller already holds."""
    if db.get(models.Engagement, engagement_id) is None:
        return []
    i = interpreted if interpreted is not None else raw.interpreted(db, engagement_id)
    caps = {c.key: c for c in i.seat_caps}
    bundle_name = {b.id: b.name for b in db.execute(select(models.Bundle)).scalars().all()}
    out: list[dict] = []
    for lim in db.execute(
            select(models.LicenseLimit).order_by(models.LicenseLimit.sort_order)).scalars():
        if lim.limit_type != "max_total_seats":
            continue
        c = caps[lim.key]
        consumed = c.used_by_others(exclude_persona_id)
        member_names = sorted(bundle_name[i] for i in c.member_bundle_ids if i in bundle_name)
        out.append({
            "key": lim.key,
            "limit_id": lim.id,
            "name": lim.name,
            "cap": c.max_quantity,
            "consumed": consumed,
            "headroom": max(0, c.max_quantity - consumed),
            "member_bundle_ids": sorted(c.member_bundle_ids),
            "member_bundle_names": member_names,
            "member_references": member_names,
        })
    return out
