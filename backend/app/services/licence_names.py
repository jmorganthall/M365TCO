"""Licence names: the "Microsoft licences we can't read yet" card on Other tools
(WALKTHROUGH step 3; TARGET_SCHEMA §4.4 "Reading a line", D23) and the Settings
list of licence names answered by hand (§3.4, D24).

The card asks once per licence name and writes the same answer to every line of
the engagement that carries that name. Names are compared the way name resolution
compares them (case and spacing ignored), so "EXO P2" and "exo  p2" are one name.

Answers, all written by a person's click:
  same_as       it's the same as a library plan (CurrentMicrosoftLicense.bundle_id)
  library       use the library's list: same_as the plan its name resolves to
  outcomes      it delivers these outcomes (CurrentLicenseOutcome rows, ticked by
                hand, or suggested by AI and confirmed)
  out_of_scope  not part of this workshop (the same flag as on the licence line)
  clear         undo: back to however the name resolves
Linking to a plan this engagement has no coverage for copies the library's list
for that plan (and any library outcome it needs, with that outcome's library
coverage on every plan), so the answer counts at once.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import bundles, compute

# The order an answered name's state is reported in when its lines differ.
_STATE_ORDER = ("unread", "linked", "mapped", "named", "out_of_scope", "resolved")


def name_key(text: str | None) -> str:
    """The comparison key of a licence name."""
    return bundles.normalize_alias(text or "")


def lines_named(eng: models.Engagement, name: str) -> list[models.CurrentMicrosoftLicense]:
    key = name_key(name)
    return [lic for lic in eng.current_licenses if name_key(lic.sku_reference) == key]


def _library_lists(db: Session) -> dict[str, list[str]]:
    """bundle key -> outcome keys, from the shared library."""
    out: dict[str, list[str]] = {}
    for dc in db.execute(select(models.DefaultBundleCoverage)).scalars():
        out.setdefault(dc.bundle_key, []).append(dc.outcome_key)
    return out


def _covered_bundle_ids(db: Session, engagement_id: str) -> set[str]:
    """Plans this engagement has ratified coverage for."""
    return set(db.execute(
        select(models.CoverageMapEntry.bundle_id).where(
            models.CoverageMapEntry.engagement_id == engagement_id,
            models.CoverageMapEntry.product_kind == "MicrosoftSku",
            models.CoverageMapEntry.ratified.is_(True),
            models.CoverageMapEntry.bundle_id.isnot(None),
        )
    ).scalars())


def list_names(db: Session, eng: models.Engagement) -> list[dict]:
    """One item per licence name that needs an answer or has a person's answer.
    Names the library reads on its own (`resolved`) aren't listed. Unread first."""
    sku_outcomes = compute._ratified_sku_outcomes(db, eng.id)
    readings = compute.licence_readings(db, eng, sku_outcomes)
    persona_names = {p.id: p.name for p in eng.personas}
    outcome_names = {o.id: o.name for o in eng.outcomes}
    plan = {b.id: b for b in bundles.list_bundles(db)}
    library = _library_lists(db)
    library_outcome_names = {d.key: d.name for d in db.execute(select(models.DefaultOutcome)).scalars()}
    covered = _covered_bundle_ids(db, eng.id)

    grouped: dict[str, list[models.CurrentMicrosoftLicense]] = {}
    for lic in eng.current_licenses:
        grouped.setdefault(name_key(lic.sku_reference), []).append(lic)

    items = []
    for key, lines in grouped.items():
        states = [readings[lic.id].state for lic in lines]
        has_answer = any(lic.bundle_id or lic.outcome_links or lic.out_of_scope for lic in lines)
        if set(states) <= {"resolved"} and not has_answer:
            continue
        state = next(s for s in _STATE_ORDER if s in states)
        name = Counter((lic.sku_reference or "").strip() for lic in lines).most_common(1)[0][0]

        groups: list[str] = []
        for lic in lines:
            if not lic.persona_ids:
                groups.append("Every group")
            groups += [persona_names[pid] for pid in lic.persona_ids if pid in persona_names]
        linked = {lic.bundle_id for lic in lines if lic.bundle_id}
        linked_id = next(iter(linked)) if len(linked) == 1 else None

        # The ticked outcomes, merged across the lines (a confirmed tick wins).
        ticked: dict[str, dict] = {}
        for lic in lines:
            for link in lic.outcome_links:
                t = ticked.setdefault(link.outcome_id, {
                    "outcome_id": link.outcome_id,
                    "name": outcome_names.get(link.outcome_id, link.outcome_id),
                    "ratified": False, "ai_suggested": False})
                t["ratified"] = t["ratified"] or bool(link.ratified)
                t["ai_suggested"] = t["ai_suggested"] or bool(link.ai_suggested)

        delivers = set()
        for lic in lines:
            delivers |= readings[lic.id].outcomes

        resolved_id = bundles.resolve_bundle(db, name) if name else None
        # "Use the library's list": the library knows this plan, and this
        # engagement has no coverage for it yet.
        offer_id = linked_id or resolved_id
        offer = None
        if offer_id in plan and offer_id not in covered and library.get(plan[offer_id].key):
            offer = {
                "bundle_id": offer_id, "bundle_name": plan[offer_id].name,
                "outcomes": sorted(library_outcome_names.get(k, k) for k in library[plan[offer_id].key]),
            }

        items.append({
            "sku_reference": name,
            "line_ids": [lic.id for lic in lines],
            "groups": list(dict.fromkeys(groups)),
            "seats": sum(lic.quantity_assigned or 0 for lic in lines),
            "state": state,
            "mixed": len(set(states)) > 1,
            "bundle_id": linked_id,
            "bundle_name": plan[linked_id].name if linked_id in plan else None,
            "resolved_bundle_id": resolved_id,
            "resolved_bundle_name": plan[resolved_id].name if resolved_id in plan else None,
            "library_offer": offer,
            "outcomes": sorted(ticked.values(), key=lambda t: t["name"]),
            "delivers": sorted(outcome_names.get(o, o) for o in delivers),
        })
    items.sort(key=lambda i: (i["state"] != "unread", i["sku_reference"].lower()))
    return items


