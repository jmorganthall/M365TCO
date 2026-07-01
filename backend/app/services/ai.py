"""OpenRouter coverage-suggestion client (PRD Section 9).

AI is advisory and never writes a final number. Coverage suggestions are written
as CoverageMapEntry rows with ai_suggested=true, ratified=false. They do not
enter the math until a human ratifies (enforced in compute hydration, not here).
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


def suggest_coverage(
    product_name: str, outcomes: list[dict], model: str | None = None
) -> list[dict]:
    """Ask the model which outcomes a third-party product delivers.

    `outcomes` is a list of {id, name, description}. Returns a list of
    {outcome_id, coverage, rationale}. Caller persists these as unratified
    ai_suggested CoverageMapEntry rows. `model` overrides the configured model.
    """
    api_key = _api_key()
    model = model or settings.openrouter_model

    outcome_lines = "\n".join(
        f"- id={o['id']} | {o['name']}: {o.get('description', '')}" for o in outcomes
    )
    system = (
        "You are a Microsoft 365 licensing analyst assisting a TCO workshop. "
        "Given a third-party security/productivity product and a list of capability "
        "outcomes, decide which outcomes the product delivers. Respond ONLY with a "
        "JSON object: {\"suggestions\": [{\"outcome_id\": \"...\", \"coverage\": "
        "\"Full\"|\"Partial\", \"rationale\": \"short\"}]}. Include only outcomes the "
        "product genuinely delivers. Coverage is Full if the product fully delivers "
        "the outcome, Partial if it covers part of it."
    )
    user = f"Product: {product_name}\n\nOutcomes:\n{outcome_lines}"

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
        data = json.loads(content)
    except json.JSONDecodeError:
        # Some models wrap JSON in prose; salvage the object.
        start, end = content.find("{"), content.rfind("}")
        data = json.loads(content[start : end + 1]) if start >= 0 else {"suggestions": []}

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
