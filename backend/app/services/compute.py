"""Hydration bridge: ORM rows -> pure engine inputs -> persisted dispositions.

This is the only place that knows both the database and the engine. It enforces
the ratified-only rule (PRD 6.6 / 5.7): unratified AI suggestions never reach the
hydrated coverage sets, so they can never feed the math.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from tco_engine import (
    CandidateBundle,
    CoverageScope,
    CurrentLicenseLine,
    Engagement as EngEngagement,
    Override,
    Persona as EngPersona,
    PersonaScenario as EngScenario,
    ResidualIntent,
    ThirdPartyProduct as EngThirdParty,
    UnusedSeatsAnswer,
    analyze_bundles,
    compute as engine_compute,
)
from tco_engine.engine import EngineResult

from .. import models
from . import bundles, limits, seeds
from . import personas as persona_order


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


def _ratified_sku_outcomes(db: Session, engagement_id: str) -> dict[str, set[str]]:
    """coverage key -> set of outcome_ids it covers (ratified, Full|Partial). The
    key is the Bundle id when set (the canonical SKU → Bundle → Outcomes spine),
    else the free-text microsoft_sku_reference (custom/unmapped entries)."""
    rows = db.execute(
        select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == engagement_id,
            models.CoverageMapEntry.product_kind == "MicrosoftSku",
            models.CoverageMapEntry.ratified.is_(True),
        )
    ).scalars()
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r.bundle_id or r.microsoft_sku_reference or "", set()).add(r.outcome_id)
    return out


def _cover_key(db: Session, ref: str) -> str:
    """Resolve a SKU/bundle reference string to its coverage key: the Bundle id if
    it maps to a bundle, else the raw string. This is what bridges a scenario's
    target or a license's sku_reference to the bundle-keyed coverage map."""
    return bundles.resolve_bundle(db, ref) or (ref or "")


@dataclass(frozen=True)
class LicenceReading:
    """How a current licence line is read (TARGET_SCHEMA §4.4 "Reading a line",
    D23): what it delivers and why. `state` is one of
      out_of_scope  set aside — in no number, delivers nothing
      linked        a person said "it's the same as" a library plan (bundle_id)
      mapped        a person ticked what it delivers (CurrentLicenseOutcome)
      named         legacy: coverage entered by hand against this exact name
      resolved      its name resolves to a plan this engagement has coverage for
      unread        none of those: its cost counts, but what it delivers is unknown
    An explicit answer always wins over resolving the name, so a mapping a person
    made keeps counting after the library later learns that name. For `unread`,
    `bundle_id` is the plan the name or link points at when there is one (the
    engagement just has no coverage for it yet), else None."""

    state: str
    outcomes: frozenset[str]
    bundle_id: Optional[str] = None


def licence_readings(db: Session, eng: models.Engagement,
                     sku_outcomes: dict[str, set[str]] | None = None) -> dict[str, LicenceReading]:
    """license id -> LicenceReading for every line of the engagement (out of scope
    included). Pure read."""
    if sku_outcomes is None:
        sku_outcomes = _ratified_sku_outcomes(db, eng.id)
    out: dict[str, LicenceReading] = {}
    for lic in eng.current_licenses:
        ref = (lic.sku_reference or "").strip()
        own = frozenset(l.outcome_id for l in lic.outcome_links if l.ratified)
        named = frozenset(sku_outcomes.get(ref, set())) if ref else frozenset()
        if lic.out_of_scope:
            reading = LicenceReading("out_of_scope", frozenset())
        elif lic.bundle_id:
            outs = frozenset(sku_outcomes.get(lic.bundle_id, set()))
            reading = LicenceReading("linked" if outs else "unread", outs, lic.bundle_id)
        elif own:
            reading = LicenceReading("mapped", own)
        elif named:
            reading = LicenceReading("named", named)
        else:
            resolved = bundles.resolve_bundle(db, ref) if ref else None
            outs = frozenset(sku_outcomes.get(resolved, set())) if resolved else frozenset()
            reading = LicenceReading("resolved" if outs else "unread", outs, resolved)
        out[lic.id] = reading
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
    readings = licence_readings(db, eng, sku_outcomes)

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
        EngPersona(id=p.id, name=p.name, headcount=p.headcount)
        for p in persona_order.ordered(eng.personas)
    ]

    current_lines = [
        CurrentLicenseLine(
            quantity_assigned=lic.quantity_assigned,
            # Effective price = the override when the line overrides list, else the
            # catalog list baseline — so an override actually lowers current spend.
            unit_price_paid_annual=lic.effective_unit_price_annual,
            sku_reference=lic.sku_reference,
            persona_ids=tuple(lic.persona_ids),
            # How many seats this line entitles — per-seat purchase vs tenant-wide
            # entitlement. Drives how many seats a duplicate tool can be credited
            # as redundant today (ENGINE_SPEC 6.10).
            coverage_scope=CoverageScope(lic.coverage_scope or "PerUser"),
            # What this existing license already delivers (how the line is read:
            # its linked plan, its own mapped outcomes, or its name's plan), for
            # quick-win duplicate detection. An unread line delivers nothing known.
            covered_outcome_ids=readings[lic.id].outcomes,
            # Over-licensing and timing (ENGINE_SPEC 6.11).
            id=lic.id,
            quantity_purchased=lic.quantity_purchased or 0,
            renewal_date=lic.renewal_date,
            unused_seats_answer=(
                UnusedSeatsAnswer(lic.unused_seats_answer) if lic.unused_seats_answer else None
            ),
        )
        # Out-of-scope lines are not engine inputs (ENGINE_SPEC 6.2): they are in
        # no number, today or after the move.
        for lic in eng.licenses_in_scope
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
                persona_ids=frozenset(tp.persona_ids),
                override=Override(disp.override) if disp else Override.NONE,
                override_reason=disp.override_reason if disp else "",
                residual_intent=(
                    ResidualIntent(disp.residual_intent) if disp else ResidualIntent.NONE
                ),
            )
        )

    # Compose each scenario's future state = base bundle + add-on bundles: union
    # the covered outcomes, sum the list prices, then apply the discount to yield
    # the net per-seat price the engine consumes. There is no target substitution
    # here any more: a persona that should move to a different plan says so on its
    # own scenario, and a persona only PART of which should move is carved into a
    # child persona with its own scenario (Persona.parent_persona_id). What the
    # engine costs is therefore always what the operator can see on the row.
    scenarios = []
    for s in eng.scenarios:
        covered = set(sku_outcomes.get(_cover_key(db, s.target_sku_reference), set()))
        for addon in s.addons:
            covered |= sku_outcomes.get(addon.bundle_id, set())
        # Net = the override when the scenario overrides list, else
        # (base + add-ons) × (1 − discount).
        net_price = s.effective_net_annual
        target_ref = s.target_sku_reference
        scenarios.append(EngScenario(
            id=s.id,
            persona_id=s.persona_id,
            target_sku_reference=target_ref,
            target_unit_price_annual=net_price,
            in_scope=s.in_scope,
            target_covered_outcome_ids=frozenset(covered),
        ))

    return EngEngagement(
        id=eng.id,
        personas=personas,
        third_party_products=third_party,
        scenarios=scenarios,
        current_licenses=current_lines,
        ecif_roi_conservative=_dec(eng.ecif_roi_conservative),
        ecif_roi_generous=_dec(eng.ecif_roi_generous),
        # Month 0 of the timed headline. An engagement created before the workshop
        # date existed falls back to the day it was created — a fixed date, so the
        # numbers never drift with the calendar.
        workshop_date=eng.workshop_date or (eng.created_at.date() if eng.created_at else None),
        microsoft_renewal_date=eng.microsoft_renewal_date,
        horizon_years=int(eng.modeling_horizon_years or 3),
    )


