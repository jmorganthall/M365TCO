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
    CoverageScope,
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
from . import bundles, limits, seeds


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
            # Effective price = the override when the line overrides list, else the
            # catalog list baseline — so an override actually lowers current spend.
            unit_price_paid_annual=lic.effective_unit_price_annual,
            sku_reference=lic.sku_reference,
            persona_ids=tuple(lic.persona_ids),
            # How many seats this line entitles — per-seat purchase vs tenant-wide
            # entitlement. Drives how many seats a duplicate tool can be credited
            # as redundant today (ENGINE_SPEC 6.10).
            coverage_scope=CoverageScope(lic.coverage_scope or "PerUser"),
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
        line_total = Decimal(line.quantity_assigned) * line.effective_unit_price_annual
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
        persona_id=persona.id,
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

    def _outcome_dicts(ids):
        return [
            {"id": oid, "name": name_by_id.get(oid, oid),
             "description": desc_by_id.get(oid, "")}
            for oid in ids
        ]

    personas = []
    for p in eng.personas:
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
        for lic in eng.current_licenses:
            if lic.persona_ids and p.id not in lic.persona_ids:
                continue
            outs = sku_outcomes.get(_cover_key(db, lic.sku_reference), set())
            ref = lic.sku_reference or ""
            if lic.persona_ids:
                ms_tagged |= outs
            else:
                ms_org_wide |= outs
                if outs and ref:
                    org_wide_by_ref.setdefault(ref, set()).update(outs)
            if not outs and ref and ref not in seen_refs:
                seen_refs.add(ref)
                unmapped.append({
                    "sku_reference": ref,
                    # True: the SKU IS a known bundle but has no ratified coverage
                    # here (fix in the Coverage map). False: the SKU matches no
                    # bundle at all (map it in Settings -> Staple bundles).
                    "resolves_to_bundle": bundles.resolve_bundle(db, ref) is not None,
                })
        ms_today = ms_tagged | ms_org_wide
        # Third parties per the ratified coverage map: tagged to this persona,
        # or untagged (org-wide).
        tp_today: set[str] = set()
        for t in eng.third_party_products:
            if t.persona_ids and p.id not in t.persona_ids:
                continue
            tp_today |= tp_outcomes.get(t.id, set())
        covered_today = ms_today | tp_today
        uncovered = sorted(target_outcomes - covered_today)
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
            "uncovered_outcomes": _outcome_dicts(uncovered),
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


def _money(value) -> str:
    """Whole-dollar money in the readout's own convention (unsigned — the sentence
    around it says the direction). Matches `exporter._usd0` so a figure phrased in
    a sentence never disagrees with the same figure in a table."""
    return f"${abs(float(value or 0)):,.0f}"


def _consolidation_story(scenario: dict, *, customer: bool = False) -> str:
    """What a move that adds no NEW capability is still worth, in this persona's own
    numbers: the third-party tools it folds into the target licensing (fewer vendors,
    contracts and audit surfaces) and the annual spend change. Grounded, never
    boilerplate — every clause comes from the computed scenario, and the framing
    follows the facts: consolidation when tools actually retire, right-sizing when
    the spend actually falls, and a plain flag when the move does neither (a claim
    of "consolidation value" next to a cost increase is exactly the kind of sentence
    a customer stops trusting the rest of the readout for)."""
    tools = [
        o["third_party_product_name"]
        for o in scenario.get("offsets", []) or []
        if float(o.get("credited_offset_annual") or 0) > 0
    ]
    delta = float(scenario.get("delta_annual") or 0)
    n = len(tools)
    parts = []
    if tools:
        named = ", ".join(tools[:4]) + (f" and {n - 4} more" if n > 4 else "")
        plural = n != 1
        parts.append(
            f"{n} third-party tool{'s' if plural else ''} ({named}) "
            f"fold{'' if plural else 's'} into the target licensing, so there "
            f"{'are' if plural else 'is'} {n} fewer vendor{'s' if plural else ''}, "
            f"contract{'s' if plural else ''} and integration{'s' if plural else ''} "
            f"to renew, secure and audit"
        )
    if delta < 0:
        parts.append(
            f"the same capability costs {_money(delta)}/yr less"
            if not tools else f"and {_money(delta)}/yr less to run"
        )
    if not tools and delta > 0:
        # No consolidation and no saving: there is no value story to lead with, so
        # don't invent one — say the outcomes are unchanged and the cost is up. The
        # next move ("drop this persona from scope") is the SA's call to make, not a
        # line a customer should read in a document prepared for them.
        return (
            f"No new functional outcomes for this persona, and the target costs "
            f"{_money(delta)}/yr more than what they hold today — one to confirm "
            f"together before it lands in the plan."
            if customer else
            f"No new functional outcomes for this persona, and the target costs "
            f"{_money(delta)}/yr more than what they hold today — worth revisiting the "
            f"target, or keeping this persona out of scope."
        )
    if not parts:
        return (
            "No new functional outcomes, no tools consolidated and no change in spend — "
            "this persona is already on the right licensing; the case for moving them has "
            "to come from standardizing the estate, not from this scenario."
        )
    lead = "consolidation" if tools else "right-sizing"
    tail = (
        f" The move itself adds {_money(delta)}/yr in licensing." if tools and delta > 0 else ""
    )
    return (
        f"No new functional outcomes for this persona — the value here is {lead}, not "
        f"added capability: " + ", ".join(parts) + f".{tail}"
    )


