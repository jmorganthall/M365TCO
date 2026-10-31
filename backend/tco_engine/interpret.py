"""The interpreter: raw data read through the rules (TARGET_SCHEMA §6, ENGINE_SPEC
"Interpretation").

`interpret(raw) -> Interpreted` is a pure, framework-free function. It takes a plain
snapshot of what people entered (`RawEngagement`, loaded by the data layer) plus the
catalog and library facts the data layer resolved for it, and returns every derived
input the engine, the screens and the exports need — each defined once, here:

- how each licence line is read, what it costs per seat and where that price came
  from, and who it applies to (untagged = Org-wide, every persona, `assumed`);
- each line's and each tool's cost share per persona, by headcount;
- each tool's annual cost, effective tooling %, effective cost, people covered and
  cost per person;
- each scenario's composed target: base + add-ons, list, net, covered outcomes.

`to_engine(interpreted)` adapts the result to the engine's input dataclasses. The
engine's math is unchanged; it now reads values defined here instead of values the
data layer derived (and, for tools, stored on the raw row) on its own.

Nothing here writes, and nothing here looks anything up: every lookup that needs
the catalog (a typed name resolved to a plan) is done by the loader and handed in
as a raw fact, so this module stays portable with the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .money import cost_per_person, managed_effective_cost
from .models import (
    CoverageScope,
    CurrentLicenseLine,
    Engagement,
    Override,
    Persona,
    PersonaScenario,
    ResidualIntent,
    ThirdPartyProduct,
    UnusedSeatsAnswer,
)

ZERO = Decimal("0")
ONE = Decimal("1")


# ---------------------------------------------------------------------------
# Shares
# ---------------------------------------------------------------------------

def headcount_shares(applies_to: list[str], headcount: dict[str, int]) -> dict[str, Decimal]:
    """Each persona's fraction of something that applies to `applies_to`, by
    headcount (ENGINE_SPEC 6.2). With no headcount among them, an even split so no
    cost is lost. Fractions sum to 1 (empty when it applies to nobody)."""
    pool = [pid for pid in applies_to if pid in headcount]
    if not pool:
        return {}
    total = sum(headcount[pid] for pid in pool)
    if total > 0:
        return {pid: Decimal(headcount[pid]) / Decimal(total) for pid in pool}
    return {pid: ONE / Decimal(len(pool)) for pid in pool}


# ---------------------------------------------------------------------------
# Raw: exactly what people entered, plus the catalog facts the loader resolved
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawPersona:
    id: str
    name: str
    headcount: int
    parent_persona_id: Optional[str] = None


@dataclass(frozen=True)
class RawLicenseLine:
    id: str
    sku_reference: str
    quantity_assigned: int
    quantity_purchased: int
    list_price_annual: Decimal          # unit_price_paid_annual as entered
    price_override: bool
    overridden_price_annual: Optional[Decimal]
    persona_ids: tuple[str, ...]
    coverage_scope: str = "PerUser"
    renewal_date: Optional[date] = None
    unused_seats_answer: Optional[str] = None
    out_of_scope: bool = False
    linked_bundle_id: Optional[str] = None            # a person's "same as" link
    mapped_outcome_ids: frozenset[str] = frozenset()  # a person's ratified ticks
    # Catalog fact from the loader: the plan the typed name resolves to (aliases
    # included), or None. Retires when lines carry a key (TARGET_SCHEMA §12).
    resolved_bundle_id: Optional[str] = None


@dataclass(frozen=True)
class RawTool:
    id: str
    name: str
    raw_cost: Decimal
    cost_period: str                    # "Annual" | "Monthly"
    is_managed: bool
    tooling_pct: Optional[Decimal]      # None = use the engagement default
    covered_count_override: Optional[int]
    persona_ids: tuple[str, ...]
    renewal_date: Optional[str] = None
    # The operator's decision on the tool (ProductDisposition).
    override: str = "None"
    override_reason: str = ""
    residual_intent: str = "None"


@dataclass(frozen=True)
class RawAddon:
    bundle_id: str
    list_price_annual: Decimal


@dataclass(frozen=True)
class RawScenario:
    id: str
    persona_id: str
    target_sku_reference: str
    list_price_annual: Decimal          # base list, as stored
    discount_pct: Optional[Decimal]
    price_override: bool
    overridden_price_annual: Optional[Decimal]
    in_scope: bool
    addons: tuple[RawAddon, ...] = ()
    # Catalog fact from the loader: the plan the target name resolves to, or None.
    resolved_bundle_id: Optional[str] = None


@dataclass(frozen=True)
class RawEngagement:
    id: str
    personas: tuple[RawPersona, ...]                 # in display order
    licence_lines: tuple[RawLicenseLine, ...]
    tools: tuple[RawTool, ...]
    scenarios: tuple[RawScenario, ...]
    # The engagement's Microsoft coverage (ratified): coverage key -> outcomes. The
    # key is a bundle id, or a typed name for coverage entered against a name.
    microsoft_coverage: dict[str, frozenset[str]] = field(default_factory=dict)
    # Each tool's ratified coverage: tool id -> outcomes.
    tool_coverage: dict[str, frozenset[str]] = field(default_factory=dict)
    default_tooling_pct: Decimal = Decimal("0.30")
    ecif_roi_conservative: Decimal = Decimal("10")
    ecif_roi_generous: Decimal = Decimal("5")
    workshop_date: Optional[date] = None
    microsoft_renewal_date: Optional[date] = None
    horizon_years: int = 3


# ---------------------------------------------------------------------------
# Interpreted (TARGET_SCHEMA §6.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IPersona:
    id: str
    name: str
    headcount: int
    parent_persona_id: Optional[str]


@dataclass(frozen=True)
class ILicenseLine:
    """How a licence line is read (D23) and what it costs.

    `read_state` is one of
      out_of_scope  set aside: in no number, delivers nothing
      linked        a person linked it to a library plan
      mapped        a person ticked what it delivers
      named         coverage was entered by hand against this exact name (legacy)
      resolved      its name resolves to a plan this engagement has coverage for
      unread        none of those: its cost counts, what it delivers is unknown
    """

    id: str
    read_state: str
    outcomes: frozenset[str]
    bundle_id: Optional[str]            # the plan it reads as, when there is one
    list_price_annual: Decimal
    effective_price_annual: Decimal
    price_source: str                   # "override" | "list"
    applies_to: tuple[str, ...]         # persona ids, Org-wide expanded
    assumed: bool                       # untagged, read as Org-wide (D12)
    annual_cost: Decimal                # assigned × effective price

    @property
    def in_scope(self) -> bool:
        return self.read_state != "out_of_scope"


@dataclass(frozen=True)
class ITool:
    id: str
    name: str
    annual_cost: Decimal
    tooling_pct: Decimal
    tooling_source: str                 # "tool" | "engagement"
    effective_annual_cost: Decimal
    people_covered: int
    covered_source: str                 # "override" | "personas"
    cost_per_person: Decimal
    applies_to: tuple[str, ...]         # persona ids, Org-wide expanded
    assumed: bool                       # untagged, read as Org-wide
    delivered_outcome_ids: frozenset[str]


@dataclass(frozen=True)
class IScenario:
    id: str
    persona_id: str
    target_sku_reference: str
    base_bundle_id: Optional[str]
    addon_bundle_ids: tuple[str, ...]
    composed_list_annual: Decimal       # base + add-ons, before discount
    net_annual: Decimal                 # per seat, what the engine costs
    price_source: str                   # "override" | "list"
    covered_outcome_ids: frozenset[str]
    in_scope: bool
    complete: bool                      # has a target and a price (or override)


@dataclass(frozen=True)
class Interpreted:
    raw: RawEngagement
    personas: tuple[IPersona, ...]
    licence_lines: tuple[ILicenseLine, ...]
    # (licence id, persona id) -> this persona's annual cost of the line
    licence_shares: dict[tuple[str, str], Decimal]
    tools: tuple[ITool, ...]
    # (tool id, persona id) -> this persona's annual cost of the tool
    tool_shares: dict[tuple[str, str], Decimal]
    scenarios: tuple[IScenario, ...]

    def licence(self, licence_id: str) -> ILicenseLine:
        return next(l for l in self.licence_lines if l.id == licence_id)

    def tool(self, tool_id: str) -> ITool:
        return next(t for t in self.tools if t.id == tool_id)

    def lines_for(self, persona_id: str) -> list[ILicenseLine]:
        """The in-scope licence lines that apply to a persona (tagged, or Org-wide)."""
        return [l for l in self.licence_lines if l.in_scope and persona_id in l.applies_to]

    def tools_for(self, persona_id: str) -> list[ITool]:
        """The tools that apply to a persona (tagged, or Org-wide)."""
        return [t for t in self.tools if persona_id in t.applies_to]


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def _read_line(line: RawLicenseLine, coverage: dict[str, frozenset[str]]):
    ref = (line.sku_reference or "").strip()
    named = coverage.get(ref, frozenset()) if ref else frozenset()
    if line.out_of_scope:
        return "out_of_scope", frozenset(), None
    if line.linked_bundle_id:
        outs = coverage.get(line.linked_bundle_id, frozenset())
        return ("linked" if outs else "unread"), outs, line.linked_bundle_id
    if line.mapped_outcome_ids:
        return "mapped", line.mapped_outcome_ids, None
    if named:
        return "named", named, None
    resolved = line.resolved_bundle_id if ref else None
    outs = coverage.get(resolved, frozenset()) if resolved else frozenset()
    return ("resolved" if outs else "unread"), outs, resolved


def interpret(raw: RawEngagement) -> Interpreted:
    personas = tuple(IPersona(p.id, p.name, p.headcount, p.parent_persona_id)
                     for p in raw.personas)
    everyone = tuple(p.id for p in raw.personas)
    known = set(everyone)
    headcount = {p.id: p.headcount for p in raw.personas}

    # ---- Licence lines ----------------------------------------------------
    lines: list[ILicenseLine] = []
    licence_shares: dict[tuple[str, str], Decimal] = {}
    for line in raw.licence_lines:
        state, outcomes, bundle_id = _read_line(line, raw.microsoft_coverage)
        overridden = line.price_override and line.overridden_price_annual is not None
        effective = line.overridden_price_annual if overridden else line.list_price_annual
        assumed = not line.persona_ids
        applies = everyone if assumed else tuple(p for p in line.persona_ids if p in known)
        il = ILicenseLine(
            id=line.id, read_state=state, outcomes=outcomes, bundle_id=bundle_id,
            list_price_annual=line.list_price_annual, effective_price_annual=effective,
            price_source="override" if overridden else "list",
            applies_to=applies, assumed=assumed,
            annual_cost=Decimal(line.quantity_assigned) * effective,
        )
        lines.append(il)
        if il.in_scope:
            for pid, frac in headcount_shares(list(applies), headcount).items():
                licence_shares[(line.id, pid)] = il.annual_cost * frac

    # ---- Tools --------------------------------------------------------------
    tools: list[ITool] = []
    tool_shares: dict[tuple[str, str], Decimal] = {}
    for t in raw.tools:
        annual = t.raw_cost * 12 if t.cost_period == "Monthly" else t.raw_cost
        if t.tooling_pct is not None:
            tooling, tooling_source = t.tooling_pct, "tool"
        else:
            tooling, tooling_source = raw.default_tooling_pct, "engagement"
        effective = managed_effective_cost(annual, t.is_managed, tooling)
        tagged = tuple(p for p in t.persona_ids if p in known)
        if t.covered_count_override is not None:
            covered, covered_source = t.covered_count_override, "override"
        else:
            covered, covered_source = sum(headcount[p] for p in tagged), "personas"
        per_person = cost_per_person(effective, covered)
        assumed = not t.persona_ids
        applies = everyone if assumed else tagged
        tools.append(ITool(
            id=t.id, name=t.name, annual_cost=annual, tooling_pct=tooling,
            tooling_source=tooling_source, effective_annual_cost=effective,
            people_covered=covered, covered_source=covered_source,
            cost_per_person=per_person, applies_to=applies, assumed=assumed,
            delivered_outcome_ids=raw.tool_coverage.get(t.id, frozenset()),
        ))
        # Definition 7: a persona's share is cost per person × its headcount. When
        # the tool covers more people than its personas, the remainder is spend no
        # persona's move can retire; it is not anyone's share.
        for pid in applies:
            tool_shares[(t.id, pid)] = per_person * Decimal(headcount.get(pid, 0))

    # ---- Scenarios ----------------------------------------------------------
    scenarios: list[IScenario] = []
    for s in raw.scenarios:
        key = s.resolved_bundle_id or (s.target_sku_reference or "")
        covered = set(raw.microsoft_coverage.get(key, frozenset()))
        for a in s.addons:
            covered |= raw.microsoft_coverage.get(a.bundle_id, frozenset())
        composed = s.list_price_annual + sum((a.list_price_annual for a in s.addons), ZERO)
        overridden = s.price_override and s.overridden_price_annual is not None
        net = (s.overridden_price_annual if overridden
               else composed * (ONE - (s.discount_pct or ZERO)))
        scenarios.append(IScenario(
            id=s.id, persona_id=s.persona_id, target_sku_reference=s.target_sku_reference,
            base_bundle_id=s.resolved_bundle_id,
            addon_bundle_ids=tuple(a.bundle_id for a in s.addons),
            composed_list_annual=composed, net_annual=net,
            price_source="override" if overridden else "list",
            covered_outcome_ids=frozenset(covered), in_scope=s.in_scope,
            complete=bool((s.target_sku_reference or "").strip()) and (overridden or net > 0),
        ))

    return Interpreted(
        raw=raw, personas=personas, licence_lines=tuple(lines),
        licence_shares=licence_shares, tools=tuple(tools), tool_shares=tool_shares,
        scenarios=tuple(scenarios),
    )


# ---------------------------------------------------------------------------
# The engine's input
# ---------------------------------------------------------------------------

def to_engine(i: Interpreted) -> Engagement:
    """The engine's input, built only from interpreted values. Out-of-scope licence
    lines are not engine inputs (ENGINE_SPEC 6.2): they are in no number."""
    raw = i.raw
    raw_lines = {l.id: l for l in raw.licence_lines}
    raw_tools = {t.id: t for t in raw.tools}

    current = []
    for il in i.licence_lines:
        if not il.in_scope:
            continue
        rl = raw_lines[il.id]
        current.append(CurrentLicenseLine(
            quantity_assigned=rl.quantity_assigned,
            unit_price_paid_annual=il.effective_price_annual,
            sku_reference=rl.sku_reference,
            persona_ids=tuple(rl.persona_ids),
            coverage_scope=CoverageScope(rl.coverage_scope or "PerUser"),
            covered_outcome_ids=il.outcomes,
            id=il.id,
            quantity_purchased=rl.quantity_purchased or 0,
            renewal_date=rl.renewal_date,
            unused_seats_answer=(UnusedSeatsAnswer(rl.unused_seats_answer)
                                 if rl.unused_seats_answer else None),
        ))

    tools = []
    for it in i.tools:
        rt = raw_tools[it.id]
        tools.append(ThirdPartyProduct(
            id=it.id, name=it.name, annual_cost=it.annual_cost,
            covered_count=it.people_covered, is_managed=rt.is_managed,
            tooling_pct=it.tooling_pct, renewal_date=rt.renewal_date,
            delivered_outcome_ids=it.delivered_outcome_ids,
            persona_ids=frozenset(rt.persona_ids),
            override=Override(rt.override or "None"),
            override_reason=rt.override_reason or "",
            residual_intent=ResidualIntent(rt.residual_intent or "None"),
        ))

    return Engagement(
        id=raw.id,
        personas=[Persona(id=p.id, name=p.name, headcount=p.headcount) for p in i.personas],
        third_party_products=tools,
        scenarios=[PersonaScenario(
            id=s.id, persona_id=s.persona_id, target_sku_reference=s.target_sku_reference,
            target_unit_price_annual=s.net_annual, in_scope=s.in_scope,
            target_covered_outcome_ids=s.covered_outcome_ids,
        ) for s in i.scenarios],
        current_licenses=current,
        ecif_roi_conservative=raw.ecif_roi_conservative,
        ecif_roi_generous=raw.ecif_roi_generous,
        workshop_date=raw.workshop_date,
        microsoft_renewal_date=raw.microsoft_renewal_date,
        horizon_years=raw.horizon_years,
    )
