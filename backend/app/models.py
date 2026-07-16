"""SQLAlchemy ORM models — a direct rendering of PRD Section 5.

All monetary values are annualized USD unless a field name says otherwise.
Period normalization to annual happens on input (services), never at read time.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

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
# Coverage is binary: a CoverageMapEntry existing means the outcome IS covered.
# ("Full" is the single stored marker — there is no partial coverage.)
COVERAGE = ("Full",)
DISPOSITIONS = ("FullyEliminated", "PartiallyReduced", "Unchanged")
OVERRIDES = ("None", "ForceFullElimination")
RESIDUAL_INTENTS = ("None", "IntendedOutOfScope")
TERM_DURATIONS = ("P1M", "P1Y", "P3Y")
BILLING_PLANS = ("Monthly", "Annual", "Triennial")
# Customer segments as they appear in the Microsoft price sheet's `Segment`
# column. This is the KNOWN default set (used to seed the global-default dropdown
# when the catalog is empty); the live segment pickers are data-driven from the
# distinct segments actually present in the catalog, so an unforeseen sheet value
# is never silently dropped. `segment` columns therefore stay plain strings, not
# a DB enum. "Commercial" is the ground-floor default of the inheritance chain
# (GlobalDefaults -> Engagement -> line item).
SEGMENTS = ("Commercial", "Education", "Government", "Nonprofit", "Charity")
# How a pricing-catalog load reached us. The two load paths (manual CSV upload,
# Partner Center price-sync) write a row on success; "Reconciled" is a row
# synthesized from an already-loaded catalog that predates provenance recording,
# so freshness can never disagree with a catalog that is demonstrably present.
CATALOG_IMPORT_SOURCES = ("CsvUpload", "PriceSyncApi", "Reconciled")


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
    # Customer-info metadata (Customer Info tab). User-entered context about the
    # customer — used for display and, later, as grounding for the AI business
    # narrative research. `customer_name` above is the engagement's display name.
    workshop_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    industry: Mapped[str] = mapped_column(String, default="")
    hq_location: Mapped[str] = mapped_column(String, default="")
    website: Mapped[str] = mapped_column(String, default="")
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Pricing basis defaults for this engagement (seeded from GlobalDefaults on
    # creation — "seed, then own"). Each is the middle tier of an inheritance
    # chain: a customer sets these once (e.g. a Nonprofit customer overrides the
    # global Commercial default), and an individual license line can still
    # override them. They select which priced catalog variant a picked SKU
    # resolves to, so the seeded list price is the right one.
    default_segment: Mapped[str] = mapped_column(String, default="Commercial")
    default_term_duration: Mapped[str] = mapped_column(String, default="P1Y")
    default_billing_plan: Mapped[str] = mapped_column(String, default="Annual")
    # Readout branding (user-entered runtime data — never a hard-coded identity).
    # A customer/practice logo as a base64 data URL, plus two theme colors applied
    # to the HTML readout. Blank = the neutral built-in theme. (A future reusable
    # BrandTheme library would seed these; today they're per-engagement fields.)
    brand_logo_data_url: Mapped[str] = mapped_column(Text, default="")
    brand_primary_color: Mapped[str] = mapped_column(String, default="")
    brand_accent_color: Mapped[str] = mapped_column(String, default="")
    # Engagement-level "swap eligible users to Microsoft 365 Business Premium to
    # save" toggle. When on, every capability-eligible scenario INHERITS the swap
    # unless that persona opts out (PersonaScenario.bp_swap_optout). The 300-seat
    # cap (LicenseLimit) bounds it. User-entered.
    bp_swap_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Engagement-level "respect the Microsoft 365 Business seat cap in best-bundle
    # recommendations" toggle. When on, the optimizer is given the remaining headroom
    # under each max_total_seats LicenseLimit (300 for Business) net of seats already
    # recommended, and will not recommend a Business plan for a persona that would push
    # the tenant over the cap — it falls through to the next-best plan. User-entered.
    business_cap_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

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
    requirement_links: Mapped[list["PersonaRequirement"]] = relationship(
        back_populates="persona", cascade="all, delete-orphan"
    )

    @property
    def required_outcome_ids(self) -> list[str]:
        """Outcomes this persona is declared to REQUIRE (Personas tab). Feeds
        recommend-a-path gap detection: a target bundle that misses one is a gap."""
        return [link.outcome_id for link in self.requirement_links]


class PersonaRequirement(Base):
    """Persona ↔ Outcome requirement (association object): a capability the persona
    needs, independent of what its current licenses happen to deliver. Used to tell
    Frontline from mainline personas (e.g. requires Desktop Software / Full-Size
    Cloud Storage) so recommend-a-path won't drop a needed capability."""

    __tablename__ = "persona_requirements"
    __table_args__ = (
        UniqueConstraint("persona_id", "outcome_id", name="uq_persona_requirement"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)
    outcome_id: Mapped[str] = mapped_column(ForeignKey("outcomes.id"))

    persona: Mapped[Persona] = relationship(back_populates="requirement_links")


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
    # SKU → Bundle (many priced variants collapse to one staple bundle). The
    # accepted/ratified mapping, editable. Null until classified.
    bundle_id: Mapped[str | None] = mapped_column(
        ForeignKey("bundles.id"), nullable=True, index=True
    )
    # The import-time AI mapper's proposal, UNRATIFIED until the operator accepts
    # it into bundle_id (mirrors CoverageMapEntry's suggested/ratified split so a
    # mapping never silently enters the spine). Cleared on accept or reject.
    suggested_bundle_id: Mapped[str | None] = mapped_column(
        ForeignKey("bundles.id"), nullable=True
    )
    bundle_suggestion_reason: Mapped[str] = mapped_column(String, default="")


class Bundle(Base):
    """A staple Microsoft bundle — the stable identity the coverage map, scenarios,
    and licenses all speak in, sitting between the many priced catalog SKUs and the
    outcomes. `kind` is 'bundle' (a full base like Microsoft 365 E3) or 'addon' (a
    composable add-on like E5 Security whose `base_bundle_id` names the base it
    layers onto). Global + editable; seeded from seeds/bundles.json."""

    __tablename__ = "bundles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="bundle")  # bundle | addon
    # The PRIMARY/canonical base an add-on is designed for (drives display and the
    # compact seed form). The FULL set of bases it may layer onto is the M:N
    # AddonEligibility below — the primary base is always a member of that set.
    base_bundle_id: Mapped[str | None] = mapped_column(
        ForeignKey("bundles.id"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class AddonEligibility(Base):
    """Which base bundles an add-on may layer onto — the composition "logic layer"
    (e.g. F5 Security only onto F3, E5 Security only onto E3). A first-class M:N
    association (per DATA_MODEL §5: model many-to-many as an association object, not
    a delimited string or a single FK), global + editable, seeded from the add-on
    `base`/`bases` in seeds/bundles.json.

    Semantics: an add-on WITH ≥1 eligibility row is restricted to exactly those
    bases; an add-on with NO rows is à-la-carte — eligible for any base (this
    preserves the legacy `base_bundle_id = null` behaviour explicitly). The
    scenario add-on API and the recommend-a-path optimizer both enforce it."""

    __tablename__ = "addon_eligibilities"
    __table_args__ = (
        UniqueConstraint("addon_bundle_id", "base_bundle_id", name="uq_addon_eligibility"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    addon_bundle_id: Mapped[str] = mapped_column(ForeignKey("bundles.id"), index=True)
    base_bundle_id: Mapped[str] = mapped_column(ForeignKey("bundles.id"), index=True)


# Kinds of licensing limit. Today only a tenant-wide seat ceiling; the field keeps
# the engine general so future caps (per-add-on, per-market) are just new rows.
LIMIT_TYPES = ("max_total_seats",)


class LicenseLimit(Base):
    """A Microsoft licensing constraint over the global Bundle spine — e.g.
    "Microsoft 365 Business (Basic/Standard/Premium) is capped at 300 seats in the
    tenant". A first-class, global, editable, seeded rule; the member bundles it
    applies to are the M:N `LicenseLimitMember` set. Evaluation is a tenant-wide
    derived aggregate (services/limits.evaluate) computed at compute time over the
    engagement's current licenses + in-scope scenarios — it persists nothing, the
    same "don't create second-class data" outcome as the best-bundle analysis."""

    __tablename__ = "license_limits"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    limit_type: Mapped[str] = mapped_column(
        SAEnum(*LIMIT_TYPES, name="limit_type"), default="max_total_seats"
    )
    max_quantity: Mapped[int] = mapped_column(Integer, default=0)
    unit_basis: Mapped[str] = mapped_column(
        SAEnum(*UNIT_BASIS, name="limit_unit_basis"), default="Users"
    )
    # Aggregation scope of the ceiling. "tenant" = summed across the whole
    # engagement (all personas/scenarios), which is how the 300-seat cap works.
    scope: Mapped[str] = mapped_column(String, default="tenant")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class LicenseLimitMember(Base):
    """Association: a bundle counts toward a LicenseLimit's pool. First-class M:N
    (per DATA_MODEL §5) so a family of bundles — Business Basic + Standard +
    Premium — shares one ceiling, rather than a delimited string of SKU names."""

    __tablename__ = "license_limit_members"
    __table_args__ = (
        UniqueConstraint("license_limit_id", "bundle_id", name="uq_license_limit_member"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    license_limit_id: Mapped[str] = mapped_column(
        ForeignKey("license_limits.id"), index=True
    )
    bundle_id: Mapped[str] = mapped_column(ForeignKey("bundles.id"), index=True)


class CatalogImport(Base):
    """Provenance for each SUCCESSFUL pricing-catalog load — the first-class
    answer to "when was pricing last refreshed, and from where". Both the manual
    CSV upload and the Partner Center price-sync API write one row here on
    success (a failed import raises before recording, so a row always means a
    load that worked). Freshness — the Readout pricing badge and the staleness
    banner — reads the NEWEST row across sources, so whichever path ran most
    recently and succeeded is the one that counts."""

    __tablename__ = "catalog_imports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(
        SAEnum(*CATALOG_IMPORT_SOURCES, name="catalog_import_source")
    )
    # YYYY-MM the pricing is dated to (drives the freshness month rule).
    data_month: Mapped[str] = mapped_column(String, default="")
    catalog_version: Mapped[str] = mapped_column(String, default="")
    sku_count: Mapped[int] = mapped_column(Integer, default=0)
    # Filesystem provenance, populated for the price-sync path (blank for CSV).
    file_name: Mapped[str] = mapped_column(String, default="")
    sha256: Mapped[str] = mapped_column(String, default="")
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


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
    # Pricing basis for THIS line. NULL = inherit the engagement default (which
    # itself inherits the global default). Set = this line overrides it. These
    # pick which priced catalog variant seeds the list price and record the
    # commitment basis (yearly vs monthly purchase/commit) for the readout.
    segment: Mapped[str | None] = mapped_column(String, nullable=True)
    term_duration: Mapped[str | None] = mapped_column(String, nullable=True)
    billing_plan: Mapped[str | None] = mapped_column(String, nullable=True)
    # DEPRECATED single-persona link. Superseded by the many-to-many persona tags
    # (CurrentLicensePersona). Kept for the one-time backfill; not read by the
    # engine or API anymore.
    persona_id: Mapped[str | None] = mapped_column(
        ForeignKey("personas.id"), nullable=True
    )
    source_tag: Mapped[str] = _source_tag_col()

    engagement: Mapped[Engagement] = relationship(back_populates="current_licenses")
    persona_links: Mapped[list["CurrentLicensePersona"]] = relationship(
        cascade="all, delete-orphan", back_populates="license"
    )

    @property
    def persona_ids(self) -> list[str]:
        """The personas this license applies to (many-to-many tags)."""
        return [pl.persona_id for pl in self.persona_links]


class CurrentLicensePersona(Base):
    """Association: a current license applies to a persona (a 'tag'). Many-to-many
    so one line can cover several personas; the engine distributes the line's cost
    across their combined headcount. A future `applies_pct` would live here to
    model partial application (e.g. 5% of a persona)."""

    __tablename__ = "current_license_personas"
    __table_args__ = (
        UniqueConstraint("current_license_id", "persona_id", name="uq_license_persona"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    current_license_id: Mapped[str] = mapped_column(
        ForeignKey("current_microsoft_licenses.id"), index=True
    )
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)

    license: Mapped[CurrentMicrosoftLicense] = relationship(back_populates="persona_links")


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
    # DERIVED effective coverage: the sum of the tagged personas' headcounts,
    # unless the operator sets covered_count_override (which always wins). Kept
    # persisted so the engine/exports read one canonical value; recomputed by
    # _normalize_third_party() on every product write and on persona changes.
    covered_count: Mapped[int] = mapped_column(Integer, default=0)
    # Operator override for covers (e.g. the product covers more users than the
    # tagged personas). NULL = derive from personas.
    covered_count_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_unit_annual_cost: Mapped[float] = mapped_column(Numeric(14, 6), default=0)
    renewal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    commitment_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    tooling_pct: Mapped[float] = mapped_column(Numeric(6, 4), default=0.30)
    effective_annual_cost: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    source_tag: Mapped[str] = _source_tag_col()

    engagement: Mapped[Engagement] = relationship(back_populates="third_party_products")
    persona_links: Mapped[list["ThirdPartyPersona"]] = relationship(
        cascade="all, delete-orphan", back_populates="product"
    )

    @property
    def persona_ids(self) -> list[str]:
        """The personas this product applies to (many-to-many tags)."""
        return [pl.persona_id for pl in self.persona_links]

    @property
    def persona_covered_count(self) -> int:
        """Derived covers: combined headcount of the tagged personas. Tolerates a
        dangling link (persona deleted) by counting it as 0."""
        return sum(
            pl.persona.headcount or 0
            for pl in self.persona_links
            if pl.persona is not None
        )


class ThirdPartyPersona(Base):
    """Association: a third-party product applies to a persona (a 'tag'), mirroring
    CurrentLicensePersona. Many-to-many so one product can serve several
    personas. A future `applies_pct` would live here for partial application."""

    __tablename__ = "third_party_personas"
    __table_args__ = (
        UniqueConstraint("third_party_product_id", "persona_id", name="uq_tp_persona"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    third_party_product_id: Mapped[str] = mapped_column(
        ForeignKey("third_party_products.id"), index=True
    )
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)

    product: Mapped[ThirdPartyProduct] = relationship(back_populates="persona_links")
    persona: Mapped[Persona] = relationship()


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
    # For MicrosoftSku coverage: the canonical Bundle it applies to (the stable
    # SKU → Bundle → Outcomes key). microsoft_sku_reference is kept for display /
    # back-compat and holds the bundle name.
    bundle_id: Mapped[str | None] = mapped_column(ForeignKey("bundles.id"), nullable=True, index=True)
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
    # The BASE bundle of the future state. Add-on bundles layer on via scenario_addons.
    target_sku_reference: Mapped[str] = mapped_column(String, default="")
    target_unit_price_annual: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    # Discount off the composed list price (fraction; 0.15 = 15%). Applies to
    # base + add-ons to yield the net target price.
    target_discount_pct: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True)
    # Per-persona opt-OUT of the engagement's Business Premium swap (the inheritance
    # override). False = inherit the engagement default; True = keep this persona's
    # own target even when the engagement swap is on. User-entered.
    bp_swap_optout: Mapped[bool] = mapped_column(Boolean, default=False)
    # Line-level quoting basis (term × billing plan): NULL inherits the
    # engagement's defaults. Changing either requotes the composed target from
    # the catalog at the new basis (prices stay hand-editable afterward).
    term_duration: Mapped[str | None] = mapped_column(String, nullable=True)
    billing_plan: Mapped[str | None] = mapped_column(String, nullable=True)
    # Derived fields cached for snapshotting; recomputed by the engine on demand.
    current_spend_annual: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    target_spend_annual: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    delta_annual: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    engagement: Mapped[Engagement] = relationship(back_populates="scenarios")
    addons: Mapped[list["ScenarioAddon"]] = relationship(
        cascade="all, delete-orphan", back_populates="scenario"
    )


class ScenarioAddon(Base):
    """An add-on bundle layered onto a scenario's base target. The engine composes
    the future state = base + add-ons (union outcomes, sum prices). Each carries its
    own list price; the scenario's discount applies to the composed total."""

    __tablename__ = "scenario_addons"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("persona_scenarios.id"), index=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("bundles.id"))
    unit_price_annual: Mapped[float] = mapped_column(Numeric(14, 4), default=0)

    scenario: Mapped[PersonaScenario] = relationship(back_populates="addons")


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
    # Ground-floor pricing-segment default. New engagements copy this into their
    # own `default_segment` on creation; changing it here retargets only NEW
    # engagements. "Commercial" out of the box.
    default_segment: Mapped[str] = mapped_column(String, default="Commercial")
    # Ground-floor quoting-basis defaults: which priced catalog variant (term ×
    # billing plan) prices a bundle. New engagements copy these on creation and
    # scenarios may vary per line — the global → engagement → line hierarchy.
    # P1Y + Monthly = 1-year commit paid monthly, the typical customer-facing case.
    default_term_duration: Mapped[str] = mapped_column(String, default="P1Y")
    default_billing_plan: Mapped[str] = mapped_column(String, default="Monthly")
    # Operator-selected OpenRouter model for AI assist. Empty = use the env
    # default (settings.openrouter_model). Operational config, runtime-editable.
    openrouter_model: Mapped[str] = mapped_column(String, default="")
    # Feed OpenRouter's web-search plugin to the main model's calls (coverage,
    # parsing, narratives). Adds live web results at extra cost/latency; off by
    # default. Operational config, runtime-editable.
    openrouter_web_search: Mapped[bool] = mapped_column(Boolean, default=False)
    # Model for the pre-readout sanity check. Empty = use the env default
    # (settings.sanity_check_model, an inexpensive model). Operational config.
    sanity_check_model: Mapped[str] = mapped_column(String, default="")
    # Feed OpenRouter's web-search plugin to the sanity-check call. Off by default
    # to keep this frequent, low-stakes pass cheap and fast. Operational config.
    sanity_check_web_search: Mapped[bool] = mapped_column(Boolean, default=False)


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


class DefaultBundleCoverage(Base):
    """Global default Microsoft bundle → outcome coverage library (PRD 5.3.1) as a
    first-class, editable table rather than a static file. It is the TEMPLATE copied
    into engagement-scoped CoverageMapEntry rows on engagement creation. Editing it
    never touches existing engagements. Seeded from seeds/coverage.json on first run.
    Keyed by stable `bundle_key` (a Bundle.key) + `outcome_key` (a DefaultOutcome.key)."""

    __tablename__ = "default_bundle_coverage"
    __table_args__ = (
        UniqueConstraint("bundle_key", "outcome_key", name="uq_default_coverage"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    bundle_key: Mapped[str] = mapped_column(String, index=True)
    outcome_key: Mapped[str] = mapped_column(String)
    coverage: Mapped[str] = mapped_column(SAEnum(*COVERAGE, name="default_coverage_cov"), default="Full")


class AiPrompt(Base):
    """Editable system instructions for one AI function — a first-class, global
    template so every AI call's prompt is visible and tunable in one place rather
    than hard-coded. One row per function key (e.g. "coverage_suggest",
    "third_party_parse"). Seeded from seeds/ai_prompts.json; the operator can edit
    the instructions when output isn't great and reset to the seeded default."""

    __tablename__ = "ai_prompts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String, unique=True)
    label: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    # False while the row still matches the shipped default, so startup seeding
    # can refresh unedited rows to an improved default without clobbering edits.
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