def _catalog_annual_erp(db: Session, bundle, basis: dict, catalog=None) -> Decimal:
    """Catalog annual ERP for a bundle — delegates to the shared deterministic
    price helper (services/bundles) so the optimizer and every scenario price
    identically: ratified SKU→Bundle rows first, then title match, ranked to
    the engagement's quoting basis (segment × term × billing plan)."""
    return bundles.catalog_annual_erp(db, bundle.name, bundle_id=bundle.id, catalog=catalog, **basis)


def _min_cost_cover(closeable: frozenset[str], options: list[dict]) -> list[dict]:
    """Cheapest subset of add-on options whose combined gap-cover ⊇ `closeable`.

    Exhaustive over the (tiny) set of add-ons that each close at least one gap —
    the "cheapest add-ons that close the outcome gaps" of the recommend-a-path
    composition. Returns the chosen option dicts (empty when nothing to close)."""
    if not closeable:
        return []
    n = len(options)
    best: tuple[Decimal, list[dict]] | None = None
    for mask in range(1 << n):
        covered: set[str] = set()
        price = Decimal("0")
        chosen: list[dict] = []
        for i in range(n):
            if mask & (1 << i):
                covered |= options[i]["cover"]
                price += options[i]["price"]
                chosen.append(options[i])
        if closeable.issubset(covered) and (best is None or price < best[0]):
            best = (price, chosen)
    return best[1] if best else []


