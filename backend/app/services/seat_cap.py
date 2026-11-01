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
from . import compute, raw


def capped_name(persona_name: str, cap: int) -> str:
    return f"{persona_name} (Up to {cap:,} Business User Cap)"


def over_name(persona_name: str) -> str:
    return f"{persona_name} (Over Business User Cap)"


def _scenario(db: Session, persona_id: str) -> models.PersonaScenario | None:
    return db.execute(
        select(models.PersonaScenario).where(models.PersonaScenario.persona_id == persona_id)
    ).scalars().first()


def split_offer(db: Session, engagement_id: str, persona_id: str, interpreted=None) -> dict | None:
    """The split offer for one persona, or None when its plan fits (or isn't capped).

    Whether a persona is over a cap — and by how much — is the interpreter's
    `seat_cap_exceeded` finding (tco_engine.interpret, the one definition Review
    reads too). This adds the offer: the best plan for the people over the cap.

    `kind` is "split" (ready to confirm), "no_seats" (nothing left to split: choose
    an uncapped plan), "nested" (a carve-out can't be split again) or
    "no_alternative" (no priced uncapped plan covers what these people need)."""
    persona = db.get(models.Persona, persona_id)
    if persona is None or persona.engagement_id != engagement_id:
        return None
    i = interpreted if interpreted is not None else raw.interpreted(db, engagement_id)
    f = next((f for f in i.findings
              if f.code == "seat_cap_exceeded" and f.persona_id == persona_id), None)
    if f is None:
        return None
    x = f.facts
    cap_name = x["cap_name"]
    headcount = x["headcount"]
    seats_left = x["seats_left"]
    other_seats = headcount - seats_left
    offer = {
        "persona_id": persona.id, "persona_name": persona.name, "headcount": headcount,
        "plan": x["plan"], "cap_name": cap_name, "cap": x["cap"],
        "used_elsewhere": x["used_elsewhere"], "seats_left": seats_left,
        "capped_seats": seats_left, "other_seats": other_seats,
        "other_plan": None, "other_addons": [], "other_price_annual": None,
        "parent_side": "capped" if seats_left >= other_seats else "other",
        "capped_name": capped_name(persona.name, x["cap"]),
        "over_name": over_name(persona.name),
    }
    if x["kind"] in ("no_seats", "nested"):
        return {**offer, "kind": x["kind"]}
    # The best plan for the people over the cap, with every capped plan removed,
    # deciding each tool the way the capped side's plan does (D29).
    rest = compute.analyze_persona_bundles(
        db, engagement_id, persona_id, headcount=other_seats, exclude_capped=True,
        follow_own_plan=True)
    best = next((b for b in rest["bundles"] if b["recommended"]), None)
    if best is None:
        return {**offer, "kind": "no_alternative"}
    offer.update(
        other_plan=best["sku_reference"],
        other_bundle_id=best.get("base_bundle_id"),
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
    i = raw.interpreted(db, engagement_id)
    out = []
    for p in persona_order.ordered(eng.personas):
        o = split_offer(db, engagement_id, p.id, interpreted=i)
        if o is not None:
            out.append(o)
    return out
