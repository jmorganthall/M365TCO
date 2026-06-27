"""Seed-library loading (PRD 5.3.1 / Section 7).

The seed content is pending from the practice; the *structure* loads it from a
versioned seed file. On engagement creation we copy the default outcomes into
engagement-scoped Outcome rows and seed (ratified) Microsoft SKU coverage into
engagement-scoped CoverageMapEntry rows.
"""

from __future__ import annotations

import functools
import json
import os

from sqlalchemy.orm import Session

from .. import models

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds")


@functools.lru_cache(maxsize=None)
def load_outcomes() -> dict:
    with open(os.path.join(SEED_DIR, "outcomes.json"), encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=None)
def load_coverage() -> dict:
    with open(os.path.join(SEED_DIR, "coverage.json"), encoding="utf-8") as fh:
        return json.load(fh)


def outcome_library_version() -> str:
    return load_outcomes().get("version", "unknown")


def seed_engagement(db: Session, engagement: models.Engagement) -> None:
    """Copy default outcomes + Microsoft SKU coverage into the engagement."""
    outcomes = load_outcomes()["outcomes"]
    key_to_outcome: dict[str, models.Outcome] = {}
    for o in outcomes:
        row = models.Outcome(
            engagement_id=engagement.id,
            name=o["name"],
            description=o.get("description", ""),
            is_custom=False,
            seed_key=o["key"],
        )
        db.add(row)
        db.flush()  # assign id
        key_to_outcome[o["key"]] = row

    coverage = load_coverage()["skus"]
    for sku in coverage:
        for entry in sku["coverage"]:
            outcome = key_to_outcome.get(entry["outcome"])
            if outcome is None:
                continue  # seed references an outcome key not in the outcome seed
            db.add(
                models.CoverageMapEntry(
                    engagement_id=engagement.id,
                    outcome_id=outcome.id,
                    product_kind="MicrosoftSku",
                    microsoft_sku_reference=sku["sku_reference"],
                    coverage=entry["coverage"],
                    ai_suggested=False,
                    ratified=True,  # default library is pre-ratified
                )
            )
