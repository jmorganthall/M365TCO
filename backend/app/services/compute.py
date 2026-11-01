"""Hydration bridge: ORM rows -> pure engine inputs -> persisted dispositions.

This is the only place that knows both the database and the engine. It enforces
the ratified-only rule (PRD 6.6 / 5.7): unratified AI suggestions never reach the
hydrated coverage sets, so they can never feed the math.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from tco_engine import (
    CandidateBundle,
    Engagement as EngEngagement,
    analyze_bundles,
)
from tco_engine.interpret import to_engine
from tco_engine.rules import applies_to, covers_tool

from .. import models
from . import bundles, limits, raw


def hydrate(db: Session, engagement_id: str) -> EngEngagement:
    """The engine's input for an engagement: its raw data loaded (services/raw) and
    read through the rules (tco_engine.interpret). Reads only."""
    return to_engine(raw.interpreted(db, engagement_id))


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

    # Everything about this engagement's data is read through the interpreter
    # (tco_engine.interpret): what each persona holds and needs, its cost share,
    # its tools and their costs, what a plan covers. This function only composes
    # and prices candidate plans from the catalog.
    interp = raw.interpreted(db, engagement_id)
    sku_outcomes = {k: set(v) for k, v in interp.raw.microsoft_coverage.items()}  # ref -> {outcome_id}
    outcome_names = {o.id: o.name for o in eng.outcomes}

    # Base bundles = full bundles (kind='bundle') with coverage; add-ons layer on.
    all_bundles = bundles.list_bundles(db)
    base_bundles = [b for b in all_bundles if b.kind == "bundle" and b.id in sku_outcomes]
    addon_bundles = [b for b in all_bundles if b.kind == "addon" and b.id in sku_outcomes]

    # Required outcomes = what the persona's current Microsoft licences deliver (the
    # "don't lose capability" baseline), and its cost today = its share of those
    # lines — both as the interpreter reads them (lines tagged to it or Org-wide).
    my_lines = interp.lines_for(persona_id)
    required: set[str] = set()
    sources: dict[str, list[str]] = {}  # outcome -> why it is required
    current_ms = Decimal("0")
    for line in my_lines:
        required |= line.outcomes
        for oid in line.outcomes:
            sources.setdefault(oid, ["licence"])
        current_ms += interp.licence_shares.get((line.id, persona_id), Decimal("0"))

    # Everything they have today (MS + this persona's tools) — used to compute the
    # ADDED outcomes a bundle brings that they don't have at all.
    current_capability = set(required)
    for t in interp.tools_for(persona_id):
        current_capability |= t.delivered_outcome_ids

    # Persona-declared required capabilities (Personas tab) are ALSO required for
    # gap detection, even when no current license delivers them — this is what
    # keeps a persona that needs Desktop Software off a Frontline bundle. Added to
    # `required` only (not `current_capability`), so such a bundle still surfaces
    # it as a newly-added capability.
    for oid in persona.required_outcome_ids:
        if oid in outcome_names:
            required.add(oid)
            sources.setdefault(oid, []).append("requirement")

    # The tools exactly as the engine costs them (interpreted cost, covers, tags):
    # a recommendation is a promise about what the engine will compute.
    third_party = to_engine(interp).third_party_products
    tp_names = {t.id: t.name for t in interp.tools}

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
                if applies_to(t.persona_ids, persona_id) and t.delivered_outcome_ids]

    def _retired(cover: set[str]) -> list:
        return [t for t in held_eng if covers_tool(t.delivered_outcome_ids, cover)]

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
    # the persona is offered a split (seat_cap.split_offer).
    cap_headroom_by_reference: dict[str, int] = {}
    seat_caps = limits.seat_cap_context(db, engagement_id, exclude_persona_id=persona_id,
                                        interpreted=interp)
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
            retired = {t.id for t in held if covers_tool(t.delivered_outcome_ids, cover)}
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
        sc = next((s for s in interp.scenarios if s.persona_id == pid), None)
        return None if sc is None else set(sc.covered_outcome_ids)

    def _family_summary(members, retires: set[str], fallback: bool, basis: str) -> dict:
        held_ids = {t.id for m in members for t in third_party
                    if applies_to(t.persona_ids, m.id) and t.delivered_outcome_ids}
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
            req = set().union(*[l.outcomes for l in interp.lines_for(m.id)])
            req |= {oid for oid in m.required_outcome_ids if oid in outcome_names}
            held = [t for t in third_party
                    if applies_to(t.persona_ids, m.id) and t.delivered_outcome_ids]
            caps: dict[str, int] = {}
            for cap in limits.seat_cap_context(db, engagement_id, exclude_persona_id=m.id,
                                               interpreted=interp):
                for ref in cap["member_references"]:
                    caps[ref] = min(cap["headroom"], caps.get(ref, cap["headroom"]))
            ctx[m.id] = (m.headcount, req, held, caps)
        # Only a tool some priced plan could cover can retire; the rest are kept.
        sellable = set().union(*[sku_outcomes.get(b.id, set()) for b in base_bundles + addon_bundles
                                 if _price(b) > 0])
        tools = sorted({t.id for c in ctx.values() for t in c[2]
                        if covers_tool(t.delivered_outcome_ids, sellable)})
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
                "key": c["key"], "name": c["name"], "cap": c["cap"], "consumed": c["consumed"],
                "headroom": c["headroom"], "member_bundle_names": c["member_bundle_names"],
                # The people beyond the seats left, if this persona took a plan under
                # the cap — what a split would move (0 when it fits or none are left).
                "over_by": max(0, n - c["headroom"]) if c["headroom"] > 0 else 0,
            }
            for c in seat_caps
        ],
    }


def persona_coverage_gaps(db: Session, engagement_id: str) -> list[dict]:
    """Per persona: the outcomes the PROPOSED plan (base + add-ons) would deliver that
    nothing delivers today, and what it would drop — read from the interpreter's
    persona × outcome tables (tco_engine.interpret, TARGET_SCHEMA §6.3). This service
    only adds what a screen shows: outcome names, the customer's answer ids, add-on
    names. Serves the Coverage Check, the readout's New outcomes and Review.

    Alongside the gaps it returns the guards that say when the comparison itself
    cannot be trusted: `unmapped_current_licenses` (cost counted, capability
    invisible), `unmapped_target`/`target_unmapped` (the plan contributes no
    ratified coverage, so it can neither add nor drop anything) and
    `org_wide_current_licenses` (untagged lines counted for every persona are what
    make the plan look redundant). Reads only."""
    eng = db.get(models.Engagement, engagement_id)
    i = raw.interpreted(db, engagement_id)
    coverage = i.raw.microsoft_coverage
    raw_lines = {l.id: l for l in i.raw.licence_lines}
    name_by_id = {o.id: o.name for o in eng.outcomes}
    desc_by_id = {o.id: o.description or "" for o in eng.outcomes}
    # The library key travels with each outcome so a frozen snapshot can still be
    # matched across engagements (the Workshop value ranking, TARGET_SCHEMA §7.2).
    seed_key_by_id = {o.id: o.seed_key for o in eng.outcomes}
    bundle_name = {b.id: b.name for b in bundles.list_bundles(db)}
    answers = {(a.persona_id, a.outcome_id): a for a in eng.gap_answers}
    plan_of = {s.persona_id: s for s in i.scenarios}

    def _outcome_dicts(ids, pid=None):
        out = []
        for oid in ids:
            d = {"id": oid, "name": name_by_id.get(oid, oid),
                 "description": desc_by_id.get(oid, ""),
                 "seed_key": seed_key_by_id.get(oid)}
            if pid is not None:
                a = answers.get((pid, oid))
                d["answer"] = a.answer if a else None
                d["answer_id"] = a.id if a else None
            out.append(d)
        return out

    personas = []
    for p in i.personas:
        plan = plan_of.get(p.id)
        target_outcomes = set(plan.covered_outcome_ids) if plan else set()
        # The references the plan draws its coverage from: the base (by the plan it
        # resolves to, else its typed name) and each add-on.
        refs = []
        if plan is not None:
            refs.append({"reference": plan.target_sku_reference or "",
                         "covers": coverage.get(plan.base_bundle_id or (plan.target_sku_reference or ""),
                                                frozenset()),
                         "resolves_to_bundle": plan.base_bundle_id is not None})
            refs += [{"reference": bundle_name.get(b, b), "covers": coverage.get(b, frozenset()),
                      "resolves_to_bundle": True} for b in plan.addon_bundle_ids]

        today = {t.outcome_id: t for t in i.today if t.persona_id == p.id}
        covered_today = {o for o, t in today.items() if t.delivered_today}
        # Microsoft coverage today, split by whether it came from a line tagged to
        # this persona or an untagged (Org-wide) line — the readout names the
        # untagged lines that decide a "nothing new" verdict.
        ms_tagged: set[str] = set()
        ms_org_wide: set[str] = set()
        org_wide_by_ref: dict[str, set[str]] = {}
        unmapped: list[dict] = []
        seen_refs: set[str] = set()
        for line in i.lines_for(p.id):
            ref = raw_lines[line.id].sku_reference or ""
            if line.assumed:
                ms_org_wide |= line.outcomes
                if line.outcomes and ref:
                    org_wide_by_ref.setdefault(ref, set()).update(line.outcomes)
            else:
                ms_tagged |= line.outcomes
            if line.read_state == "unread" and bundles.normalize_alias(ref) not in seen_refs:
                seen_refs.add(bundles.normalize_alias(ref))
                unmapped.append({"sku_reference": ref or "A licence line",
                                 "resolves_to_bundle": line.bundle_id is not None})
        tp_today = {o for o, t in today.items() if t.by_tool}
        outside = {o for o, t in today.items() if t.by_outside}

        uncovered = sorted(target_outcomes - covered_today)
        confirmed = [o for o in uncovered
                     if (a := answers.get((p.id, o))) and a.answer == "NotDeliveredToday"]
        unconfirmed = [o for o in uncovered if (p.id, o) not in answers]
        unmapped_target = [{"reference": r["reference"], "resolves_to_bundle": r["resolves_to_bundle"]}
                           for r in refs if not r["covers"] and r["reference"]]
        target_unmapped = bool(refs) and not target_outcomes
        ms_today = {o for o, t in today.items() if t.by_microsoft}
        # Capability the move would DROP: Microsoft capability today the plan won't
        # deliver. Only meaningful with a plan whose coverage is known.
        dropped = sorted(ms_today - target_outcomes) if plan and not target_unmapped else []
        decisive = (target_outcomes & ms_org_wide) - ms_tagged - tp_today
        org_wide_decisive = sorted(ref for ref, outs in org_wide_by_ref.items() if outs & decisive)
        personas.append({
            "persona_id": p.id,
            "persona_name": p.name,
            "headcount": p.headcount,
            "has_scenario": plan is not None,
            "target_outcome_count": len(target_outcomes),
            "covered_of_target": len(target_outcomes & covered_today),
            "uncovered_outcomes": _outcome_dicts(uncovered, p.id),
            "confirmed_new_outcomes": _outcome_dicts(confirmed),
            "unconfirmed_outcomes": _outcome_dicts(unconfirmed),
            "covered_outside_outcomes": _outcome_dicts(sorted(outside & target_outcomes), p.id),
            "dropped_outcomes": _outcome_dicts(dropped),
            "unmapped_current_licenses": unmapped,
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
    the add-on names live on the DB scenario. The engine costs exactly the scenario's
    own target (there is no substitution), so the label is always the row's base plus
    its add-ons. In place; also returned for convenience."""
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return result
    bundle_name = {b.id: b.name for b in bundles.list_bundles(db)}
    addons_by_persona = {
        s.persona_id: [bundle_name.get(a.bundle_id, a.bundle_id) for a in s.addons]
        for s in eng.scenarios
    }
    for sr in result.get("scenarios", []) or []:
        addons = addons_by_persona.get(sr.get("persona_id"), [])
        sr["target_addons"] = addons
        sr["target_label"] = (sr.get("target_sku_reference") or "") + "".join(f" + {a}" for a in addons)
    return result
