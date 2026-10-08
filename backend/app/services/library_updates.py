"""The Library updates review (TARGET_SCHEMA §4.7, D8 revised; WALKTHROUGH W13).

An engagement owns its copy of the library: the outcomes and Microsoft coverage it
was created with. When the shared library changes later, this review lists each
difference. It is derived on read; nothing changes until a person applies an item,
and "not for this customer" is remembered (LibraryUpdateDecision).

Kinds, each one item per plan, outcome or licence name:
  outcome_added       a library outcome this engagement doesn't have
                      Apply: add it, with the library's coverage of it on every plan
  coverage_added      a plan the library says delivers outcomes this engagement
                      uses, where the engagement has no row for them
                      Apply: add the rows (source "library")
  coverage_removed    rows the library no longer lists for a plan (not the
                      engagement's own additions)
                      Apply: remove them
  licence_in_library  a licence name answered with outcomes that the library now
                      reads as a plan
                      Apply: link the lines to the plan (the answer "same as")
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import bundles, compute, licence_names

KINDS = ("outcome_added", "coverage_added", "coverage_removed", "licence_in_library")


def _library_pairs(db: Session) -> set[tuple[str, str]]:
    return {(dc.bundle_key, dc.outcome_key)
            for dc in db.execute(select(models.DefaultBundleCoverage)).scalars()}


def _decisions(eng: models.Engagement) -> set[tuple]:
    return {(d.kind, d.bundle_id, d.outcome_key, d.license_id) for d in eng.library_update_decisions}


def _microsoft_rows(db: Session, eid: str) -> list[models.CoverageMapEntry]:
    return db.execute(
        select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == eid,
            models.CoverageMapEntry.product_kind == "MicrosoftSku",
            models.CoverageMapEntry.bundle_id.isnot(None),
        )
    ).scalars().all()


def pending(db: Session, eng: models.Engagement) -> list[dict]:
    """Every difference not yet applied or declined, in the order Apply all takes
    them. Each item carries `kind` plus its subject (`outcome_key`, `bundle_id` or
    `sku_reference`), which is what apply/decline take back."""
    plans = bundles.list_bundles(db)
    plan_by_key = {b.key: b for b in plans}
    plan_by_id = {b.id: b for b in plans}
    pairs = _library_pairs(db)
    library_plan_keys = {bk for bk, _ in pairs}
    defaults = db.execute(
        select(models.DefaultOutcome).order_by(models.DefaultOutcome.sort_order)
    ).scalars().all()
    default_keys = {d.key for d in defaults}
    by_seed = {o.seed_key: o for o in eng.outcomes if o.seed_key}
    outcome_by_id = {o.id: o for o in eng.outcomes}
    rows = _microsoft_rows(db, eng.id)
    have = {(r.bundle_id, r.outcome_id) for r in rows}
    declined = _decisions(eng)
    items: list[dict] = []

    def plan_names(keys):
        return sorted(plan_by_key[k].name for k in keys if k in plan_by_key)

    for d in defaults:
        if d.key in by_seed or ("outcome_added", None, d.key, None) in declined:
            continue
        items.append({
            "kind": "outcome_added", "key": f"outcome_added:{d.key}",
            "outcome_key": d.key, "name": d.name, "description": d.description,
            "plans": plan_names(bk for bk, ok in pairs if ok == d.key),
        })

    added: dict[str, list[models.Outcome]] = {}
    for bk, ok in sorted(pairs):
        plan, outcome = plan_by_key.get(bk), by_seed.get(ok)
        if plan is None or outcome is None or (plan.id, outcome.id) in have:
            continue
        if ("coverage_added", plan.id, ok, None) in declined:
            continue
        added.setdefault(plan.id, []).append(outcome)
    for bid, outs in added.items():
        lists = sorted(outcome_by_id[r.outcome_id].name for r in rows
                       if r.bundle_id == bid and r.outcome_id in outcome_by_id)
        items.append({
            "kind": "coverage_added", "key": f"coverage_added:{bid}",
            "bundle_id": bid, "bundle_name": plan_by_id[bid].name,
            "new_plan": not lists, "engagement_lists": lists,
            "outcomes": sorted(o.name for o in outs),
            "outcome_ids": [o.id for o in outs], "outcome_keys": [o.seed_key for o in outs],
        })

    removed: dict[str, list[models.CoverageMapEntry]] = {}
    for r in rows:
        plan, outcome = plan_by_id.get(r.bundle_id), outcome_by_id.get(r.outcome_id)
        if plan is None or outcome is None or r.source == "engagement":
            continue
        # Only outcomes the library still has, on plans the library lists (a row
        # of unknown origin on a plan the library never covered is the engagement's).
        if outcome.seed_key not in default_keys or plan.key not in library_plan_keys:
            continue
        if (plan.key, outcome.seed_key) in pairs:
            continue
        if ("coverage_removed", plan.id, outcome.seed_key, None) in declined:
            continue
        removed.setdefault(plan.id, []).append(r)
    for bid, gone in removed.items():
        items.append({
            "kind": "coverage_removed", "key": f"coverage_removed:{bid}",
            "bundle_id": bid, "bundle_name": plan_by_id[bid].name,
            "outcomes": sorted({outcome_by_id[r.outcome_id].name for r in gone}),
            "row_ids": [r.id for r in gone],
            # Rows from before origins were recorded may be this customer's own
            # tuning: Apply all leaves them for an individual click.
            "origin_unknown": any(r.source is None for r in gone),
            "outcome_keys": sorted({outcome_by_id[r.outcome_id].seed_key for r in gone}),
        })

    sku_outcomes = compute._ratified_sku_outcomes(db, eng.id)
    readings = compute.licence_readings(db, eng, sku_outcomes)
    names: dict[str, list[models.CurrentMicrosoftLicense]] = {}
    for lic in eng.current_licenses:
        if readings[lic.id].state == "mapped" and \
                ("licence_in_library", None, None, lic.id) not in declined:
            names.setdefault(licence_names.name_key(lic.sku_reference), []).append(lic)
    for key, lines in names.items():
        name = (lines[0].sku_reference or "").strip()
        bid = bundles.resolve_bundle(db, name) if name else None
        if bid is None or bid not in plan_by_id:
            continue
        plan = plan_by_id[bid]
        mine = set().union(*(readings[lic.id].outcomes for lic in lines))
        theirs = sku_outcomes.get(bid) or {
            by_seed[ok].id for bk, ok in pairs if bk == plan.key and ok in by_seed}
        items.append({
            "kind": "licence_in_library", "key": f"licence_in_library:{key}",
            "sku_reference": name, "line_ids": [lic.id for lic in lines],
            "bundle_id": bid, "bundle_name": plan.name,
            "line_outcomes": sorted(outcome_by_id[o].name for o in mine if o in outcome_by_id),
            "plan_outcomes": sorted(outcome_by_id[o].name for o in theirs if o in outcome_by_id),
        })
    return items


def declined(eng: models.Engagement, db: Session) -> list[dict]:
    """The remembered "not for this customer" answers, labelled, for undo."""
    plan_by_id = {b.id: b.name for b in bundles.list_bundles(db)}
    library_names = {d.key: d.name for d in db.execute(select(models.DefaultOutcome)).scalars()}
    lines = {lic.id: lic.sku_reference for lic in eng.current_licenses}
    out = []
    for d in sorted(eng.library_update_decisions, key=lambda x: x.decided_at, reverse=True):
        outcome = library_names.get(d.outcome_key, d.outcome_key)
        plan = plan_by_id.get(d.bundle_id, "a plan")
        label = {
            "outcome_added": f"New outcome {outcome}",
            "coverage_added": f"{plan} includes {outcome}",
            "coverage_removed": f"The library no longer lists {outcome} for {plan}",
            "licence_in_library": f"Link {lines.get(d.license_id) or 'a licence line'} to the library's plan",
        }.get(d.kind, d.kind)
        out.append({"id": d.id, "kind": d.kind, "label": label, "reason": d.reason,
                    "decided_at": d.decided_at.isoformat() if d.decided_at else None})
    return out


def _find(db: Session, eng: models.Engagement, kind: str, subject: dict) -> dict:
    for item in pending(db, eng):
        if item["kind"] != kind:
            continue
        if kind == "outcome_added" and item["outcome_key"] == subject.get("outcome_key"):
            return item
        if kind in ("coverage_added", "coverage_removed") and item["bundle_id"] == subject.get("bundle_id"):
            return item
        if kind == "licence_in_library" and licence_names.name_key(item["sku_reference"]) \
                == licence_names.name_key(subject.get("sku_reference")):
            return item
    raise LookupError("That library update isn't waiting any more.")


def _add_outcome(db: Session, eng: models.Engagement, key: str) -> models.Outcome:
    """Add a library outcome, with the library's coverage of it on every plan."""
    d = db.execute(select(models.DefaultOutcome).where(models.DefaultOutcome.key == key)).scalar_one()
    row = models.Outcome(engagement_id=eng.id, name=d.name, description=d.description,
                         is_custom=False, seed_key=d.key)
    db.add(row)
    db.flush()
    plan_by_key = {b.key: b for b in bundles.list_bundles(db)}
    for dc in db.execute(select(models.DefaultBundleCoverage).where(
            models.DefaultBundleCoverage.outcome_key == key)).scalars():
        plan = plan_by_key.get(dc.bundle_key)
        if plan is not None:
            db.add(models.CoverageMapEntry(
                engagement_id=eng.id, outcome_id=row.id, product_kind="MicrosoftSku",
                bundle_id=plan.id, microsoft_sku_reference=plan.name,
                coverage=dc.coverage or "Full", ai_suggested=False, ratified=True,
                source="library"))
    return row


