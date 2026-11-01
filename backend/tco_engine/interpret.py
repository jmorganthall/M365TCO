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
from functools import cached_property
from datetime import date
from decimal import Decimal
from typing import Optional

from .money import cost_per_person, managed_effective_cost
from .rules import covers_tool, headcount_shares
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
# Raw: exactly what people entered, plus the catalog facts the loader resolved
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawPersona:
    id: str
    name: str
    headcount: int
    parent_persona_id: Optional[str] = None
    # The outcomes this persona is declared to need (Personas step).
    required_outcome_ids: frozenset[str] = frozenset()


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
    # The plan a person picked (TARGET_SCHEMA §5.2); wins over the name.
    linked_bundle_id: Optional[str] = None


@dataclass(frozen=True)
class RawLimit:
    """A licensing limit from the catalog (e.g. the Business family's 300-seat
    tenant cap): its key, the plans it counts, and the ceiling."""

    key: str
    name: str
    limit_type: str                     # "max_total_seats", …
    max_quantity: int
    member_bundle_ids: frozenset[str]


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
    # The customer's answers about coverage gaps (TARGET_SCHEMA §4.8):
    # (persona id, outcome id) -> "NotDeliveredToday" | "CoveredOutsideInventory".
    gap_answers: dict[tuple[str, str], str] = field(default_factory=dict)
    employee_count: Optional[int] = None
    licence_limits: tuple[RawLimit, ...] = ()


# ---------------------------------------------------------------------------
# Interpreted (TARGET_SCHEMA §6.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IPersona:
    id: str
    name: str
    headcount: int
    parent_persona_id: Optional[str]
    # A licence line that applies to it is unread (D23): its outcome changes are
    # left out of every readout. Its money is unaffected.
    outcomes_unknown: bool = False
    carved_out_seats: int = 0           # headcount now in this persona's carve-outs


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
    pct_off_list: Decimal = ZERO        # how far below list the effective price is (≥ 0)
    unused_seats: int = 0               # purchased − assigned (≥ 0)
    unused_annual: Decimal = ZERO       # unused seats × effective price

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
    personas_headcount: int = 0         # the tagged personas' headcount (what "covers" defaults to)


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
    discounted_net_annual: Decimal = ZERO   # composed list × (1 − discount), before any override
    pct_off_list: Decimal = ZERO            # how far below the composed list the net is (≥ 0)
    plan_source: Optional[str] = None       # "picked" | "name" | None (no plan)


@dataclass(frozen=True)
class IToday:
    """One outcome a persona needs or gets today (TARGET_SCHEMA §6.3 `i_today`)."""

    persona_id: str
    outcome_id: str
    needed: bool
    needed_source: Optional[str]        # "required" | "default_today" | None
    delivered_today: bool
    by_microsoft: bool
    by_tool: bool
    by_outside: bool                    # answered "covered outside this inventory"
    gap_answer: Optional[str]


@dataclass(frozen=True)
class ISource:
    """What delivers a persona's outcome: exactly one of a licence line, a tool or a
    plan (`i_today_sources` / `i_proposed_sources`)."""

    persona_id: str
    outcome_id: str
    licence_id: Optional[str] = None
    tool_id: Optional[str] = None
    bundle_id: Optional[str] = None


@dataclass(frozen=True)
class IProposed:
    """One outcome a persona needs, gets today, or gets after its move (`i_proposed`).

    `status`:
      Kept              delivered today and after, and not a move from tools to Microsoft
      MovedToMicrosoft  delivered today only by tools; after, by the Microsoft plan
      Gained            delivered by nothing today, confirmed as a gap, delivered after
      Unconfirmed       delivered by nothing today, delivered after, gap not answered
      Lost              delivered today, not after
      Gap               needed, delivered neither today nor after
      Pending           the persona's plan is incomplete
    A persona with no plan keeps what it has today."""

    persona_id: str
    outcome_id: str
    needed: bool
    delivered_today: bool
    delivered_after: bool
    by_target: bool
    by_kept_tool: bool
    in_scope: bool
    has_plan: bool
    status: str


