"""The headline, timed by renewals — ENGINE_SPEC Section 6.11.

Pure functions over the engine's own outputs. The headline has three sub-lines:

  duplicate_spend   each quick win (6.10): a tool the customer's CURRENT Microsoft
                    licensing already covers, saved from the tool's renewal.
  consolidation     each in-scope persona's move: its displaced-tool credit beyond
                    the quick-win portion (6.3a), saved from each tool's renewal,
                    less its Microsoft change — an increase from when the first
                    tool only the move can retire renews (day one if there is
                    none), a reduction from the Microsoft renewal of the
                    persona's lines.
  overlicensing     each licence line whose unused seats the customer confirmed are
                    not needed: unused seats × effective price, from the line's
                    Microsoft renewal.

Amounts are SAVINGS-positive (a positive amount is money saved; a negative one is
money added). Month 0 is the workshop date. An item with annual amount A that
starts at month s counts for max(0, H − s) months, H = horizon_years × 12, so it
contributes A × months / 12, rounded to the cent. Sub-lines and the headline are
sums of the rounded items, so every displayed total reconciles.

Run-rate identity: before timing, the three sub-lines' annual amounts sum to
quick_win_savings_annual − move_incremental_delta_annual + overlicensing — the
untimed total opportunity the readout showed before this section existed.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .models import Engagement, UnusedSeatsAnswer

CENTS = Decimal("0.01")
# A missing renewal date is assumed to be this many months after the workshop.
ASSUMED_RENEWAL_MONTHS = 12

DUPLICATE_SPEND = "duplicate_spend"
CONSOLIDATION = "consolidation"
OVERLICENSING = "overlicensing"


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENTS)


@dataclass
class TimingItem:
    """One timed amount behind the headline (TARGET_SCHEMA c_timing_items)."""

    item_key: str
    sub_line: str                 # duplicate_spend | consolidation | overlicensing
    kind: str                     # quick_win | tool_credit | microsoft_increase |
                                  # microsoft_reduction | unused_seats
    label: str
    annual_amount: Decimal        # savings-positive, at full run-rate
    start_month: int              # month 0 = the workshop
    timed_by: Optional[str]       # ISO date the item starts (after roll-forward)
    date_assumed: bool            # True when a missing date was assumed
    months_counted: int
    amount: Decimal               # savings-positive, over the horizon
    persona_id: Optional[str] = None
    third_party_product_id: Optional[str] = None
    license_id: Optional[str] = None


@dataclass
class UnusedSeatLine:
    """A licence line with seats bought but not assigned, and the answer for them.
    Intended seats are shown, never counted; unanswered ones are left out."""

    license_id: str
    sku_reference: str
    unused_seats: int
    unit_price_annual: Decimal
    annual_value: Decimal
    answer: Optional[str]         # "Intended" | "NotNeeded" | None (unanswered)


@dataclass
class Headline:
    horizon_months: int
    workshop_date: Optional[str]
    duplicate_spend_amount: Decimal
    consolidation_amount: Decimal
    overlicensing_amount: Decimal
    amount: Decimal               # the sum of the three sub-lines
    direction: str                # saved | added | none
    run_rate_annual: Decimal      # Σ annual amounts, once everything has renewed
    items: list[TimingItem] = field(default_factory=list)
    unused_seat_lines: list[UnusedSeatLine] = field(default_factory=list)


# --------------------------------------------------------------------------
# Dates → start months
# --------------------------------------------------------------------------

def add_months(d: date, months: int) -> date:
    """d plus a whole number of months, clamping the day to the month's length
    (31 Jan + 1 month = 28/29 Feb)."""
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, monthrange(year, month)[1]))


def start_month(workshop: Optional[date], when: Optional[date]) -> tuple[int, Optional[date], bool]:
    """The month an amount timed by `when` starts, counted from the workshop.

    Returns (start_month, the date it was timed by, assumed?).
      * A missing date (or no workshop to measure from) is assumed to be
        ASSUMED_RENEWAL_MONTHS after the workshop.
      * A date before the workshop is a past renewal: rolled forward a year at a
        time to its next anniversary on or after the workshop.
      * Otherwise the smallest whole month m with workshop + m months ≥ the date,
        so a saving is never counted for a month before its renewal.
    """
    if workshop is None or when is None:
        timed = add_months(workshop, ASSUMED_RENEWAL_MONTHS) if workshop else None
        return ASSUMED_RENEWAL_MONTHS, timed, True
    while when < workshop:
        when = add_months(when, 12)
    m = (when.year - workshop.year) * 12 + (when.month - workshop.month)
    if add_months(workshop, m) < when:
        m += 1
    return max(m, 0), when, False


def _parse(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# --------------------------------------------------------------------------
# The headline
# --------------------------------------------------------------------------

def timed_headline(engagement: Engagement, scenario_results, quick_wins) -> Headline:
    """Build the timed headline from the engine's scenario results and quick wins
    (both already computed by `compute`)."""
    workshop = engagement.workshop_date
    horizon_months = max(int(engagement.horizon_years or 0), 0) * 12
    products = {p.id: p for p in engagement.third_party_products}
    personas = {p.id: p for p in engagement.personas}
    items: list[TimingItem] = []

    def add(key, sub_line, kind, label, annual, when, *, persona_id=None,
            product_id=None, license_id=None, start_override=None):
        annual = _money(annual)
        if annual == 0:
            return
        if start_override is not None:
            s, timed, assumed = start_override
        else:
            s, timed, assumed = start_month(workshop, when)
        months = max(horizon_months - s, 0)
        items.append(TimingItem(
            item_key=key, sub_line=sub_line, kind=kind, label=label,
            annual_amount=annual, start_month=s,
            timed_by=timed.isoformat() if timed else None, date_assumed=assumed,
            months_counted=months,
            amount=_money(annual * Decimal(months) / Decimal(12)),
            persona_id=persona_id, third_party_product_id=product_id,
            license_id=license_id,
        ))

    def tool_renewal(product_id):
        p = products.get(product_id)
        return _parse(p.renewal_date) if p else None

    # ---- Duplicate spend today: each quick win, from the tool's renewal ----
    for q in quick_wins:
        add(f"qw:{q.third_party_product_id}", DUPLICATE_SPEND, "quick_win",
            q.third_party_product_name, q.credited_annual,
            tool_renewal(q.third_party_product_id),
            product_id=q.third_party_product_id)

    # ---- Consolidation: each in-scope persona's move ----
    scenario_persona_ids = {
        s.persona_id for s in engagement.scenarios if s.persona_id in personas
    }

    def persona_lines(pid):
        """The licence lines that apply to this persona (tagged to it, or untagged
        = the org-wide pool of personas with a scenario), as in Section 6.2."""
        return [
            line for line in engagement.current_licenses
            if (pid in line.persona_ids if line.persona_ids else pid in scenario_persona_ids)
        ]

    def line_renewal(line):
        return line.renewal_date or engagement.microsoft_renewal_date

    for r in scenario_results:
        if not r.in_scope:
            continue
        # The move's own tool credit, each from its tool's renewal.
        for o in r.offsets:
            add(f"tool:{r.persona_id}:{o.third_party_product_id}", CONSOLIDATION,
                "tool_credit", f"{r.persona_name}: retire {o.third_party_product_name}",
                o.move_unlocked_annual, tool_renewal(o.third_party_product_id),
                persona_id=r.persona_id, product_id=o.third_party_product_id)
        # The Microsoft change (target − today's Microsoft): savings-positive is
        # its negation.
        ms_change = _money(r.target_spend_annual - r.current_microsoft_annual)
        if ms_change > 0:
            # An increase can start any time; it starts when there is something only
            # the move can retire — the first tool whose credit the move itself
            # unlocks (a quick win retires without it) — else on day one.
            starts = [start_month(workshop, tool_renewal(o.third_party_product_id))
                      for o in r.offsets if o.move_unlocked_annual > 0]
            first = min(starts, key=lambda t: t[0]) if starts else (0, workshop, False)
            add(f"ms:{r.persona_id}", CONSOLIDATION, "microsoft_increase",
                f"{r.persona_name}: Microsoft licensing change",
                -ms_change, None, persona_id=r.persona_id, start_override=first)
        elif ms_change < 0:
            # A reduction waits for every line it touches to renew: the latest
            # Microsoft renewal among the persona's lines.
            lines = persona_lines(r.persona_id)
            starts = [start_month(workshop, line_renewal(l)) for l in lines] or [
                start_month(workshop, engagement.microsoft_renewal_date)]
            latest = max(starts, key=lambda t: t[0])
            add(f"ms:{r.persona_id}", CONSOLIDATION, "microsoft_reduction",
                f"{r.persona_name}: Microsoft licensing change",
                -ms_change, None, persona_id=r.persona_id, start_override=latest)

    # ---- Over-licensing: unused seats the customer confirmed are not needed ----
    unused_lines: list[UnusedSeatLine] = []
    for idx, line in enumerate(engagement.current_licenses):
        unused = max(int(line.quantity_purchased) - int(line.quantity_assigned), 0)
        if unused <= 0:
            continue
        lid = line.id or f"line-{idx}"
        value = _money(Decimal(unused) * line.unit_price_paid_annual)
        answer = line.unused_seats_answer
        unused_lines.append(UnusedSeatLine(
            license_id=lid, sku_reference=line.sku_reference, unused_seats=unused,
            unit_price_annual=line.unit_price_paid_annual, annual_value=value,
            answer=answer.value if answer else None,
        ))
        if answer is UnusedSeatsAnswer.NOT_NEEDED:
            add(f"unused:{lid}", OVERLICENSING, "unused_seats",
                f"{line.sku_reference}: {unused} unused seats",
                value, line_renewal(line), license_id=lid)

    def total(sub_line):
        return _money(sum((i.amount for i in items if i.sub_line == sub_line), Decimal("0")))

    dup, cons, over = total(DUPLICATE_SPEND), total(CONSOLIDATION), total(OVERLICENSING)
    amount = _money(dup + cons + over)
    return Headline(
        horizon_months=horizon_months,
        workshop_date=workshop.isoformat() if workshop else None,
        duplicate_spend_amount=dup,
        consolidation_amount=cons,
        overlicensing_amount=over,
        amount=amount,
        direction="saved" if amount > 0 else "added" if amount < 0 else "none",
        run_rate_annual=_money(sum((i.annual_amount for i in items), Decimal("0"))),
        items=items,
        unused_seat_lines=unused_lines,
    )
