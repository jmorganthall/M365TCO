"""The Business seat cap split (TARGET_SCHEMA D27, ENGINE_SPEC 6.13).

The recommender always proposes the best plan. When a persona's plan is in a
seat-capped family (Microsoft 365 Business, 300 seats per tenant) and the persona
is larger than the seats left in the future state, it is offered a split:

- the capped side: exactly the seats left, on the plan it has;
- the other side: the rest, on the best plan the recommender finds for them with
  every capped plan removed (never a hard-coded answer);
- the larger side is the parent, the smaller the carve-out.

The offer is derived on read; nothing changes until a person confirms it
(routers/entities.split_for_seat_cap). Until then Review raises
`seat_cap_exceeded`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import bundles, compute, limits


def capped_name(persona_name: str, cap: int) -> str:
    return f"{persona_name} (Up to {cap:,} Business User Cap)"


def over_name(persona_name: str) -> str:
    return f"{persona_name} (Over Business User Cap)"


def _scenario(db: Session, persona_id: str) -> models.PersonaScenario | None:
    return db.execute(
        select(models.PersonaScenario).where(models.PersonaScenario.persona_id == persona_id)
    ).scalars().first()


def split_offer(db: Session, engagement_id: str, persona_id: str) -> dict | None:
    """The split offer for one persona, or None when its plan fits (or isn't capped).

    `kind` is "split" (ready to confirm), "no_seats" (nothing left to split: choose
    an uncapped plan), "nested" (a carve-out can't be split again) or
    "no_alternative" (no priced uncapped plan covers what these people need)."""
    persona = db.get(models.Persona, persona_id)
    if persona is None or persona.engagement_id != engagement_id:
        return None
    s = _scenario(db, persona_id)
    if s is None or not s.in_scope or not s.target_sku_reference:
        return None
    plan_ids = {bundles.resolve_bundle(db, s.target_sku_reference)} | {a.bundle_id for a in s.addons}
    members = limits._members_by_limit(db)
    caps = {c["name"]: c for c in limits.seat_cap_context(db, engagement_id, exclude_persona_id=persona_id)}
    limit_rows = {lim.name: lim for lim in db.execute(select(models.LicenseLimit)).scalars()}
    # The tightest cap the plan falls under decides.
    hit = None
    for name, ctx in caps.items():
        lim = limit_rows.get(name)
        if lim is not None and members.get(lim.id, set()) & plan_ids:
            if hit is None or ctx["headroom"] < hit["headroom"]:
                hit = ctx
    headcount = persona.headcount or 0
    if hit is None or headcount <= hit["headroom"]:
        return None

    seats_left = hit["headroom"]
    other_seats = headcount - seats_left
    offer = {
        "persona_id": persona.id, "persona_name": persona.name, "headcount": headcount,
        "plan": s.target_sku_reference, "cap_name": hit["name"], "cap": hit["cap"],
        "used_elsewhere": hit["consumed"], "seats_left": seats_left,
        "capped_seats": seats_left, "other_seats": other_seats,
        "other_plan": None, "other_addons": [], "other_price_annual": None,
        "parent_side": "capped" if seats_left >= other_seats else "other",
        "capped_name": capped_name(persona.name, hit["cap"]),
        "over_name": over_name(persona.name),
    }
    if seats_left <= 0:
        return {**offer, "kind": "no_seats"}
    if persona.parent_persona_id is not None:
        return {**offer, "kind": "nested"}
    # The best plan for the people over the cap, with every capped plan removed.
    rest = compute.analyze_persona_bundles(
        db, engagement_id, persona_id, headcount=other_seats, exclude_capped=True)
    best = next((b for b in rest["bundles"] if b["recommended"]), None)
    if best is None:
        return {**offer, "kind": "no_alternative"}
    offer.update(
        other_plan=best["sku_reference"],
        other_addons=[{"bundle_id": a["bundle_id"], "name": a.get("name", ""),
                       "unit_price_annual": a["unit_price_annual"]} for a in best["addons"]],
        other_price_annual=best["base_price_annual"],
    )
    # The carve-out is the smaller side; the parent keeps its own name (code never
    # renames what a person typed).
    offer["carve_out_name"] = offer["over_name"] if offer["parent_side"] == "capped" else offer["capped_name"]
    return {**offer, "kind": "split"}


def split_offers(db: Session, engagement_id: str) -> list[dict]:
    """Every persona's offer, in persona order (carve-outs under their parents)."""
    from . import personas as persona_order

    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return []
    out = []
    for p in persona_order.ordered(eng.personas):
        o = split_offer(db, engagement_id, p.id)
        if o is not None:
            out.append(o)
    return out
