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
from . import bundles, limits, seeds, swap


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
            # What this existing license already delivers (its bundle's ratified
            # coverage), for quick-win duplicate detection.
            covered_outcome_ids=frozenset(
                sku_outcomes.get(_cover_key(db, lic.sku_reference), set())
            ),
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

    # Compose each scenario's future state = base bundle + add-on bundles: union
    # the covered outcomes, sum the list prices, then apply the discount to yield
    # the net per-seat price the engine consumes. When the engagement's Business
    # Premium swap is active for a scenario (inherited, not opted out, capability-
    # eligible), the effective target is substituted with Business Premium.
    swap_ctx = swap.compute_context(db, eng)
    scenarios = []
    for s in eng.scenarios:
        if swap.applies(eng, swap_ctx, s):
            bp = swap_ctx["bp"]
            covered = set(swap_ctx["bp_covered"])
            list_price = swap_ctx["bp_price"]
            target_ref = bp.name
        else:
            covered = set(sku_outcomes.get(_cover_key(db, s.target_sku_reference), set()))
            list_price = _dec(s.target_unit_price_annual)
            for addon in s.addons:
                covered |= sku_outcomes.get(addon.bundle_id, set())
                list_price += _dec(addon.unit_price_annual)
            target_ref = s.target_sku_reference
        net_price = list_price * (Decimal("1") - _dec(s.target_discount_pct))
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
    )


