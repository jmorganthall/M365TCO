"""SQLAlchemy ORM models — a direct rendering of PRD Section 5.

All monetary values are annualized USD unless a field name says otherwise.
Period normalization to annual happens on input (services), never at read time.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# ---- Enumerations (PRD 5.x / 5.11) ----
SOURCE_TAGS = ("Invoice", "CustomerStated", "ListPrice", "Estimate", "AISuggestedUnconfirmed")
PRICE_BASIS = ("ERP", "EA", "MCA-E", "CSP", "Negotiated", "Unknown")
COST_PERIODS = ("Monthly", "Annual")
UNIT_BASIS = ("Users", "Devices", "Units")
PRODUCT_KINDS = ("MicrosoftSku", "ThirdParty")
COVERAGE = ("Full", "Partial")
DISPOSITIONS = ("FullyEliminated", "PartiallyReduced", "Unchanged")
OVERRIDES = ("None", "ForceFullElimination")
RESIDUAL_INTENTS = ("None", "IntendedOutOfScope")
TERM_DURATIONS = ("P1M", "P1Y", "P3Y")
BILLING_PLANS = ("Monthly", "Annual")


def _uuid() -> str:
    return str(uuid.uuid4())


def _source_tag_col(default="CustomerStated"):
    return mapped_column(SAEnum(*SOURCE_TAGS, name="source_tag"), default=default)


class Engagement(Base):
    __tablename__ = "engagements"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    customer_name: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    market: Mapped[str] = mapped_column(String, default="US")
    currency: Mapped[str] = mapped_column(String, default="USD")
    modeling_horizon_years: Mapped[int] = mapped_column(Integer, default=3)
    global_tooling_pct: Mapped[float] = mapped_column(Numeric(6, 4), default=0.30)
    notes: Mapped[str] = mapped_column(Text, default="")

    personas: Mapped[list["Persona"]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )
    outcomes: Mapped[list["Outcome"]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )
    current_licenses: Mapped[list["CurrentMicrosoftLicense"]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )
    third_party_products: Mapped[list["ThirdPartyProduct"]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )
    coverage_entries: Mapped[list["CoverageMapEntry"]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )
    scenarios: Mapped[list["PersonaScenario"]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )
    dispositions: Mapped[list["ProductDisposition"]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["EngagementSnapshot"]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"))
    name: Mapped[str] = mapped_column(String)
    headcount: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    source_tag: Mapped[str] = _source_tag_col()

    engagement: Mapped[Engagement] = relationship(back_populates="personas")


class Outcome(Base):
    """Capability buckets, engagement-scoped (5.3.1) so edits don't mutate the
    global library."""

    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"))
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    seed_key: Mapped[str | None] = mapped_column(String, nullable=True)

    engagement: Mapped[Engagement] = relationship(back_populates="outcomes")


class MicrosoftSku(Base):
    """The catalog. One row per priced SKU variant (PRD 5.4 / Section 8)."""

    __tablename__ = "microsoft_skus"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "sku_id",
            "term_duration",
            "billing_plan",
            "market",
            name="uq_sku_natural_key",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(String, index=True)
    sku_id: Mapped[str] = mapped_column(String)
    product_title: Mapped[str] = mapped_column(String, default="")
    sku_title: Mapped[str] = mapped_column(String, default="")
    term_duration: Mapped[str] = mapped_column(String, default="P1Y")
    billing_plan: Mapped[str] = mapped_column(String, default="Annual")
    segment: Mapped[str] = mapped_column(String, default="Commercial")
    unit_price_monthly: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    erp_price_monthly: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    annual_unit_price: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    annual_erp_price: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    effective_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    market: Mapped[str] = mapped_column(String, default="US")
    currency: Mapped[str] = mapped_column(String, default="USD")
    catalog_version: Mapped[str] = mapped_column(String, default="")


class CurrentMicrosoftLicense(Base):
    __tablename__ = "current_microsoft_licenses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"))
    sku_reference: Mapped[str] = mapped_column(String, default="")
    quantity_purchased: Mapped[int] = mapped_column(Integer, default=0)
    quantity_assigned: Mapped[int] = mapped_column(Integer, default=0)
    unit_price_paid_annual: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    price_basis: Mapped[str] = mapped_column(
        SAEnum(*PRICE_BASIS, name="price_basis"), default="Unknown"
    )
    discount_pct: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    persona_id: Mapped[str | None] = mapped_column(
        ForeignKey("personas.id"), nullable=True
    )
    source_tag: Mapped[str] = _source_tag_col()

    engagement: Mapped[Engagement] = relationship(back_populates="current_licenses")


class ThirdPartyProduct(Base):
    __tablename__ = "third_party_products"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"))
    name: Mapped[str] = mapped_column(String)
    vendor: Mapped[str] = mapped_column(String, default="")
    raw_cost: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    cost_period: Mapped[str] = mapped_column(
        SAEnum(*COST_PERIODS, name="cost_period"), default="Annual"
    )
    annual_cost: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    unit_basis: Mapped[str] = mapped_column(
        SAEnum(*UNIT_BASIS, name="unit_basis"), default="Users"
    )
    covered_count: Mapped[int] = mapped_column(Integer, default=0)
    per_unit_annual_cost: Mapped[float] = mapped_column(Numeric(14, 6), default=0)
    renewal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    commitment_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    tooling_pct: Mapped[float] = mapped_column(Numeric(6, 4), default=0.30)
    effective_annual_cost: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    source_tag: Mapped[str] = _source_tag_col()

    engagement: Mapped[Engagement] = relationship(back_populates="third_party_products")


class CoverageMapEntry(Base):
    """Product -> outcome matrix (PRD 5.7). Unratified AI suggestions never feed
    the math."""

    __tablename__ = "coverage_map_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"))
    outcome_id: Mapped[str] = mapped_column(ForeignKey("outcomes.id"))
    product_kind: Mapped[str] = mapped_column(
        SAEnum(*PRODUCT_KINDS, name="product_kind")
    )
    microsoft_sku_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    third_party_product_id: Mapped[str | None] = mapped_column(
        ForeignKey("third_party_products.id"), nullable=True
    )
    coverage: Mapped[str] = mapped_column(SAEnum(*COVERAGE, name="coverage"), default="Full")
    ai_suggested: Mapped[bool] = mapped_column(Boolean, default=False)
    ratified: Mapped[bool] = mapped_column(Boolean, default=False)

    engagement: Mapped[Engagement] = relationship(back_populates="coverage_entries")


class PersonaScenario(Base):
    __tablename__ = "persona_scenarios"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"))
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"))
    target_sku_reference: Mapped[str] = mapped_column(String, default="")
    target_unit_price_annual: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True)
    # Derived fields cached for snapshotting; recomputed by the engine on demand.
    current_spend_annual: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    target_spend_annual: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    delta_annual: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    engagement: Mapped[Engagement] = relationship(back_populates="scenarios")


class ProductDisposition(Base):
    """Persisted engine output per third-party product (PRD 5.9). Holds the
    operator's override/intent choices so reasons survive a recompute."""

    __tablename__ = "product_dispositions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"))
    third_party_product_id: Mapped[str] = mapped_column(
        ForeignKey("third_party_products.id")
    )
    displaced_users: Mapped[int] = mapped_column(Integer, default=0)
    disposition: Mapped[str] = mapped_column(
        SAEnum(*DISPOSITIONS, name="disposition"), default="Unchanged"
    )
    residual_count: Mapped[int] = mapped_column(Integer, default=0)
    residual_annual_cost: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    override: Mapped[str] = mapped_column(
        SAEnum(*OVERRIDES, name="override"), default="None"
    )
    override_reason: Mapped[str] = mapped_column(Text, default="")
    residual_intent: Mapped[str] = mapped_column(
        SAEnum(*RESIDUAL_INTENTS, name="residual_intent"), default="None"
    )

    engagement: Mapped[Engagement] = relationship(back_populates="dispositions")


