"""Names for the two sides of a seat-cap split (GUIDED_PLAN G22): the people kept
on the capped plan and the people over it. The split itself is decided across all
groups by the seat plan (services/seat_plan)."""

from __future__ import annotations


def capped_name(persona_name: str, cap: int) -> str:
    return f"{persona_name} (Up to {cap:,} Business User Cap)"


def over_name(persona_name: str) -> str:
    return f"{persona_name} (Over Business User Cap)"
