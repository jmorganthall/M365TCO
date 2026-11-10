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

from dataclasses import dataclass, field, replace
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
    Persona,
    PersonaScenario,
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
    # The customer's answer about seats beyond the personas who hold the tool
    # (GUIDED_PLAN G4): "ExtraSeats" | "PeopleWhoStay" | None (read as PeopleWhoStay).
    extra_seats_answer: Optional[str] = None
    # The vendor contract this tool is a cost line of (G12), or None.
    contract_id: Optional[str] = None


@dataclass(frozen=True)
class RawContract:
    """A vendor contract broken into cost lines (GUIDED_PLAN G12)."""
    id: str
    name: str
    total_cost: Decimal
    cost_period: str                    # "Annual" | "Monthly"
    renewal_date: Optional[str] = None


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
    # The practice's services in this future state (G13).
    services: tuple["RawService", ...] = ()


@dataclass(frozen=True)
class RawService:
    """A practice service as the loader reads it (GUIDED_PLAN G13): its kind, its
    price per user per year, and the engagement's outcome ids it delivers (the
    library keys mapped to this engagement's outcomes)."""
    id: str
    name: str
    kind: str                           # "ManagedSecurity" | "Calling" | "Other"
    price_annual: Decimal                # as locked in this future state
    outcome_ids: frozenset[str] = frozenset()
    current_price_annual: Optional[Decimal] = None   # the price in Settings now


@dataclass(frozen=True)
class RawQuote:
    """A practice service's quote for this customer (GUIDED_PLAN G13a): a total
    cost for a number of users, from the practice that delivers it."""
    service_id: str
    total_cost: Decimal
    cost_period: str                    # "Annual" | "Monthly"
    users_covered: Optional[int] = None
    name: str = ""
    kind: str = "Other"


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
    # How many people IT supports, as asked first in the guided flow (G8).
    supported_headcount: Optional[int] = None
    contracts: tuple[RawContract, ...] = ()
    service_quotes: tuple[RawQuote, ...] = ()
    # The engagement's outcome id(s) for "24x7 managed detection & response": what
    # the managed share of a managed tool delivers (G14).
    managed_outcome_ids: frozenset[str] = frozenset()
    # Catalog fact: the library plans that are full plans (kind "bundle"), not
    # add-ons — two of them on one group is likely double licensing.
    base_bundle_ids: frozenset[str] = frozenset()


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
    covered_source: str                 # "override" | "personas" | "extra_seats"  (ENGINE_SPEC 6.0a)
    cost_per_person: Decimal
    applies_to: tuple[str, ...]         # persona ids, Org-wide expanded
    assumed: bool                       # untagged, read as Org-wide
    delivered_outcome_ids: frozenset[str]
    personas_headcount: int = 0         # the tagged personas' headcount
    # ENGINE_SPEC 6.0a: the headcount of the personas it applies to (Org-wide = all),
    # the seat count the customer gave (None = not given), and the seats beyond the
    # personas that stay on the tool (their cost is in no persona's share).
    people_held: int = 0
    stated_seats: Optional[int] = None
    standing_seats: int = 0
    standing_annual: Decimal = Decimal("0")
    extra_seats: int = 0                # stated seats beyond people_held (asked about, G4)
    extra_seats_answer: Optional[str] = None   # the customer's answer, as entered
    contract_id: Optional[str] = None          # a cost line of this contract (G12)
    managed_share_of: Optional[str] = None     # the managed share of this managed tool (G14)
    renewal_date: Optional[str] = None         # its own, else its contract's


@dataclass(frozen=True)
class IContract:
    """A contract read through G12: its annual total, what its lines account for,
    and the "not broken down" remainder (a line of its own, `remainder_tool_id`)."""

    id: str
    name: str
    annual_cost: Decimal
    lines_annual: Decimal
    remainder_annual: Decimal
    line_ids: tuple[str, ...]
    remainder_tool_id: Optional[str] = None


