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
    # Pricing-basis defaults. `default_segment` omitted inherits GlobalDefaults;
    # term/billing default to yearly commit / yearly purchase.
    default_segment: Optional[str] = None
    default_term_duration: str = "P1Y"
    default_billing_plan: str = "Annual"
    notes: str = ""


class GlobalDefaultsOut(ORMModel):
    default_tooling_pct: Decimal
    default_modeling_horizon_years: int
    default_segment: str
    openrouter_model: str


class GlobalDefaultsUpdate(BaseModel):
    default_tooling_pct: Optional[Decimal] = None
    default_modeling_horizon_years: Optional[int] = None
    default_segment: Optional[str] = None
    openrouter_model: Optional[str] = None


class DefaultOutcomeIn(BaseModel):
    name: str
    description: str = ""
    sort_order: Optional[int] = None


class DefaultOutcomeOut(ORMModel):
    id: str
    key: str
    name: str
    description: str
    sort_order: int


class DefaultCoverageIn(BaseModel):
    bundle_key: str
    outcome_key: str
    coverage: str = "Full"


class DefaultCoverageOut(ORMModel):
    id: str
    bundle_key: str
    outcome_key: str
    coverage: str


class BundleIn(BaseModel):
    key: str
    name: str
    kind: str = "bundle"  # bundle | addon
    base_bundle_id: Optional[str] = None
    sort_order: int = 0


class BundleUpdate(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    base_bundle_id: Optional[str] = None
    sort_order: Optional[int] = None


class EngagementUpdate(BaseModel):
    customer_name: Optional[str] = None
    market: Optional[str] = None
    currency: Optional[str] = None
    modeling_horizon_years: Optional[int] = None
    global_tooling_pct: Optional[Decimal] = None
    default_segment: Optional[str] = None
    default_term_duration: Optional[str] = None
    default_billing_plan: Optional[str] = None
    notes: Optional[str] = None


class EngagementOut(ORMModel):
    id: str
    customer_name: str
    market: str
    currency: str
    modeling_horizon_years: int
    global_tooling_pct: Decimal
    default_segment: str
    default_term_duration: str
    default_billing_plan: str
    notes: str


# ---- Persona ----
class PersonaIn(BaseModel):
    name: str = ""  # optional so a partial PATCH (e.g. just requirements) validates
    headcount: int = 0
    description: str = ""
    source_tag: str = "CustomerStated"
    # Outcomes this persona requires (Personas tab). None = leave unchanged on PATCH.
    required_outcome_ids: Optional[list[str]] = None


class PersonaOut(ORMModel):
    id: str
    name: str
    headcount: int
    description: str
    source_tag: str
    required_outcome_ids: list[str] = []


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
    # Per-line pricing-basis overrides. None = inherit the engagement default.
    segment: Optional[str] = None
    term_duration: Optional[str] = None
    billing_plan: Optional[str] = None
    # Personas this line applies to (many-to-many tags).
    persona_ids: list[str] = []
    source_tag: str = "CustomerStated"


class CurrentLicenseOut(ORMModel):
    id: str
    sku_reference: str
    quantity_purchased: int
    quantity_assigned: int
    unit_price_paid_annual: Decimal
    price_basis: str
    discount_pct: Optional[Decimal]
    segment: Optional[str]
    term_duration: Optional[str]
    billing_plan: Optional[str]
    persona_ids: list[str]
    source_tag: str


# ---- Third-party product ----
class ThirdPartyIn(BaseModel):
    # Default "" so partial PATCH bodies validate (create guards non-empty).
    name: str = ""
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
    # Personas this product applies to (many-to-many tags).
    persona_ids: list[str] = []


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
    persona_ids: list[str]


# ---- Coverage map entry ----
class CoverageIn(BaseModel):
    outcome_id: str
    product_kind: str  # MicrosoftSku | ThirdParty
    bundle_id: Optional[str] = None
    microsoft_sku_reference: Optional[str] = None
    third_party_product_id: Optional[str] = None
    coverage: str = "Full"
    ai_suggested: bool = False
    ratified: bool = True


class CoverageOut(ORMModel):
    id: str
    outcome_id: str
    product_kind: str
    bundle_id: Optional[str]
    microsoft_sku_reference: Optional[str]
    third_party_product_id: Optional[str]
    coverage: str
    ai_suggested: bool
    ratified: bool


# ---- Persona scenario ----
class ScenarioAddonIn(BaseModel):
    bundle_id: str
    unit_price_annual: Decimal = Decimal("0")


class ScenarioAddonOut(ORMModel):
    id: str
    bundle_id: str
    unit_price_annual: Decimal


class ScenarioIn(BaseModel):
    persona_id: str
    target_sku_reference: str = ""
    target_unit_price_annual: Decimal = Decimal("0")
    target_discount_pct: Optional[Decimal] = None
    in_scope: bool = True
    addons: list[ScenarioAddonIn] = []


class ScenarioUpdate(BaseModel):
    target_sku_reference: Optional[str] = None
    target_unit_price_annual: Optional[Decimal] = None
    target_discount_pct: Optional[Decimal] = None
    in_scope: Optional[bool] = None
    addons: Optional[list[ScenarioAddonIn]] = None


class ScenarioOut(ORMModel):
    id: str
    persona_id: str
    target_sku_reference: str
    target_unit_price_annual: Decimal
    target_discount_pct: Optional[Decimal]
    in_scope: bool
    addons: list[ScenarioAddonOut]
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


class TextParseRequest(BaseModel):
    """Raw pasted text for any AI paste-to-parse function (third-party, licenses)."""
    raw_text: str


class AiPromptOut(ORMModel):
    key: str
    label: str
    description: str
    instructions: str
    is_default: bool = False


class AiPromptUpdate(BaseModel):
    instructions: str


class BundleAnalysisRequest(BaseModel):
    # Optional per-bundle price override map: {sku_reference: annual_per_seat}.
    prices: Optional[dict[str, float]] = None


# ---- Price-sheet sync GUI config ----
class PriceSyncConfigUpdate(BaseModel):
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    pricesheet_view: Optional[str] = None
    market: Optional[str] = None
    timeline: Optional[str] = None
    aging_days: Optional[int] = None
    stale_days: Optional[int] = None
    use_month_rule: Optional[bool] = None
    retention_count: Optional[int] = None
    notify_webhook_url: Optional[str] = None


class PriceSyncCredentialIn(BaseModel):
    kind: str  # "secret" | "certificate"
    value: str  # the client secret string, or the certificate PEM (key + cert)


class PriceSyncRefreshTokenIn(BaseModel):
    value: str  # the refresh token from the one-time partner consent
