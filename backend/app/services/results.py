"""The one read path for calculated results (TARGET_SCHEMA §1.2, §1.5, §7).

Every consumer of engine output — the Future state and Readout screens, the HTML,
xlsx and PDF readouts, snapshots, the AI sanity check and narrative, Review and
Workshop value — reads it from here, so they all see the same numbers built the
same way.

It is **pure**: it reads raw rows, runs the engine and returns the result. It never
writes. Viewing a readout therefore never changes stored data (D18), and every read
reflects every edit made so far — there is no separate "recompute" step to forget.

Until the calculated layer is stored (`c_*` tables, TARGET_SCHEMA §7), the result is
rebuilt on each read. That is the interim shape: nothing derived is persisted, so
nothing derived can go stale.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from tco_engine.engine import EngineResult
from tco_engine.engine import compute as engine_compute

from .. import models
from . import compute, limits, raw
from . import personas as persona_order
from .serialize import result_to_dict


def engine_result(db: Session, engagement_id: str) -> EngineResult:
    """The engine's result for the engagement's current raw data. Writes nothing.

    Two presentation steps sit on top of the engine:

    * **Order.** Every carve-out directly after its parent, and the headline's
      timed items grouped by sub-line in that persona order (the engine itself is
      order-free).
    """
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise ValueError(f"Engagement {engagement_id} not found")
    result = engine_compute(compute.hydrate(db, engagement_id))

    order = persona_order.rank(eng.personas)
    result.scenarios.sort(key=lambda r: order.get(r.persona_id, len(order)))
    if result.rollup.headline is not None:
        items = result.rollup.headline.items
        group: dict[str, int] = {}
        for i in items:
            group.setdefault(i.sub_line, len(group))
        items.sort(key=lambda i: (group[i.sub_line], order.get(i.persona_id, -1)))

    return result


def current_result(db: Session, engagement_id: str) -> dict:
    """The full calculated result every screen and export reads: the engine result
    plus the licence-limit check, the per-persona new and dropped outcomes, each
    scenario's composed target label and the stored business narratives. Writes
    nothing."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise ValueError(f"Engagement {engagement_id} not found")
    result = result_to_dict(engine_result(db, engagement_id))
    result["license_limits"] = limits.evaluate(db, engagement_id)
    result["new_outcomes"] = compute.new_outcomes(db, engagement_id, result)
    result["dropped_capability"] = compute.dropped_capability(db, engagement_id, result)
    compute.attach_target_labels(db, engagement_id, result)
    result["narratives"] = narratives_view(eng)["narratives"]
    for lim in result["license_limits"]:
        lim["over_by"] = max(lim["current_over_by"], lim["target_over_by"])
    result["views"] = views(result)
    interp = raw.interpreted(db, engagement_id)
    result["views"]["personas"] = persona_view(interp, result)
    result["views"]["sections"] = sections_view(interp, result)
    return result


SECTION_ORDER = ("Microsoft", "ManagedSecurity", "Calling", "Other")