@dataclass(frozen=True)
class IPersonaBaseline:
    """A persona's all-in cost today (GUIDED_PLAN G3, ENGINE_SPEC 6.0a): its share
    of every in-scope licence line and every tool it holds. Needs no future state."""

    persona_id: str
    headcount: int
    microsoft_annual: Decimal
    tools_annual: Decimal
    total_annual: Decimal
    per_user_monthly: Decimal
    microsoft_per_user_monthly: Decimal = Decimal("0")
    tools_per_user_monthly: Decimal = Decimal("0")


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
    # G13: what the Microsoft plan alone delivers, and the practice services added.
    microsoft_outcome_ids: frozenset[str] = frozenset()
    service_ids: tuple[str, ...] = ()
    # Each service's price per user per year here, in service_ids order: its quote
    # spread over the people who get it (G13a), else the price locked from Settings.
    service_prices_annual: tuple[Decimal, ...] = ()
    services_annual: Decimal = ZERO         # per user per year


@dataclass(frozen=True)
class IServiceQuote:
    """A practice service's quote read through the splitting rule (ENGINE_SPEC
    6.2a): its yearly cost spread over the people in the groups whose future state
    includes it, so the groups together carry exactly the quote."""
    service_id: str
    name: str
    kind: str
    annual_cost: Decimal
    users_covered: Optional[int]        # as quoted; NULL = not stated
    people: int                         # in the groups that get it (in-scope futures)
    persona_ids: tuple[str, ...]
    per_user_annual: Decimal            # annual_cost / people (0 when nobody gets it)
    per_user_monthly: Decimal


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
    tool delivers (the engine's displacement rule, ENGINE_SPEC 6.6). `quick_win`: the persona's current Microsoft licences already deliver
    every outcome the tool delivers."""

    persona_id: str
    tool_id: str
    retired: bool
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
    baselines: tuple[IPersonaBaseline, ...] = ()
    summary: dict = field(default_factory=dict)
    contracts: tuple[IContract, ...] = ()
    service_quotes: tuple[IServiceQuote, ...] = ()

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

def people_covered(held: int, stated: Optional[int], extra_seats_answer: Optional[str]) -> tuple[int, str]:
    """ENGINE_SPEC 6.0a: the one splitting rule. A tool's cost lands on the personas
    that hold it unless the customer says seats beyond them belong to people who stay
    on it (or hasn't answered, which is read the cautious way)."""
    if stated is None or stated <= held:
        return held, "personas"
    if extra_seats_answer == "ExtraSeats":
        return held, "extra_seats"
    return stated, "override"


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


def _annual(cost: Decimal, period: str) -> Decimal:
    return cost * 12 if period == "Monthly" else cost


def remainder_id(contract_id: str) -> str:
    return f"contract-rest:{contract_id}"


def _expand_contracts(raw: RawEngagement) -> tuple[RawEngagement, tuple[IContract, ...]]:
    """GUIDED_PLAN G12. A contract line renews with its contract unless it has its
    own date. What the lines don't account for becomes one more line, "<contract>
    (not broken down)": it delivers every outcome the lines deliver and is held by
    every persona any line is held by (Org-wide if any line is), so it goes away only
    when every one of those outcomes is replaced. The stated total stays the truth."""
    if not raw.contracts:
        return raw, ()
    by_id = {c.id: c for c in raw.contracts}
    tools = []
    for t in raw.tools:
        c = by_id.get(t.contract_id) if t.contract_id else None
        if c is not None and not t.renewal_date and c.renewal_date:
            t = replace(t, renewal_date=c.renewal_date)
        tools.append(t)
    coverage = dict(raw.tool_coverage)
    out: list[IContract] = []
    for c in raw.contracts:
        lines = [t for t in tools if t.contract_id == c.id]
        total = _annual(c.total_cost, c.cost_period)
        lines_annual = sum((_annual(t.raw_cost, t.cost_period) for t in lines), ZERO)
        rest = total - lines_annual
        rest_id = None
        if rest > 0:
            rest_id = remainder_id(c.id)
            untagged = (not lines) or any(not t.persona_ids for t in lines)
            persona_ids = () if untagged else tuple(dict.fromkeys(p for t in lines for p in t.persona_ids))
            tools.append(RawTool(
                id=rest_id, name=f"{c.name} (not broken down)", raw_cost=rest, cost_period="Annual",
                is_managed=any(t.is_managed for t in lines),
                tooling_pct=next((t.tooling_pct for t in lines if t.tooling_pct is not None), None),
                covered_count_override=None, persona_ids=persona_ids,
                renewal_date=c.renewal_date, contract_id=c.id))
            coverage[rest_id] = frozenset(o for t in lines for o in raw.tool_coverage.get(t.id, frozenset()))
        out.append(IContract(c.id, c.name, total, lines_annual, max(rest, ZERO),
                             tuple(t.id for t in lines), rest_id))
    return replace(raw, tools=tuple(tools), tool_coverage=coverage), tuple(out)


MANAGED_PREFIX = "managed-share:"


def _expand_managed(raw: RawEngagement) -> RawEngagement:
    """GUIDED_PLAN G14 / ENGINE_SPEC 6.5. Today's cost is what the customer pays, so
    a managed tool counts in full: its software share (tooling %) as the tool itself,
    and the rest as one more line, "<tool> (managed service)", held by the same
    people. Microsoft replaces only software, so that line delivers only the
    managed outcome (24x7 managed detection & response) when the tool delivers it:
    a managed-security service that delivers it replaces it, and nothing else
    does. The tool's own line keeps its other outcomes, so Microsoft can still
    replace the software share."""
    tools, coverage = [], dict(raw.tool_coverage)
    for t in raw.tools:
        tools.append(t)
        if not t.is_managed:
            continue
        tooling = t.tooling_pct if t.tooling_pct is not None else raw.default_tooling_pct
        if tooling >= 1:
            continue
        share_id = MANAGED_PREFIX + t.id
        delivered = raw.tool_coverage.get(t.id, frozenset())
        managed = delivered & raw.managed_outcome_ids
        if managed and delivered - managed:
            coverage[t.id] = delivered - managed
        coverage[share_id] = managed
        tools.append(replace(t, id=share_id, name=f"{t.name} (managed service)",
                             raw_cost=_annual(t.raw_cost, t.cost_period) * (1 - tooling),
                             cost_period="Annual", is_managed=False, tooling_pct=None))
    return replace(raw, tools=tuple(tools), tool_coverage=coverage)


def interpret(raw: RawEngagement) -> Interpreted:
    raw, contracts = _expand_contracts(raw)
    raw = _expand_managed(raw)
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
        assumed = not t.persona_ids
        applies = everyone if assumed else tagged
        held = sum(headcount[p] for p in applies)
        covered, covered_source = people_covered(held, t.covered_count_override,
                                                 t.extra_seats_answer)
        per_person = cost_per_person(effective, covered)
        standing = max(covered - held, 0)
        tools.append(ITool(
            id=t.id, name=t.name, annual_cost=annual, tooling_pct=tooling,
            tooling_source=tooling_source, effective_annual_cost=effective,
            people_covered=covered, covered_source=covered_source,
            cost_per_person=per_person, applies_to=applies, assumed=assumed,
            delivered_outcome_ids=raw.tool_coverage.get(t.id, frozenset()),
            personas_headcount=sum(headcount[p] for p in tagged),
            people_held=held, stated_seats=t.covered_count_override,
            standing_seats=standing, standing_annual=per_person * Decimal(standing),
            extra_seats=max((t.covered_count_override or 0) - held, 0),
            extra_seats_answer=t.extra_seats_answer,
            contract_id=t.contract_id, renewal_date=t.renewal_date,
            managed_share_of=t.id[len(MANAGED_PREFIX):] if t.id.startswith(MANAGED_PREFIX) else None,
        ))
        # Definition 7 (ENGINE_SPEC 6.0a): a persona's share is cost per person × its
        # headcount. Only seats the customer says belong to people who stay on the
        # tool (standing seats) are nobody's share.
        for pid in applies:
            tool_shares[(t.id, pid)] = per_person * Decimal(headcount.get(pid, 0))

    # ---- Practice service quotes (G13a) --------------------------------------
    # One splitting rule: a quote's cost lands on the groups that get the service,
    # by headcount, so they carry exactly the quote whatever the users it covers.
    quotes: list[IServiceQuote] = []
    quote_rate: dict[str, Decimal] = {}
    for q in raw.service_quotes:
        holders = [s for s in raw.scenarios if s.in_scope and any(sv.id == q.service_id for sv in s.services)]
        people = sum(headcount.get(s.persona_id, 0) for s in holders)
        annual = _annual(q.total_cost, q.cost_period)
        rate = annual / Decimal(people) if people else ZERO
        quote_rate[q.service_id] = rate
        quotes.append(IServiceQuote(
            service_id=q.service_id, name=q.name, kind=q.kind,
            annual_cost=annual, users_covered=q.users_covered, people=people,
            persona_ids=tuple(dict.fromkeys(s.persona_id for s in holders)),
            per_user_annual=rate, per_user_monthly=rate / 12))

    # ---- Scenarios ----------------------------------------------------------
    scenarios: list[IScenario] = []
    for s in raw.scenarios:
        base = s.linked_bundle_id or s.resolved_bundle_id
        key = base or (s.target_sku_reference or "")
        covered = set(raw.microsoft_coverage.get(key, frozenset()))
        for a in s.addons:
            covered |= raw.microsoft_coverage.get(a.bundle_id, frozenset())
        microsoft = frozenset(covered)
        for sv in s.services:
            covered |= sv.outcome_ids
        prices = tuple(quote_rate.get(sv.id, sv.price_annual) for sv in s.services)
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
            microsoft_outcome_ids=microsoft, service_ids=tuple(sv.id for sv in s.services),
            service_prices_annual=prices,
            services_annual=sum(prices, ZERO),
        ))

    personas = tuple(
        IPersona(p.id, p.name, p.headcount, p.parent_persona_id,
                 outcomes_unknown=any(l.read_state == "unread" and p.id in l.applies_to
                                      for l in lines),
                 carved_out_seats=sum(c.headcount for c in raw.personas
                                      if c.parent_persona_id == p.id))
        for p in raw.personas)

    baselines = []
    for p in raw.personas:
        ms = sum((v for (lid, pid), v in licence_shares.items() if pid == p.id), ZERO)
        tl = sum((v for (tid, pid), v in tool_shares.items() if pid == p.id), ZERO)
        total = ms + tl

        def per_user(v):
            return (v / Decimal(p.headcount) / 12).quantize(Decimal("0.01")) if p.headcount > 0 else ZERO
        baselines.append(IPersonaBaseline(p.id, p.headcount, ms.quantize(Decimal("0.01")),
                                          tl.quantize(Decimal("0.01")), total.quantize(Decimal("0.01")),
                                          per_user(total), per_user(ms), per_user(tl)))

    groups_hc = sum(p.headcount for p in raw.personas)
    total_annual = sum((b.total_annual for b in baselines), ZERO)
    summary = {
        # GUIDED_PLAN G8: the groups count down to the headcount IT supports.
        "supported_headcount": raw.supported_headcount,
        "groups_headcount": groups_hc,
        "remaining_headcount": (raw.supported_headcount - groups_hc
                                if raw.supported_headcount is not None else None),
        # G3: everyone's all-in cost today, and seats outside the personas that stay.
        "baseline_microsoft_annual": sum((b.microsoft_annual for b in baselines), ZERO),
        "baseline_tools_annual": sum((b.tools_annual for b in baselines), ZERO),
        "baseline_total_annual": total_annual,
        "baseline_per_user_monthly": ((total_annual / Decimal(groups_hc) / 12).quantize(Decimal("0.01"))
                                      if groups_hc > 0 else ZERO),
        "standing_annual": sum((t.standing_annual for t in tools), ZERO).quantize(Decimal("0.01")),
    }

    tables = _outcome_tables(raw, lines, tools, scenarios)
    seat_caps = _seat_caps(raw, lines, scenarios)
    findings = _findings(raw, lines, tools, scenarios, tables, seat_caps, contracts, tuple(quotes))
    return Interpreted(
        raw=raw, personas=personas, licence_lines=tuple(lines),
        licence_shares=licence_shares, tools=tuple(tools), tool_shares=tool_shares,
        scenarios=tuple(scenarios), seat_caps=seat_caps, findings=findings,
        baselines=tuple(baselines), summary=summary, contracts=contracts,
        service_quotes=tuple(quotes), **tables,
    )


