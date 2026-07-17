"""Seed-library loading (PRD 5.3.1 / Section 7).

The default outcome library lives in a first-class, editable table
(DefaultOutcome), seeded on first run from the versioned seeds/outcomes.json.
On engagement creation we copy the default outcomes into engagement-scoped
Outcome rows and seed (ratified) Microsoft SKU coverage into engagement-scoped
CoverageMapEntry rows. Editing the global defaults never mutates existing
engagements — they hold their own copy.
"""

from __future__ import annotations

import functools
import json
import os
import re

from sqlalchemy import select
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


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "outcome"


def seed_default_outcomes(db: Session) -> None:
    """Populate the DefaultOutcome table from the seed file if it is empty."""
    exists = db.execute(select(models.DefaultOutcome.id).limit(1)).first()
    if exists:
        return
    for i, o in enumerate(load_outcomes()["outcomes"]):
        db.add(
            models.DefaultOutcome(
                key=o["key"], name=o["name"],
                description=o.get("description", ""), sort_order=i,
            )
        )
    db.commit()


def coverage_library_version() -> str:
    return load_coverage().get("version", "unknown")


def seed_default_coverage(db: Session) -> None:
    """Populate the DefaultBundleCoverage table from the seed file if it is empty.
    The template for Microsoft bundle coverage new engagements inherit."""
    exists = db.execute(select(models.DefaultBundleCoverage.id).limit(1)).first()
    if exists:
        return
    for item in load_coverage()["bundles"]:
        for entry in item["coverage"]:
            db.add(
                models.DefaultBundleCoverage(
                    bundle_key=item["bundle"],
                    outcome_key=entry["outcome"],
                    coverage=entry["coverage"],
                )
            )
    db.commit()


def sync_engagement_outcomes(db: Session, engagement: models.Engagement) -> dict:
    """FULL OVERWRITE of an engagement's outcome set to the CURRENT global
    default library — the 'Update outcomes' domain action. Destructive by
    design (the GUI confirms before calling):

    - a library outcome the engagement already holds (matched by its stable
      `seed_key`) is KEPT — same row id, so its coverage mappings and persona
      requirements survive — with name/description reset to the library's
      current values;
    - a library outcome the engagement lacks is ADDED, wired with its default
      Microsoft bundle coverage;
    - anything else — CUSTOM outcomes and retired seed keys — is DELETED,
      together with its coverage entries and persona-requirement links;
    - default Microsoft coverage pairs missing on kept outcomes are added
      (ratified), so a library change like a new bundle mapping arrives too.
      Operator-added coverage on kept outcomes is not touched.

    Returns a summary dict for the GUI: {added, updated, removed, coverage_added}.
    """
    defaults = db.execute(
        select(models.DefaultOutcome).order_by(models.DefaultOutcome.sort_order)
    ).scalars().all()
    default_keys = {o.key for o in defaults}
    by_seed_key = {o.seed_key: o for o in engagement.outcomes if o.seed_key}

    added: list[str] = []
    removed: list[str] = []
    updated = 0

    # Delete customs + retired keys, with their dependents.
    for o in list(engagement.outcomes):
        if o.seed_key in default_keys:
            continue
        removed.append(o.name)
        for c in db.execute(
            select(models.CoverageMapEntry).where(models.CoverageMapEntry.outcome_id == o.id)
        ).scalars():
            db.delete(c)
        for r in db.execute(
            select(models.PersonaRequirement).where(models.PersonaRequirement.outcome_id == o.id)
        ).scalars():
            db.delete(r)
        db.delete(o)

    # Keep-and-reset matches; add what's missing.
    key_to_outcome: dict[str, models.Outcome] = {}
    for d in defaults:
        row = by_seed_key.get(d.key)
        if row is not None:
            if row.name != d.name or row.description != d.description or row.is_custom:
                row.name = d.name
                row.description = d.description
                row.is_custom = False
                updated += 1
        else:
            row = models.Outcome(
                engagement_id=engagement.id, name=d.name, description=d.description,
                is_custom=False, seed_key=d.key,
            )
            db.add(row)
            added.append(d.name)
        key_to_outcome[d.key] = row
    db.flush()

    # Reconcile default Microsoft coverage over the resulting outcome set:
    # add any (bundle, outcome) pair from the global template that's missing.
    from . import bundles as bundles_service

    bundle_by_key = {b.key: b for b in bundles_service.list_bundles(db)}
    have_pairs = {
        (c.bundle_id, c.outcome_id)
        for c in db.execute(
            select(models.CoverageMapEntry).where(
                models.CoverageMapEntry.engagement_id == engagement.id,
                models.CoverageMapEntry.product_kind == "MicrosoftSku",
            )
        ).scalars()
    }
    coverage_added = 0
    for dc in db.execute(select(models.DefaultBundleCoverage)).scalars():
        bundle = bundle_by_key.get(dc.bundle_key)
        outcome = key_to_outcome.get(dc.outcome_key)
        if bundle is None or outcome is None:
            continue
        if (bundle.id, outcome.id) in have_pairs:
            continue
        db.add(models.CoverageMapEntry(
            engagement_id=engagement.id, outcome_id=outcome.id,
            product_kind="MicrosoftSku", bundle_id=bundle.id,
            microsoft_sku_reference=bundle.name, coverage=dc.coverage,
            ai_suggested=False, ratified=True,
        ))
        have_pairs.add((bundle.id, outcome.id))
        coverage_added += 1

    db.commit()
    return {
        "added": added, "updated": updated, "removed": removed,
        "coverage_added": coverage_added,
        "library_version": outcome_library_version(),
    }


def seed_engagement(db: Session, engagement: models.Engagement) -> None:
    """Copy default outcomes + Microsoft SKU coverage into the engagement."""
    seed_default_outcomes(db)  # ensure the global library exists
    seed_default_coverage(db)

    defaults = db.execute(
        select(models.DefaultOutcome).order_by(models.DefaultOutcome.sort_order)
    ).scalars().all()

    key_to_outcome: dict[str, models.Outcome] = {}
    for o in defaults:
        row = models.Outcome(
            engagement_id=engagement.id,
            name=o.name,
            description=o.description,
            is_custom=False,
            seed_key=o.key,
        )
        db.add(row)
        db.flush()  # assign id
        key_to_outcome[o.key] = row

    from . import bundles as bundles_service

    bundle_by_key = {b.key: b for b in bundles_service.list_bundles(db)}
    # Copy Microsoft coverage from the editable global default library, not the
    # static file — so operator edits to the default (Settings) flow into new
    # engagements while existing ones keep their own copy.
    defaults_cov = db.execute(select(models.DefaultBundleCoverage)).scalars().all()
    for dc in defaults_cov:
        bundle = bundle_by_key.get(dc.bundle_key)
        if bundle is None:
            continue  # coverage references a bundle not in the library
        outcome = key_to_outcome.get(dc.outcome_key)
        if outcome is None:
            continue  # coverage references an outcome key not in the library
        db.add(
            models.CoverageMapEntry(
                engagement_id=engagement.id,
                outcome_id=outcome.id,
                product_kind="MicrosoftSku",
                bundle_id=bundle.id,
                microsoft_sku_reference=bundle.name,  # display / back-compat
                coverage=dc.coverage,
                ai_suggested=False,
                ratified=True,  # default library is pre-ratified
            )
        )