def sections_view(i, result: dict) -> dict:
    """The result in sections (GUIDED_PLAN G15): Microsoft 365, and one per kind of
    practice service, each with its own cost after, what it retires and its change
    per year, plus the combined total.

    A tool a move retires is credited to Microsoft when the Microsoft plan alone
    delivers everything it does; otherwise to the kind of the practice service that
    delivers what Microsoft doesn't. G14: the managed share of a managed tool is a
    line of its own (ENGINE_SPEC 6.5) that only a managed-security service replaces;
    `managed_share_retired` is the part of `retired` that came from such lines. The
    sections add up exactly to the engine's net change."""
    from decimal import Decimal

    from tco_engine.rules import covers_tool

    zero = Decimal("0")
    raw_sc = {s.id: s for s in i.raw.scenarios}
    isc = {s.id: s for s in i.scenarios}
    tools = {t.id: t for t in i.tools}
    sec = {k: {"key": k, "cost_after": zero, "cost_today": zero, "retired": zero,
               "managed_share_retired": zero, "services": []} for k in SECTION_ORDER}
    named: dict[str, set] = {k: set() for k in SECTION_ORDER}
    for s in result.get("scenarios") or []:
        if not s.get("in_scope") or s["scenario_id"] not in isc:
            continue
        sc, rs = isc[s["scenario_id"]], raw_sc[s["scenario_id"]]
        hc = Decimal(s["headcount"])
        services = Decimal(str(s.get("target_services_annual") or 0))
        sec["Microsoft"]["cost_after"] += Decimal(str(s["target_spend_annual"])) - services
        sec["Microsoft"]["cost_today"] += Decimal(str(s["current_microsoft_annual"]))
        for sv in rs.services:
            kind = sv.kind if sv.kind in sec else "Other"
            sec[kind]["cost_after"] += hc * sv.price_annual
            if sv.name not in named[kind]:
                named[kind].add(sv.name)
                sec[kind]["services"].append(sv.name)
        for o in s.get("offsets") or []:
            t = tools.get(o["third_party_product_id"])
            credit = Decimal(str(o["credited_offset_annual"]))
            if t is None or covers_tool(t.delivered_outcome_ids, sc.microsoft_outcome_ids):
                sec["Microsoft"]["retired"] += credit
                continue
            missing = set(t.delivered_outcome_ids) - set(sc.microsoft_outcome_ids)
            kind = next((sv.kind for sv in rs.services if sv.outcome_ids & missing), "Other")
            kind = kind if kind in sec else "Other"
            sec[kind]["retired"] += credit
            if t.managed_share_of:
                sec[kind]["managed_share_retired"] += credit
    out = []
    for k in SECTION_ORDER:
        x = sec[k]
        if k != "Microsoft" and not x["services"]:
            continue
        change = x["cost_after"] - x["cost_today"] - x["retired"]
        out.append({**{f: (float(v.quantize(Decimal("0.01"))) if isinstance(v, Decimal) else v)
                       for f, v in x.items()},
                    "retired_total": float(x["retired"].quantize(Decimal("0.01"))),
                    "change_annual": float(change.quantize(Decimal("0.01")))})
    return {"sections": out,
            "combined_change_annual": round(sum(x["change_annual"] for x in out), 2),
            "managed_share_retired_annual": round(sum(x["managed_share_retired"] for x in out), 2)}


def persona_view(i, result: dict) -> list[dict]:
    """Each persona today and after its move, per user per month (GUIDED_PLAN G3).
    Today is the interpreter's baseline. After = today + the scenario's delta: a
    kept tool sits on both sides and cancels (ENGINE_SPEC 6.0a), so the engine's
    delta is exactly the change in the persona's all-in cost. No plan = no change."""
    from decimal import Decimal

    by_persona = {s["persona_id"]: s for s in result.get("scenarios") or [] if s.get("in_scope")}
    out = []
    for b in i.baselines:
        s = by_persona.get(b.persona_id)
        delta = Decimal(str(s["delta_annual"])) if s else Decimal("0")
        after = b.total_annual + delta
        hc = Decimal(b.headcount) if b.headcount > 0 else None
        per = (lambda v: float((v / hc / 12).quantize(Decimal("0.01"))) if hc else 0.0)
        out.append({
            "persona_id": b.persona_id, "headcount": b.headcount,
            "has_plan": s is not None, "plan": (s or {}).get("target_label") or (s or {}).get("target_sku_reference"),
            "today_annual": float(b.total_annual), "after_annual": float(after), "delta_annual": float(delta),
            "today_per_user_monthly": float(b.per_user_monthly),
            "after_per_user_monthly": per(after), "delta_per_user_monthly": per(delta),
        })
    return out


# The bridge's third-party groups: which products each draws on, and which of their
# amounts (ENGINE_SPEC 6.8, 6.8a). The offset field is the per-scenario counterpart.
_BRIDGE_GROUPS = (
    ("already_today", True, "redundant_today_annual", "redundant_today_annual"),
    ("already_rest", True, "move_unlocked_annual", "move_unlocked_annual"),
    ("replaced", False, "credited_annual", "credited_offset_annual"),
)


