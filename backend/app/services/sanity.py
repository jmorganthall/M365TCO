"""Pre-readout AI sanity check (PRD Section 9 — advisory only).

Before an SA shows a readout on a live call, this runs a cheap "does the data
we gave you make sense?" pass over the engagement's inputs and the computed
result, and returns a list of findings (anomalies, likely mistakes, internal
contradictions) for the operator to eyeball. It is ADVISORY: it never edits data
and never enters the math — exactly like coverage suggestions and the parsers.

The payload builder is pure and I/O-free so it can be unit-tested without a live
model; the HTTP call lives in `services/ai.sanity_check`.
"""

from __future__ import annotations

SEVERITIES = ("error", "warn", "info")


def build_sanity_payload(eng, result: dict) -> dict:
    """Assemble a compact, model-friendly summary of an engagement's inputs and
    computed rollup. Pure: takes the ORM engagement and the serialized compute
    result, returns a plain dict. Kept small on purpose — the check reasons over
    the shape of the numbers, not every row."""
    rollup = result.get("rollup", {}) or {}
    pop = rollup.get("population_check", {}) or {}
    scenarios = []
    for s in result.get("scenarios", []) or []:
        scenarios.append({
            "persona": s.get("persona_name"),
            "in_scope": s.get("in_scope"),
            "current_annual": s.get("current_spend_annual"),
            "target_annual": s.get("target_spend_annual"),
            "delta_annual": s.get("delta_annual"),
        })
    licenses = [
        {
            "sku": l.sku_reference,
            "qty_purchased": l.quantity_purchased,
            "qty_assigned": l.quantity_assigned,
            "unit_price_annual": float(l.unit_price_paid_annual or 0),
            "segment": l.segment or eng.default_segment,
        }
        for l in eng.current_licenses
    ]
    third_party = [
        {
            "name": t.name, "annual_cost": float(t.annual_cost or 0),
            "covered_count": t.covered_count, "is_managed": t.is_managed,
        }
        for t in eng.third_party_products
    ]
    return {
        "customer": eng.customer_name,
        "market": eng.market,
        "currency": eng.currency,
        "default_segment": eng.default_segment,
        "personas": [
            {"name": p.name, "headcount": p.headcount} for p in eng.personas
        ],
        "current_licenses": licenses,
        "third_party_products": third_party,
        "scenarios": scenarios,
        "rollup": {
            "net_tco_delta_annual": rollup.get("net_tco_delta_annual"),
            "in_scope_headcount": pop.get("in_scope_persona_headcount"),
            "third_party_covered_population": pop.get("third_party_covered_population"),
        },
    }


def normalize_findings(data: dict) -> list[dict]:
    """Pure: model output -> validated findings [{severity, field, message}].
    Kept separate from the HTTP call for unit testing. Clamps severity to the
    known set, requires a non-empty message, and drops anything malformed rather
    than surfacing junk on a customer call."""
    out = []
    for f in data.get("findings", []) or []:
        message = (f.get("message") or "").strip()
        if not message:
            continue
        sev = (f.get("severity") or "").strip().lower()
        sev = sev if sev in SEVERITIES else "info"
        out.append({
            "severity": sev,
            "field": (f.get("field") or "").strip(),
            "message": message,
        })
    # Most severe first so the worst problems lead on the readout.
    order = {s: i for i, s in enumerate(SEVERITIES)}
    out.sort(key=lambda f: order.get(f["severity"], 99))
    return out
