"""Editable AI instruction templates (the AiPrompt first-class object).

Every AI function reads its system instructions from here, so the prompt that is
consistently being sent is visible and tunable in one place. Seeded from
seeds/ai_prompts.json; missing keys are inserted on startup so a new AI function
gets its default on upgrade without wiping operator edits to existing ones.
"""

from __future__ import annotations

import functools
import json
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds")


@functools.lru_cache(maxsize=None)
def _seed() -> dict:
    with open(os.path.join(SEED_DIR, "ai_prompts.json"), encoding="utf-8") as fh:
        return json.load(fh)


def seed_defaults(db: Session) -> None:
    """Insert any seed prompt whose key isn't in the table yet. Never overwrites
    an existing row, so operator edits survive upgrades."""
    existing = {
        k for (k,) in db.execute(select(models.AiPrompt.key)).all()
    }
    changed = False
    for p in _seed()["prompts"]:
        if p["key"] in existing:
            continue
        db.add(models.AiPrompt(
            key=p["key"], label=p.get("label", p["key"]),
            description=p.get("description", ""), instructions=p["instructions"],
        ))
        changed = True
    if changed:
        db.commit()


def default_instructions(key: str) -> str:
    """The seeded default for a key (used for reset + fallback)."""
    for p in _seed()["prompts"]:
        if p["key"] == key:
            return p["instructions"]
    return ""


def get_instructions(db: Session, key: str) -> str:
    """The current (possibly operator-edited) instructions for an AI function,
    falling back to the seeded default if the row is somehow missing."""
    seed_defaults(db)
    row = db.execute(
        select(models.AiPrompt).where(models.AiPrompt.key == key)
    ).scalar_one_or_none()
    return row.instructions if row and row.instructions else default_instructions(key)


def list_prompts(db: Session) -> list[models.AiPrompt]:
    seed_defaults(db)
    return db.execute(
        select(models.AiPrompt).order_by(models.AiPrompt.label)
    ).scalars().all()


def update_instructions(db: Session, key: str, instructions: str) -> models.AiPrompt | None:
    row = db.execute(
        select(models.AiPrompt).where(models.AiPrompt.key == key)
    ).scalar_one_or_none()
    if row is None:
        return None
    row.instructions = instructions
    db.commit()
    db.refresh(row)
    return row


def reset_instructions(db: Session, key: str) -> models.AiPrompt | None:
    return update_instructions(db, key, default_instructions(key))
