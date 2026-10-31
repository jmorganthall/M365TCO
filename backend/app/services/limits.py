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


def _future_seat_usage(db: Session, eng: models.Engagement, mset: set[str], resolve) -> tuple[dict[str, float], float]:
    """THE count of a seat cap's seats in the future state — the one definition the
    Readout's licence-limit card, the recommender, the split offer and Review all
    use (TARGET_SCHEMA D27, §6.4 `seat_cap_exceeded`).

    Per persona:
    - a persona with an in-scope scenario counts its headcount when the scenario's
      base or any add-on is a member bundle (its current lines don't count: the
      move replaces them);
    - every other persona keeps its current licences, so it counts its share of the
      assigned seats on member lines (a line shared by several personas splits by
      headcount; an untagged line applies to every persona).

    Returns (seats per persona id, unattributed seats). Unattributed = member lines
    whose personas have no headcount to share them by; they count in full."""
    headcount = {p.id: p.headcount or 0 for p in eng.personas}
    moving = {s.persona_id: s for s in eng.scenarios if s.in_scope}
    usage: dict[str, float] = {}
    unattributed = 0.0
    for pid, s in moving.items():
        plan = {resolve(s.target_sku_reference)} | {a.bundle_id for a in s.addons}
        if plan & mset:
            usage[pid] = usage.get(pid, 0.0) + headcount.get(pid, 0)
    for lic in eng.licenses_in_scope:
        if resolve(lic.sku_reference) not in mset:
            continue
        applies = list(lic.persona_ids) or list(headcount)
        total_hc = sum(headcount.get(pid, 0) for pid in applies)
        if not total_hc:
            unattributed += lic.quantity_assigned or 0
            continue
        for pid in applies:
            if pid in moving or not headcount.get(pid, 0):
                continue
            usage[pid] = usage.get(pid, 0.0) + (lic.quantity_assigned or 0) * headcount[pid] / total_hc
    return usage, unattributed


def _resolver(db: Session):
    cache: dict[str, str | None] = {}

    def resolve(ref: str) -> str | None:
        if ref not in cache:
            cache[ref] = bundles_service.resolve_bundle(db, ref or "")
        return cache[ref]
    return resolve


def evaluate(db: Session, engagement_id: str) -> list[dict]:
    """Every license limit against this engagement, tenant-wide: seats today (the
    assigned seats on member licence lines) and seats in the future state — counted
    by `_future_seat_usage`, the same definition the split offer and Review use, so
    the Readout card can never disagree with them. Reads only. Empty when no limits
    are defined."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return []
    limits = db.execute(
        select(models.LicenseLimit).order_by(models.LicenseLimit.sort_order)
    ).scalars().all()
    if not limits:
        return []

    members = _members_by_limit(db)
    bundle_name = {b.id: b.name for b in db.execute(select(models.Bundle)).scalars().all()}
    resolve = _resolver(db)

    out: list[dict] = []
    for lim in limits:
        mset = members.get(lim.id, set())
        current_seats = sum(
            lic.quantity_assigned
            for lic in eng.licenses_in_scope
            if resolve(lic.sku_reference) in mset
        )
        usage, unattributed = _future_seat_usage(db, eng, mset, resolve)
        target_seats = int(round(sum(usage.values()) + unattributed))
        cap = lim.max_quantity
        out.append({
            "id": lim.id, "key": lim.key, "name": lim.name,
            "limit_type": lim.limit_type, "max_quantity": cap,
            "unit_basis": lim.unit_basis, "scope": lim.scope,
            "member_bundle_names": sorted(
                bundle_name[i] for i in mset if i in bundle_name
            ),
            "current_seats": current_seats,
            "target_seats": target_seats,
            "current_over_by": max(0, current_seats - cap),
            "target_over_by": max(0, target_seats - cap),
            # The actionable signal: the plan (future state) or the existing tenant
            # already exceeds the ceiling.
            "violated": current_seats > cap or target_seats > cap,
        })
    return out


def seat_cap_context(
    db: Session, engagement_id: str, exclude_persona_id: str | None = None
) -> list[dict]:
    """Seats left under each tenant seat cap (max_total_seats) in the FUTURE state,
    for one persona's decision (TARGET_SCHEMA §3.1, D27): the future-state count
    (`_future_seat_usage`, shared with the Readout card) less the excluded
    persona's own seats — they are what is being decided.

    Returns one dict per cap: {key, limit_id, name, cap, consumed, headroom,
    member_bundle_ids, member_bundle_names, member_references}; member_references
    are the member bundle NAMES (an optimizer candidate's sku_reference). Empty when
    no such limit is defined."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return []
    limits = [
        lim for lim in db.execute(
            select(models.LicenseLimit).order_by(models.LicenseLimit.sort_order)
        ).scalars().all()
        if lim.limit_type == "max_total_seats"
    ]
    if not limits:
        return []

    members = _members_by_limit(db)
    bundle_name = {b.id: b.name for b in db.execute(select(models.Bundle)).scalars().all()}
    resolve = _resolver(db)

    out: list[dict] = []
    for lim in limits:
        mset = members.get(lim.id, set())
        usage, unattributed = _future_seat_usage(db, eng, mset, resolve)
        consumed = int(round(
            sum(v for pid, v in usage.items() if pid != exclude_persona_id) + unattributed))
        member_names = sorted(bundle_name[i] for i in mset if i in bundle_name)
        out.append({
            "key": lim.key,
            "limit_id": lim.id,
            "name": lim.name,
            "cap": lim.max_quantity,
            "consumed": consumed,
            "headroom": max(0, lim.max_quantity - consumed),
            "member_bundle_ids": sorted(mset),
            "member_bundle_names": member_names,
            "member_references": member_names,
        })
    return out