def adopt_library_plan(db: Session, eng: models.Engagement, bundle: models.Bundle) -> dict:
    """Copy the library's list for one plan into this engagement when it has no
    coverage for that plan. A library outcome the engagement lacks is added with
    its library coverage on every plan, so adding it can't make a plan the
    engagement already has look as though it drops that outcome. Flushes only."""
    if bundle.id in _covered_bundle_ids(db, eng.id):
        return {"coverage_added": 0, "outcomes_added": []}
    library = db.execute(select(models.DefaultBundleCoverage)).scalars().all()
    by_seed = {o.seed_key: o for o in eng.outcomes if o.seed_key}
    defaults = {d.key: d for d in db.execute(select(models.DefaultOutcome)).scalars()}
    plan_by_key = {b.key: b for b in bundles.list_bundles(db)}
    existing = {
        (c.bundle_id, c.outcome_id): c for c in db.execute(
            select(models.CoverageMapEntry).where(
                models.CoverageMapEntry.engagement_id == eng.id,
                models.CoverageMapEntry.product_kind == "MicrosoftSku",
            )
        ).scalars()
    }
    added_outcomes: list[models.Outcome] = []
    for dc in library:
        if dc.bundle_key != bundle.key or dc.outcome_key in by_seed or dc.outcome_key not in defaults:
            continue
        d = defaults[dc.outcome_key]
        row = models.Outcome(engagement_id=eng.id, name=d.name, description=d.description,
                             is_custom=False, seed_key=d.key)
        db.add(row)
        db.flush()
        by_seed[d.key] = row
        added_outcomes.append(row)
    new_outcome_keys = {o.seed_key for o in added_outcomes}

    count = 0
    for dc in library:
        if dc.bundle_key != bundle.key and dc.outcome_key not in new_outcome_keys:
            continue
        target, outcome = plan_by_key.get(dc.bundle_key), by_seed.get(dc.outcome_key)
        if target is None or outcome is None:
            continue
        row = existing.get((target.id, outcome.id))
        if row is not None:
            if not row.ratified:
                row.ratified = True  # the person chose the library's list
                count += 1
            continue
        row = models.CoverageMapEntry(
            engagement_id=eng.id, outcome_id=outcome.id, product_kind="MicrosoftSku",
            bundle_id=target.id, microsoft_sku_reference=target.name,
            coverage=dc.coverage or "Full", ai_suggested=False, ratified=True, source="library")
        db.add(row)
        existing[(target.id, outcome.id)] = row
        count += 1
    db.flush()
    return {"coverage_added": count, "outcomes_added": [o.name for o in added_outcomes]}