def views(result: dict) -> dict:
    """Totals and groupings every readout shows — the Summary screen, the HTML and
    PDF readout — computed once here so neither renderer adds anything up itself
    (TARGET_SCHEMA §1.5). Pure over the serialized result.

    - `bridge`: the third-party groups of the spend bridge, each with its products,
      every amount per in-scope scenario, and the totals. A product the move
      replaces but whose credit is $0 is kept, flagged `zero_credit` (its covered
      population isn't set); a renderer may show or drop it.
    - `new_outcome_count`: distinct outcomes any in-scope persona gains.
    - `consolidation_by_persona`: each persona's consolidation, yearly and as timed
      over the horizon (the headline's items, ENGINE_SPEC 6.11).
    - `quick_win_who`: per quick-win tool, who already holds its capability — one
      entry per persona, with its seats and the licences covering them, most seats
      first (ENGINE_SPEC 6.10 `covered_by`)."""
    in_scope = [s for s in result.get("scenarios") or [] if s.get("in_scope")]
    rollup = result.get("rollup") or {}

    def offset(s, pid, field):
        return next((float(o.get(field) or 0) for o in s.get("offsets") or []
                     if o["third_party_product_id"] == pid), 0.0)

    bridge = []
    for key, already, field, off_field in _BRIDGE_GROUPS:
        rows = []
        for f in rollup.get("freed_third_party") or []:
            if bool(f.get("already_covered")) != already:
                continue
            total = float(f.get(field) or 0)
            zero_credit = field == "credited_annual" and total == 0
            if not total and not zero_credit:
                continue
            rows.append({
                "third_party_product_id": f["third_party_product_id"],
                "third_party_product_name": f["third_party_product_name"],
                "total": total, "zero_credit": zero_credit,
                "by_scenario": {s.get("scenario_id"): offset(s, f["third_party_product_id"], off_field)
                                for s in in_scope},
            })
        bridge.append({
            "key": key, "field": field, "items": rows,
            "total": sum(r["total"] for r in rows),
            "by_scenario": {s.get("scenario_id"): sum(r["by_scenario"][s.get("scenario_id")] for r in rows)
                            for s in in_scope},
        })

    new_ids = {o["id"] for n in result.get("new_outcomes") or [] for o in n.get("outcomes") or []}

    consolidation: dict[str, dict] = {}
    for i in (rollup.get("headline") or {}).get("items") or []:
        if i.get("sub_line") == "consolidation" and i.get("persona_id"):
            c = consolidation.setdefault(i["persona_id"], {"annual": 0.0, "timed": 0.0})
            c["annual"] += float(i.get("annual_amount") or 0)
            c["timed"] += float(i.get("amount") or 0)

    who: dict[str, list[dict]] = {}
    for q in rollup.get("quick_wins") or []:
        by: dict[str, list[dict]] = {}
        for c in q.get("covered_by") or []:
            by.setdefault(c["persona_name"], []).append(c)
        who[q["third_party_product_id"]] = sorted(
            ({"persona_name": name, "seats": sum(c["seats"] for c in cs),
              "licences": [{"sku_reference": c.get("sku_reference") or "", "seats": c["seats"]} for c in cs]}
             for name, cs in by.items()),
            key=lambda w: -w["seats"])

    return {"bridge": bridge, "new_outcome_count": len(new_ids),
            "consolidation_by_persona": consolidation, "quick_win_who": who,
            # The tools a disposition table lists: not a $0 line (a contract part
            # with no amount yet says nothing; the contract's remainder carries it).
            "dispositions_shown": [d["third_party_product_id"] for d in result.get("dispositions") or []
                                   if float(d.get("effective_annual_cost") or 0) > 0]}


def narratives_view(eng: models.Engagement) -> dict:
    """The stored per-persona business narratives, as the API and readouts show them.

    Each is joined to its persona by id, so a renamed persona keeps its narrative
    and shows its current name. The name stored with the narrative is shown only
    when the persona is gone (or the row predates the link)."""
    current = {p.id: p.name for p in eng.personas}
    order = persona_order.rank(eng.personas)

    def name(n):
        return current.get(n.persona_id) or n.persona_name

    rows = sorted(eng.narratives, key=lambda n: (order.get(n.persona_id, len(order)), name(n)))
    return {
        "narratives": [
            {"id": n.id, "persona_id": n.persona_id if n.persona_id in current else None,
             "persona": name(n), "today": n.today,
             "whats_new": n.whats_new, "value": n.value,
             "source_tag": n.source_tag}
            for n in rows
        ],
        "generated_at": rows[0].generated_at.isoformat() if rows else None,
    }