def _pct_off(list_price: Decimal, price: Decimal) -> Decimal:
    """How far below list a price sits, as a fraction (0 at or above list)."""
    return max(ZERO, (list_price - price) / list_price) if list_price > 0 else ZERO


def normalize_name(text: str) -> str:
    """How a licence name is compared: lower case, single spaces. The one
    definition — name resolution and alias storage use it too."""
    return " ".join((text or "").lower().split())


def plans_by_persona(scenarios) -> dict:
    """Each persona's plan: its first scenario. One scenario per persona is the rule
    (TARGET_SCHEMA §5.2, refused on create); a persona that still has more is flagged
    (`plan_duplicated`), and every reading here uses this same one."""
    out: dict = {}
    for s in scenarios:
        out.setdefault(s.persona_id, s)
    return out


def _plan_bundles(s: IScenario) -> set:
    return {s.base_bundle_id} | set(s.addon_bundle_ids)


def _seat_caps(raw: RawEngagement, lines: list[ILicenseLine],
               scenarios: list[IScenario]) -> tuple[ISeatCap, ...]:
    """THE count of each licensing limit's seats (TARGET_SCHEMA D27). Every
    screen — the Readout's licence-limit card, the recommender, the split offer and
    Review — reads it. Licence lines count by the plan their typed name resolves to."""
    headcount = {p.id: p.headcount for p in raw.personas}
    raw_lines = {l.id: l for l in raw.licence_lines}
    # A persona moves when its in-scope plan names a plan; a blank plan leaves it on
    # the licences it has today.
    moving = {pid: s for pid, s in plans_by_persona(scenarios).items()
              if s.in_scope and ((s.target_sku_reference or "").strip() or s.base_bundle_id)}
    out = []
    for lim in raw.licence_limits:
        members = lim.member_bundle_ids
        # A line counts under its linked plan when a person linked it, else under the
        # plan its typed name resolves to.
        member_lines = [l for l in lines if l.in_scope
                        and (raw_lines[l.id].linked_bundle_id or raw_lines[l.id].resolved_bundle_id) in members]
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


