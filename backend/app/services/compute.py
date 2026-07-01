"""Hydration bridge: ORM rows -> pure engine inputs -> persisted dispositions.

This is the only place that knows both the database and the engine. It enforces
the ratified-only rule (PRD 6.6 / 5.7): unratified AI suggestions never reach the
hydrated coverage sets, so they can never feed the math.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tco_engine import (
    CurrentLicenseLine,
    Engagement as EngEngagement,
    Override,
    Persona as EngPersona,
    PersonaScenario as EngScenario,
    ResidualIntent,
    ThirdPartyProduct as EngThirdParty,
    compute as engine_compute,
)
from tco_engine.engine import EngineResult

from .. import models


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


def _ratified_sku_outcomes(db: Session, engagement_id: str) -> dict[str, set[str]]:
    """sku_reference -> set of outcome_ids it covers (ratified, Full|Partial)."""
    rows = db.execute(
        select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == engagement_id,
            models.CoverageMapEntry.product_kind == "MicrosoftSku",
            models.CoverageMapEntry.ratified.is_(True),
        )
    ).scalars()
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r.microsoft_sku_reference or "", set()).add(r.outcome_id)
    return out


def _ratified_thirdparty_outcomes(db: Session, engagement_id: str) -> dict[str, set[str]]:
    """third_party_product_id -> set of outcome_ids it delivers (ratified)."""
    rows = db.execute(
        select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == engagement_id,
            models.CoverageMapEntry.product_kind == "ThirdParty",
            models.CoverageMapEntry.ratified.is_(True),
        )
    ).scalars()
    out: dict[str, set[str]] = {}
    for r in rows:
        if r.third_party_product_id:
            out.setdefault(r.third_party_product_id, set()).add(r.outcome_id)
    return out


def hydrate(db: Session, engagement_id: str) -> EngEngagement:
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise ValueError(f"Engagement {engagement_id} not found")

    sku_outcomes = _ratified_sku_outcomes(db, engagement_id)
    tp_outcomes = _ratified_thirdparty_outcomes(db, engagement_id)

    # Operator choices persisted on disposition rows.
    disp_rows = {
        d.third_party_product_id: d
        for d in db.execute(
            select(models.ProductDisposition).where(
                models.ProductDisposition.engagement_id == engagement_id
            )
        ).scalars()
    }

    personas = [
        EngPersona(id=p.id, name=p.name, headcount=p.headcount) for p in eng.personas
    ]

    current_by_persona: dict[str, list[CurrentLicenseLine]] = {}
    for lic in eng.current_licenses:
        if not lic.persona_id:
            continue
        current_by_persona.setdefault(lic.persona_id, []).append(
            CurrentLicenseLine(
                quantity_assigned=lic.quantity_assigned,
                unit_price_paid_annual=_dec(lic.unit_price_paid_annual),
                sku_reference=lic.sku_reference,
            )
        )

    third_party = []
    for tp in eng.third_party_products:
        disp = disp_rows.get(tp.id)
        third_party.append(
            EngThirdParty(
                id=tp.id,
                name=tp.name,
                annual_cost=_dec(tp.annual_cost),
                covered_count=tp.covered_count,
                is_managed=tp.is_managed,
                tooling_pct=_dec(tp.tooling_pct),
                renewal_date=tp.renewal_date.isoformat() if tp.renewal_date else None,
                delivered_outcome_ids=frozenset(tp_outcomes.get(tp.id, set())),
                override=Override(disp.override) if disp else Override.NONE,
                override_reason=disp.override_reason if disp else "",
                residual_intent=(
                    ResidualIntent(disp.residual_intent) if disp else ResidualIntent.NONE
                ),
            )
        )

    scenarios = [
        EngScenario(
            id=s.id,
            persona_id=s.persona_id,
            target_sku_reference=s.target_sku_reference,
            target_unit_price_annual=_dec(s.target_unit_price_annual),
            in_scope=s.in_scope,
            target_covered_outcome_ids=frozenset(
                sku_outcomes.get(s.target_sku_reference, set())
            ),
        )
        for s in eng.scenarios
    ]

    return EngEngagement(
        id=eng.id,
        personas=personas,
        third_party_products=third_party,
        scenarios=scenarios,
        current_licenses_by_persona=current_by_persona,
    )


def compute_and_persist(db: Session, engagement_id: str) -> EngineResult:
    """Run the engine and write derived fields back (PRD 5.8/5.9 persistence).

    Operator-owned fields (override, override_reason, residual_intent) are
    preserved; only engine-derived fields are overwritten.
    """
    hydrated = hydrate(db, engagement_id)
    result = engine_compute(hydrated)

    # Persist scenario-derived spend (5.8 cached fields).
    scenarios = {s.id: s for s in db.get(models.Engagement, engagement_id).scenarios}
    for sr in result.scenarios:
        row = scenarios.get(sr.scenario_id)
        if row:
            row.current_spend_annual = sr.current_spend_annual
            row.target_spend_annual = sr.target_spend_annual
            row.delta_annual = sr.delta_annual

    # Upsert dispositions (5.9), preserving operator choices.
    existing = {
        d.third_party_product_id: d
        for d in db.execute(
            select(models.ProductDisposition).where(
                models.ProductDisposition.engagement_id == engagement_id
            )
        ).scalars()
    }
    for dr in result.dispositions:
        row = existing.get(dr.third_party_product_id)
        if row is None:
            row = models.ProductDisposition(
                engagement_id=engagement_id,
                third_party_product_id=dr.third_party_product_id,
            )
            db.add(row)
        row.displaced_users = dr.displaced_users
        row.disposition = dr.disposition.value
        row.residual_count = dr.residual_count
        row.residual_annual_cost = dr.residual_annual_cost

    db.commit()
    return result
