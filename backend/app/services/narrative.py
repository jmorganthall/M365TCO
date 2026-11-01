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


def build_narrative_payload(eng, result: dict, interpreted) -> list[dict]:
    """One grounded narrative-input dict per in-scope scenario: the persona and
    headcount, the licences they hold today, the target bundle + add-ons, the
    third-party tools the move displaces, and the annual current/target/delta.
    Pure: what each persona holds is the interpreter's (`interpreted`, lines tagged
    to it or Org-wide); everything else is the serialized result."""
    raw_lines = {l.id: l for l in interpreted.raw.licence_lines}
    skus_by_persona: dict[str, list[str]] = {
        p.id: [ref for l in interpreted.lines_for(p.id)
               if (ref := (raw_lines[l.id].sku_reference or "").strip())]
        for p in interpreted.personas
    }
    # The plan's add-on names, as every readout labels them (compute.attach_target_labels).
    addons_by_persona = {s.get("persona_id"): s.get("target_addons") or []
                         for s in result.get("scenarios", []) or []}

    # Outcomes the move lights up per persona (computed by services/compute and
    # attached to the result) — the concrete "what's new" the prompt grounds in.
    new_by_pid = {
        n["persona_id"]: [o["name"] for o in n.get("outcomes", [])]
        for n in (result.get("new_outcomes") or [])
    }
    # Capabilities the move DROPS per persona — so the narrative stays honest and
    # never sells a right-sizing as pure gain when it sheds capability.
    dropped_by_pid = {
        d["persona_id"]: [o["name"] for o in d.get("outcomes", [])]
        for d in (result.get("dropped_capability") or [])
    }

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
            "new_outcomes": new_by_pid.get(pid, []),
            "dropped_outcomes": dropped_by_pid.get(pid, []),
            "current_annual": s.get("current_spend_annual"),
            "target_annual": s.get("target_spend_annual"),
            "delta_annual": s.get("delta_annual"),
        })
    return out


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
