"""Per-scenario business narrative (PRD Section 9 — advisory only).

For each in-scope persona scenario, generate the story an SA tells on the call:
what the persona has today, what the target Microsoft licensing adds, and the
value it brings (capability + consolidation of displaced third-party tools +
the hard-dollar delta). Advisory: it writes nothing to the model of record and
never feeds the math — a draft the SA reviews and edits verbally.

The payload builder is pure and I/O-free (unit-testable without a live model);
the HTTP call lives in `services/ai.scenario_narratives`.
"""

from __future__ import annotations


def build_customer_context(eng) -> dict:
    """Customer-identity grounding for the narrative prompt — the Customer Info
    fields (who they are, industry, HQ, size, operator notes). The prompt uses
    this (plus live web search when the operator enabled it) to consider the
    customer's market direction, recent headlines, and M&A activity, and weave
    supportable context into the narrative. Pure; empty fields are omitted."""
    return {
        k: v for k, v in {
            "name": eng.customer_name,
            "industry": eng.industry,
            "hq_location": eng.hq_location,
            "website": eng.website,
            "employee_count": eng.employee_count,
            "notes": eng.notes,
        }.items() if v
    }


def build_narrative_payload(eng, result: dict) -> list[dict]:
    """One grounded narrative-input dict per in-scope scenario: the persona and
    headcount, the SKUs they hold today, the target bundle + add-ons, the
    third-party tools the move displaces, and the annual current/target/delta.
    Pure: takes the ORM engagement and the serialized compute result."""
    # Current SKUs a persona holds today, from the engagement's licenses (tags).
    skus_by_persona: dict[str, list[str]] = {}
    for lic in eng.current_licenses:
        ref = (lic.sku_reference or "").strip()
        if not ref:
            continue
        for pid in (lic.persona_ids or []):
            skus_by_persona.setdefault(pid, []).append(ref)

    # Add-on bundle names per scenario (base is target_sku_reference in result).
    bundle_name = {b.id: b.name for b in _bundles(eng)}
    addons_by_persona: dict[str, list[str]] = {}
    for s in eng.scenarios:
        addons_by_persona[s.persona_id] = [
            bundle_name.get(a.bundle_id, a.bundle_id) for a in s.addons
        ]

    out = []
    for s in result.get("scenarios", []) or []:
        if not s.get("in_scope"):
            continue
        pid = s.get("persona_id")
        out.append({
            "persona": s.get("persona_name"),
            "headcount": s.get("headcount"),
            "current_skus": skus_by_persona.get(pid, []),
            "target_bundle": s.get("target_sku_reference"),
            "target_addons": addons_by_persona.get(pid, []),
            "displaced_tools": [o.get("third_party_product_name") for o in s.get("offsets", []) or []],
            "current_annual": s.get("current_spend_annual"),
            "target_annual": s.get("target_spend_annual"),
            "delta_annual": s.get("delta_annual"),
        })
    return out


def _bundles(eng):
    """The global bundle library, via the engagement's session. Falls back to an
    empty list if unavailable (add-on ids then show as ids, never crash)."""
    from sqlalchemy import inspect as _inspect
    from sqlalchemy import select
    from .. import models

    session = _inspect(eng).session
    if session is None:
        return []
    return session.execute(select(models.Bundle)).scalars().all()


def normalize_narratives(data: dict, personas: list[str]) -> list[dict]:
    """Pure: model output -> validated [{persona, today, whats_new, value}].
    Keeps only entries whose `persona` matches a real in-scope persona, requires
    a non-empty value line, and de-dupes by persona. Kept separate from the HTTP
    call for unit testing."""
    valid = set(personas)
    out, seen = [], set()
    for n in data.get("narratives", []) or []:
        who = (n.get("persona") or "").strip()
        if who not in valid or who in seen:
            continue
        value = (n.get("value") or "").strip()
        if not value:
            continue
        seen.add(who)
        out.append({
            "persona": who,
            "today": (n.get("today") or "").strip(),
            "whats_new": (n.get("whats_new") or "").strip(),
            "value": value,
        })
    return out