@dataclass(frozen=True)
class IToolRetirement:
    """A tool that applies to a persona, and whether that persona's move retires it
    (`i_tool_retirements`). `retired`: the persona's plan covers every outcome the
    tool delivers (the engine's displacement rule, ENGINE_SPEC 6.6) or the operator
    forced it. `quick_win`: the persona's current Microsoft licences already deliver
    every outcome the tool delivers."""

    persona_id: str
    tool_id: str
    retired: bool
    forced: bool
    quick_win: bool


@dataclass(frozen=True)
class ISeatCap:
    """A licensing limit counted for this engagement (TARGET_SCHEMA `c_limit_results`,
    D27): seats today on member licence lines, and seats in the future state."""

    key: str
    name: str
    limit_type: str
    max_quantity: int
    member_bundle_ids: frozenset[str]
    current_seats: int
    # Future state, per persona: a persona whose in-scope plan (base or any add-on)
    # is a member counts its headcount; every other persona its share of the member
    # lines it keeps. Unattributed: member lines with no headcount to share them by.
    usage: dict[str, float]
    unattributed: float

    @property
    def future_seats(self) -> int:
        return int(round(sum(self.usage.values()) + self.unattributed))

    def used_by_others(self, persona_id: str) -> int:
        """Future-state seats used by everyone but this persona — what's left for
        this persona's own decision is the cap less this."""
        return int(round(sum(v for pid, v in self.usage.items() if pid != persona_id)
                         + self.unattributed))

    def seats_left_for(self, persona_id: str) -> int:
        return max(0, self.max_quantity - self.used_by_others(persona_id))


@dataclass(frozen=True)
class IFinding:
    """Something to answer or know before the readout (TARGET_SCHEMA §6.4
    `i_findings`): a code, the walkthrough step it is fixed on, its subject, and the
    facts a screen needs to word it. Wording is the screen's job (services/review)."""

    code: str
    step: str
    severity: str = "warn"              # "warn" | "info" | "left_out"
    persona_id: Optional[str] = None
    licence_id: Optional[str] = None
    tool_id: Optional[str] = None
    outcome_ids: tuple[str, ...] = ()
    facts: dict = field(default_factory=dict)


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
    today: tuple[IToday, ...] = ()
    today_sources: tuple[ISource, ...] = ()
    proposed: tuple[IProposed, ...] = ()
    proposed_sources: tuple[ISource, ...] = ()
    tool_retirements: tuple[IToolRetirement, ...] = ()
    seat_caps: tuple[ISeatCap, ...] = ()
    findings: tuple[IFinding, ...] = ()

    @cached_property
    def _licence_index(self) -> dict[str, ILicenseLine]:
        return {l.id: l for l in self.licence_lines}

    @cached_property
    def _tool_index(self) -> dict[str, ITool]:
        return {t.id: t for t in self.tools}

    def licence(self, licence_id: str) -> ILicenseLine:
        return self._licence_index[licence_id]

    def tool(self, tool_id: str) -> ITool:
        return self._tool_index[tool_id]

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
        unused = max((line.quantity_purchased or 0) - (line.quantity_assigned or 0), 0)
        il = ILicenseLine(
            id=line.id, read_state=state, outcomes=outcomes, bundle_id=bundle_id,
            list_price_annual=line.list_price_annual, effective_price_annual=effective,
            price_source="override" if overridden else "list",
            applies_to=applies, assumed=assumed,
            annual_cost=Decimal(line.quantity_assigned) * effective,
            pct_off_list=_pct_off(line.list_price_annual, effective),
            unused_seats=unused, unused_annual=Decimal(unused) * effective,
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
            personas_headcount=sum(headcount[p] for p in tagged),
        ))
        # Definition 7: a persona's share is cost per person × its headcount. When
        # the tool covers more people than its personas, the remainder is spend no
        # persona's move can retire; it is not anyone's share.
        for pid in applies:
            tool_shares[(t.id, pid)] = per_person * Decimal(headcount.get(pid, 0))

    # ---- Scenarios ----------------------------------------------------------
    scenarios: list[IScenario] = []
    for s in raw.scenarios:
        base = s.linked_bundle_id or s.resolved_bundle_id
        key = base or (s.target_sku_reference or "")
        covered = set(raw.microsoft_coverage.get(key, frozenset()))
        for a in s.addons:
            covered |= raw.microsoft_coverage.get(a.bundle_id, frozenset())
        composed = s.list_price_annual + sum((a.list_price_annual for a in s.addons), ZERO)
        overridden = s.price_override and s.overridden_price_annual is not None
        discounted = composed * (ONE - (s.discount_pct or ZERO))
        net = s.overridden_price_annual if overridden else discounted
        scenarios.append(IScenario(
            id=s.id, persona_id=s.persona_id, target_sku_reference=s.target_sku_reference,
            base_bundle_id=base,
            addon_bundle_ids=tuple(a.bundle_id for a in s.addons),
            composed_list_annual=composed, net_annual=net,
            price_source="override" if overridden else "list",
            covered_outcome_ids=frozenset(covered), in_scope=s.in_scope,
            complete=bool((s.target_sku_reference or "").strip()) and (overridden or net > 0),
            discounted_net_annual=discounted, pct_off_list=_pct_off(composed, net),
            plan_source="picked" if s.linked_bundle_id else "name" if s.resolved_bundle_id else None,
        ))

    personas = tuple(
        IPersona(p.id, p.name, p.headcount, p.parent_persona_id,
                 outcomes_unknown=any(l.read_state == "unread" and p.id in l.applies_to
                                      for l in lines),
                 carved_out_seats=sum(c.headcount for c in raw.personas
                                      if c.parent_persona_id == p.id))
        for p in raw.personas)

    tables = _outcome_tables(raw, lines, tools, scenarios)
    seat_caps = _seat_caps(raw, lines, scenarios)
    findings = _findings(raw, lines, tools, scenarios, tables, seat_caps)
    return Interpreted(
        raw=raw, personas=personas, licence_lines=tuple(lines),
        licence_shares=licence_shares, tools=tuple(tools), tool_shares=tool_shares,
        scenarios=tuple(scenarios), seat_caps=seat_caps, findings=findings, **tables,
    )


