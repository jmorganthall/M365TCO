"""Load an engagement's raw layer into the interpreter's plain snapshot.

`load_raw(db, engagement_id) -> RawEngagement` reads what people entered, as
entered, plus the catalog facts the interpreter can't look up itself (which plan a
typed name resolves to; the engagement's ratified coverage). It decides nothing:
every rule lives in `tco_engine.interpret`. Reads only.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tco_engine.interpret import (
    RawAddon,
    RawEngagement,
    RawLimit,
    RawLicenseLine,
    RawPersona,
    RawScenario,
    RawTool,
    Interpreted,
    interpret,
)

from .. import models
from . import bundles
from . import personas as persona_order


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


def _opt_dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _coverage(db: Session, engagement_id: str, kind: str) -> dict[str, frozenset[str]]:
    rows = db.execute(
        select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == engagement_id,
            models.CoverageMapEntry.product_kind == kind,
            models.CoverageMapEntry.ratified.is_(True),
        )
    ).scalars()
    out: dict[str, set[str]] = {}
    for r in rows:
        if kind == "ThirdParty":
            key = r.third_party_product_id
            if not key:
                continue
        else:
            key = r.bundle_id or r.microsoft_sku_reference or ""
        out.setdefault(key, set()).add(r.outcome_id)
    return {k: frozenset(v) for k, v in out.items()}


def load_raw(db: Session, engagement_id: str) -> RawEngagement:
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise ValueError(f"Engagement {engagement_id} not found")

    cache: dict[str, str | None] = {}

    def resolve(ref: str | None) -> str | None:
        ref = (ref or "").strip()
        if not ref:
            return None
        if ref not in cache:
            cache[ref] = bundles.resolve_bundle(db, ref)
        return cache[ref]

    decisions = {
        d.third_party_product_id: d
        for d in db.execute(
            select(models.ProductDisposition).where(
                models.ProductDisposition.engagement_id == engagement_id)
        ).scalars()
    }

    personas = tuple(
        RawPersona(id=p.id, name=p.name, headcount=p.headcount or 0,
                   parent_persona_id=p.parent_persona_id,
                   required_outcome_ids=frozenset(p.required_outcome_ids))
        for p in persona_order.ordered(eng.personas)
    )

    lines = tuple(
        RawLicenseLine(
            id=lic.id,
            sku_reference=lic.sku_reference or "",
            quantity_assigned=lic.quantity_assigned or 0,
            quantity_purchased=lic.quantity_purchased or 0,
            list_price_annual=_dec(lic.unit_price_paid_annual),
            price_override=bool(lic.price_override),
            overridden_price_annual=_opt_dec(lic.overridden_price_annual),
            persona_ids=tuple(lic.persona_ids),
            coverage_scope=lic.coverage_scope or "PerUser",
            renewal_date=lic.renewal_date,
            unused_seats_answer=lic.unused_seats_answer,
            out_of_scope=bool(lic.out_of_scope),
            linked_bundle_id=lic.bundle_id,
            mapped_outcome_ids=frozenset(l.outcome_id for l in lic.outcome_links if l.ratified),
            resolved_bundle_id=resolve(lic.sku_reference),
        )
        for lic in eng.current_licenses
    )

    tools = []
    for tp in eng.third_party_products:
        d = decisions.get(tp.id)
        tools.append(RawTool(
            id=tp.id, name=tp.name or "",
            raw_cost=_dec(tp.raw_cost), cost_period=tp.cost_period or "Annual",
            is_managed=bool(tp.is_managed),
            tooling_pct=_opt_dec(tp.tooling_pct),
            covered_count_override=tp.covered_count_override,
            persona_ids=tuple(tp.persona_ids),
            renewal_date=tp.renewal_date.isoformat() if tp.renewal_date else None,
            override=d.override if d else "None",
            override_reason=d.override_reason if d else "",
            residual_intent=d.residual_intent if d else "None",
        ))

    scenarios = tuple(
        RawScenario(
            id=s.id, persona_id=s.persona_id,
            target_sku_reference=s.target_sku_reference or "",
            list_price_annual=_dec(s.target_unit_price_annual),
            discount_pct=_opt_dec(s.target_discount_pct),
            price_override=bool(s.price_override),
            overridden_price_annual=_opt_dec(s.overridden_price_annual),
            in_scope=bool(s.in_scope),
            addons=tuple(RawAddon(bundle_id=a.bundle_id, list_price_annual=_dec(a.unit_price_annual))
                         for a in s.addons),
            resolved_bundle_id=resolve(s.target_sku_reference),
        )
        for s in eng.scenarios
    )

    return RawEngagement(
        id=eng.id, personas=personas, licence_lines=lines, tools=tuple(tools),
        scenarios=scenarios,
        microsoft_coverage=_coverage(db, engagement_id, "MicrosoftSku"),
        tool_coverage=_coverage(db, engagement_id, "ThirdParty"),
        default_tooling_pct=_dec(eng.global_tooling_pct if eng.global_tooling_pct is not None else "0.30"),
        ecif_roi_conservative=_dec(eng.ecif_roi_conservative),
        ecif_roi_generous=_dec(eng.ecif_roi_generous),
        # Month 0 of the timed headline. An engagement created before the workshop
        # date existed falls back to the day it was created — a fixed date, so the
        # numbers never drift with the calendar.
        workshop_date=eng.workshop_date or (eng.created_at.date() if eng.created_at else None),
        microsoft_renewal_date=eng.microsoft_renewal_date,
        horizon_years=int(eng.modeling_horizon_years or 3),
        gap_answers={(a.persona_id, a.outcome_id): a.answer for a in eng.gap_answers},
        employee_count=eng.employee_count,
        licence_limits=_limits(db),
    )


def _limits(db: Session) -> tuple[RawLimit, ...]:
    members: dict[str, set[str]] = {}
    for m in db.execute(select(models.LicenseLimitMember)).scalars():
        members.setdefault(m.license_limit_id, set()).add(m.bundle_id)
    return tuple(
        RawLimit(key=lim.key, name=lim.name, limit_type=lim.limit_type,
                 max_quantity=lim.max_quantity, member_bundle_ids=frozenset(members.get(lim.id, ())))
        for lim in db.execute(
            select(models.LicenseLimit).order_by(models.LicenseLimit.sort_order)).scalars()
    )


def interpreted(db: Session, engagement_id: str) -> Interpreted:
    """The engagement read through the rules (tco_engine.interpret). Reads only."""
    return interpret(load_raw(db, engagement_id))


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (frozenset, set)):
        return sorted(v)
    if isinstance(v, tuple):
        return list(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def interpreted_view(db: Session, engagement_id: str) -> dict:
    """The interpreted layer as the API shows it: derived, read-only (TARGET_SCHEMA
    §1.1). Every value here is what the engine and the screens read."""
    from dataclasses import fields

    i = interpreted(db, engagement_id)

    def rows(items):
        return [{f.name: _plain(getattr(x, f.name)) for f in fields(x)} for x in items]

    return {
        "derived": True,
        "personas": rows(i.personas),
        "licence_lines": rows(i.licence_lines),
        "licence_shares": [{"licence_id": l, "persona_id": p, "annual": float(v)}
                           for (l, p), v in i.licence_shares.items()],
        "tools": rows(i.tools),
        "tool_shares": [{"tool_id": t, "persona_id": p, "annual": float(v)}
                        for (t, p), v in i.tool_shares.items()],
        "scenarios": rows(i.scenarios),
        # The persona × outcome tables (TARGET_SCHEMA §6.3).
        "today": rows(i.today),
        "today_sources": rows(i.today_sources),
        "proposed": rows(i.proposed),
        "proposed_sources": rows(i.proposed_sources),
        "tool_retirements": rows(i.tool_retirements),
        # The licensing limits counted (TARGET_SCHEMA D27) and every finding (§6.4).
        "seat_caps": [{**{k: _plain(v) for k, v in r.items() if k != "usage"},
                       "future_seats": c.future_seats,
                       "usage": [{"persona_id": p, "seats": u} for p, u in c.usage.items()]}
                      for c, r in ((c, {f.name: getattr(c, f.name) for f in fields(c)})
                                   for c in i.seat_caps)],
        "findings": [{**{f.name: _plain(getattr(x, f.name)) for f in fields(x) if f.name != "facts"},
                      "facts": {k: _plain(v) for k, v in x.facts.items()}}
                     for x in i.findings],
    }