def _no_new_capability_reason(gap: dict, scenario: dict) -> tuple[str, str, str]:
    """Why an in-scope persona gained NOTHING — a code plus two sentences: the
    operator's (with the fix, for the app and the QA spreadsheet) and the
    customer's (for the customer-facing HTML readout, which must never print
    internal instructions). An empty New-outcomes list has three very different
    meanings — an unmappable target and coverage attributed org-wide are data to
    fix, "nothing new" is a real finding — and printing none of them is how the
    section silently disappears.

    "Nothing new" is not a failure of the move and is never phrased as one: a
    consolidation play delivers its value by retiring tools and spend, and the
    honest line is that the functional outcomes stay the same while the vendor,
    contract and audit surface shrinks."""
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
        ), (
            f"Capability comparison for this persona is still being confirmed against the "
            f"target ({refs}) — the cost story below stands on its own."
        )
    if gap["org_wide_current_licenses"]:
        refs = ", ".join(gap["org_wide_current_licenses"])
        return "covered_org_wide", (
            "Nothing new to show: everything the target delivers already counts as delivered "
            f"today — partly from current licence line(s) ({refs}) carrying no persona tag, so "
            "they count for every persona. Tag them on the Current licensing tab for a "
            "per-persona value story."
        ), _consolidation_story(scenario, customer=True)
    return (
        "covered_today",
        _consolidation_story(scenario),
        _consolidation_story(scenario, customer=True),
    )


def new_outcomes(db: Session, engagement_id: str, result: dict) -> list[dict]:
    """The readout's New-outcomes story: per IN-SCOPE persona, the outcomes the
    move lights up that nothing they hold today delivers. EVERY in-scope persona
    with a target is listed — one with nothing new carries `empty_reason` plus
    `empty_reason_text` (operator-facing, with the fix) and
    `empty_reason_customer_text` (what the customer-facing readout prints).
    Silence is not an answer: an omitted persona is indistinguishable from a lost
    section, and the reasons mean very different things (two are data to fix, one
    is a real finding — a consolidation play whose value is the tools and spend it
    retires, stated as such rather than as a shortfall)."""
    in_scope = {s["persona_id"] for s in result.get("scenarios", []) if s.get("in_scope")}
    scenario_by_pid = {
        s["persona_id"]: s for s in result.get("scenarios", []) or [] if s.get("in_scope")
    }
    out = []
    for g in persona_coverage_gaps(db, engagement_id):
        if not g["has_scenario"] or g["persona_id"] not in in_scope:
            continue
        reason, reason_text, customer_text = (
            (None, "", "") if g["uncovered_outcomes"]
            else _no_new_capability_reason(g, scenario_by_pid.get(g["persona_id"], {}))
        )
        out.append({
            "persona_id": g["persona_id"],
            "persona_name": g["persona_name"],
            "headcount": g["headcount"],
            "outcomes": g["uncovered_outcomes"],
            "empty_reason": reason,
            "empty_reason_text": reason_text,
            "empty_reason_customer_text": customer_text,
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