def _catalog_annual_erp(db: Session, bundle, basis: dict) -> Decimal:
    """Catalog annual ERP for a bundle — delegates to the shared deterministic
    price helper (services/bundles) so the optimizer and the BP swap price
    identically: ratified SKU→Bundle rows first, then title match, ranked to
    the engagement's quoting basis (segment × term × billing plan)."""
    return bundles.catalog_annual_erp(db, bundle.name, bundle_id=bundle.id, **basis)


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
    db: Session, engagement_id: str, persona_id: str, prices: dict | None = None
) -> dict:
    """Recommend a path for this persona: compose each staple base bundle with the
    cheapest add-ons that close its capability gaps, then rank the composed
    options by TCO. Each candidate is a base + gap-closing add-ons (outcomes
    unioned, prices summed) — the same composition the scenario editor applies."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise ValueError("Engagement not found")
    persona = db.get(models.Persona, persona_id)
    if persona is None or persona.engagement_id != engagement_id:
        raise ValueError("Persona not found")

    sku_outcomes = _ratified_sku_outcomes(db, engagement_id)  # ref -> {outcome_id}
    tp_outcomes = _ratified_thirdparty_outcomes(db, engagement_id)
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
    persona_lines = [l for l in eng.current_licenses if persona_id in l.persona_ids]
    required: set[str] = set()
    current_ms = Decimal("0")
    for line in persona_lines:
        required |= sku_outcomes.get(_cover_key(db, line.sku_reference), set())
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

    # Persona-declared required capabilities (Personas tab) are ALSO required for
    # gap detection, even when no current license delivers them — this is what
    # keeps a persona that needs Desktop Software off a Frontline bundle. Added to
    # `required` only (not `current_capability`), so such a bundle still surfaces
    # it as a newly-added capability.
    required |= {oid for oid in persona.required_outcome_ids if oid in outcome_names}

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
    basis = bundles.engagement_price_basis(eng)

    def _price(bundle) -> Decimal:
        override = prices.get(bundle.name)
        return Decimal(str(override)) if override is not None else _catalog_annual_erp(db, bundle, basis)

    # Compose each base bundle with the cheapest add-ons that close its gaps. An
    # add-on is applicable to a base when it is eligible for it — à-la-carte add-ons
    # (no eligibility rows) apply to any base; otherwise the base must be in the
    # add-on's AddonEligibility set (e.g. E5 Security → E3). `composition[name]`
    # carries the chosen add-ons back to the UI (and the "Use" apply).
    elig_map = bundles.eligibility_map(db)
    candidates = []
    composition: dict[str, dict] = {}
    for base in base_bundles:
        base_cover = set(sku_outcomes.get(base.id, set()))
        base_price = _price(base)
        gaps = frozenset(required - base_cover)
        options = []
        for a in addon_bundles:
            if not bundles.addon_applies(a.id, base.id, elig_map):
                continue
            cover = frozenset(sku_outcomes.get(a.id, set())) & gaps
            if cover:  # only add-ons that close a real gap are worth composing
                options.append({"bundle": a, "cover": cover, "price": _price(a)})
        closeable = frozenset().union(*[o["cover"] for o in options]) if options else frozenset()
        chosen = _min_cost_cover(closeable, options)

        composed_cover = set(base_cover)
        addon_total = Decimal("0")
        chosen_meta = []
        for o in chosen:
            composed_cover |= sku_outcomes.get(o["bundle"].id, set())
            addon_total += o["price"]
            chosen_meta.append({
                "bundle_id": o["bundle"].id,
                "name": o["bundle"].name,
                "unit_price_annual": float(o["price"]),
                "closes": [outcome_names.get(x, x) for x in sorted(o["cover"])],
            })
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

    # Seat-cap headroom (opt-in): when the engagement enables the Business seat cap,
    # tell the optimizer how many seats each capped family (e.g. M365 Business ≤ 300)
    # has left AFTER the seats already recommended for OTHER personas + current
    # licenses — so it won't recommend a Business plan this persona can't fully fit.
    cap_headroom_by_reference: dict[str, int] = {}
    seat_caps: list[dict] = []
    if eng.business_cap_enabled:
        seat_caps = limits.seat_cap_context(db, engagement_id, exclude_persona_id=persona_id)
        for cap in seat_caps:
            for ref in cap["member_references"]:
                # If a reference is capped by more than one limit, the tightest wins.
                cap_headroom_by_reference[ref] = min(
                    cap["headroom"], cap_headroom_by_reference.get(ref, cap["headroom"])
                )

    analyses = analyze_bundles(
        persona.headcount, current_ms, frozenset(required),
        frozenset(current_capability), candidates, third_party,
        cap_headroom_by_reference=cap_headroom_by_reference,
    )

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
                "base_price_annual": composition.get(b.sku_reference, {}).get("base_price_annual", 0.0),
                "addons": composition.get(b.sku_reference, {}).get("addons", []),
                "addon_total_annual": composition.get(b.sku_reference, {}).get("addon_total_annual", 0.0),
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
                "cap_limited": b.cap_limited,
                "cap_headroom": b.cap_headroom,
                "positioning": positioning(b),
            }
            for b in analyses
        ],
        # Seat-cap context (empty unless the engagement opted in) so the UI can show
        # how many capped-family seats are already recommended and how many remain.
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
    New-outcomes section (whatever remains unresolved is genuinely new)."""
    eng = db.get(models.Engagement, engagement_id)
    sku_outcomes = _ratified_sku_outcomes(db, engagement_id)
    tp_outcomes = _ratified_thirdparty_outcomes(db, engagement_id)
    name_by_id = {o.id: o.name for o in eng.outcomes}

    # Outcomes each persona's proposed scenario (base target + add-ons) delivers.
    target_by_persona: dict[str, set[str]] = {}
    for s in eng.scenarios:
        covered = set(sku_outcomes.get(_cover_key(db, s.target_sku_reference), set()))
        for addon in s.addons:
            covered |= sku_outcomes.get(addon.bundle_id, set())
        target_by_persona[s.persona_id] = covered

    personas = []
    for p in eng.personas:
        target_outcomes = target_by_persona.get(p.id, set())
        covered_today: set[str] = set()
        # Current Microsoft licensing: lines tagged to this persona, plus
        # untagged (org-wide) lines that apply to everyone.
        for lic in eng.current_licenses:
            if lic.persona_ids and p.id not in lic.persona_ids:
                continue
            covered_today |= sku_outcomes.get(_cover_key(db, lic.sku_reference), set())
        # Third parties per the ratified coverage map: tagged to this persona,
        # or untagged (org-wide).
        for t in eng.third_party_products:
            if t.persona_ids and p.id not in t.persona_ids:
                continue
            covered_today |= tp_outcomes.get(t.id, set())
        uncovered = sorted(target_outcomes - covered_today)
        personas.append({
            "persona_id": p.id,
            "persona_name": p.name,
            "headcount": p.headcount,
            "has_scenario": p.id in target_by_persona,
            "target_outcome_count": len(target_outcomes),
            "covered_of_target": len(target_outcomes & covered_today),
            "uncovered_outcomes": [{"id": oid, "name": name_by_id.get(oid, oid)} for oid in uncovered],
        })
    return personas


def new_outcomes(db: Session, engagement_id: str, result: dict) -> list[dict]:
    """The readout's New-outcomes story: per IN-SCOPE persona, the outcomes the
    move lights up that nothing they hold today delivers. Personas with nothing
    new are omitted (the readout never prints an empty block)."""
    in_scope = {s["persona_id"] for s in result.get("scenarios", []) if s.get("in_scope")}
    return [
        {
            "persona_id": g["persona_id"],
            "persona_name": g["persona_name"],
            "headcount": g["headcount"],
            "outcomes": g["uncovered_outcomes"],
        }
        for g in persona_coverage_gaps(db, engagement_id)
        if g["has_scenario"] and g["persona_id"] in in_scope and g["uncovered_outcomes"]
    ]