def answer(db: Session, eng: models.Engagement, name: str, kind: str,
           bundle_id: str | None = None) -> dict:
    """Write one answer to every line of that name. Raises LookupError when no line
    has the name, ValueError for an answer that can't apply. Commits."""
    lines = lines_named(eng, name)
    if not lines:
        raise LookupError("No licence line has that name.")
    adopted = {"coverage_added": 0, "outcomes_added": []}
    if kind == "library":
        bundle_id = next((lic.bundle_id for lic in lines if lic.bundle_id), None) \
            or bundles.resolve_bundle(db, name)
        if not bundle_id:
            raise ValueError("The library doesn't know this licence; say what it is the same as instead.")
        kind = "same_as"
    if kind == "same_as":
        plan = db.get(models.Bundle, bundle_id) if bundle_id else None
        if plan is None:
            raise ValueError("Pick a library plan.")
        adopted = adopt_library_plan(db, eng, plan)
        for lic in lines:
            lic.bundle_id, lic.out_of_scope = plan.id, False
            lic.outcome_links.clear()
    elif kind == "out_of_scope":
        for lic in lines:
            lic.bundle_id, lic.out_of_scope = None, True
            lic.outcome_links.clear()
    elif kind == "clear":
        for lic in lines:
            lic.bundle_id, lic.out_of_scope = None, False
            lic.outcome_links.clear()
    else:
        raise ValueError(f"Unknown answer: {kind}")
    db.commit()
    return adopted


def set_outcome(db: Session, eng: models.Engagement, name: str, outcome_id: str, action: str) -> None:
    """Tick (`add`), confirm an AI suggestion (`confirm`) or untick (`remove`) one
    outcome on every line of that name. Ticking or confirming is the "it delivers
    these outcomes" answer, so it replaces a plan link or out-of-scope. Commits."""
    lines = lines_named(eng, name)
    if not lines:
        raise LookupError("No licence line has that name.")
    outcome = db.get(models.Outcome, outcome_id)
    if outcome is None or outcome.engagement_id != eng.id:
        raise LookupError("Outcome not found.")
    if action not in ("add", "confirm", "remove"):
        raise ValueError(f"Unknown action: {action}")
    for lic in lines:
        link = next((x for x in lic.outcome_links if x.outcome_id == outcome_id), None)
        if action == "remove":
            if link is not None:
                lic.outcome_links.remove(link)
            continue
        if action == "confirm" and link is None:
            continue
        lic.bundle_id, lic.out_of_scope = None, False
        if link is None:
            lic.outcome_links.append(models.CurrentLicenseOutcome(
                outcome_id=outcome_id, ai_suggested=False, ratified=True))
        else:
            link.ratified = True
    db.commit()


def persist_suggestions(eng: models.Engagement, name: str, suggestions: list[dict]) -> list[str]:
    """Write AI suggestions as unratified ticks on every line of that name, skipping
    outcomes already ticked. Call only AFTER the model has answered (no write held
    across the call). Does not commit. Returns the outcome ids suggested."""
    created: list[str] = []
    for lic in lines_named(eng, name):
        have = {x.outcome_id for x in lic.outcome_links}
        for s in suggestions:
            if s["outcome_id"] in have:
                continue
            lic.outcome_links.append(models.CurrentLicenseOutcome(
                outcome_id=s["outcome_id"], ai_suggested=True, ratified=False))
            have.add(s["outcome_id"])
            if s["outcome_id"] not in created:
                created.append(s["outcome_id"])
    return created


# ---- Settings: licence names answered by hand (TARGET_SCHEMA §3.4, D24) ------