def _apply(db: Session, eng: models.Engagement, item: dict) -> None:
    kind = item["kind"]
    if kind == "outcome_added":
        _add_outcome(db, eng, item["outcome_key"])
    elif kind == "coverage_added":
        plan = db.get(models.Bundle, item["bundle_id"])
        for oid in item["outcome_ids"]:
            db.add(models.CoverageMapEntry(
                engagement_id=eng.id, outcome_id=oid, product_kind="MicrosoftSku",
                bundle_id=plan.id, microsoft_sku_reference=plan.name, coverage="Full",
                ai_suggested=False, ratified=True, source="library"))
    elif kind == "coverage_removed":
        for rid in item["row_ids"]:
            db.delete(db.get(models.CoverageMapEntry, rid))
    elif kind == "licence_in_library":
        # The "same as" answer: links every line of the name and clears its ticks.
        licence_names.answer(db, eng, item["sku_reference"], "same_as", item["bundle_id"])
        return
    db.commit()


def apply(db: Session, eng: models.Engagement, kind: str, subject: dict) -> None:
    """Apply one waiting item. Raises LookupError when it isn't waiting."""
    _apply(db, eng, _find(db, eng, kind, subject))


def apply_all(db: Session, eng: models.Engagement) -> int:
    """Apply every waiting item: outcomes first, so their coverage arrives with
    them; each pass re-reads what is still waiting. A removal touching rows of
    unknown origin is left for an individual click."""
    done: set[str] = set()
    for kind in KINDS:
        while True:
            items = [i for i in pending(db, eng) if i["kind"] == kind and i["key"] not in done
                     and not i.get("origin_unknown")]
            if not items:
                break
            _apply(db, eng, items[0])
            done.add(items[0]["key"])  # never twice, even if an item can't clear
    return len(done)


