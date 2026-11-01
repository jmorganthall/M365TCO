"""The one definition of each derived per-tool money figure (ENGINE_SPEC 6.3, 6.5).

Shared by the interpreter (which reports them) and the engine's input models
(which cost with them), so the figure on screen and the figure costed can never
be computed two ways."""

from __future__ import annotations

from decimal import Decimal

ZERO = Decimal("0")


def managed_effective_cost(annual_cost: Decimal, is_managed: bool, tooling_pct: Decimal) -> Decimal:
    """6.5 managed split: a managed service's software share is its annual cost ×
    tooling %; anything else counts in full."""
    if is_managed:
        return (annual_cost * tooling_pct).quantize(Decimal("0.01"))
    return annual_cost


def cost_per_person(effective_annual_cost: Decimal, people_covered: int) -> Decimal:
    """6.3 linear-by-user unit cost on the effective basis."""
    if people_covered <= 0:
        return ZERO
    return (effective_annual_cost / Decimal(people_covered)).quantize(Decimal("0.000001"))
