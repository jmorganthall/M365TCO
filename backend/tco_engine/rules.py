"""The one definition of each coverage rule (ENGINE_SPEC 6.3a, 6.6).

Shared by the engine, which costs with them, and the interpreter, which reports
them (persona × outcome tables, tool retirement), so a screen can never say a tool
retires when the engine doesn't credit it, or the reverse."""

from __future__ import annotations

from decimal import Decimal


def applies_to(persona_ids, persona_id: str) -> bool:
    """6.3a: a tool (or licence line) tagged to personas applies only to them; an
    untagged one is Org-wide and applies to every persona."""
    return not persona_ids or persona_id in persona_ids


def covers_tool(delivered_outcome_ids, covered_outcome_ids) -> bool:
    """6.6 displacement test: a plan covers a tool when it covers every outcome the
    tool delivers. A tool that delivers no outcomes can't be shown to be covered."""
    return bool(delivered_outcome_ids) and set(delivered_outcome_ids) <= set(covered_outcome_ids)


def headcount_shares(persona_ids: list[str], headcount: dict[str, int]) -> dict[str, Decimal]:
    """Each persona's fraction of something that applies to `persona_ids`, by
    headcount (ENGINE_SPEC 6.2). With no headcount among them, an even split so no
    cost is lost. Fractions sum to 1 (empty when it applies to nobody)."""
    pool = [pid for pid in persona_ids if pid in headcount]
    if not pool:
        return {}
    total = sum(headcount[pid] for pid in pool)
    if total > 0:
        return {pid: Decimal(headcount[pid]) / Decimal(total) for pid in pool}
    return {pid: Decimal(1) / Decimal(len(pool)) for pid in pool}