def answered_by_hand(db: Session) -> list[dict]:
    """Every licence name engagements answered by hand, with each distinct answer
    and how many engagements gave it. Licence names, plans, outcome names and
    counts only — never which customers or engagements. Derived; never stored."""
    plan = {b.id: b for b in bundles.list_bundles(db)}
    library_names = {d.key: d.name for d in db.execute(select(models.DefaultOutcome)).scalars()}
    outcomes = {o.id: o for o in db.execute(select(models.Outcome)).scalars()}
    lines = db.execute(
        select(models.CurrentMicrosoftLicense).where(
            models.CurrentMicrosoftLicense.out_of_scope.is_(False))
    ).scalars().all()

    # name key -> answer -> set of engagement ids; plus the spellings seen.
    answers: dict[str, dict[tuple, set[str]]] = {}
    spellings: dict[str, Counter] = {}
    for lic in lines:
        key = name_key(lic.sku_reference)
        if not key:
            continue
        if lic.bundle_id:
            if lic.bundle_id not in plan:
                continue
            ans = ("linked", lic.bundle_id)
        else:
            ticked = [outcomes[x.outcome_id] for x in lic.outcome_links
                      if x.ratified and x.outcome_id in outcomes]
            if not ticked:
                continue
            # Compared across engagements by the library key, else by name (custom).
            ans = ("mapped", tuple(sorted({o.seed_key or f"custom:{o.name}" for o in ticked})))
        answers.setdefault(key, {}).setdefault(ans, set()).add(lic.engagement_id)
        spellings.setdefault(key, Counter())[(lic.sku_reference or "").strip()] += 1

    out = []
    for key, by_answer in answers.items():
        name = spellings[key].most_common(1)[0][0]
        resolved = bundles.resolve_bundle(db, name)
        variants = []
        for (kind, payload), engs in by_answer.items():
            if kind == "linked":
                variants.append({
                    "kind": "linked", "bundle_id": payload, "bundle_name": plan[payload].name,
                    "engagements": len(engs),
                    # An alias makes the library read the name as this plan.
                    "can_add_alias": resolved != payload,
                })
            else:
                keys = [k for k in payload if not k.startswith("custom:")]
                custom = [k.removeprefix("custom:") for k in payload if k.startswith("custom:")]
                variants.append({
                    "kind": "mapped", "engagements": len(engs),
                    "outcome_keys": keys,
                    "outcomes": sorted(library_names.get(k, k) for k in keys),
                    "custom_outcomes": sorted(custom),
                    "can_add_plan": resolved is None and bool(keys),
                })
        # A name every engagement linked to the plan the library already reads it
        # as leaves the admin nothing to do.
        if all(v["kind"] == "linked" and not v["can_add_alias"] for v in variants):
            continue
        variants.sort(key=lambda v: -v["engagements"])
        out.append({
            "name": name,
            "library_reads_as": plan[resolved].name if resolved in plan else None,
            "engagements": len(set().union(*by_answer.values())),
            "variants": variants,
        })
    out.sort(key=lambda i: (-i["engagements"], i["name"].lower()))
    return out


def add_alias(db: Session, name: str, bundle_id: str) -> list[str]:
    """Teach the library a licence name: add it as an alias of the plan. Raises
    LookupError for an unknown plan, ValueError when another plan holds the name."""
    plan = db.get(models.Bundle, bundle_id)
    if plan is None:
        raise LookupError("Plan not found.")
    current = bundles.aliases_by_bundle(db).get(plan.id, [])
    return bundles.set_bundle_aliases(db, plan.id, current + [name])


def add_plan(db: Session, name: str, outcome_keys: list[str]) -> models.Bundle:
    """Add a licence name to the library as a new plan: a base plan with this
    library coverage and the name as its alias. It has no price until a catalog
    SKU is mapped to it, and an unpriced plan is never recommended. Its kind, base
    and coverage stay editable in Settings. New engagements get it; existing ones
    see it on their Library updates review. Raises ValueError. Commits."""
    from .seeds import slugify

    name = (name or "").strip()
    if not name:
        raise ValueError("A plan needs a name.")
    if bundles.resolve_bundle(db, name):
        raise ValueError("The library already reads this name as a plan.")
    known = set(db.execute(select(models.DefaultOutcome.key)).scalars())
    keys = [k for k in dict.fromkeys(outcome_keys) if k in known]
    if not keys:
        raise ValueError("Pick at least one library outcome.")
    taken = set(db.execute(select(models.Bundle.key)).scalars())
    base_key = slugify(name) or "plan"
    key, n = base_key, 2
    while key in taken:
        key, n = f"{base_key}-{n}", n + 1
    last = db.execute(select(models.Bundle.sort_order).order_by(models.Bundle.sort_order.desc())).scalar()
    row = models.Bundle(key=key, name=name, kind="bundle", sort_order=(last or 0) + 10)
    db.add(row)
    db.flush()
    for k in keys:
        db.add(models.DefaultBundleCoverage(bundle_key=key, outcome_key=k, coverage="Full"))
    db.add(models.BundleAlias(alias=name_key(name), bundle_id=row.id))
    db.commit()
    return row