class EngagementSnapshot(Base):
    """Reproducible saved readout (PRD 12). Stores the full computed result JSON
    plus the catalog version so a readout survives later catalog updates."""

    __tablename__ = "engagement_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    label: Mapped[str] = mapped_column(String, default="")
    catalog_version: Mapped[str] = mapped_column(String, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

    engagement: Mapped[Engagement] = relationship(back_populates="snapshots")


class GlobalDefaults(Base):
    """System-wide operator-editable defaults (PRD 5.10) as a first-class,
    single-row object rather than scattered constants. New engagements seed
    their own copy of these values on creation ("seed, then own")."""

    __tablename__ = "global_defaults"

    # Single well-known row.
    id: Mapped[str] = mapped_column(String, primary_key=True, default="singleton")
    default_tooling_pct: Mapped[float] = mapped_column(Numeric(6, 4), default=0.30)
    default_modeling_horizon_years: Mapped[int] = mapped_column(Integer, default=3)
    # Operator-selected OpenRouter model for AI assist. Empty = use the env
    # default (settings.openrouter_model). Operational config, runtime-editable.
    openrouter_model: Mapped[str] = mapped_column(String, default="")


class PriceSyncSettings(Base):
    """In-app, GUI-editable configuration for the price-sheet sync module — a
    first-class singleton so none of it lives in environment variables. The
    credential (client secret / certificate PEM) is NOT here; it lives in the
    encrypted secret store."""

    __tablename__ = "price_sync_settings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default="singleton")
    # Auto-discovered from the token (tid) on first sign-in; not asked upfront.
    tenant_id: Mapped[str] = mapped_column(String, default="")
    client_id: Mapped[str] = mapped_column(String, default="")
    # Blank = auto-derive from the app's request origin at sign-in time.
    redirect_uri: Mapped[str] = mapped_column(String, default="")
    pricesheet_view: Mapped[str] = mapped_column(String, default="updatedlicensebased")
    market: Mapped[str] = mapped_column(String, default="US")
    # Captured from the token claims after a successful sign-in (display only).
    signed_in_user: Mapped[str] = mapped_column(String, default="")
    timeline: Mapped[str] = mapped_column(String, default="current")
    aging_days: Mapped[int] = mapped_column(Integer, default=25)
    stale_days: Mapped[int] = mapped_column(Integer, default=30)
    use_month_rule: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_count: Mapped[int] = mapped_column(Integer, default=2)
    notify_webhook_url: Mapped[str] = mapped_column(String, default="")


class DefaultOutcome(Base):
    """Global default outcome library (PRD 5.3.1) as a first-class, editable
    table rather than a static file. It is the TEMPLATE copied into engagement-
    scoped Outcome rows on engagement creation. Editing it never touches existing
    engagements. Seeded from seeds/outcomes.json on first run."""

    __tablename__ = "default_outcomes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