def _pct_off(list_price: Decimal, price: Decimal) -> Decimal:
    """How far below list a price sits, as a fraction (0 at or above list)."""
    return max(ZERO, (list_price - price) / list_price) if list_price > 0 else ZERO


def normalize_name(text: str) -> str:
    """How a licence name is compared: lower case, single spaces. The one
    definition — name resolution and alias storage use it too."""
    return " ".join((text or "").lower().split())


def _plan_bundles(s: IScenario) -> set:
    return {s.base_bundle_id} | set(s.addon_bundle_ids)


def _seat_caps(raw: RawEngagement, lines: list[ILicenseLine],
               scenarios: list[IScenario]) -> tuple[ISeatCap, ...]:
    """THE count of each licensing limit's seats (TARGET_SCHEMA D27). Every
    screen — the Readout's licence-limit card, the recommender, the split offer and
    Review — reads it. Licence lines count by the plan their typed name resolves to."""
    headcount = {p.id: p.headcount for p in raw.personas}
    raw_lines = {l.id: l for l in raw.licence_lines}
    moving = {s.persona_id: s for s in scenarios if s.in_scope}
    out = []
    for lim in raw.licence_limits:
        members = lim.member_bundle_ids
        member_lines = [l for l in lines if l.in_scope
                        and raw_lines[l.id].resolved_bundle_id in members]
        current = sum(raw_lines[l.id].quantity_assigned for l in member_lines)
        usage: dict[str, float] = {}
        unattributed = 0.0
        for pid, s in moving.items():
            if _plan_bundles(s) & members:
                usage[pid] = usage.get(pid, 0.0) + headcount.get(pid, 0)
        for l in member_lines:
            assigned = raw_lines[l.id].quantity_assigned
            total = sum(headcount.get(pid, 0) for pid in l.applies_to)
            if not total:
                unattributed += assigned
                continue
            for pid in l.applies_to:
                if pid in moving or not headcount.get(pid, 0):
                    continue
                usage[pid] = usage.get(pid, 0.0) + assigned * headcount[pid] / total
        out.append(ISeatCap(
            key=lim.key, name=lim.name, limit_type=lim.limit_type,
            max_quantity=lim.max_quantity, member_bundle_ids=members,
            current_seats=current, usage=usage, unattributed=unattributed))
    return tuple(out)