def _decide(db: Session, eng: models.Engagement, kind: str, *, bundle_id=None,
            outcome_key=None, license_id=None, reason: str) -> None:
    if (kind, bundle_id, outcome_key, license_id) in _decisions(eng):
        return
    eng.library_update_decisions.append(models.LibraryUpdateDecision(
        kind=kind, bundle_id=bundle_id, outcome_key=outcome_key, license_id=license_id,
        reason=reason))


NOT_FOR_THIS_CUSTOMER = "Not for this customer"


def decline(db: Session, eng: models.Engagement, kind: str, subject: dict) -> None:
    """"Not for this customer": remember it so the item doesn't come back."""
    item = _find(db, eng, kind, subject)
    if kind == "outcome_added":
        _decide(db, eng, kind, outcome_key=item["outcome_key"], reason=NOT_FOR_THIS_CUSTOMER)
    elif kind in ("coverage_added", "coverage_removed"):
        for key in item["outcome_keys"]:
            _decide(db, eng, kind, bundle_id=item["bundle_id"], outcome_key=key,
                    reason=NOT_FOR_THIS_CUSTOMER)
    elif kind == "licence_in_library":
        for lid in item["line_ids"]:
            _decide(db, eng, kind, license_id=lid, reason=NOT_FOR_THIS_CUSTOMER)
    db.commit()


def record_removed_by_hand(db: Session, eng: models.Engagement, row: models.CoverageMapEntry) -> None:
    """A person removed a Microsoft coverage row the library lists: that is this
    engagement's answer (TARGET_SCHEMA §4.7 archives it), so the review must not
    offer it back. Does not commit."""
    if row.product_kind != "MicrosoftSku" or not row.bundle_id:
        return
    plan = db.get(models.Bundle, row.bundle_id)
    outcome = db.get(models.Outcome, row.outcome_id)
    if plan is None or outcome is None or not outcome.seed_key:
        return
    if (plan.key, outcome.seed_key) in _library_pairs(db):
        _decide(db, eng, "coverage_added", bundle_id=plan.id, outcome_key=outcome.seed_key,
                reason="Removed from this engagement's plan coverage by hand")


def record_outcome_removed_by_hand(db: Session, eng: models.Engagement, outcome: models.Outcome) -> None:
    """A person deleted a library outcome from this engagement: that is its answer,
    so the review must not offer it back as new. Does not commit."""
    if outcome.seed_key and db.execute(select(models.DefaultOutcome.id).where(
            models.DefaultOutcome.key == outcome.seed_key)).first():
        _decide(db, eng, "outcome_added", outcome_key=outcome.seed_key,
                reason="Removed from this engagement by hand")


def undo(db: Session, eng: models.Engagement, decision_id: str) -> None:
    row = db.get(models.LibraryUpdateDecision, decision_id)
    if row is None or row.engagement_id != eng.id:
        raise LookupError("Decision not found.")
    db.delete(row)
    db.commit()