def _findings(raw, lines, tools, scenarios, tables, seat_caps, contracts=(), quotes=()) -> tuple[IFinding, ...]:
    """Every check that works without AI (TARGET_SCHEMA §6.4, WALKTHROUGH §6), in
    walkthrough order. One more comes from outside the interpreter and is added by
    the Review step: pending library updates (the shared library)."""
    f: list[IFinding] = []
    raw_lines = {l.id: l for l in raw.licence_lines}
    raw_tools = {t.id: t for t in raw.tools}
    headcount = {p.id: p.headcount for p in raw.personas}
    names = {p.id: p.name for p in raw.personas}
    total_hc = sum(headcount.values())

    # ---- Personas & licences ----
    if not raw.personas:
        f.append(IFinding("no_groups", "groups"))
    if raw.supported_headcount is not None and raw.personas and total_hc != raw.supported_headcount:
        f.append(IFinding("headcount_mismatch", "groups", facts={
            "total": total_hc, "supported": raw.supported_headcount}))
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
    # A licence given only to groups that no longer exist (a database from before a
    # group's deletion cleaned up its tags): it applies to nobody, so its cost is in
    # no number. A person says who has it.
    known = set(headcount)
    for l in in_scope:
        rl = raw_lines[l.id]
        if rl.persona_ids and not any(p in known for p in rl.persona_ids):
            f.append(IFinding("tagged_to_missing_group", "groups", "left_out", licence_id=l.id,
                              facts={"name": rl.sku_reference or "A licence line", "kind": "licence"}))
    # Two or more full plans for the same people (e.g. Office 365 E3 and Business
    # Standard): either double licensing, or a group that should be split.
    for p in raw.personas:
        mine = [l for l in in_scope if p.id in l.applies_to
                and (raw_lines[l.id].linked_bundle_id or raw_lines[l.id].resolved_bundle_id) in raw.base_bundle_ids]
        if len(mine) > 1:
            f.append(IFinding("two_base_plans", "groups", persona_id=p.id, facts={
                "persona_name": p.name, "names": [raw_lines[l.id].sku_reference for l in mine]}))
    # Licensing limits already broken by today's licences (e.g. more than 300
    # Business seats): the future state can't keep everyone on that family.
    for c in seat_caps:
        if c.limit_type == "max_total_seats" and c.current_seats > c.max_quantity:
            f.append(IFinding("current_over_cap", "groups", facts={
                "cap_name": c.name, "cap": c.max_quantity, "seats": c.current_seats}))
    set_aside = [raw_lines[l.id].sku_reference or "A licence line" for l in lines if not l.in_scope]
    if set_aside:
        f.append(IFinding("license_out_of_scope", "groups", "info", facts={"names": set_aside}))

    # ---- Other tools ----
    for c in contracts:
        if c.lines_annual > c.annual_cost:
            f.append(IFinding("contract_lines_exceed", "tools", facts={
                "contract_id": c.id, "name": c.name, "total": c.annual_cost, "lines": c.lines_annual}))
    in_scope_planned = {s.persona_id for s in scenarios if s.in_scope}
    for t in tools:
        if t.managed_share_of:          # mirrors its tool; the tool's findings cover it
            continue
        rt = raw_tools[t.id]
        missing = []
        # A contract line may be $0 until its share is typed: the contract's
        # remainder line carries the money meanwhile.
        if (not rt.raw_cost or rt.raw_cost <= 0) and not rt.contract_id:
            missing.append("no cost")
        tagged = [p for p in rt.persona_ids if p in headcount]
        if rt.persona_ids and not tagged:
            f.append(IFinding("tagged_to_missing_group", "tools", "left_out", tool_id=t.id,
                              facts={"name": t.name or "A tool", "kind": "tool"}))
            continue
        if not t.people_covered:
            # Given only to groups with no people yet: say which, so the fix is clear.
            missing.append(f"no people in {', '.join(names[p] for p in tagged)}" if tagged else "no users")
        if not t.delivered_outcome_ids:
            missing.append("no uses ticked")
        if missing:
            f.append(IFinding("tool_incomplete", "tools", tool_id=t.id,
                              facts={"name": t.name or "A tool", "missing": missing}))
        elif not rt.renewal_date:
            f.append(IFinding("renewal_date_missing", "tools", "info", tool_id=t.id,
                              facts={"subject": "tool", "name": t.name}))
        # ENGINE_SPEC 6.0a: the seat count doesn't match the personas who hold it.
        if t.stated_seats is not None and t.stated_seats < t.people_held:
            f.append(IFinding("tool_seats_short", "tools", "info", tool_id=t.id, facts={
                "name": t.name, "seats": t.stated_seats, "held": t.people_held}))
        if (t.stated_seats is not None and t.stated_seats > t.people_held
                and rt.extra_seats_answer is None
                and any(r.retired and r.tool_id == t.id and r.persona_id in in_scope_planned
                        for r in tables["tool_retirements"])):
            f.append(IFinding("extra_seats_unanswered", "future", tool_id=t.id, facts={
                "name": t.name, "seats": t.stated_seats, "held": t.people_held,
                "extra": t.stated_seats - t.people_held}))

    # ---- Future state ----
    planned = {s.persona_id for s in scenarios}
    for p in raw.personas:
        if p.id not in planned:
            f.append(IFinding("group_without_plan", "future", persona_id=p.id,
                              facts={"name": p.name}))
    first_plan = plans_by_persona(scenarios)
    counts: dict[str, int] = {}
    for s in scenarios:
        counts[s.persona_id] = counts.get(s.persona_id, 0) + 1
    for p in raw.personas:
        if counts.get(p.id, 0) > 1:
            f.append(IFinding("plan_duplicated", "future", persona_id=p.id,
                              facts={"name": p.name, "count": counts[p.id]}))
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
            "plan": s.target_sku_reference, "cap_key": c.key, "cap_name": c.name, "cap": c.max_quantity,
            "used_elsewhere": c.used_by_others(p.id), "seats_left": left}))

    # A practice service whose price in Settings changed after it was added here
    # (a quoted service doesn't use the Settings price).
    quoted = {q.service_id for q in quotes}
    changed: dict[str, IFinding] = {}
    for rs in raw.scenarios:
        for sv in rs.services:
            if sv.id not in quoted and sv.current_price_annual is not None and sv.current_price_annual != sv.price_annual \
                    and sv.id not in changed:
                changed[sv.id] = IFinding("service_price_changed", "future", "info", facts={
                    "service_id": sv.id, "name": sv.name,
                    "locked_monthly": sv.price_annual / 12, "current_monthly": sv.current_price_annual / 12})
    f.extend(changed.values())
    # A quote nobody gets is in no number; a quote for a different number of users
    # than the groups that get it is spread over those groups all the same (G13a).
    for q in quotes:
        facts = {"service_id": q.service_id, "name": q.name, "users_covered": q.users_covered,
                 "people": q.people, "annual": q.annual_cost}
        if q.people == 0:
            f.append(IFinding("service_quote_unused", "future", facts=facts))
        elif q.users_covered is not None and q.users_covered != q.people:
            f.append(IFinding("service_quote_seats", "future", "info", facts=facts))

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
    for p in raw.personas:
        plan = first_plan.get(p.id)
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
        # Nothing to confirm for a plan that's out of scope or a group with no people:
        # neither is in the numbers.
        if unanswered and plan.in_scope and p.headcount > 0:
            f.append(IFinding("gap_unanswered", "gaps", persona_id=p.id,
                              outcome_ids=tuple(unanswered),
                              facts={"persona_name": p.name, "count": len(unanswered)}))
    return tuple(f)


