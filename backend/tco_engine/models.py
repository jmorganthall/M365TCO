"""Pure input dataclasses for the reconciliation engine.

These are framework-free value objects. The data/persistence layer (SQLAlchemy)
hydrates these from the database; the engine consumes only these types. Keeping
them separate from the ORM is what lets the engine port to another platform.

Money is Decimal, annualized USD. All period normalization (monthly->annual)
happens at the data layer on input, never here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class Coverage(str, Enum):
    FULL = "Full"
    PARTIAL = "Partial"


class Disposition(str, Enum):
    FULLY_ELIMINATED = "FullyEliminated"
    PARTIALLY_REDUCED = "PartiallyReduced"
    UNCHANGED = "Unchanged"


class Override(str, Enum):
    NONE = "None"
    FORCE_FULL_ELIMINATION = "ForceFullElimination"


class ResidualIntent(str, Enum):
    NONE = "None"
    INTENDED_OUT_OF_SCOPE = "IntendedOutOfScope"


@dataclass(frozen=True)
class CurrentLicenseLine:
    """An existing Microsoft license holding (already normalized).

    Model on assigned, not purchased — shelfware is a savings source. A line may
    apply to several personas (`persona_ids`); its total cost is distributed
    across the combined headcount of those personas (see engine §6.2).
    """

    quantity_assigned: int
    unit_price_paid_annual: Decimal
    sku_reference: str = ""
    persona_ids: tuple[str, ...] = ()


@dataclass
class Persona:
    id: str
    name: str
    headcount: int


@dataclass
class ThirdPartyProduct:
    """Non-Microsoft spend, with the outcomes it delivers (ratified ids only).

    `delivered_outcome_ids` is the set of outcome ids this product covers per
    the ratified coverage map. Unratified AI suggestions must be excluded by
    the caller before hydration.
    """

    id: str
    name: str
    annual_cost: Decimal
    covered_count: int
    is_managed: bool = False
    tooling_pct: Decimal = Decimal("0.30")
    renewal_date: Optional[str] = None
    delivered_outcome_ids: frozenset[str] = field(default_factory=frozenset)
    # Engine-output overrides (persisted on ProductDisposition, fed back in):
    override: Override = Override.NONE
    override_reason: str = ""
    residual_intent: ResidualIntent = ResidualIntent.NONE

    @property
    def effective_annual_cost(self) -> Decimal:
        """Section 6.5 managed split. The only cost basis used in the math."""
        if self.is_managed:
            return (self.annual_cost * self.tooling_pct).quantize(Decimal("0.01"))
        return self.annual_cost

    @property
    def per_unit_annual_cost(self) -> Decimal:
        """Section 6.3 linear-by-user unit cost on the effective basis."""
        if self.covered_count <= 0:
            return Decimal("0")
        return (self.effective_annual_cost / Decimal(self.covered_count)).quantize(
            Decimal("0.000001")
        )


@dataclass
class PersonaScenario:
    """One target-state plan per persona.

    `target_covered_outcome_ids` is the set of outcome ids the target SKU covers
    (Full or Partial, ratified) per the coverage map. The engine uses it for the
    Section 6.6 displacement test.
    """

    id: str
    persona_id: str
    target_sku_reference: str
    target_unit_price_annual: Decimal
    in_scope: bool = True
    target_covered_outcome_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass
class Engagement:
    """A fully hydrated engagement: the engine's sole input."""

    id: str
    personas: list[Persona] = field(default_factory=list)
    third_party_products: list[ThirdPartyProduct] = field(default_factory=list)
    scenarios: list[PersonaScenario] = field(default_factory=list)
    # Existing MS licenses as a flat list; each line carries the personas it
    # applies to. The engine allocates each line across its personas by headcount.
    current_licenses: list[CurrentLicenseLine] = field(default_factory=list)