def analyze_persona_bundles(
    db: Session, engagement_id: str, persona_id: str, prices: dict | None = None,
    *, headcount: int | None = None, exclude_capped: bool = False,
    follow_own_plan: bool = False,
) -> dict:
    """Recommend a path for this persona: compose each staple base bundle with the
    cheapest add-ons that close its capability gaps, then rank the composed
    options by TCO. Each candidate is a base + gap-closing add-ons (outcomes
    unioned, prices summed) — the same composition the scenario editor applies.

    The seat cap always applies, measured in the future state (TARGET_SCHEMA D27).
    `headcount` analyses part of the persona (the remainder of a split) and
    `exclude_capped` drops every seat-capped plan — together, "the best plan for
    the people over the cap". `follow_own_plan` makes that plan decide each tool
    the way the persona's own chosen plan does (the split's other side, D29).

    A persona in a carve-out family (a parent and its carve-outs) is recommended
    the plan the family's shared tool decision gives it (TARGET_SCHEMA D29); its
    own cheapest plan stays listed, tagged, when it is cheaper and decides
    differently."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise ValueError("Engagement not found")
    persona = db.get(models.Persona, persona_id)
    if persona is None or persona.engagement_id != engagement_id:
        raise ValueError("Persona not found")

    sku_outcomes = _ratified_sku_outcomes(db, engagement_id)  # ref -> {outcome_id}
    tp_outcomes = _ratified_thirdparty_outcomes(db, engagement_id)
    readings = licence_readings(db, eng, sku_outcomes)
    outcome_names = {o.id: o.name for o in eng.outcomes}

    # Base bundles = full bundles (kind='bundle') with coverage; add-ons layer on.
    all_bundles = bundles.list_bundles(db)
    base_bundles = [b for b in all_bundles if b.kind == "bundle" and b.id in sku_outcomes]
    addon_bundles = [b for b in all_bundles if b.kind == "addon" and b.id in sku_outcomes]

    # Required outcomes = what the persona's current Microsoft licenses deliver
    # (the "don't lose capability" baseline used for gap detection). A line may be
    # tagged to several personas; its cost is split across their combined headcount
    # (mirrors the engine's §6.2 allocation).
    hc = {p.id: p.headcount for p in eng.personas}
    persona_lines = [l for l in eng.licenses_in_scope if persona_id in l.persona_ids]
    required: set[str] = set()
    sources: dict[str, list[str]] = {}  # outcome -> why it is required
    current_ms = Decimal("0")
    for line in persona_lines:
        required |= readings[line.id].outcomes
        for oid in readings[line.id].outcomes:
            sources.setdefault(oid, ["licence"])
        line_total = Decimal(line.quantity_assigned) * line.effective_unit_price_annual
        tagged = [pid for pid in line.persona_ids if pid in hc]
        tagged_hc = sum(hc[pid] for pid in tagged)
        share = (Decimal(hc.get(persona_id, 0)) / Decimal(tagged_hc)) if tagged_hc > 0 \
            else Decimal(1) / Decimal(len(tagged) or 1)
        current_ms += line_total * share

    # The tools that apply to this persona: tagged to it, or to no persona (the
    # engine's rule, ENGINE_SPEC 6.3a).
    held_tools = [tp for tp in eng.third_party_products
                  if not tp.persona_ids or persona_id in tp.persona_ids]

    # Everything they have today (MS + this persona's tools) — used to compute the
    # ADDED outcomes a bundle brings that they don't have at all.
    current_capability = set(required)
    for tp in held_tools:
        current_capability |= tp_outcomes.get(tp.id, set())

    # Persona-declared required capabilities (Personas tab) are ALSO required for
    # gap detection, even when no current license delivers them — this is what
    # keeps a persona that needs Desktop Software off a Frontline bundle. Added to
    # `required` only (not `current_capability`), so such a bundle still surfaces
    # it as a newly-added capability.
    for oid in persona.required_outcome_ids:
        if oid in outcome_names:
            required.add(oid)
            sources.setdefault(oid, []).append("requirement")

    third_party = [
        EngThirdParty(
            id=tp.id, name=tp.name, annual_cost=_dec(tp.annual_cost),
            covered_count=tp.covered_count, is_managed=tp.is_managed,
            tooling_pct=_dec(tp.tooling_pct),
            delivered_outcome_ids=frozenset(tp_outcomes.get(tp.id, set())),
            # The tags matter here exactly as they do in the engine: without them
            # the optimizer would credit this persona for a tool a DIFFERENT
            # persona holds (ENGINE_SPEC 6.3a).
            persona_ids=frozenset(tp.persona_ids),
        )
        for tp in eng.third_party_products
    ]
    tp_names = {tp.id: tp.name for tp in eng.third_party_products}

    prices = prices or {}
    basis = bundles.engagement_price_basis(eng)

    price_cache: dict[str, Decimal] = {}
    catalog = None  # bundles.PriceCatalog, read once on the first catalog lookup

    def _price(bundle) -> Decimal:
        nonlocal catalog
        if bundle.id not in price_cache:
            override = prices.get(bundle.name)
            if override is not None:
                price_cache[bundle.id] = Decimal(str(override))
            else:
                if catalog is None:
                    catalog = bundles.PriceCatalog(db)
                price_cache[bundle.id] = _catalog_annual_erp(db, bundle, basis, catalog)
        return price_cache[bundle.id]

    # A tool's outcomes are kept by the tool or by Microsoft (TARGET_SCHEMA D28):
    # it retires only when a plan covers everything it delivers. An add-on joins to
    # retire tools only when its cost is less than their credit — the engine's credit
    # (ENGINE_SPEC 6.3a: this persona's tools, at most the seats they cover).
    n = persona.headcount if headcount is None else headcount
    held_eng = [t for t in third_party
                if (not t.persona_ids or persona_id in t.persona_ids) and t.delivered_outcome_ids]

    def _retired(cover: set[str]) -> list:
        return [t for t in held_eng if t.delivered_outcome_ids <= cover]

    def _credit(tools) -> Decimal:
        return sum((Decimal(min(n, t.covered_count)) * t.per_unit_annual_cost for t in tools),
                   Decimal("0"))

    # Compose each base bundle with the cheapest add-ons that close its gaps. An
    # add-on is applicable to a base when it is eligible for it — à-la-carte add-ons
    # (no eligibility rows) apply to any base; otherwise the base must be in the
    # add-on's AddonEligibility set (e.g. E5 Security → E3). `composition[name]`
    # carries the chosen add-ons back to the UI (and the "Use" apply).
    elig_map = bundles.eligibility_map(db)
    candidates = []
    composition: dict[str, dict] = {}
    kept_tools: dict[str, list[str]] = {}

    def _retiring_addons(cover: set[str], taken: list[str], base_id: str) -> list[dict]:
        """The priced add-ons worth adding to retire tools this composition keeps:
        the set whose tool credit most exceeds its cost (none when nothing pays)."""
        missing = set().union(*[t.delivered_outcome_ids for t in held_eng
                                if not t.delivered_outcome_ids <= cover]) - cover
        opts = []
        for a in addon_bundles:
            if a.id in taken or not bundles.addon_applies(a.id, base_id, elig_map):
                continue
            helps = frozenset(sku_outcomes.get(a.id, set())) & missing
            if helps and _price(a) > 0:
                opts.append({"bundle": a, "cover": helps, "price": _price(a)})
        opts = sorted(opts, key=lambda o: o["price"])[:10]  # exhaustive, kept small
        base_credit = _credit(_retired(cover))
        best: tuple[Decimal, Decimal, list[dict]] = (Decimal("0"), Decimal("0"), [])
        for mask in range(1, 1 << len(opts)):
            pick = [opts[i] for i in range(len(opts)) if mask & (1 << i)]
            cost = sum((o["price"] for o in pick), Decimal("0"))
            c = set(cover).union(*[sku_outcomes.get(o["bundle"].id, set()) for o in pick])
            gain = _credit(_retired(c)) - base_credit - Decimal(n) * cost
            if gain > best[0] or (gain == best[0] and gain > 0 and cost < best[1]):
                best = (gain, cost, pick)
        return best[2]
    for base in base_bundles:
        base_cover = set(sku_outcomes.get(base.id, set()))
        base_price = _price(base)
        gaps = frozenset(required - base_cover)
        options = []
        for a in addon_bundles:
            if not bundles.addon_applies(a.id, base.id, elig_map):
                continue
            cover = frozenset(sku_outcomes.get(a.id, set())) & gaps
            # Only add-ons that close a real gap are worth composing, and only priced
            # ones: an unpriced add-on would join at $0 and make the plan read as
            # cheaper than anything the customer can buy.
            if cover and _price(a) > 0:
                options.append({"bundle": a, "cover": cover, "price": _price(a)})
        closeable = frozenset().union(*[o["cover"] for o in options]) if options else frozenset()
        # An unpriced base stays unpriced: composing priced add-ons onto it would
        # quote the add-ons alone as the plan's price (and could recommend it).
        chosen = _min_cost_cover(closeable, options) if base_price > 0 else []
        cover_now = set(base_cover).union(*[sku_outcomes.get(o["bundle"].id, set()) for o in chosen])
        if base_price > 0:
            chosen = chosen + _retiring_addons(
                cover_now, [o["bundle"].id for o in chosen], base.id)

        composed_cover = set(base_cover)
        addon_total = Decimal("0")
        for o in chosen:
            composed_cover |= sku_outcomes.get(o["bundle"].id, set())
            addon_total += o["price"]
        retired_ids = {t.id for t in _retired(composed_cover)}
        chosen_meta = []
        for o in chosen:
            # The tools this add-on is what retires: retired with it, not without it.
            without = set(base_cover).union(*[sku_outcomes.get(x["bundle"].id, set())
                                              for x in chosen if x is not o])
            retires = sorted(t.name for t in _retired(composed_cover)
                             if t.id not in {r.id for r in _retired(without)})
            chosen_meta.append({
                "bundle_id": o["bundle"].id,
                "name": o["bundle"].name,
                "unit_price_annual": float(o["price"]),
                "closes": [outcome_names.get(x, x) for x in sorted(o["cover"])],
                "retires": retires,
            })
        kept_tools[base.name] = sorted(t.name for t in held_eng if t.id not in retired_ids)
        candidates.append(
            CandidateBundle(
                sku_reference=base.name,  # the bundle name is what a scenario targets
                covered_outcome_ids=frozenset(composed_cover),
                target_unit_price_annual=base_price + addon_total,
            )
        )
        composition[base.name] = {
            "base_bundle_id": base.id,
            "base_price_annual": float(base_price),
            "addons": chosen_meta,
            "addon_total_annual": float(addon_total),
        }

    # Seat cap (D27): tell the optimizer how many seats each capped family (e.g.
    # M365 Business ≤ 300) has left in the future state of everyone else. With none
    # left the family isn't recommended; with too few, the recommendation stands and
    # the persona is offered a split (limits.seat_cap_split).
    cap_headroom_by_reference: dict[str, int] = {}
    seat_caps = limits.seat_cap_context(db, engagement_id, exclude_persona_id=persona_id)
    capped_refs: set[str] = set()
    if exclude_capped:
        capped_refs = {ref for cap in seat_caps for ref in cap["member_references"]}
        candidates = [c for c in candidates if c.sku_reference not in capped_refs]
    else:
        for cap in seat_caps:
            for ref in cap["member_references"]:
                # If a reference is capped by more than one limit, the tightest wins.
                cap_headroom_by_reference[ref] = min(
                    cap["headroom"], cap_headroom_by_reference.get(ref, cap["headroom"])
                )

    if headcount is not None and persona.headcount:
        current_ms = current_ms * Decimal(n) / Decimal(persona.headcount)
    analyses = analyze_bundles(
        n, current_ms, frozenset(required),
        frozenset(current_capability), candidates, third_party,
        cap_headroom_by_reference=cap_headroom_by_reference,
        persona_id=persona.id,
    )

    # ---- Carve-out family (TARGET_SCHEMA D29) ---------------------------------
    def _tool_credit(tools, seats: int) -> Decimal:
        return sum((Decimal(min(seats, t.covered_count)) * t.per_unit_annual_cost for t in tools),
                   Decimal("0"))

    def _plan_fitting(pid: str, seats: int, req: set[str], held: list, want: set[str],
                      caps: dict[str, int], excluded: set[str]) -> dict | None:
        """The cheapest priced, gap-free, not-cap-limited plan for this member that
        retires exactly the tools in `want` (of those it holds): each base plus the
        cheapest add-ons covering its required gaps and every outcome of `want`."""
        need = set(req).union(*[t.delivered_outcome_ids for t in held if t.id in want])
        best = None
        for base in base_bundles:
            base_price = _price(base)
            headroom = caps.get(base.name)
            if base_price <= 0 or base.name in excluded or (headroom is not None and headroom <= 0):
                continue
            base_cover = set(sku_outcomes.get(base.id, set()))
            missing = frozenset(need - base_cover)
            opts = []
            for a in addon_bundles:
                if not bundles.addon_applies(a.id, base.id, elig_map):
                    continue
                helps = frozenset(sku_outcomes.get(a.id, set())) & missing
                if helps and _price(a) > 0:
                    opts.append({"bundle": a, "cover": helps, "price": _price(a)})
            if not missing <= frozenset().union(*[o["cover"] for o in opts]):
                continue
            chosen = _min_cost_cover(missing, opts)
            cover = base_cover.union(*[sku_outcomes.get(o["bundle"].id, set()) for o in chosen])
            retired = {t.id for t in held if t.delivered_outcome_ids <= cover}
            if retired != want:
                continue  # covers another tool incidentally: a different decision
            price = base_price + sum((o["price"] for o in chosen), Decimal("0"))
            cost = Decimal(seats) * price - _tool_credit([t for t in held if t.id in retired], seats)
            if best is None or (cost, price) < (best["cost"], best["price"]):
                best = {"base": base, "base_price": base_price, "addons": chosen, "price": price,
                        "cover": cover, "base_cover": base_cover, "retired": retired, "cost": cost}
        return best

    def _scenario_cover(pid: str) -> set[str] | None:
        """What a persona's chosen plan (base + add-ons) covers; None with no plan."""
        sc = db.execute(select(models.PersonaScenario).where(
            models.PersonaScenario.persona_id == pid)).scalars().first()
        if sc is None:
            return None
        cover = set(sku_outcomes.get(_cover_key(db, sc.target_sku_reference), set()))
        for addon in sc.addons:
            cover |= sku_outcomes.get(addon.bundle_id, set())
        return cover

    def _family_summary(members, retires: set[str], fallback: bool, basis: str) -> dict:
        held_ids = {t.id for m in members for t in third_party
                    if (not t.persona_ids or m.id in t.persona_ids) and t.delivered_outcome_ids}
        return {
            "basis": basis,  # "family": decided with the carve-outs; "split": follows its own plan
            "members": [{"id": m.id, "name": m.name, "headcount": m.headcount} for m in members],
            "retires": sorted(tp_names.get(i, i) for i in retires),
            "keeps": sorted(tp_names.get(i, i) for i in held_ids - retires),
            # No plan fits the shared decision for every member: each is on its own (D28).
            "fallback": fallback,
        }

    def _apply_family_plan(rows: list[dict], plan: dict) -> None:
        """Make `plan` this persona's recommendation, tagged as the family's choice;
        tag the persona's own cheaper plan when it decides a tool differently."""
        cand = CandidateBundle(sku_reference=plan["base"].name,
                               covered_outcome_ids=frozenset(plan["cover"]),
                               target_unit_price_annual=plan["price"])
        [b] = analyze_bundles(n, current_ms, frozenset(required), frozenset(current_capability),
                              [cand], third_party,
                              cap_headroom_by_reference=cap_headroom_by_reference,
                              persona_id=persona.id)
        addons = []
        for o in plan["addons"]:
            without = plan["base_cover"].union(*[sku_outcomes.get(x["bundle"].id, set())
                                                 for x in plan["addons"] if x is not o])
            addons.append({
                "bundle_id": o["bundle"].id, "name": o["bundle"].name,
                "unit_price_annual": float(o["price"]),
                "closes": [outcome_names.get(x, x) for x in sorted(o["cover"] & required)],
                "retires": sorted(t.name for t in _retired(plan["cover"])
                                  if t.id not in {r.id for r in _retired(without)}),
            })
        comp = {"base_bundle_id": plan["base"].id, "base_price_annual": float(plan["base_price"]),
                "addons": addons,
                "addon_total_annual": float(sum((o["price"] for o in plan["addons"]), Decimal("0")))}
        kept = sorted(t.name for t in held_eng if t.id not in plan["retired"])
        alone = next((r for r in rows if r["recommended"]), None)
        ids = sorted(a["bundle_id"] for a in addons)
        fam = next((r for r in rows if r["sku_reference"] == plan["base"].name
                    and sorted(a["bundle_id"] for a in r["addons"]) == ids), None)
        if fam is None:
            fam = _row(b, comp, kept)
            rows.append(fam)
        for r in rows:
            r["recommended"] = False
        fam["recommended"] = fam["family_choice"] = True
        if alone is not None and alone is not fam and alone["delta_annual"] < fam["delta_annual"]:
            alone["cheaper_alone"] = True
            alone["alone_saves_annual"] = round(fam["delta_annual"] - alone["delta_annual"], 2)
        rows.sort(key=lambda r: (not r["family_choice"], not r["cheaper_alone"]))

    def _family_decision(rows: list[dict]) -> dict | None:
        """D29: the family's tools retire for every member or none, whichever costs
        the family less (ties retire more); this persona gets its plan under it."""
        root = persona if persona.parent_persona_id is None else db.get(models.Persona, persona.parent_persona_id)
        if root is None:
            return None
        members = [root] + [p for p in eng.personas if p.parent_persona_id == root.id]
        if len(members) < 2:
            return None
        ctx = {}
        for m in members:
            if m.id == persona.id:
                ctx[m.id] = (n, required, held_eng, cap_headroom_by_reference)
                continue
            lines = [l for l in eng.licenses_in_scope if m.id in l.persona_ids]
            req = set().union(*[readings[l.id].outcomes for l in lines])
            req |= {oid for oid in m.required_outcome_ids if oid in outcome_names}
            held = [t for t in third_party
                    if (not t.persona_ids or m.id in t.persona_ids) and t.delivered_outcome_ids]
            caps: dict[str, int] = {}
            for cap in limits.seat_cap_context(db, engagement_id, exclude_persona_id=m.id):
                for ref in cap["member_references"]:
                    caps[ref] = min(cap["headroom"], caps.get(ref, cap["headroom"]))
            ctx[m.id] = (m.headcount, req, held, caps)
        # Only a tool some priced plan could cover can retire; the rest are kept.
        sellable = set().union(*[sku_outcomes.get(b.id, set()) for b in base_bundles + addon_bundles
                                 if _price(b) > 0])
        tools = sorted({t.id for c in ctx.values() for t in c[2] if t.delivered_outcome_ids <= sellable})
        best = None
        if len(tools) <= 12:  # exhaustive; a family rarely holds more than a few tools
            for mask in range(1 << len(tools)):
                retire = {tools[i] for i in range(len(tools)) if mask & (1 << i)}
                plans, total = {}, Decimal("0")
                for m in members:
                    seats, req, held, caps = ctx[m.id]
                    plan = _plan_fitting(m.id, seats, req, held, retire & {t.id for t in held},
                                         caps, capped_refs)
                    if plan is None:
                        break
                    plans[m.id], total = plan, total + plan["cost"]
                else:
                    if best is None or (total, -len(retire)) < (best[0], -len(best[1])):
                        best = (total, retire, plans)
        if best is None:
            return _family_summary(members, set(), True, "family")
        _apply_family_plan(rows, best[2][persona.id])
        return _family_summary(members, best[1], False, "family")

    def names(ids):
        return [outcome_names.get(i, i) for i in ids]

    def positioning(b) -> str:
        """The value story to lead with for this bundle. Cost-change convention:
        delta < 0 saves money, delta > 0 costs more."""
        saves = b.delta_annual < 0
        higher = b.delta_annual > 0
        added = bool(b.added_outcome_ids)
        if saves and added:
            return "Lower TCO + new capabilities"
        if saves:
            return "Lower TCO"
        if added:
            return "New capabilities + integrated ecosystem"
        if higher:
            return "Higher cost — consider reimagining required outcomes"
        return "Cost-neutral"

    def _row(b, comp: dict | None = None, kept: list[str] | None = None) -> dict:
        comp = composition.get(b.sku_reference, {}) if comp is None else comp
        return {
            "sku_reference": b.sku_reference,
            "base_price_annual": comp.get("base_price_annual", 0.0),
            "addons": comp.get("addons", []),
            "addon_total_annual": comp.get("addon_total_annual", 0.0),
            "target_unit_price_annual": float(b.target_unit_price_annual),
            "target_spend_annual": float(b.target_spend_annual),
            "current_spend_annual": float(b.current_spend_annual),
            "delta_annual": float(b.delta_annual),
            "third_party_offset_annual": float(b.third_party_offset_annual),
            "covered_required_outcomes": names(b.covered_required_outcome_ids),
            "gap_outcomes": names(b.gap_outcome_ids),
            "added_outcomes": names(b.added_outcome_ids),
            "displaced_products": [tp_names.get(i, i) for i in b.displaced_product_ids],
            # This persona's tools the option leaves in place (they keep costing).
            "kept_products": kept_tools.get(b.sku_reference, []) if kept is None else kept,
            "covers_all_required": b.covers_all_required,
            "price_known": b.price_known,
            "recommended": b.recommended,
            "cap_limited": b.cap_limited,
            "cap_headroom": b.cap_headroom,
            "over_cap_by": b.over_cap_by,
            "positioning": positioning(b),
            # D29: the family's shared tool decision gives this plan; and this
            # persona's own cheaper plan, which decides a tool differently.
            "family_choice": False,
            "cheaper_alone": False,
            "alone_saves_annual": 0.0,
        }

    rows = [_row(b) for b in analyses]
    family = None
    if follow_own_plan:
        own = _scenario_cover(persona.id)
        if own is not None:
            want = {t.id for t in held_eng if t.delivered_outcome_ids <= own}
            plan = _plan_fitting(persona.id, n, required, held_eng, want,
                                 cap_headroom_by_reference, capped_refs)
            family = _family_summary([persona], want, plan is None, "split")
            if plan:
                _apply_family_plan(rows, plan)
    else:
        family = _family_decision(rows)

    return {
        "persona_id": persona.id,
        "persona_name": persona.name,
        "headcount": n,
        "current_microsoft_annual": float(current_ms),
        # Each with why it is required: "licence" and/or "requirement".
        "required_outcomes": [
            {"id": i, "name": outcome_names.get(i, i), "sources": sources.get(i, [])}
            for i in sorted(required, key=lambda x: outcome_names.get(x, x))
        ],
        "bundles": rows,
        # The carve-out family's shared tool decision (D29); None outside a family.
        "family": family,
        # Seat-cap context: how many capped-family seats the rest of the future state
        # uses and how many are left for this persona.
        "seat_caps": [
            {
                "name": c["name"], "cap": c["cap"], "consumed": c["consumed"],
                "headroom": c["headroom"], "member_bundle_names": c["member_bundle_names"],
            }
            for c in seat_caps
        ],
    }


