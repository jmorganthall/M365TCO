"""Pydantic request/response schemas. Loose by design — the engagement editor
is a workshop tool where most fields are optional and patched incrementally."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- Engagement ----
class EngagementCreate(BaseModel):
    customer_name: str = ""
    market: str = "US"
    currency: str = "USD"
    # When omitted, these inherit from GlobalDefaults at creation time.
    modeling_horizon_years: Optional[int] = None
    global_tooling_pct: Optional[Decimal] = None
    notes: str = ""


class GlobalDefaultsOut(ORMModel):
    default_tooling_pct: Decimal
    default_modeling_horizon_years: int
    openrouter_model: str


class GlobalDefaultsUpdate(BaseModel):
    default_tooling_pct: Optional[Decimal] = None
    default_modeling_horizon_years: Optional[int] = None
    openrouter_model: Optional[str] = None


class EngagementUpdate(BaseModel):
    customer_name: Optional[str] = None
    market: Optional[str] = None
    currency: Optional[str] = None
    modeling_horizon_years: Optional[int] = None
    global_tooling_pct: Optional[Decimal] = None
    notes: Optional[str] = None


class EngagementOut(ORMModel):
    id: str
    customer_name: str
    market: str
    currency: str
    modeling_horizon_years: int
    global_tooling_pct: Decimal
    notes: str


# ---- Persona ----
class PersonaIn(BaseModel):
    name: str
    headcount: int = 0
    description: str = ""
    source_tag: str = "CustomerStated"


class PersonaOut(ORMModel):
    id: str
    name: str
    headcount: int
    description: str
    source_tag: str


# ---- Outcome ----
class OutcomeIn(BaseModel):
    name: str
    description: str = ""
    is_custom: bool = True


class OutcomeOut(ORMModel):
    id: str
    name: str
    description: str
    is_custom: bool


# ---- Current Microsoft license ----
class CurrentLicenseIn(BaseModel):
    sku_reference: str = ""
    quantity_purchased: int = 0
    quantity_assigned: int = 0
    unit_price_paid_annual: Decimal = Decimal("0")
    price_basis: str = "Unknown"
    discount_pct: Optional[Decimal] = None
    persona_id: Optional[str] = None
    source_tag: str = "CustomerStated"


class CurrentLicenseOut(ORMModel):
    id: str
    sku_reference: str
    quantity_purchased: int
    quantity_assigned: int
    unit_price_paid_annual: Decimal
    price_basis: str
    discount_pct: Optional[Decimal]
    persona_id: Optional[str]
    source_tag: str


# ---- Third-party product ----
class ThirdPartyIn(BaseModel):
    name: str
    vendor: str = ""
    raw_cost: Decimal = Decimal("0")
    cost_period: str = "Annual"
    unit_basis: str = "Users"
    covered_count: int = 0
    renewal_date: Optional[date] = None
    commitment_term_months: Optional[int] = None
    is_managed: bool = False
    tooling_pct: Optional[Decimal] = None
    source_tag: str = "CustomerStated"


class ThirdPartyOut(ORMModel):
    id: str
    name: str
    vendor: str
    raw_cost: Decimal
    cost_period: str
    annual_cost: Decimal
    unit_basis: str
    covered_count: int
    per_unit_annual_cost: Decimal
    renewal_date: Optional[date]
    commitment_term_months: Optional[int]
    is_managed: bool
    tooling_pct: Decimal
    effective_annual_cost: Decimal
    source_tag: str


# ---- Coverage map entry ----
class CoverageIn(BaseModel):
    outcome_id: str
    product_kind: str  # MicrosoftSku | ThirdParty
    microsoft_sku_reference: Optional[str] = None
    third_party_product_id: Optional[str] = None
    coverage: str = "Full"
    ai_suggested: bool = False
    ratified: bool = True


class CoverageOut(ORMModel):
    id: str
    outcome_id: str
    product_kind: str
    microsoft_sku_reference: Optional[str]
    third_party_product_id: Optional[str]
    coverage: str
    ai_suggested: bool
    ratified: bool


# ---- Persona scenario ----
class ScenarioIn(BaseModel):
    persona_id: str
    target_sku_reference: str = ""
    target_unit_price_annual: Decimal = Decimal("0")
    in_scope: bool = True


class ScenarioUpdate(BaseModel):
    target_sku_reference: Optional[str] = None
    target_unit_price_annual: Optional[Decimal] = None
    in_scope: Optional[bool] = None


class ScenarioOut(ORMModel):
    id: str
    persona_id: str
    target_sku_reference: str
    target_unit_price_annual: Decimal
    in_scope: bool
    current_spend_annual: Decimal
    target_spend_annual: Decimal
    delta_annual: Decimal


# ---- Disposition override ----
class DispositionOverrideIn(BaseModel):
    override: str = "None"  # None | ForceFullElimination
    override_reason: str = ""
    residual_intent: str = "None"  # None | IntendedOutOfScope


# ---- Secrets ----
class SecretIn(BaseModel):
    key: str
    value: str


# ---- AI ----
class CoverageSuggestRequest(BaseModel):
    third_party_product_id: str
