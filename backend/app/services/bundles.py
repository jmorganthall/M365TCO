"""Staple Microsoft bundle library — the SKU → Bundle → Outcomes spine.

Global, editable, seeded from seeds/bundles.json. Bundles are the stable
identities that coverage, scenarios, and licenses resolve to; the many priced
catalog SKUs collapse onto a bundle via MicrosoftSku.bundle_id.
"""

from __future__ import annotations

import functools
import json
import os
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds")


@functools.lru_cache(maxsize=None)
def _seed() -> dict:
    with open(os.path.join(SEED_DIR, "bundles.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _seed_base_keys(entry: dict) -> list[str]:
    """The base keys an add-on seed entry declares — `bases: [...]` (multi) unioned
    with the single-`base` sugar. Empty for à-la-carte add-ons and base bundles."""
    keys = list(entry.get("bases") or [])
    if entry.get("base"):
        keys.append(entry["base"])
    # De-dup, preserve order.
    seen: set[str] = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


def seed_bundles(db: Session) -> None:
    """Insert any seed bundle whose key isn't present yet, then reconcile the
    add-on → base primary link and the M:N AddonEligibility set. Never overwrites
    operator edits to name/kind. Idempotent."""
    by_key = {b.key: b for b in db.execute(select(models.Bundle)).scalars().all()}
    changed = False
    for b in _seed()["bundles"]:
        if b["key"] not in by_key:
            row = models.Bundle(
                key=b["key"], name=b["name"], kind=b.get("kind", "bundle"),
                sort_order=b.get("sort_order", 0),
            )
            db.add(row)
            by_key[b["key"]] = row
            changed = True
    if changed:
        db.flush()
    # Resolve the primary add-on → base link now that every bundle row exists.
    for b in _seed()["bundles"]:
        base_keys = _seed_base_keys(b)
        row = by_key.get(b["key"])
        if base_keys and row is not None and row.base_bundle_id is None:
            base = by_key.get(base_keys[0])
            if base is not None:
                row.base_bundle_id = base.id
                changed = True
    if changed:
        db.flush()
    # Reconcile the eligibility set: ensure a row for every declared base of every
    # seeded add-on. Additive — never removes operator-added eligibilities.
    have = {
        (e.addon_bundle_id, e.base_bundle_id)
        for e in db.execute(select(models.AddonEligibility)).scalars().all()
    }
    for b in _seed()["bundles"]:
        row = by_key.get(b["key"])
        if row is None:
            continue
        for base_key in _seed_base_keys(b):
            base = by_key.get(base_key)
            if base is not None and (row.id, base.id) not in have:
                db.add(models.AddonEligibility(addon_bundle_id=row.id, base_bundle_id=base.id))
                have.add((row.id, base.id))
                changed = True
    if changed:
        db.commit()


def eligibility_map(db: Session) -> dict[str, set[str]]:
    """`{addon_bundle_id: {eligible base_bundle_id, …}}` for every add-on that has
    at least one eligibility row. An add-on ABSENT from this map is à-la-carte
    (eligible for any base) — see AddonEligibility."""
    out: dict[str, set[str]] = {}
    for e in db.execute(select(models.AddonEligibility)).scalars().all():
        out.setdefault(e.addon_bundle_id, set()).add(e.base_bundle_id)
    return out


def addon_applies(addon_id: str, base_id: str, elig_map: dict[str, set[str]]) -> bool:
    """True iff an add-on may layer onto a base: à-la-carte (no eligibility rows) →
    any base; otherwise the base must be in the add-on's eligibility set."""
    allowed = elig_map.get(addon_id)
    return allowed is None or base_id in allowed


def eligible_base_ids(db: Session, addon_id: str) -> list[str]:
    return [
        e.base_bundle_id
        for e in db.execute(
            select(models.AddonEligibility).where(
                models.AddonEligibility.addon_bundle_id == addon_id
            )
        ).scalars().all()
    ]


def set_addon_eligibility(db: Session, addon_id: str, base_ids: list[str]) -> list[str]:
    """Replace an add-on's eligible-base set (à-la-carte when empty). Returns the
    resulting base id list. Assumes caller validated the ids."""
    existing = db.execute(
        select(models.AddonEligibility).where(
            models.AddonEligibility.addon_bundle_id == addon_id
        )
    ).scalars().all()
    want = set(base_ids)
    have = {e.base_bundle_id: e for e in existing}
    for bid, row in have.items():
        if bid not in want:
            db.delete(row)
    for bid in want:
        if bid not in have:
            db.add(models.AddonEligibility(addon_bundle_id=addon_id, base_bundle_id=bid))
    db.commit()
    return eligible_base_ids(db, addon_id)


def list_bundles(db: Session) -> list[models.Bundle]:
    seed_bundles(db)
    return db.execute(
        select(models.Bundle).order_by(models.Bundle.sort_order, models.Bundle.name)
    ).scalars().all()


def _norm(s: str) -> str:
    return (s or "").lower().replace(" ", " ").replace("  ", " ").strip()


# Legacy shortcodes / common aliases → bundle key, so existing scenario targets
# and current-license references still resolve after the re-key.
_ALIASES = {
    "f1": "m365-f1", "f3": "m365-f3", "e3": "m365-e3", "e5": "m365-e5",
    "e7": "m365-e7", "business premium": "m365-business-premium",
    "o365 e1": "o365-e1", "o365 e3": "o365-e3", "o365 e5": "o365-e5",
    "entra id p2": "entra-id-p2", "defender for endpoint p2": "defender-endpoint-p2",
    "defender for office 365 p2": "defender-office-p2", "sentinel": "sentinel",
    "teams phone": "teams-phone", "power bi pro": "power-bi-pro",
    "power automate premium": "power-automate-premium",
}


def resolve_bundle(db: Session, ref: str) -> str | None:
    """Resolve a free-text SKU/bundle reference to a Bundle id, or None. Tiered:
    exact key, legacy alias, exact bundle name, then a mapped catalog SKU whose
    title matches. Read-only (assumes bundles are seeded)."""
    if not ref:
        return None
    r = _norm(ref)
    rows = db.execute(select(models.Bundle)).scalars().all()
    by_key = {b.key: b.id for b in rows}
    if r in by_key:
        return by_key[r]
    if r in _ALIASES and _ALIASES[r] in by_key:
        return by_key[_ALIASES[r]]
    for b in rows:
        if _norm(b.name) == r:
            return b.id
    like = f"%{ref}%"
    row = db.execute(
        select(models.MicrosoftSku).where(
            models.MicrosoftSku.bundle_id.isnot(None),
            (models.MicrosoftSku.sku_title.ilike(like))
            | (models.MicrosoftSku.product_title.ilike(like)),
        )
    ).scalars().first()
    return row.bundle_id if row else None


# Deterministic price-row ranking (see catalog_price_row). The requested quoting
# basis (term × billing plan, from the global → engagement → line hierarchy)
# wins outright; when that exact variant isn't in the sheet these fallback
# orders pick the closest one. Sheet ERPs are whole-term prices and variants
# carry billing premiums (~+5% monthly-billed, ~+20% month-to-month) — the
# P1Y/Annual row is the familiar published list price.
_TERM_FALLBACK = {"P1Y": 0, "P3Y": 1, "P1M": 2}
_BILLING_FALLBACK = {"Annual": 0, "Monthly": 1, "Triennial": 2}


def _pref_rank(value: str, preferred: str, fallback: dict[str, int]) -> int:
    if value == preferred:
        return 0
    return 1 + fallback.get(value, len(fallback))


def _title_candidates(rows, reference: str):
    """Catalog rows whose title matches the reference, tiered so broader matching
    never shadows a direct hit:
    1. reference contained in a title (classic), or the reference being exactly
       'Microsoft ' + a title (the sheet drops the prefix on some products —
       sheet 'Power BI Pro' vs bundle 'Microsoft Power BI Pro'; exact-with-prefix
       only, because loose containment let 'Microsoft 365 E5' rows answer for
       'Microsoft 365 E5 Security');
    2. only if tier 1 is empty: a title STARTING WITH the reference minus its
       'Microsoft ' prefix (e.g. 'Defender for Office 365 P2 Add On'). Starts-with,
       not contains, because stripped references get loose ('Microsoft 365 E3' →
       '365 e3' must not match Office 365 E3 titles)."""
    ref = _norm(reference)
    if not ref:
        return []

    def titles(r):
        return [t for t in (_norm(r.sku_title or ""), _norm(r.product_title or "")) if t]

    direct = [
        r for r in rows
        if any(ref in t or f"microsoft {t}" == ref for t in titles(r))
    ]
    if direct:
        return direct
    stripped = ref.removeprefix("microsoft ")
    if stripped == ref:
        return []
    return [r for r in rows if any(t.startswith(stripped) for t in titles(r))]


def engagement_price_basis(eng) -> dict:
    """The engagement's effective quoting basis — kwargs for catalog_annual_erp /
    catalog_price_row. Level 2 of the global → engagement → line hierarchy (an
    engagement copies the global defaults on creation; a scenario line may
    override term/billing on top of this)."""
    return {
        "segment": eng.default_segment or "Commercial",
        "term": eng.default_term_duration or "P1Y",
        "billing": eng.default_billing_plan or "Annual",
    }


def catalog_price_row(
    db: Session, sku_reference: str, *,
    bundle_id: str | None = None, segment: str = "Commercial",
    term: str = "P1Y", billing: str = "Annual",
) -> models.MicrosoftSku | None:
    """The catalog row that prices a bundle/SKU reference, chosen deterministically
    for a quoting basis (segment × term × billing plan — callers pass the
    engagement/scenario's effective basis; the out-of-box default is the familiar
    published list: a 1-year commit billed annually).

    Selection: rows RATIFIED onto the bundle (MicrosoftSku.bundle_id — the
    first-class SKU → Bundle mapping) when any exist; otherwise tolerant title
    matching (_title_candidates). The winner is then ranked: priced rows beat
    $0/trial rows, the requested segment beats other segments (the sheet ships
    Commercial/Education/Charity variants of the same title), the requested
    term and billing plan beat the fallback orders, an EXACT title match beats
    everything else at the same basis (an add-on can carry its parent family as
    its ProductTitle — 'Entra P2 Add On' under product 'Microsoft 365 E3'), and
    the shortest title wins remaining ties (the plain variant over '(no Teams)' /
    'Unattended' superstrings). Returns None when nothing matches."""
    rows: list[models.MicrosoftSku] = []
    if bundle_id is not None:
        rows = db.execute(
            select(models.MicrosoftSku).where(models.MicrosoftSku.bundle_id == bundle_id)
        ).scalars().all()
    if not rows:
        rows = _title_candidates(
            db.execute(select(models.MicrosoftSku)).scalars().all(), sku_reference
        )
    if not rows:
        return None

    ref = _norm(sku_reference)

    def rank(r: models.MicrosoftSku):
        return (
            0 if Decimal(str(r.annual_erp_price or 0)) > 0 else 1,
            0 if (r.segment or "") == segment else 1,
            _pref_rank(r.term_duration or "", term, _TERM_FALLBACK),
            _pref_rank(r.billing_plan or "", billing, _BILLING_FALLBACK),
            0 if _norm(r.sku_title or "") == ref else 1,
            0 if _norm(r.product_title or "") == ref else 1,
            len(r.sku_title or r.product_title or ""),
        )

    return min(rows, key=rank)


def catalog_annual_erp(
    db: Session, sku_reference: str, *,
    bundle_id: str | None = None, segment: str = "Commercial",
    term: str = "P1Y", billing: str = "Annual",
) -> Decimal:
    """Catalog price for a bundle: the annual ERP (the customer-facing retail
    baseline; sheet prices are annualized on import, so ÷12 is the monthly
    payment) of the deterministically-selected row — see catalog_price_row.
    0 if the catalog isn't loaded / no match. Shared by the recommend-a-path
    optimizer (services/compute), the Business Premium swap (services/swap),
    the scenario requote (routers/entities), and the bundle-list autofill
    (routers/catalog) so all price identically."""
    row = catalog_price_row(
        db, sku_reference, bundle_id=bundle_id, segment=segment, term=term, billing=billing,
    )
    return Decimal(str(row.annual_erp_price)) if row else Decimal("0")
