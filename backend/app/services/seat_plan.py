"""The Business seat plan (GUIDED_PLAN G22, ENGINE_SPEC 6.13a).

A seat-capped plan family (Microsoft 365 Business, 300 seats per tenant) is shared
by every group in the engagement. Instead of offering each group its own split,
the seats are handed out across all groups where each seat saves the most:

1. Each group (a parent and its carve-outs are one population) is priced both
   ways by the recommender (services/compute): the best plan inside the cap, and
   the best plan with every capped plan removed. Each cost includes the tools that
   plan retires, as the engine credits them.
2. Saving per seat = (cost on the uncapped plan − cost on the capped plan) ÷ people.
3. Seats go to the group with the largest saving per seat first, then the next,
   until none are left. A group where the capped plan saves nothing gets none.

Every cost the engine models is per person (licence price × people; a tool's credit
× the people who move, up to the seats it covers), so each seat's saving is fixed
and this order is the cheapest allocation. The plan is derived on read; nothing
changes until a person applies it (routers/entities.apply_seat_plan).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import compute, limits, raw
from . import personas as persona_order


def _scenario(db: Session, persona_id: str) -> models.PersonaScenario | None:
    return db.execute(
        select(models.PersonaScenario).where(models.PersonaScenario.persona_id == persona_id)
    ).scalars().first()


def _option(row: dict) -> dict:
    """A recommender row as a plan the seat plan can apply."""
    return {
        "plan": row["sku_reference"], "bundle_id": row.get("base_bundle_id"),
        "base_price_annual": row["base_price_annual"],
        "addons": [{"bundle_id": a["bundle_id"], "name": a.get("name", ""),
                    "unit_price_annual": a["unit_price_annual"]} for a in row.get("addons") or []],
        "label": " + ".join([row["sku_reference"]] + [a.get("name", "") for a in row.get("addons") or []]),
        "price_annual": row["target_unit_price_annual"],
    }


def _best(rows: list[dict], refs: set[str], inside: bool) -> dict | None:
    """The cheapest gap-free, priced plan inside (or outside) the capped family,
    ignoring the cap itself: that is what the plan decides."""
    ok = [r for r in rows if (r["sku_reference"] in refs) == inside
          and r["covers_all_required"] and r["price_known"]]
    return min(ok, key=lambda r: (r["delta_annual"], r["target_spend_annual"]), default=None)


def plan(db: Session, engagement_id: str) -> list[dict]:
    """One plan per seat cap (usually one: the Business family). Each lists, per
    group, the seats on the capped plan and on the other, and what that saves."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return []
    interp = raw.interpreted(db, engagement_id)
    caps = limits.seat_cap_context(db, engagement_id, interpreted=interp)
    if not caps:
        return []
    by_id = {p.id: p for p in eng.personas}
    in_scope = {s.persona_id: s for s in eng.scenarios if s.in_scope}
    families: dict[str, list[models.Persona]] = {}
    for p in persona_order.ordered(eng.personas):
        root = by_id.get(p.parent_persona_id) if p.parent_persona_id else p
        root = root or p
        families.setdefault(root.id, []).append(p)
    seat_caps = {c.key: c for c in interp.seat_caps}
    out = []
    taken: set[str] = set()
    for cap in caps:
        refs = set(cap["member_references"])
        ic = seat_caps[cap["key"]]
        units = []
        for root_id, members in families.items():
            if root_id in taken:
                continue
            people = sum(m.headcount or 0 for m in members)
            # Only groups whose future state is being decided (an in-scope plan).
            if people <= 0 or not any(m.id in in_scope for m in members):
                continue
            rows = compute.analyze_persona_bundles(db, engagement_id, root_id, headcount=people)["bundles"]
            inside, outside = _best(rows, refs, True), _best(rows, refs, False)
            if inside is None:
                continue
            saving = (Decimal(str(outside["delta_annual"])) - Decimal(str(inside["delta_annual"]))) / people \
                if outside is not None else None
            now = sum(m.headcount or 0 for m in members
                      if m.id in in_scope and _on_cap(in_scope[m.id], refs, cap["member_bundle_ids"]))
            units.append({"root": by_id[root_id], "members": members, "people": people,
                          "inside": inside, "outside": outside, "saving": saving, "now": now})
        if not units:
            continue
        unit_ids = {m.id for u in units for m in u["members"]}
        # Seats the rest of the engagement keeps using (groups not being decided,
        # licence lines nobody's plan accounts for).
        fixed = int(round(sum(v for pid, v in ic.usage.items() if pid not in unit_ids) + ic.unattributed))
        left = max(0, cap["cap"] - fixed)
        # Largest saving per seat first; a group with no uncapped option takes the
        # capped plan whatever it costs (there is nowhere else to put them).
        order = sorted(units, key=lambda u: (u["saving"] is not None, -(u["saving"] or 0)))
        for u in order:
            want = u["people"] if u["saving"] is None or u["saving"] > 0 else 0
            u["capped"] = min(want, left)
            left -= u["capped"]
            # A split group decides each tool together (D29): its people off the cap
            # get the uncapped plan that retires what the capped plan retires.
            if 0 < u["capped"] < u["people"]:
                ins = u["inside"]
                cover = set(interp.raw.microsoft_coverage.get(ins.get("base_bundle_id") or ins["sku_reference"], ()))
                for a in ins.get("addons") or []:
                    cover |= interp.raw.microsoft_coverage.get(a["bundle_id"], frozenset())
                rest = compute.analyze_persona_bundles(
                    db, engagement_id, u["root"].id, headcount=u["people"] - u["capped"],
                    exclude_capped=True, follow_own_plan=True, follow_cover=frozenset(cover))["bundles"]
                follow = next((r for r in rest if r["recommended"]), None)
                if follow is not None:
                    u["outside"] = follow
        rows_out = []
        for u in units:
            other = u["people"] - u["capped"]
            if other and u["outside"] is None:
                status = "no_alternative"
            else:
                status = "ok"
            rows_out.append({
                "persona_id": u["root"].id, "persona_name": u["root"].name,
                "member_ids": [m.id for m in u["members"]], "people": u["people"],
                "capped_seats": u["capped"], "other_seats": other, "capped_now": u["now"],
                "capped_plan": _option(u["inside"]),
                "other_plan": _option(u["outside"]) if u["outside"] else None,
                "saving_per_seat_annual": float(round(u["saving"], 2)) if u["saving"] is not None else None,
                "saving_annual": float(round((u["saving"] or 0) * u["capped"], 2)),
                "changes": u["capped"] != u["now"],
                "status": status,
            })
        taken |= {u["root"].id for u in units}
        used = sum(r["capped_seats"] for r in rows_out)
        out.append({
            "cap_key": cap["key"], "cap_name": cap["name"], "cap": cap["cap"],
            "member_references": cap["member_references"], "member_bundle_ids": cap["member_bundle_ids"],
            "used_elsewhere": fixed, "seats_used": used + fixed,
            "saving_annual": round(sum(r["saving_annual"] for r in rows_out), 2),
            "changes": any(r["changes"] for r in rows_out),
            "groups": rows_out,
        })
    return out


def _on_cap(s: models.PersonaScenario, refs: set[str], member_ids: list[str]) -> bool:
    """Whether a plan is in the capped family: its library plan, its name, or an add-on."""
    if s.bundle_id in member_ids or (s.target_sku_reference or "") in refs:
        return True
    return any(a.bundle_id in member_ids for a in s.addons)