def compute_and_persist(db: Session, engagement_id: str) -> EngineResult:
    """Run the engine and write derived fields back (PRD 5.8/5.9 persistence).

    Operator-owned fields (override, override_reason, residual_intent) are
    preserved; only engine-derived fields are overwritten.
    """
    hydrated = hydrate(db, engagement_id)
    result = engine_compute(hydrated)
    # Every carve-out directly after its parent, for every consumer of the result
    # (services/personas); the engine itself is order-free.
    order = persona_order.rank(db.get(models.Engagement, engagement_id).personas)
    result.scenarios.sort(key=lambda r: order.get(r.persona_id, len(order)))
    if result.rollup.headline is not None:
        # Same for the headline's timed items, within each sub-line.
        items = result.rollup.headline.items
        group = {}
        for i in items:
            group.setdefault(i.sub_line, len(group))
        items.sort(key=lambda i: (group[i.sub_line], order.get(i.persona_id, -1)))

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
        # A classification (intended residual / forced elimination) exists to
        # answer for a RESIDUAL. When natural displacement alone fully
        # eliminates the product, there is no residual left to classify — any
        # stored classification is stale, so clear it automatically rather than
        # showing an override on a row with nothing to override. (If coverage
        # later shrinks and a residual reappears, the classification gate asks
        # again.) This is the one deliberate exception to "operator fields
        # survive recompute".
        naturally_full = dr.displaced_users > 0 and dr.displaced_users >= dr.covered_count
        if naturally_full and (row.override != "None" or row.residual_intent != "None"):
            row.override = "None"
            row.override_reason = ""
            row.residual_intent = "None"
            # Mirror the clear onto this run's result so the response (and the
            # readout rendered from it) never shows the just-cleared override.
            dr.override = Override.NONE
            dr.override_reason = ""
            dr.residual_intent = ResidualIntent.NONE

    db.commit()
    return result