def _outcome_tables(raw: RawEngagement, lines: list[ILicenseLine], tools: list[ITool],
                    scenarios: list[IScenario]) -> dict:
    """The persona × outcome tables (TARGET_SCHEMA §6.3): the single model of what
    each persona has today and after its move, and which tools each move retires."""
    plan_of = plans_by_persona(scenarios)
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
            if covers:
                retired.add(t.id)
            retirements.append(IToolRetirement(
                persona_id=p.id, tool_id=t.id, retired=covers,
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
        ))

    return Engagement(
        id=raw.id,
        personas=[Persona(id=p.id, name=p.name, headcount=p.headcount) for p in i.personas],
        third_party_products=tools,
        scenarios=[PersonaScenario(
            id=s.id, persona_id=s.persona_id, target_sku_reference=s.target_sku_reference,
            target_unit_price_annual=s.net_annual, in_scope=s.in_scope,
            target_covered_outcome_ids=s.covered_outcome_ids,
            services_unit_price_annual=s.services_annual,
        ) for s in i.scenarios],
        current_licenses=current,
        ecif_roi_conservative=raw.ecif_roi_conservative,
        ecif_roi_generous=raw.ecif_roi_generous,
        workshop_date=raw.workshop_date,
        microsoft_renewal_date=raw.microsoft_renewal_date,
        horizon_years=raw.horizon_years,
    )
