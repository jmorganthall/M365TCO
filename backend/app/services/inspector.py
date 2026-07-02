"""Data inspector — a live, GUI-facing view of an engagement's whole data model.

Introspects the ORM models so EVERY persisted field of every engagement-scoped
object is surfaced (the "no hidden data" law), classifies each field
(input / derived / provenance / reference / identity), resolves references to
human labels, and validates soft SKU references against the catalog. Also emits a
simple input → engine → output flow. Read-only; nothing here mutates state.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

# Columns that are structural noise in the inspector (scope is implied by being
# inside the engagement; the deprecated persona_id is replaced by persona_ids).
_SKIP = {"engagement_id", ("current_microsoft_licenses", "persona_id")}

_PROVENANCE = {"source_tag", "ai_suggested", "ratified", "seed_key", "catalog_version"}
_DERIVED = {
    "annual_cost", "effective_annual_cost", "per_unit_annual_cost",
    "current_spend_annual", "target_spend_annual", "delta_annual",
    "displaced_users", "disposition", "residual_count", "residual_annual_cost",
}
_SYSTEM = {"created_at", "updated_at"}

_LABELS = {
    "sku_reference": "SKU reference", "unit_price_paid_annual": "Price paid ($/seat/yr)",
    "quantity_assigned": "Assigned", "quantity_purchased": "Purchased",
    "discount_pct": "Discount", "price_basis": "Price basis", "persona_ids": "Applies to",
    "raw_cost": "Cost (as entered)", "cost_period": "Period", "annual_cost": "Annual cost",
    "covered_count": "Covers", "per_unit_annual_cost": "$/unit/yr", "is_managed": "Managed",
    "tooling_pct": "Tooling %", "effective_annual_cost": "Effective $/yr",
    "commitment_term_months": "Commitment (months)", "unit_basis": "Unit basis",
    "renewal_date": "Renewal", "source_tag": "Source", "outcome_id": "Outcome",
    "product_kind": "Product kind", "microsoft_sku_reference": "Microsoft SKU",
    "third_party_product_id": "Third-party product", "coverage": "Coverage",
    "ai_suggested": "AI-suggested", "ratified": "Ratified", "persona_id": "Persona",
    "target_sku_reference": "Target SKU", "target_unit_price_annual": "Target $/seat/yr",
    "in_scope": "In scope", "delta_annual": "Δ TCO/yr", "override": "Override",
    "override_reason": "Override reason", "residual_intent": "Residual intent",
    "residual_annual_cost": "Residual $/yr", "displaced_users": "Displaced users",
    "disposition": "Disposition", "residual_count": "Residual units", "headcount": "Headcount",
    "is_custom": "Custom", "seed_key": "Seed key",
}


def _label(key: str) -> str:
    return _LABELS.get(key, key.replace("_", " ").capitalize())


def _kind(table: str, key: str) -> str:
    if key == "id":
        return "identity"
    if key in _SYSTEM:
        return "system"
    if key in _PROVENANCE:
        return "provenance"
    if key in _DERIVED:
        return "derived"
    if key.endswith("_id") or key.endswith("_reference") or key == "persona_ids":
        return "reference"
    return "input"


def _fmt(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        return f"{value:g}"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _registry():
    return [
        {"cls": models.Persona, "type": "Persona", "label": "Personas",
         "desc": "Population segments. Headcount drives per-persona spend and cost allocation.",
         "primary": ["name", "headcount"], "extra": []},
        {"cls": models.Outcome, "type": "Outcome", "label": "Outcomes",
         "desc": "Capability buckets copied from the default library (plus custom).",
         "primary": ["name", "is_custom"], "extra": []},
        {"cls": models.CurrentMicrosoftLicense, "type": "CurrentMicrosoftLicense",
         "label": "Current Microsoft licensing",
         "desc": "What the customer holds today. Feeds the Microsoft side of current spend.",
         "primary": ["sku_reference", "quantity_assigned", "unit_price_paid_annual"],
         "extra": ["persona_ids"]},
        {"cls": models.ThirdPartyProduct, "type": "ThirdPartyProduct",
         "label": "Third-party products",
         "desc": "Non-Microsoft spend. Effective cost (managed split) feeds displacement.",
         "primary": ["name", "annual_cost", "covered_count"], "extra": []},
        {"cls": models.CoverageMapEntry, "type": "CoverageMapEntry", "label": "Coverage map",
         "desc": "Product ↔ outcome matrix. Only ratified entries reach the engine.",
         "primary": ["outcome_id", "coverage", "ratified"], "extra": []},
        {"cls": models.PersonaScenario, "type": "PersonaScenario", "label": "Scenarios",
         "desc": "One target-state plan per persona. Delta is engine-derived.",
         "primary": ["persona_id", "target_sku_reference", "delta_annual"], "extra": []},
        {"cls": models.ProductDisposition, "type": "ProductDisposition",
         "label": "Product dispositions",
         "desc": "Per-product outcome of the reconciliation (mostly engine-derived).",
         "primary": ["third_party_product_id", "disposition", "residual_annual_cost"],
         "extra": []},
    ]


def inspect_engagement(db: Session, eng: models.Engagement) -> dict:
    eid = eng.id
    personas = {p.id: p.name for p in eng.personas}
    outcomes = {o.id: o.name for o in eng.outcomes}
    products = {t.id: t.name for t in eng.third_party_products}

    # Cache soft-SKU resolutions against the catalog.
    _sku_cache: dict[str, str | None] = {}

    def resolve_sku(ref: str):
        if not ref:
            return None
        if ref not in _sku_cache:
            like = f"%{ref}%"
            row = db.execute(
                select(models.MicrosoftSku.sku_title).where(
                    (models.MicrosoftSku.sku_title.ilike(like))
                    | (models.MicrosoftSku.product_title.ilike(like))
                )
            ).scalars().first()
            _sku_cache[ref] = row
        title = _sku_cache[ref]
        return {"label": title or f"{ref} — not in catalog", "ok": title is not None}

    def resolve_ref(key: str, value):
        if value in (None, "", []):
            return None
        if key == "persona_ids":
            names = [personas.get(v, f"{v} — missing") for v in value]
            return {"label": ", ".join(names) or "—", "ok": all(v in personas for v in value)}
        if key in ("persona_id",):
            return {"label": personas.get(value, f"{value} — missing"), "ok": value in personas}
        if key == "outcome_id":
            return {"label": outcomes.get(value, f"{value} — missing"), "ok": value in outcomes}
        if key == "third_party_product_id":
            return {"label": products.get(value, f"{value} — missing"), "ok": value in products}
        if key in ("sku_reference", "target_sku_reference", "microsoft_sku_reference"):
            return resolve_sku(value)
        return None

    objects = []
    for spec in _registry():
        cls = spec["cls"]
        col_keys = [c.name for c in cls.__table__.columns
                    if c.name not in _SKIP and (cls.__tablename__, c.name) not in _SKIP]
        field_keys = col_keys + spec["extra"]
        fields = [{"key": k, "label": _label(k), "kind": _kind(cls.__tablename__, k)}
                  for k in field_keys]

        rows = db.execute(
            select(cls).where(cls.engagement_id == eid)
        ).scalars().all()
        records = []
        for row in rows:
            cells = {}
            for k in field_keys:
                value = getattr(row, k, None)
                ref = resolve_ref(k, value)
                display = ref["label"] if ref else _fmt(value)
                cells[k] = {"display": display, "ref": ref}
            records.append({"id": row.id, "cells": cells})

        objects.append({
            "type": spec["type"], "label": spec["label"], "description": spec["desc"],
            "primary": spec["primary"], "fields": fields,
            "count": len(records), "records": records,
        })

    ratified = sum(1 for c in eng.coverage_entries if c.ratified)
    flow = [
        {"stage": "Inputs", "items": [
            f"Personas · {len(eng.personas)}",
            f"Current licensing · {len(eng.current_licenses)}",
            f"Third-party products · {len(eng.third_party_products)}",
            f"Coverage · {ratified} ratified / {len(eng.coverage_entries)} total",
        ]},
        {"stage": "Engine", "items": [
            "Per-persona current spend (headcount-weighted)",
            "Displacement test (ratified coverage only)",
            "Rollup + integrity rules",
        ]},
        {"stage": "Outputs", "items": [
            f"Scenario results · {len(eng.scenarios)}",
            f"Dispositions · {len(eng.third_party_products)}",
            "Readout snapshot",
        ]},
    ]

    return {
        "engagement": {
            "id": eng.id, "customer_name": eng.customer_name,
            "market": eng.market, "currency": eng.currency,
            "global_tooling_pct": float(eng.global_tooling_pct),
        },
        "objects": objects,
        "flow": flow,
    }
