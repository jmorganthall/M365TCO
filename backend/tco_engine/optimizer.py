"""Best-bundle analysis — pure, I/O-free (companion to engine.py).

Given a persona and a set of candidate Microsoft bundles, evaluate each bundle's
target-state TCO and rank them. This does NOT change the reconciliation engine;
it reuses the same displacement rule (a bundle displaces a third-party product
when the bundle covers every outcome that product delivers) and the same
linear-by-user offset, applied per candidate bundle.

Ranking (v1): among bundles that cover every REQUIRED outcome (no capability
gap), the one with the highest annual delta (savings vs today) is recommended.
Bundles with gaps are returned and flagged, but never recommended.

Seat caps (v2): a candidate may belong to a seat-capped bundle family (e.g.
Microsoft 365 Business is limited to 300 seats per tenant). The caller passes the
remaining headroom for such references; a candidate whose whole persona headcount
would not fit the remaining seats is flagged `cap_limited` and, like a gapped or
unpriced bundle, is returned but never recommended — the optimizer falls through to
the next-best bundle that is not seat-capped. This keeps the recommendation from
proposing a Business plan for more users than the tenant may license.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .models import ThirdPartyProduct

CENTS = Decimal("0.01")


def _money(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENTS)


@dataclass(frozen=True)
class CandidateBundle:
    sku_reference: str
    covered_outcome_ids: frozenset[str]
    target_unit_price_annual: Decimal


@dataclass
class BundleAnalysis:
    sku_reference: str
    target_unit_price_annual: Decimal
    target_spend_annual: Decimal
    current_spend_annual: Decimal
    delta_annual: Decimal
    third_party_offset_annual: Decimal
    covered_required_outcome_ids: list[str]
    gap_outcome_ids: list[str]
    added_outcome_ids: list[str]
    displaced_product_ids: list[str]
    covers_all_required: bool
    price_known: bool
    recommended: bool = False
    # Seat-cap gate: True when this candidate is a seat-capped family (e.g. M365
    # Business) and the persona's headcount exceeds the remaining seats under the
    # tenant cap. cap_headroom is the remaining seats for that family (None = the
    # candidate is not seat-capped). A cap_limited candidate is never recommended.
    cap_limited: bool = False
    cap_headroom: int | None = None


def analyze_bundles(
    headcount: int,
    current_microsoft_annual: Decimal,
    required_outcome_ids: frozenset[str],
    current_capability_outcome_ids: frozenset[str],
    candidates: list[CandidateBundle],
    third_party_products: list[ThirdPartyProduct],
    cap_headroom_by_reference: dict[str, int] | None = None,
) -> list[BundleAnalysis]:
    """`cap_headroom_by_reference` maps a candidate's `sku_reference` to the seats
    still available under a tenant cap its bundle family shares (e.g. all Business
    references → the seats left under the 300 cap). A candidate whose reference is in
    the map and whose `headcount` exceeds that headroom is flagged `cap_limited` and
    excluded from the recommendation. Omit (or pass empty) for no cap awareness."""
    caps = cap_headroom_by_reference or {}
    results: list[BundleAnalysis] = []

    for c in candidates:
        offset = Decimal("0")
        displaced: list[str] = []
        for p in third_party_products:
            # Same displacement test as the engine (6.6).
            if p.delivered_outcome_ids and p.delivered_outcome_ids.issubset(
                c.covered_outcome_ids
            ):
                offset += Decimal(headcount) * p.per_unit_annual_cost
                displaced.append(p.id)

        offset = _money(offset)
        target_spend = _money(Decimal(headcount) * c.target_unit_price_annual)
        current_spend = _money(current_microsoft_annual + offset)
        # Cost-change convention: delta = new − old (+ = costs more, − = saves).
        delta = _money(target_spend - current_spend)

        gaps = sorted(required_outcome_ids - c.covered_outcome_ids)
        covered_req = sorted(required_outcome_ids & c.covered_outcome_ids)
        # Capabilities the bundle delivers that they do NOT have today — the
        # upside story (new outcomes), even when the delta is a net increase.
        added = sorted(c.covered_outcome_ids - current_capability_outcome_ids)

        # Seat-cap gate: only when the caller supplied headroom for this reference.
        headroom = caps.get(c.sku_reference)
        cap_limited = headroom is not None and headcount > headroom

        results.append(
            BundleAnalysis(
                sku_reference=c.sku_reference,
                target_unit_price_annual=c.target_unit_price_annual,
                target_spend_annual=target_spend,
                current_spend_annual=current_spend,
                delta_annual=delta,
                third_party_offset_annual=offset,
                covered_required_outcome_ids=covered_req,
                gap_outcome_ids=gaps,
                added_outcome_ids=added,
                displaced_product_ids=displaced,
                covers_all_required=not gaps,
                price_known=c.target_unit_price_annual > 0,
                cap_limited=cap_limited,
                cap_headroom=headroom,
            )
        )

    # Recommend the biggest-saving bundle (lowest cost-change delta) that has no
    # capability gap, a known price, and is not seat-capped for this headcount.
    # (Delta ties broken by lower target spend.)
    best = None
    for r in results:
        if not r.covers_all_required or not r.price_known or r.cap_limited:
            continue
        if best is None or r.delta_annual < best.delta_annual or (
            r.delta_annual == best.delta_annual
            and r.target_spend_annual < best.target_spend_annual
        ):
            best = r
    if best is not None:
        best.recommended = True

    # Sort: recommended first, then no-gap, then within-cap, then biggest saving.
    results.sort(
        key=lambda r: (not r.recommended, not r.covers_all_required, r.cap_limited, r.delta_annual)
    )
    return results