def _findings(raw, lines, tools, scenarios, tables, seat_caps) -> tuple[IFinding, ...]:
    """Every check that works without AI (TARGET_SCHEMA §6.4, WALKTHROUGH §6), in
    walkthrough order. Two more come from outside the interpreter and are added by
    the Review step: a partly replaced tool with no keep-or-retire answer (an engine
    result) and pending library updates (the shared library)."""
    f: list[IFinding] = []
    raw_lines = {l.id: l for l in raw.licence_lines}
    raw_tools = {t.id: t for t in raw.tools}
    headcount = {p.id: p.headcount for p in raw.personas}
    names = {p.id: p.name for p in raw.personas}
    total_hc = sum(headcount.values())

    # ---- Personas & licences ----
    if not raw.personas:
        f.append(IFinding("no_groups", "groups"))
    if raw.employee_count and total_hc and total_hc != raw.employee_count:
        f.append(IFinding("headcount_vs_employees", "groups", "info",
                          facts={"total": total_hc, "employees": raw.employee_count}))
    in_scope = [l for l in lines if l.in_scope]
    for l in in_scope:
        rl = raw_lines[l.id]
        name = rl.sku_reference or "A licence line"
        if l.assumed:
            f.append(IFinding("licence_untagged", "groups", licence_id=l.id, facts={"name": name}))
        elif rl.coverage_scope != "TenantWide":
            hc = sum(headcount[pid] for pid in l.applies_to)
            if rl.quantity_assigned != hc:
                f.append(IFinding("seats_vs_headcount", "groups", "info", licence_id=l.id,
                                  facts={"name": name, "assigned": rl.quantity_assigned,
                                         "headcount": hc}))
        unused = max((rl.quantity_purchased or 0) - (rl.quantity_assigned or 0), 0)
        if unused and not rl.unused_seats_answer:
            f.append(IFinding("unused_seats_unanswered", "groups", licence_id=l.id,
                              facts={"name": name, "unused": unused}))
    if in_scope and not raw.microsoft_renewal_date and any(
            not raw_lines[l.id].renewal_date for l in in_scope):
        f.append(IFinding("renewal_date_missing", "groups", "info", facts={"subject": "agreement"}))
    set_aside = [raw_lines[l.id].sku_reference or "A licence line" for l in lines if not l.in_scope]
    if set_aside:
        f.append(IFinding("license_out_of_scope", "groups", "info", facts={"names": set_aside}))

    # ---- Other tools ----
    for t in tools:
        rt = raw_tools[t.id]
        missing = []
        if not rt.raw_cost or rt.raw_cost <= 0:
            missing.append("no cost")
        if not t.people_covered:
            missing.append("no users")
        if not t.delivered_outcome_ids:
            missing.append("no uses ticked")
        if missing:
            f.append(IFinding("tool_incomplete", "tools", tool_id=t.id,
                              facts={"name": t.name or "A tool", "missing": missing}))
        elif not rt.renewal_date:
            f.append(IFinding("renewal_date_missing", "tools", "info", tool_id=t.id,
                              facts={"subject": "tool", "name": t.name}))

    # ---- Future state ----
    planned = {s.persona_id for s in scenarios}
    for p in raw.personas:
        if p.id not in planned:
            f.append(IFinding("group_without_plan", "future", persona_id=p.id,
                              facts={"name": p.name}))
    first_plan: dict[str, IScenario] = {}
    for s in scenarios:
        first_plan.setdefault(s.persona_id, s)
    caps = [c for c in seat_caps if c.limit_type == "max_total_seats"]
    for p in raw.personas:
        s = first_plan.get(p.id)
        if s is None or not s.in_scope or not s.target_sku_reference:
            continue
        hit = None
        for c in caps:
            if c.member_bundle_ids & _plan_bundles(s):
                left = c.seats_left_for(p.id)
                if hit is None or left < hit[1]:
                    hit = (c, left)
        if hit is None or p.headcount <= hit[1]:
            continue
        c, left = hit
        kind = "no_seats" if left <= 0 else "nested" if p.parent_persona_id else "split"
        f.append(IFinding("seat_cap_exceeded", "future", persona_id=p.id, facts={
            "kind": kind, "persona_name": p.name, "headcount": p.headcount,
            "plan": s.target_sku_reference, "cap_key": c.key, "cap": c.max_quantity,
            "used_elsewhere": c.used_by_others(p.id), "seats_left": left}))

    # ---- Capability comparison ----
    # A licence nobody can read (D23): one question per licence name.
    unread_by_persona: dict[str, list[str]] = {}
    for p in raw.personas:
        seen: set[str] = set()
        for l in in_scope:
            if l.read_state != "unread" or p.id not in l.applies_to:
                continue
            ref = raw_lines[l.id].sku_reference or ""
            if normalize_name(ref) in seen:
                continue
            seen.add(normalize_name(ref))
            unread_by_persona.setdefault(p.id, []).append(ref or "A licence line")
    grouped: dict[str, tuple[str, list[str]]] = {}
    for p in raw.personas:
        for ref in unread_by_persona.get(p.id, []):
            grouped.setdefault(normalize_name(ref), (ref, []))[1].append(p.id)
    for ref, pids in grouped.values():
        f.append(IFinding("license_unread", "tools", facts={
            "name": ref, "persona_ids": pids, "personas": [names[p] for p in pids]}))

    proposed = tables["proposed"]
    today = tables["today"]
    # The outcome tables read a persona's last scenario (one per persona in practice).
    last_plan = {s.persona_id: s for s in scenarios}
    for p in raw.personas:
        plan = last_plan.get(p.id)
        if p.id in unread_by_persona:
            if plan is not None:
                f.append(IFinding("capability_changes_unknown", "tools", "left_out",
                                  persona_id=p.id, facts={
                                      "persona_name": p.name, "names": unread_by_persona[p.id]}))
            continue
        if plan is None:
            continue
        rows = [r for r in proposed if r.persona_id == p.id]
        target_unmapped = not plan.covered_outcome_ids
        if target_unmapped:
            f.append(IFinding("target_unmapped", "future", persona_id=p.id,
                              facts={"persona_name": p.name}))
        else:
            target = plan.covered_outcome_ids
            dropped = sorted(t.outcome_id for t in today
                             if t.persona_id == p.id and t.by_microsoft and t.outcome_id not in target)
            if dropped:
                f.append(IFinding("capability_dropped", "future", "info", persona_id=p.id,
                                  outcome_ids=tuple(dropped), facts={"persona_name": p.name}))
        unanswered = sorted(r.outcome_id for r in rows if r.by_target and not r.delivered_today
                            and (p.id, r.outcome_id) not in raw.gap_answers)
        if unanswered:
            f.append(IFinding("gap_unanswered", "gaps", persona_id=p.id,
                              outcome_ids=tuple(unanswered),
                              facts={"persona_name": p.name, "count": len(unanswered)}))
    return tuple(f)