def persona_coverage_gaps(db: Session, engagement_id: str) -> list[dict]:
    """Per persona: the outcomes the PROPOSED target scenario (base bundle +
    add-ons) would deliver that nothing delivers today. "Delivered today" reads
    the existing coverage map: the persona's current Microsoft licensing (its
    bundles' ratified coverage, tagged-or-org-wide lines) plus third parties
    whose ratified coverage applies to the persona. Derived, persists nothing.
    Serves both the Coverage Check step (as gaps to resolve) and the readout's
    New-outcomes section (whatever remains unresolved is genuinely new).

    Alongside the gaps it returns the guards that say when the comparison itself
    cannot be trusted: `unmapped_current_licenses` (cost counted, capability
    invisible), `unmapped_target`/`target_unmapped` (the target contributes no
    ratified coverage, so it can neither add nor drop anything) and
    `org_wide_current_licenses` (untagged lines counted for every persona are
    what make the target look redundant)."""
    eng = db.get(models.Engagement, engagement_id)
    sku_outcomes = _ratified_sku_outcomes(db, engagement_id)
    tp_outcomes = _ratified_thirdparty_outcomes(db, engagement_id)
    readings = licence_readings(db, eng, sku_outcomes)
    name_by_id = {o.id: o.name for o in eng.outcomes}
    desc_by_id = {o.id: o.description or "" for o in eng.outcomes}

    # Outcomes each persona's proposed scenario (base target + add-ons) delivers,
    # plus the references it draws that coverage from. Keeping the references is
    # what lets an UNMAPPED target (a SKU with no ratified coverage here) be
    # reported as a data gap instead of reading as "delivers nothing" — which
    # would silently empty the New-outcomes story and invent a full-capability drop.
    bundle_name = {b.id: b.name for b in bundles.list_bundles(db)}
    target_by_persona: dict[str, set[str]] = {}
    target_refs_by_persona: dict[str, list[dict]] = {}
    for s in eng.scenarios:
        refs = [{
            "reference": s.target_sku_reference or "",
            "key": _cover_key(db, s.target_sku_reference),
            # True: a known bundle whose coverage is missing here (fix in the
            # Coverage map). False: the SKU matches no bundle at all (map it in
            # Settings -> Staple bundles). Add-ons are bundle-keyed by construction.
            "resolves_to_bundle": bundles.resolve_bundle(db, s.target_sku_reference) is not None,
        }]
        refs += [
            {"reference": bundle_name.get(a.bundle_id, a.bundle_id),
             "key": a.bundle_id, "resolves_to_bundle": True}
            for a in s.addons
        ]
        covered: set[str] = set()
        for r in refs:
            covered |= sku_outcomes.get(r["key"], set())
        target_by_persona[s.persona_id] = covered
        target_refs_by_persona[s.persona_id] = refs

    # The customer's answers about gaps (TARGET_SCHEMA §4.8): CoveredOutsideInventory
    # counts as delivered today; NotDeliveredToday confirms a gap; no answer leaves
    # it unconfirmed (never claimed as new, D21).
    answers = {
        (a.persona_id, a.outcome_id): a for a in eng.gap_answers
    }

    def _outcome_dicts(ids, pid=None):
        out = []
        for oid in ids:
            d = {"id": oid, "name": name_by_id.get(oid, oid),
                 "description": desc_by_id.get(oid, "")}
            if pid is not None:
                a = answers.get((pid, oid))
                d["answer"] = a.answer if a else None
                d["answer_id"] = a.id if a else None
            out.append(d)
        return out

    personas = []
    for p in persona_order.ordered(eng.personas):
        target_outcomes = target_by_persona.get(p.id, set())
        # Current Microsoft licensing that applies to this persona (tagged to it, or
        # untagged = org-wide). A persona can hold SEVERAL current licenses (a
        # many-to-one relationship); its delivered capability is the UNION of all of
        # them. We also record any line whose SKU resolves to NO mapped capability —
        # its outcomes are invisible to the comparison, so a smaller target can
        # silently drop it. That is the multi-license trap: e.g. a persona on
        # "Office 365 E3" + "Enterprise Mobility + Security E3" where EMS maps to no
        # bundle looks fully covered by "Office 365 E3" alone, hiding the EMS loss.
        # Tagged and untagged (org-wide) coverage is tracked separately: an untagged
        # line counts for EVERY persona, which is deliberately conservative but can
        # make a persona look like it already holds what the target adds. Knowing
        # which coverage came from an untagged line is what lets the readout say so.
        ms_tagged: set[str] = set()
        ms_org_wide: set[str] = set()
        unmapped: list[dict] = []
        org_wide_by_ref: dict[str, set[str]] = {}
        seen_refs: set[str] = set()
        for lic in eng.licenses_in_scope:
            if lic.persona_ids and p.id not in lic.persona_ids:
                continue
            reading = readings[lic.id]
            outs = set(reading.outcomes)
            ref = lic.sku_reference or ""
            if lic.persona_ids:
                ms_tagged |= outs
            else:
                ms_org_wide |= outs
                if outs and ref:
                    org_wide_by_ref.setdefault(ref, set()).update(outs)
            # Once per licence name, compared as name resolution compares it.
            if reading.state == "unread" and bundles.normalize_alias(ref) not in seen_refs:
                seen_refs.add(bundles.normalize_alias(ref))
                unmapped.append({
                    "sku_reference": ref or "A licence line",
                    # True: the line names (or is linked to) a library plan that
                    # this engagement has no coverage for — "use the library's
                    # list". False: the library doesn't know it. Either way it is
                    # answered on the unknown-licence card (Other tools).
                    "resolves_to_bundle": reading.bundle_id is not None,
                })
        ms_today = ms_tagged | ms_org_wide
        # Third parties per the ratified coverage map: tagged to this persona,
        # or untagged (org-wide).
        tp_today: set[str] = set()
        for t in eng.third_party_products:
            if t.persona_ids and p.id not in t.persona_ids:
                continue
            tp_today |= tp_outcomes.get(t.id, set())
        outside = {
            oid for (pid, oid), a in answers.items()
            if pid == p.id and a.answer == "CoveredOutsideInventory"
        }
        covered_today = ms_today | tp_today | outside
        uncovered = sorted(target_outcomes - covered_today)
        confirmed = [o for o in uncovered
                     if (a := answers.get((p.id, o))) and a.answer == "NotDeliveredToday"]
        unconfirmed = [o for o in uncovered if (p.id, o) not in answers]
        # The target references that contribute NO ratified coverage. With all of
        # them unmapped there is nothing to compare against, so the capability
        # story is a data gap, not a finding.
        target_refs = target_refs_by_persona.get(p.id, [])
        unmapped_target = [
            {"reference": r["reference"], "resolves_to_bundle": r["resolves_to_bundle"]}
            for r in target_refs if not sku_outcomes.get(r["key"]) and r["reference"]
        ]
        target_unmapped = bool(target_refs) and not target_outcomes
        # Capability the move would DROP: outcomes the persona's current Microsoft
        # licensing delivers today that the proposed target won't — the reverse of
        # `uncovered`. Surfaced so a downgrade is a confirmed choice, never a silent
        # loss. Only meaningful once a target scenario exists AND that target has
        # ratified coverage — an unmapped target would otherwise "drop" everything.
        dropped = (
            sorted(ms_today - target_outcomes)
            if p.id in target_by_persona and not target_unmapped else []
        )
        # Which untagged (org-wide) lines actually decide the "nothing new" verdict:
        # the lines covering target outcomes that NOTHING tagged to this persona
        # covers. These are the lines to persona-tag before the value story can be
        # trusted — an untagged line that changes no verdict is not named.
        decisive = (target_outcomes & ms_org_wide) - ms_tagged - tp_today
        org_wide_decisive = sorted(
            ref for ref, outs in org_wide_by_ref.items() if outs & decisive
        )
        personas.append({
            "persona_id": p.id,
            "persona_name": p.name,
            "headcount": p.headcount,
            "has_scenario": p.id in target_by_persona,
            "target_outcome_count": len(target_outcomes),
            "covered_of_target": len(target_outcomes & covered_today),
            # Every gap the move would fill, each with the customer's answer.
            "uncovered_outcomes": _outcome_dicts(uncovered, p.id),
            # ...split: confirmed gaps are new outcomes; unanswered ones are not
            # claimed until the customer confirms nothing delivers them today.
            "confirmed_new_outcomes": _outcome_dicts(confirmed),
            "unconfirmed_outcomes": _outcome_dicts(unconfirmed),
            "covered_outside_outcomes": _outcome_dicts(sorted(outside & target_outcomes), p.id),
            # Honesty guards for a target that delivers LESS than today (below).
            "dropped_outcomes": _outcome_dicts(dropped),
            "unmapped_current_licenses": unmapped,
            # Honesty guards for a comparison that cannot be trusted: a target with
            # no ratified coverage, and current coverage attributed org-wide.
            "unmapped_target": unmapped_target,
            "target_unmapped": target_unmapped,
            "org_wide_current_licenses": org_wide_decisive,
        })
    return personas


