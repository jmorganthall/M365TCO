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
    out = []
    for s in data.get("suggestions", []):
        if s.get("outcome_id") in valid_ids and s.get("coverage") in ("Full", "Partial"):
            out.append(
                {
                    "outcome_id": s["outcome_id"],
                    "coverage": s["coverage"],
                    "rationale": s.get("rationale", ""),
                }
            )
    return out


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
