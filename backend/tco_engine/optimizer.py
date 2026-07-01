"""Best-bundle analysis — pure, I/O-free (companion to engine.py).

Given a persona and a set of candidate Microsoft bundles, evaluate each bundle's
target-state TCO and rank them. This does NOT change the reconciliation engine;
it reuses the same displacement rule (a bundle displaces a third-party product
when the bundle covers every outcome that product delivers) and the same
linear-by-user offset, applied per candidate bundle.

Ranking (v1): among bundles that cover every REQUIRED outcome (no capability
gap), the one with the highest annual delta (savings vs today) is recommended.
Bundles with gaps are returned and flagged, but never recommended.
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


def analyze_bundles(
    headcount: int,
    current_microsoft_annual: Decimal,
    required_outcome_ids: frozenset[str],
    current_capability_outcome_ids: frozenset[str],
    candidates: list[CandidateBundle],
    third_party_products: list[ThirdPartyProduct],
) -> list[BundleAnalysis]:
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
        delta = _money(current_spend - target_spend)

        gaps = sorted(required_outcome_ids - c.covered_outcome_ids)
        covered_req = sorted(required_outcome_ids & c.covered_outcome_ids)
        # Capabilities the bundle delivers that they do NOT have today — the
        # upside story (new outcomes), even when the delta is a net increase.
        added = sorted(c.covered_outcome_ids - current_capability_outcome_ids)

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
            )
        )

    # Recommend the highest-delta bundle that has no capability gap and a known
    # price. (Delta ties broken by lower target spend.)
    best = None
    for r in results:
        if not r.covers_all_required or not r.price_known:
            continue
        if best is None or r.delta_annual > best.delta_annual or (
            r.delta_annual == best.delta_annual
            and r.target_spend_annual < best.target_spend_annual
        ):
            best = r
    if best is not None:
        best.recommended = True

    # Sort: recommended first, then no-gap, then highest savings.
    results.sort(
        key=lambda r: (not r.recommended, not r.covers_all_required, -r.delta_annual)
    )
    return results
