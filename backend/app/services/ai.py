"""OpenRouter AI client (PRD Section 9).

AI is advisory and never writes a final number. Coverage suggestions are written
as CoverageMapEntry rows with ai_suggested=true, ratified=false; the third-party
parser only returns rows for the operator to review. Nothing here enters the math
until a human accepts it. Every function's system prompt is an editable AiPrompt
passed in by the caller, not hard-coded.
"""

from __future__ import annotations

import json

import httpx

from ..config import settings
from . import secrets


class AIDisabledError(RuntimeError):
    pass


def is_enabled() -> bool:
    store = secrets.get_store()
    return store.enabled and bool(store.get(secrets.OPENROUTER_API_KEY))


def _api_key() -> str:
    store = secrets.get_store()
    key = store.get(secrets.OPENROUTER_API_KEY) if store.enabled else None
    if not key:
        raise AIDisabledError(
            "OpenRouter key not configured. Set it via the secret store."
        )
    return key


def list_models() -> list[dict]:
    """Fetch the operator's available OpenRouter models so the UI can offer a
    live picker (avoids hardcoding a model slug that may be retired)."""
    key = _api_key()
    resp = httpx.get(
        f"{settings.openrouter_base_url}/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    resp.raise_for_status()
    out = []
    for m in resp.json().get("data", []):
        mid = m.get("id")
        if mid:
            out.append({"id": mid, "name": m.get("name", mid)})
    # Anthropic models first, then alphabetical.
    out.sort(key=lambda m: (not m["id"].startswith("anthropic/"), m["id"]))
    return out


def _chat_json(system: str, user: str, model: str | None) -> dict:
    """POST a system+user turn to OpenRouter and return the parsed JSON object.

    Shared by every AI function so the auth, JSON-mode, error surfacing, and
    prose-wrapped-JSON salvage live in one place. `system` is the editable
    instruction template (an AiPrompt); `user` is the call-specific payload.
    """
    api_key = _api_key()
    model = model or settings.openrouter_model
    resp = httpx.post(
        f"{settings.openrouter_base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "M365 TCO Tool",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Surface OpenRouter's own message (e.g. an invalid/retired model id)
        # instead of a bare "404 Not Found".
        raise RuntimeError(
            f"OpenRouter {resp.status_code} for model '{model}': {resp.text[:400]}"
        ) from exc
    content = resp.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Some models wrap JSON in prose; salvage the object.
        start, end = content.find("{"), content.rfind("}")
        return json.loads(content[start : end + 1]) if start >= 0 else {}


def suggest_coverage(
    product_name: str, outcomes: list[dict], instructions: str, model: str | None = None
) -> list[dict]:
    """Ask the model which outcomes a third-party product delivers.

    `outcomes` is a list of {id, name, description}. `instructions` is the
    editable system prompt (an AiPrompt). Returns a list of
    {outcome_id, coverage, rationale}. Caller persists these as unratified
    ai_suggested CoverageMapEntry rows. `model` overrides the configured model.
    """
    outcome_lines = "\n".join(
        f"- id={o['id']} | {o['name']}: {o.get('description', '')}" for o in outcomes
    )
    user = f"Product: {product_name}\n\nOutcomes:\n{outcome_lines}"
    data = _chat_json(instructions, user, model)

    valid_ids = {o["id"] for o in outcomes}
    out, seen = [], set()
    # Coverage is binary: any outcome the model returns is "covered" (stored as
    # the single "Full" marker). We only validate the id and de-dupe.
    for s in data.get("suggestions", []):
        oid = s.get("outcome_id")
        if oid in valid_ids and oid not in seen:
            seen.add(oid)
            out.append({"outcome_id": oid, "coverage": "Full", "rationale": s.get("rationale", "")})
    return out


def normalize_bundle_suggestions(
    data: dict, valid_sku_ids: set[str], valid_bundle_keys: set[str]
) -> list[dict]:
    """Pure: model output -> validated [{sku_id, bundle_key, reason}]. Kept separate
    from the HTTP call for unit testing. Drops rows whose sku_id isn't a real
    catalog row (or is repeated), and clamps bundle_key to a known bundle — an
    explicit non-match is kept as bundle_key=None so the caller records "no match"
    rather than silently mapping."""
    out, seen = [], set()
    for m in data.get("mappings", []) or []:
        sku_id = (m.get("sku_id") or "").strip()
        if sku_id not in valid_sku_ids or sku_id in seen:
            continue
        seen.add(sku_id)
        key = (m.get("bundle_key") or "").strip()
        out.append({
            "sku_id": sku_id,
            "bundle_key": key if key in valid_bundle_keys else None,
            "reason": (m.get("reason") or "").strip(),
        })
    return out


def suggest_bundle_mappings(
    skus: list[dict], bundles: list[dict], instructions: str, model: str | None = None
) -> list[dict]:
    """Classify priced catalog SKUs onto staple bundles. `skus` is a list of
    {id, product_title, sku_title}; `bundles` is a list of {key, name, kind}.
    `instructions` is the editable system prompt (an AiPrompt). Returns a list of
    {sku_id, bundle_key|None, reason} for the caller to persist as UNRATIFIED
    suggested_bundle_id — nothing is decided here."""
    sku_lines = "\n".join(
        f"- sku_id={s['id']} | {s.get('product_title', '')} | {s.get('sku_title', '')}"
        for s in skus
    )
    bundle_lines = "\n".join(
        f"- key={b['key']} | {b['name']} ({b['kind']})" for b in bundles
    )
    user = f"SKUs to classify:\n{sku_lines}\n\nBundles:\n{bundle_lines}"
    data = _chat_json(instructions, user, model)
    return normalize_bundle_suggestions(
        data, {s["id"] for s in skus}, {b["key"] for b in bundles}
    )


def _to_number(value) -> float:
    """Best-effort numeric parse: strips $, commas, spaces; non-numeric -> 0."""
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    cleaned = "".join(c for c in str(value) if c.isdigit() or c in ".-")
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def _to_bool(value) -> bool:
    """Coerce a model-returned managed flag. Note bool('false') is True, so
    strings must be compared explicitly rather than via bool()."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "yes", "1", "managed")


def normalize_parsed_products(data: dict) -> list[dict]:
    """Pure, model-output -> validated third-party rows. Kept separate from the
    HTTP call so it can be unit-tested without a live model."""
    out = []
    for p in data.get("products", []) or []:
        name = (p.get("name") or "").strip()
        if not name:
            continue  # a product must have a name
        period = p.get("cost_period")
        period = period if period in ("Monthly", "Annual") else "Annual"
        out.append({
            "name": name,
            "vendor": (p.get("vendor") or "").strip(),
            "raw_cost": _to_number(p.get("raw_cost")),
            "cost_period": period,
            "covered_count": int(_to_number(p.get("covered_count"))),
            "is_managed": _to_bool(p.get("is_managed")),
        })
    return out


def parse_third_party(raw_text: str, instructions: str, model: str | None = None) -> list[dict]:
    """Parse a block of customer-provided text into third-party product rows.

    `instructions` is the editable system prompt (an AiPrompt). Returns a list of
    {name, vendor, raw_cost, cost_period, covered_count} for the caller to show
    for review — nothing is persisted here.
    """
    data = _chat_json(instructions, raw_text, model)
    return normalize_parsed_products(data)


# Price cadence -> annual multiplier for existing-license import.
_PRICE_PERIOD_FACTOR = {"Monthly": 12, "Quarterly": 4, "Annual": 1}


def normalize_parsed_licenses(data: dict) -> list[dict]:
    """Pure, model-output -> validated existing-license rows. Normalizes the
    stated price to an annual PER-SEAT figure so it can be stored directly:
    annualize by cadence (monthly x12, quarterly x4), and divide a line Total by
    the quantity. Kept separate from the HTTP call for unit testing."""
    out = []
    for l in data.get("licenses", []) or []:
        desc = (l.get("product_description") or "").strip()
        if not desc:
            continue  # a line must name a product
        qty = int(_to_number(l.get("license_quantity")))
        price = _to_number(l.get("price"))
        period = l.get("price_period")
        period = period if period in _PRICE_PERIOD_FACTOR else "Annual"
        scope = l.get("price_scope")
        scope = scope if scope in ("PerSeat", "Total") else "PerSeat"

        annual = price * _PRICE_PERIOD_FACTOR[period]
        if scope == "Total" and qty > 0:
            annual = annual / qty
        out.append({
            "product_description": desc,
            "license_quantity": qty,
            "price": price,
            "price_period": period,
            "price_scope": scope,
            # Convenience for the caller: the annual per-seat we'd store.
            "unit_price_paid_annual": round(annual, 4),
        })
    return out


def normalize_customer_research(data: dict) -> dict:
    """Pure: model output -> validated suggested customer-info fields. Returns only
    the keys the model gave a non-empty value for; `employee_count` coerced to a
    positive int. Kept separate from the HTTP call for unit testing."""
    src = data.get("customer") if isinstance(data.get("customer"), dict) else data
    out: dict = {}
    for k in ("industry", "hq_location", "website", "description"):
        v = src.get(k)
        v = v.strip() if isinstance(v, str) else v
        if v:
            out[k] = str(v)
    # A bare domain, not a URL.
    if "website" in out:
        out["website"] = out["website"].removeprefix("https://").removeprefix("http://").rstrip("/")
    n = int(_to_number(src.get("employee_count")))
    if n > 0:
        out["employee_count"] = n
    return out


def research_customer(known: dict, instructions: str, model: str | None = None) -> dict:
    """Given whatever is known about a customer (at minimum a name, maybe a location
    or website), ask the model to fill in the rest — industry, HQ location, website,
    employee count, and a short description — from its knowledge. `instructions` is
    the editable system prompt (an AiPrompt). Advisory: the caller shows the
    suggestions for review and fills empty fields only; nothing is persisted here.
    Returns a dict of just the fields the model was confident about."""
    lines = [f"{k}: {v}" for k, v in known.items() if v]
    user = "Known about the customer company:\n" + ("\n".join(lines) if lines else "(name only)")
    data = _chat_json(instructions, user, model)
    return normalize_customer_research(data)


def sanity_check(summary: dict, instructions: str, model: str | None = None) -> list[dict]:
    """Run the pre-readout "does this make sense?" pass over a compact engagement
    summary (built by services/sanity.build_sanity_payload). `instructions` is the
    editable system prompt (an AiPrompt). Returns advisory findings
    [{severity, field, message}] — nothing is persisted or fed to the math. The
    caller resolves an inexpensive `model` for this frequent, low-stakes check."""
    from .sanity import normalize_findings

    user = json.dumps(summary, default=str, indent=2)
    data = _chat_json(instructions, user, model)
    return normalize_findings(data)


def scenario_narratives(
    scenarios: list[dict], instructions: str, model: str | None = None
) -> list[dict]:
    """Draft a per-scenario business narrative (today / what's new / value) from
    the grounded inputs built by services/narrative.build_narrative_payload.
    `instructions` is the editable system prompt (an AiPrompt). Returns
    [{persona, today, whats_new, value}] for the operator to review — nothing is
    persisted or fed to the math."""
    from .narrative import normalize_narratives

    user = json.dumps({"scenarios": scenarios}, default=str, indent=2)
    data = _chat_json(instructions, user, model)
    return normalize_narratives(data, [s.get("persona") for s in scenarios])


def parse_current_licenses(raw_text: str, instructions: str, model: str | None = None) -> list[dict]:
    """Parse a block of customer-provided text into existing-license rows.

    `instructions` is the editable system prompt (an AiPrompt). Returns rows with
    the stated price/period/scope plus a normalized annual per-seat price, for the
    caller to review — nothing is persisted here.
    """
    data = _chat_json(instructions, raw_text, model)
    return normalize_parsed_licenses(data)
