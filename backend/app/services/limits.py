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
from . import swap as swap_service

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
    """Evaluate every license limit against this engagement's totality — current
    state (current licenses) and future state (in-scope scenarios), summed
    tenant-wide across all personas. Seats are counted once per line/scenario that
    touches a member bundle (base OR add-on), so a scenario is never double-counted.
    Returns a list of evaluation dicts (empty when no limits are defined)."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return []
    seed_license_limits(db)  # populate-if-empty, so the cap is evaluated on first compute
    limits = db.execute(
        select(models.LicenseLimit).order_by(models.LicenseLimit.sort_order)
    ).scalars().all()
    if not limits:
        return []

    members = _members_by_limit(db)
    bundle_name = {b.id: b.name for b in db.execute(select(models.Bundle)).scalars().all()}
    headcount = {p.id: p.headcount for p in eng.personas}

    # A scenario's effective target counts against the cap — including one that the
    # Business Premium swap redirects onto BP (so the swap's own seats are counted).
    swap_ctx = swap_service.compute_context(db, eng)
    bp_id = swap_ctx["bp"].id if swap_ctx["bp"] is not None else None

    # Resolve each current license's bundle once (cache by reference string).
    ref_cache: dict[str, str | None] = {}

    def _resolve(ref: str) -> str | None:
        if ref not in ref_cache:
            ref_cache[ref] = bundles_service.resolve_bundle(db, ref or "")
        return ref_cache[ref]

    def _scenario_bundle_ids(s: models.PersonaScenario) -> set[str]:
        """The bundle ids a scenario's future state touches — its Business Premium
        override when the swap applies, else its base + add-ons."""
        if bp_id is not None and swap_service.applies(eng, swap_ctx, s):
            return {bp_id}
        return {_resolve(s.target_sku_reference)} | {a.bundle_id for a in s.addons}

    out: list[dict] = []
    for lim in limits:
        mset = members.get(lim.id, set())

        # Current state: seats on current-license lines whose bundle is a member.
        # quantity_assigned is the modeled seat count the rest of the readout uses.
        current_seats = sum(
            lic.quantity_assigned
            for lic in eng.current_licenses
            if _resolve(lic.sku_reference) in mset
        )

        # Future state: in-scope scenarios whose base OR any add-on is a member —
        # the persona headcount counts once per qualifying scenario.
        target_seats = 0
        for s in eng.scenarios:
            if not s.in_scope:
                continue
            if _scenario_bundle_ids(s) & mset:
                target_seats += headcount.get(s.persona_id, 0)

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
    """The remaining headroom under each tenant seat cap (max_total_seats), for the
    best-bundle optimizer. Mirrors `evaluate`'s counting — current-license seats plus
    the headcount of in-scope scenarios touching a member bundle — but EXCLUDES the
    persona currently being analyzed, so the optimizer sees how many Business seats are
    already recommended elsewhere and how many are left for this persona.

    Returns one dict per cap: {name, cap, consumed, headroom, member_bundle_names,
    member_references} where member_references are the member bundle NAMES (what an
    optimizer candidate's sku_reference is). Empty when no such limit is defined."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return []
    seed_license_limits(db)
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
    headcount = {p.id: p.headcount for p in eng.personas}

    swap_ctx = swap_service.compute_context(db, eng)
    bp_id = swap_ctx["bp"].id if swap_ctx["bp"] is not None else None
    ref_cache: dict[str, str | None] = {}

    def _resolve(ref: str) -> str | None:
        if ref not in ref_cache:
            ref_cache[ref] = bundles_service.resolve_bundle(db, ref or "")
        return ref_cache[ref]

    def _scenario_bundle_ids(s: models.PersonaScenario) -> set[str]:
        if bp_id is not None and swap_service.applies(eng, swap_ctx, s):
            return {bp_id}
        return {_resolve(s.target_sku_reference)} | {a.bundle_id for a in s.addons}

    out: list[dict] = []
    for lim in limits:
        mset = members.get(lim.id, set())
        consumed = sum(
            lic.quantity_assigned
            for lic in eng.current_licenses
            if _resolve(lic.sku_reference) in mset
        )
        for s in eng.scenarios:
            if not s.in_scope or s.persona_id == exclude_persona_id:
                continue
            if _scenario_bundle_ids(s) & mset:
                consumed += headcount.get(s.persona_id, 0)
        member_names = sorted(bundle_name[i] for i in mset if i in bundle_name)
        out.append({
            "name": lim.name,
            "cap": lim.max_quantity,
            "consumed": consumed,
            "headroom": max(0, lim.max_quantity - consumed),
            "member_bundle_names": member_names,
            "member_references": member_names,
        })
    return out