def _outcome_tables(raw: RawEngagement, lines: list[ILicenseLine], tools: list[ITool],
                    scenarios: list[IScenario]) -> dict:
    """The persona × outcome tables (TARGET_SCHEMA §6.3): the single model of what
    each persona has today and after its move, and which tools each move retires."""
    plan_of = {s.persona_id: s for s in scenarios}
    forced = {t.id for t in raw.tools if t.override == "ForceFullElimination"}
    today: list[IToday] = []
    today_src: list[ISource] = []
    proposed: list[IProposed] = []
    proposed_src: list[ISource] = []
    retirements: list[IToolRetirement] = []

    for p in raw.personas:
        my_lines = [l for l in lines if l.in_scope and p.id in l.applies_to]
        my_tools = [t for t in tools if p.id in t.applies_to]
        by_ms: dict[str, list[str]] = {}
        for l in my_lines:
            for o in l.outcomes:
                by_ms.setdefault(o, []).append(l.id)
        by_tool: dict[str, list[str]] = {}
        for t in my_tools:
            for o in t.delivered_outcome_ids:
                by_tool.setdefault(o, []).append(t.id)
        answers = {o: a for (pid, o), a in raw.gap_answers.items() if pid == p.id}
        outside = {o for o, a in answers.items() if a == "CoveredOutsideInventory"}
        delivered = set(by_ms) | set(by_tool) | outside
        # Definition 1: needed = declared requirements, else everything delivered today.
        if p.required_outcome_ids:
            needed, needed_source = set(p.required_outcome_ids), "required"
        else:
            needed, needed_source = set(delivered), "default_today"

        for o in sorted(needed | delivered):
            today.append(IToday(
                persona_id=p.id, outcome_id=o, needed=o in needed,
                needed_source=needed_source if o in needed else None,
                delivered_today=o in delivered, by_microsoft=o in by_ms,
                by_tool=o in by_tool, by_outside=o in outside, gap_answer=answers.get(o)))
            today_src += [ISource(p.id, o, licence_id=lid) for lid in by_ms.get(o, [])]
            today_src += [ISource(p.id, o, tool_id=tid) for tid in by_tool.get(o, [])]

        # ---- After the move -------------------------------------------------
        plan = plan_of.get(p.id)
        ms_today = set(by_ms)
        retired: set[str] = set()
        for t in my_tools:
            covers = plan is not None and covers_tool(t.delivered_outcome_ids, plan.covered_outcome_ids)
            is_forced = plan is not None and t.id in forced
            if covers or is_forced:
                retired.add(t.id)
            retirements.append(IToolRetirement(
                persona_id=p.id, tool_id=t.id, retired=covers or is_forced,
                forced=is_forced and not covers,
                quick_win=covers_tool(t.delivered_outcome_ids, ms_today)))
        if plan is None:
            # No plan: the persona keeps what it has today.
            for o in sorted(needed | delivered):
                proposed.append(IProposed(
                    persona_id=p.id, outcome_id=o, needed=o in needed,
                    delivered_today=o in delivered, delivered_after=o in delivered,
                    by_target=False, by_kept_tool=o in by_tool, in_scope=False,
                    has_plan=False,
                    status="Kept" if o in delivered else "Gap"))
            continue

        target = set(plan.covered_outcome_ids)
        kept_by_tool: dict[str, list[str]] = {}
        for t in my_tools:
            if t.id not in retired:
                for o in t.delivered_outcome_ids:
                    kept_by_tool.setdefault(o, []).append(t.id)
        after = target | set(kept_by_tool) | outside
        for o in sorted(needed | delivered | after):
            d_today, d_after = o in delivered, o in after
            if not plan.complete:
                status = "Pending"
            elif d_today and d_after:
                only_tools = o in by_tool and o not in by_ms and o not in outside
                status = "MovedToMicrosoft" if only_tools and o in target else "Kept"
            elif d_after:
                status = "Gained" if answers.get(o) == "NotDeliveredToday" else "Unconfirmed"
            elif d_today:
                status = "Lost"
            else:
                status = "Gap"
            proposed.append(IProposed(
                persona_id=p.id, outcome_id=o, needed=o in needed,
                delivered_today=d_today, delivered_after=d_after,
                by_target=o in target, by_kept_tool=o in kept_by_tool,
                in_scope=plan.in_scope, has_plan=True, status=status))
            if o in target:
                for bid in ([plan.base_bundle_id] if plan.base_bundle_id else []) + list(plan.addon_bundle_ids):
                    if o in raw.microsoft_coverage.get(bid, frozenset()):
                        proposed_src.append(ISource(p.id, o, bundle_id=bid))
            proposed_src += [ISource(p.id, o, tool_id=tid) for tid in kept_by_tool.get(o, [])]

    return {
        "today": tuple(today), "today_sources": tuple(today_src),
        "proposed": tuple(proposed), "proposed_sources": tuple(proposed_src),
        "tool_retirements": tuple(retirements),
    }


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
