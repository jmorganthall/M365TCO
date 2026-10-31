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
from tco_engine.models import Override, ResidualIntent

from .. import models
from . import compute, limits
from . import personas as persona_order
from .serialize import result_to_dict


def engine_result(db: Session, engagement_id: str) -> EngineResult:
    """The engine's result for the engagement's current raw data. Writes nothing.

    Two presentation steps sit on top of the engine:

    * **Order.** Every carve-out directly after its parent, and the headline's
      timed items grouped by sub-line in that persona order (the engine itself is
      order-free).
    * **Decisions that currently have no effect** (TARGET_SCHEMA §1.2.3). An
      operator's force-retire or intended-residual choice answers a residual. When
      the move alone fully retires the tool there is no residual, so the choice
      does nothing right now. The stored choice is kept as entered; the result
      shows the tool as having no decision and flags it `decision_not_applicable`.
      If coverage later shrinks and a residual returns, the stored choice applies
      again.
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

    for dr in result.dispositions:
        naturally_full = dr.displaced_users > 0 and dr.displaced_users >= dr.covered_count
        has_decision = dr.override != Override.NONE or dr.residual_intent != ResidualIntent.NONE
        dr.decision_not_applicable = bool(naturally_full and has_decision)
        if dr.decision_not_applicable:
            dr.override = Override.NONE
            dr.override_reason = ""
            dr.residual_intent = ResidualIntent.NONE
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
    return result


def narratives_view(eng: models.Engagement) -> dict:
    """The stored per-persona business narratives, as the API and readouts show them."""
    rows = sorted(eng.narratives, key=lambda n: n.persona_name)
    return {
        "narratives": [
            {"id": n.id, "persona": n.persona_name, "today": n.today,
             "whats_new": n.whats_new, "value": n.value,
             "source_tag": n.source_tag}
            for n in rows
        ],
        "generated_at": rows[0].generated_at.isoformat() if rows else None,
    }
