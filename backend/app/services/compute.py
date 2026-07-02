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
    CandidateBundle,
    CurrentLicenseLine,
    Engagement as EngEngagement,
    Override,
    Persona as EngPersona,
    PersonaScenario as EngScenario,
    ResidualIntent,
    ThirdPartyProduct as EngThirdParty,
    analyze_bundles,
    compute as engine_compute,
)
from tco_engine.engine import EngineResult

from .. import models
from . import seeds


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

    current_lines = [
        CurrentLicenseLine(
            quantity_assigned=lic.quantity_assigned,
            unit_price_paid_annual=_dec(lic.unit_price_paid_annual),
            sku_reference=lic.sku_reference,
            persona_ids=tuple(lic.persona_ids),
        )
        for lic in eng.current_licenses
    ]

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
        current_licenses=current_lines,
    )


def _bundle_refs() -> list[str]:
    """Candidate bundle sku_references from the seed library (is_bundle=true)."""
    return [s["sku_reference"] for s in seeds.load_coverage()["skus"] if s.get("is_bundle")]


def _catalog_annual_erp(db: Session, sku_reference: str) -> Decimal:
    """Best-effort catalog price for a bundle: the annual ERP of a P1Y row whose
    title contains the reference. 0 if the catalog isn't loaded / no match."""
    like = f"%{sku_reference}%"
    row = db.execute(
        select(models.MicrosoftSku)
        .where(
            (models.MicrosoftSku.sku_title.ilike(like))
            | (models.MicrosoftSku.product_title.ilike(like))
        )
        .order_by(models.MicrosoftSku.term_duration.desc())  # prefer P1Y over P1M/P3Y-ish
    ).scalars().first()
    return _dec(row.annual_erp_price) if row else Decimal("0")


def analyze_persona_bundles(
    db: Session, engagement_id: str, persona_id: str, prices: dict | None = None
) -> dict:
    """Evaluate every candidate bundle as this persona's target and rank by TCO."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise ValueError("Engagement not found")
    persona = db.get(models.Persona, persona_id)
    if persona is None or persona.engagement_id != engagement_id:
        raise ValueError("Persona not found")

    sku_outcomes = _ratified_sku_outcomes(db, engagement_id)  # ref -> {outcome_id}
    tp_outcomes = _ratified_thirdparty_outcomes(db, engagement_id)
    outcome_names = {o.id: o.name for o in eng.outcomes}

    # Candidate bundles present in this engagement's coverage map.
    candidate_refs = [r for r in _bundle_refs() if r in sku_outcomes]

    # Required outcomes = what the persona's current Microsoft licenses deliver
    # (the "don't lose capability" baseline used for gap detection). A line may be
    # tagged to several personas; its cost is split across their combined headcount
    # (mirrors the engine's §6.2 allocation).
    hc = {p.id: p.headcount for p in eng.personas}
    persona_lines = [l for l in eng.current_licenses if persona_id in l.persona_ids]
    required: set[str] = set()
    current_ms = Decimal("0")
    for line in persona_lines:
        required |= sku_outcomes.get(line.sku_reference, set())
        line_total = Decimal(line.quantity_assigned) * _dec(line.unit_price_paid_annual)
        tagged = [pid for pid in line.persona_ids if pid in hc]
        tagged_hc = sum(hc[pid] for pid in tagged)
        share = (Decimal(hc.get(persona_id, 0)) / Decimal(tagged_hc)) if tagged_hc > 0 \
            else Decimal(1) / Decimal(len(tagged) or 1)
        current_ms += line_total * share

    # Everything they have today (MS + third-party) — used to compute the ADDED
    # outcomes a bundle brings that they don't have at all.
    current_capability = set(required)
    for tp_id, outs in tp_outcomes.items():
        current_capability |= outs

    third_party = [
        EngThirdParty(
            id=tp.id, name=tp.name, annual_cost=_dec(tp.annual_cost),
            covered_count=tp.covered_count, is_managed=tp.is_managed,
            tooling_pct=_dec(tp.tooling_pct),
            delivered_outcome_ids=frozenset(tp_outcomes.get(tp.id, set())),
        )
        for tp in eng.third_party_products
    ]
    tp_names = {tp.id: tp.name for tp in eng.third_party_products}

    prices = prices or {}
    candidates = []
    for ref in candidate_refs:
        override = prices.get(ref)
        price = Decimal(str(override)) if override is not None else _catalog_annual_erp(db, ref)
        candidates.append(
            CandidateBundle(
                sku_reference=ref,
                covered_outcome_ids=frozenset(sku_outcomes.get(ref, set())),
                target_unit_price_annual=price,
            )
        )

    analyses = analyze_bundles(
        persona.headcount, current_ms, frozenset(required),
        frozenset(current_capability), candidates, third_party,
    )

    def names(ids):
        return [outcome_names.get(i, i) for i in ids]

    def positioning(b) -> str:
        """The value story to lead with for this bundle."""
        saves = b.delta_annual >= 0
        added = bool(b.added_outcome_ids)
        if saves and added:
            return "Lower TCO + new capabilities"
        if saves:
            return "Lower TCO"
        if added:
            return "New capabilities + integrated ecosystem"
        return "Higher cost — consider reimagining required outcomes"

    return {
        "persona_id": persona.id,
        "persona_name": persona.name,
        "headcount": persona.headcount,
        "current_microsoft_annual": float(current_ms),
        "required_outcomes": [
            {"id": i, "name": outcome_names.get(i, i)} for i in sorted(required)
        ],
        "bundles": [
            {
                "sku_reference": b.sku_reference,
                "target_unit_price_annual": float(b.target_unit_price_annual),
                "target_spend_annual": float(b.target_spend_annual),
                "current_spend_annual": float(b.current_spend_annual),
                "delta_annual": float(b.delta_annual),
                "third_party_offset_annual": float(b.third_party_offset_annual),
                "covered_required_outcomes": names(b.covered_required_outcome_ids),
                "gap_outcomes": names(b.gap_outcome_ids),
                "added_outcomes": names(b.added_outcome_ids),
                "displaced_products": [tp_names.get(i, i) for i in b.displaced_product_ids],
                "covers_all_required": b.covers_all_required,
                "price_known": b.price_known,
                "recommended": b.recommended,
                "positioning": positioning(b),
            }
            for b in analyses
        ],
    }


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