def _no_new_capability_reason(gap: dict) -> tuple[str, str]:
    """Why an in-scope persona gained NOTHING — a code plus the sentence every
    surface prints. An empty New-outcomes list has three very different meanings
    (unmappable target, coverage attributed org-wide, genuinely nothing new) and
    printing none of them is how the section silently disappears."""
    if gap["target_unmapped"]:
        refs = ", ".join(r["reference"] for r in gap["unmapped_target"]) or "the target"
        fix = (
            "add its outcomes in the Coverage map"
            if all(r["resolves_to_bundle"] for r in gap["unmapped_target"])
            else "map the SKU to a bundle in Settings → Staple bundles, then map its outcomes"
        )
        return "target_unmapped", (
            f"No capability comparison is possible: the target ({refs}) has no ratified "
            f"coverage in this engagement, so nothing can be shown as gained or given up "
            f"here — {fix}."
        )
    if gap["org_wide_current_licenses"]:
        refs = ", ".join(gap["org_wide_current_licenses"])
        return "covered_org_wide", (
            "Nothing new: everything the target delivers already counts as delivered today "
            f"— partly from current licence line(s) ({refs}) carrying no persona tag, so they "
            "count for every persona. Tag them on the Current licensing tab for a per-persona "
            "value story."
        )
    return "covered_today", (
        "Nothing new: the target delivers no outcome this persona's current licensing "
        "(and its mapped third-party tools) does not already deliver."
    )


