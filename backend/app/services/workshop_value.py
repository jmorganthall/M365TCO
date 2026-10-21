"""Workshop value: every delivered workshop rolled up (TARGET_SCHEMA §7.2, D30).

An internal page for sales collateral: "the average customer saves $X and lights
up Y new outcomes". Derived on read; it stores nothing (D18) and returns no
customer name, id or logo, because the page is screenshotted. Everything rolls up
per workshop; nothing is broken down by persona.

Each delivered workshop is counted two ways:
- as completed: the snapshot frozen when it was marked delivered;
- if completed today: recomputed now, without persisting anything.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from tco_engine import compute as engine_compute

from .. import models
from . import compute
from .serialize import result_to_dict

# Below this many delivered workshops the page shows no headline: a "typical
# customer" drawn from two or three workshops is neither safe to publish nor
# anonymous.
MIN_WORKSHOPS = 5


def workshop_figures(result: dict, seed_key_by_id: dict[str, str | None]) -> dict:
    """One workshop's figures from a computed result (a snapshot payload or a fresh
    recompute). Yearly, savings-positive (the headline's sign, ENGINE_SPEC 6.11)."""
    rollup = result.get("rollup") or {}
    headline = rollup.get("headline") or {}
    savings = float(headline.get("run_rate_annual") or 0)
    current = (
        float(rollup.get("existing_microsoft_annual") or 0)
        + float(rollup.get("existing_third_party_annual") or 0)
        + float(rollup.get("residual_third_party_cost_annual") or 0)
    )
    users = int((rollup.get("population_check") or {}).get("in_scope_persona_headcount") or 0)
    personas = sum(1 for s in result.get("scenarios") or [] if s.get("in_scope"))
    # Distinct new outcomes across the workshop's personas: an outcome three
    # personas gain counts once. Keyed by library seed_key where there is one.
    outcome_ids: set[str] = set()
    library_keys: set[str] = set()
    for group in result.get("new_outcomes") or []:
        for o in group.get("outcomes") or []:
            outcome_ids.add(o["id"])
            key = o.get("seed_key") or seed_key_by_id.get(o["id"])
            if key:
                library_keys.add(key)
    return {
        "savings": savings,
        "current_spend": current,
        "duplicate_spend": float(headline.get("duplicate_spend_annual") or 0),
        "optimization": float(headline.get("consolidation_annual") or 0)
        + float(headline.get("overlicensing_annual") or 0),
        "users": users,
        "personas": personas,
        "new_outcomes": len(outcome_ids),
        "library_outcome_keys": library_keys,
    }


def _stronger(values: list[float]) -> dict | None:
    """The higher of the mean ("average") and the median ("typical"), with which
    one it is, so the wording can say so. None when there is nothing to summarise."""
    if not values:
        return None
    avg, mid = mean(values), median(values)
    if mid > avg:
        return {"value": mid, "statistic": "median"}
    return {"value": avg, "statistic": "mean"}


def summarize(workshops: list[dict], outcome_names: dict[str, str]) -> dict:
    """Roll one basis's per-workshop figures up into the page's numbers."""
    saving = [w for w in workshops if w["savings"] > 0]
    total_savings = sum(w["savings"] for w in saving)
    total_current = sum(w["current_spend"] for w in saving)
    duplicate = sum(w["duplicate_spend"] for w in saving)
    optimization = sum(w["optimization"] for w in saving)
    per_user = sorted(w["savings"] / w["users"] for w in saving if w["users"] > 0)

    lit = Counter(k for w in workshops for k in w["library_outcome_keys"])
    ranking = sorted(
        (
            {"key": k, "name": outcome_names.get(k, k), "workshops": n,
             "share": n / len(workshops)}
            for k, n in lit.items()
        ),
        key=lambda r: (-r["workshops"], r["name"].lower()),
    )
    return {
        "workshops": len(workshops),
        "saving_workshops": len(saving),
        "headline": {
            "savings_per_customer": _stronger([w["savings"] for w in saving]),
            "savings_pct": _stronger(
                [w["savings"] / w["current_spend"] for w in saving if w["current_spend"] > 0]),
            "savings_per_user": _stronger(per_user),
            "new_outcomes_per_customer": _stronger([w["new_outcomes"] for w in workshops]),
        },
        "totals": {
            "savings": total_savings,
            "current_spend": total_current,
            "optimized_spend": total_current - total_savings,
            "savings_pct": total_savings / total_current if total_current > 0 else None,
            "duplicate_spend": duplicate,
            "optimization": optimization,
            "duplicate_share": duplicate / total_savings if total_savings > 0 else None,
            "optimization_share": optimization / total_savings if total_savings > 0 else None,
            "users": sum(w["users"] for w in workshops),
            "personas": sum(w["personas"] for w in workshops),
            "new_outcomes": sum(w["new_outcomes"] for w in workshops),
        },
        "savings_per_user_spread": per_user,
        "outcome_ranking": ranking,
    }


def _today_result(db: Session, engagement_id: str) -> dict:
    """The engagement recomputed now, persisting nothing (the Review step's path)."""
    result = result_to_dict(engine_compute(compute.hydrate(db, engagement_id)))
    result["new_outcomes"] = compute.new_outcomes(db, engagement_id, result)
    return result


def workshop_value(db: Session) -> dict:
    delivered = db.execute(
        select(models.Engagement).where(models.Engagement.delivered_at.is_not(None))
    ).scalars().all()
    outcome_names = {
        d.key: d.name for d in db.execute(select(models.DefaultOutcome)).scalars()
    }

    as_completed: list[dict] = []
    today: list[dict] = []
    for eng in delivered:
        seed_key_by_id = {o.id: o.seed_key for o in eng.outcomes}
        snap = db.get(models.EngagementSnapshot, eng.delivered_snapshot_id) \
            if eng.delivered_snapshot_id else None
        if snap is not None:
            as_completed.append(workshop_figures(json.loads(snap.payload_json), seed_key_by_id))
        today.append(workshop_figures(_today_result(db, eng.id), seed_key_by_id))
    # Viewing never writes (D18): drop anything the recompute touched.
    db.rollback()

    bases = {
        "as_completed": summarize(as_completed, outcome_names),
        "today": summarize(today, outcome_names),
    }

    def lead_value(b: dict) -> float:
        h = b["headline"]["savings_per_customer"]
        return h["value"] if h else float("-inf")

    lead = "today" if lead_value(bases["today"]) > lead_value(bases["as_completed"]) \
        else "as_completed"
    currencies = Counter(e.currency for e in delivered)
    return {
        "as_of": date.today().isoformat(),
        "delivered": len(delivered),
        "min_workshops": MIN_WORKSHOPS,
        "enough": len(delivered) >= MIN_WORKSHOPS,
        "currency": currencies.most_common(1)[0][0] if currencies else None,
        "mixed_currencies": len(currencies) > 1,
        "lead": lead,
        "bases": bases,
    }