def new_outcomes(db: Session, engagement_id: str, result: dict) -> list[dict]:
    """The readout's New-outcomes story: per IN-SCOPE persona, the outcomes the
    move lights up that nothing they hold today delivers. EVERY in-scope persona
    with a target is listed — one with nothing new carries `empty_reason` /
    `empty_reason_text` saying why (unmappable target, coverage attributed
    org-wide, or genuinely nothing new). Silence is not an answer: an omitted
    persona is indistinguishable from a lost section, and the three reasons mean
    very different things (two are data to fix, one is a real finding)."""
    in_scope = {s["persona_id"] for s in result.get("scenarios", []) if s.get("in_scope")}
    out = []
    for g in persona_coverage_gaps(db, engagement_id):
        if not g["has_scenario"] or g["persona_id"] not in in_scope:
            continue
        # Only a gap the customer confirmed nothing delivers today is a new outcome
        # (D21); unanswered gaps are listed as awaiting confirmation, never claimed.
        confirmed = g["confirmed_new_outcomes"]
        unconfirmed = g["unconfirmed_outcomes"]
        if g["unmapped_current_licenses"]:
            # D23: a licence nobody can read leaves this group's capability changes
            # out — what it delivers today is unknown, so a "new" capability may be
            # one it already has (its money still counts).
            confirmed, unconfirmed = [], []
            refs = ", ".join(u["sku_reference"] for u in g["unmapped_current_licenses"])
            reason, reason_text = "licence_unread", (
                f"Capability changes are left out: what {refs} includes isn't answered "
                f"yet, so nothing can be shown as gained or given up. Answer it on Other tools."
            )
        elif confirmed:
            reason, reason_text = None, ""
        elif unconfirmed:
            n = len(unconfirmed)
            reason, reason_text = "awaiting_confirmation", (
                f"Not yet claimed: the move adds {n} capabilit{'y' if n == 1 else 'ies'} "
                f"nothing in the inventory delivers today, but the customer has not "
                f"confirmed {'it is' if n == 1 else 'they are'} not covered some other "
                f"way. Answer {'it' if n == 1 else 'them'} in Coverage Check."
            )
        else:
            reason, reason_text = _no_new_capability_reason(g)
        out.append({
            "persona_id": g["persona_id"],
            "persona_name": g["persona_name"],
            "headcount": g["headcount"],
            "outcomes": confirmed,
            "unconfirmed_outcomes": unconfirmed,
            "empty_reason": reason,
            "empty_reason_text": reason_text,
        })
    return out


def dropped_capability(db: Session, engagement_id: str, result: dict) -> list[dict]:
    """The readout's capability-trade-off story — the mirror of `new_outcomes`: per
    IN-SCOPE persona, the outcomes their CURRENT Microsoft licensing delivers today
    that the proposed target will NOT. A right-sizing move can legitimately drop
    capability (that is often the point), so the saved-dollars headline stays — but
    it must never read as a free win. Surfacing the drop reconciles the number with
    what the customer gives up. Personas with no drop are omitted, as is a target
    with no ratified coverage at all (nothing to compare — that is a data gap,
    reported as the New-outcomes reason, not a capability loss)."""
    in_scope = {s["persona_id"] for s in result.get("scenarios", []) if s.get("in_scope")}
    return [
        {
            "persona_id": g["persona_id"],
            "persona_name": g["persona_name"],
            "headcount": g["headcount"],
            "outcomes": g["dropped_outcomes"],
        }
        for g in persona_coverage_gaps(db, engagement_id)
        if g["has_scenario"] and g["persona_id"] in in_scope and g["dropped_outcomes"]
        # A licence nobody can read leaves the group's capability changes out (D23).
        and not g["unmapped_current_licenses"]
    ]


def attach_target_labels(db: Session, engagement_id: str, result: dict) -> dict:
    """Enrich each scenario dict with the COMPOSED target name — base bundle **+**
    add-ons ("Office 365 E3 + Enterprise Mobility + Security E3") — so every display
    shows the whole target, not just the base. The engine composes coverage and
    price from base + add-ons but carries only the base name (`target_sku_reference`);
    the add-on names live on the DB scenario. A Business-Premium-swapped scenario is
    substituted wholesale (its engine target differs from the DB base), so its own
    add-ons don't apply — it's labelled by the swapped target alone. In place; also
    returned for convenience."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return result
    bundle_name = {b.id: b.name for b in bundles.list_bundles(db)}
    addons_by_persona = {
        s.persona_id: [bundle_name.get(a.bundle_id, a.bundle_id) for a in s.addons]
        for s in eng.scenarios
    }
    base_by_persona = {s.persona_id: s.target_sku_reference for s in eng.scenarios}
    for sr in result.get("scenarios", []) or []:
        pid = sr.get("persona_id")
        swapped = pid in base_by_persona and sr.get("target_sku_reference") != base_by_persona[pid]
        addons = [] if swapped else addons_by_persona.get(pid, [])
        sr["target_addons"] = addons
        sr["target_label"] = (sr.get("target_sku_reference") or "") + "".join(f" + {a}" for a in addons)
    return result
