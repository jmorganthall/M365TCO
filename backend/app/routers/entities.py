"""Engagement-scoped sub-resources: personas, outcomes, current licenses,
third-party products, coverage map and scenarios."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..services import bundles, compute, inspector, library_updates, licence_names, lineage, raw, seat_cap
from ..services import personas as persona_order

router = APIRouter(prefix="/api/engagements/{engagement_id}", tags=["entities"])


@router.get("/inspect")
def inspect_data(engagement_id: str, db: Session = Depends(get_db)):
    """Live, read-only view of the whole engagement data model for the GUI Data
    inspector: every object, every persisted field (classified), references
    resolved, plus the input → engine → output flow."""
    eng = _require_engagement(db, engagement_id)
    return inspector.inspect_engagement(db, eng)


def _require_engagement(db: Session, engagement_id: str) -> models.Engagement:
    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        raise HTTPException(404, "Engagement not found")
    return eng


def _derived_covers(db: Session, tp: models.ThirdPartyProduct) -> int:
    """Combined headcount of the personas this product is tagged to. Queried by
    the link's persona_id (not the relationship) so it is correct for links that
    are still pending flush."""
    persona_ids = [pl.persona_id for pl in tp.persona_links]
    if not persona_ids:
        return 0
    return sum(
        p.headcount or 0
        for p in db.execute(
            select(models.Persona).where(models.Persona.id.in_(persona_ids))
        ).scalars()
    )


# ---------- Personas ----------
@router.get("/personas", response_model=list[schemas.PersonaOut])
def list_personas(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    # Carve-outs directly under their parent (services/personas).
    return persona_order.ordered(db.execute(
        select(models.Persona).where(models.Persona.engagement_id == engagement_id)
    ).scalars().all())


def _set_persona_requirements(db: Session, row: models.Persona, outcome_ids: list[str]):
    """Diff-reconcile a persona's required-outcome links (add/remove only the
    deltas, so a UNIQUE re-insert never fires). Ignores outcome ids not in this
    engagement."""
    valid = {
        o.id for o in db.execute(
            select(models.Outcome).where(models.Outcome.engagement_id == row.engagement_id)
        ).scalars()
    }
    want = [oid for oid in dict.fromkeys(outcome_ids) if oid in valid]
    have = {link.outcome_id: link for link in row.requirement_links}
    for oid, link in list(have.items()):
        if oid not in want:
            row.requirement_links.remove(link)
    for oid in want:
        if oid not in have:
            row.requirement_links.append(models.PersonaRequirement(outcome_id=oid))


@router.post("/personas", response_model=schemas.PersonaOut, status_code=201)
def create_persona(engagement_id: str, payload: schemas.PersonaIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    if not payload.name.strip():
        raise HTTPException(422, "Persona name is required.")
    data = payload.model_dump()
    requirements = data.pop("required_outcome_ids", None)
    row = models.Persona(engagement_id=engagement_id, **data)
    db.add(row)
    db.flush()
    if requirements is not None:
        _set_persona_requirements(db, row, requirements)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/personas/{persona_id}", response_model=schemas.PersonaOut)
def update_persona(engagement_id: str, persona_id: str, payload: schemas.PersonaIn, db: Session = Depends(get_db)):
    row = db.get(models.Persona, persona_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Persona not found")
    data = payload.model_dump(exclude_unset=True)
    if "required_outcome_ids" in data:
        _set_persona_requirements(db, row, data.pop("required_outcome_ids") or [])
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.get("/personas/{persona_id}/carve-preview",
            response_model=schemas.PersonaCarvePreviewOut)
def carve_preview(engagement_id: str, persona_id: str, db: Session = Depends(get_db)):
    """What carving this persona would copy, so the GUI can say so before it happens.

    Read from the same associations `carve_persona` copies — a warning that is
    derived separately from the behaviour it describes is a warning that will
    eventually lie."""
    _require_engagement(db, engagement_id)
    persona = db.get(models.Persona, persona_id)
    if persona is None or persona.engagement_id != engagement_id:
        raise HTTPException(404, "Persona not found")

    licences = [
        lic.sku_reference or "(unnamed line)"
        for lic in db.execute(
            select(models.CurrentMicrosoftLicense).where(
                models.CurrentMicrosoftLicense.engagement_id == engagement_id
            )
        ).scalars()
        if persona_id in lic.persona_ids
    ]
    tools = [
        tp.name or "(unnamed tool)"
        for tp in db.execute(
            select(models.ThirdPartyProduct).where(
                models.ThirdPartyProduct.engagement_id == engagement_id
            )
        ).scalars()
        if persona_id in tp.persona_ids
    ]
    outcome_names = {
        o.id: o.name for o in db.execute(
            select(models.Outcome).where(models.Outcome.engagement_id == engagement_id)
        ).scalars()
    }
    scenario = db.execute(
        select(models.PersonaScenario).where(
            models.PersonaScenario.persona_id == persona_id
        )
    ).scalars().first()

    return schemas.PersonaCarvePreviewOut(
        persona_name=persona.name,
        headcount=persona.headcount,
        current_licenses=sorted(licences),
        third_party_tools=sorted(tools),
        required_capabilities=sorted(
            outcome_names.get(oid, oid) for oid in persona.required_outcome_ids
        ),
        scenario_target=(scenario.target_sku_reference if scenario else ""),
        has_scenario=scenario is not None,
    )


@router.post("/personas/{persona_id}/carve", response_model=schemas.PersonaOut,
             status_code=201)
def carve_persona(engagement_id: str, persona_id: str, payload: schemas.PersonaCarveIn,
                  db: Session = Depends(get_db)):
    """Carve seats out of a persona into a child persona with its own target.

    This is how a PARTIAL move is modelled. The licensing unit here is the persona
    — one persona, one scenario, one target — so "300 of these 2,518 people move to
    Business Premium" is a second persona, not a hidden split inside the first.
    Making it a real Persona means every downstream consumer (spend allocation,
    quick wins, seat caps, coverage gaps, the readout) costs it correctly with no
    special-casing, and the operator can edit it like any other.

    The seats MOVE, they are not copied: the parent's headcount drops by the same
    number, so the engagement's population is conserved. The child inherits the
    parent's current-licence tags, third-party tags and required outcomes, because
    those people hold exactly what they held a moment ago — only their target
    differs. Deleting the child returns its seats to the parent.
    """
    _require_engagement(db, engagement_id)
    parent = db.get(models.Persona, persona_id)
    if parent is None or parent.engagement_id != engagement_id:
        raise HTTPException(404, "Persona not found")
    child = _carve(db, engagement_id, parent, int(payload.seats or 0), payload.name,
                   payload.target_sku_reference, payload.target_unit_price_annual)
    db.commit()
    db.refresh(child)
    return child


def _carve(db: Session, engagement_id: str, parent: models.Persona, seats: int,
           name: str | None, target_sku_reference: str | None,
           target_unit_price_annual) -> models.Persona:
    """The carve itself (see carve_persona); flushes, doesn't commit."""
    if parent.parent_persona_id is not None:
        raise HTTPException(
            422, "Carve from the original persona, not from a carve-out — nested "
                 "splits would make the lineage ambiguous.")
    if seats <= 0:
        raise HTTPException(422, "Carve out at least one seat.")
    if seats >= parent.headcount:
        raise HTTPException(
            422, f"{parent.name} has {parent.headcount} seats — carving {seats} would "
                 f"leave nothing behind. To move everyone, change this persona's "
                 f"target instead.")

    parent_scenario = db.execute(
        select(models.PersonaScenario).where(
            models.PersonaScenario.persona_id == parent.id
        )
    ).scalars().first()

    target_ref = (target_sku_reference or "").strip() or (
        parent_scenario.target_sku_reference if parent_scenario else "")
    name = (name or "").strip() or (
        f"{parent.name} — {target_ref}" if target_ref else f"{parent.name} — carve-out")

    child = models.Persona(
        engagement_id=engagement_id, name=name, headcount=seats,
        description=parent.description, source_tag=parent.source_tag,
        parent_persona_id=parent.id,
    )
    db.add(child)
    db.flush()

    # The carved seats leave the parent — population conserved, no double-count.
    parent.headcount -= seats

    # Inherit what these people already have. Tags are per-persona associations, so
    # the child needs its own rows; a current-licence line's cost then splits across
    # parent + child by headcount exactly as it did across the whole group before.
    for link in parent.requirement_links:
        db.add(models.PersonaRequirement(persona_id=child.id, outcome_id=link.outcome_id))
    # The customer's coverage-gap answers describe these people's current state too.
    for ans in parent.gap_answers:
        db.add(models.CoverageGapAnswer(
            engagement_id=engagement_id, persona_id=child.id,
            outcome_id=ans.outcome_id, answer=ans.answer, source_tag=ans.source_tag))
    for lic in db.execute(
        select(models.CurrentLicensePersona).where(
            models.CurrentLicensePersona.persona_id == parent.id
        )
    ).scalars().all():
        db.add(models.CurrentLicensePersona(
            current_license_id=lic.current_license_id, persona_id=child.id))
    for tp in db.execute(
        select(models.ThirdPartyPersona).where(
            models.ThirdPartyPersona.persona_id == parent.id
        )
    ).scalars().all():
        db.add(models.ThirdPartyPersona(
            third_party_product_id=tp.third_party_product_id, persona_id=child.id))

    # The child needs a scenario or it contributes no future state. Copy the
    # parent's quoting basis so only the target differs.
    if parent_scenario is not None:
        price = target_unit_price_annual
        if price is None:
            price = parent_scenario.target_unit_price_annual
        child_scenario = models.PersonaScenario(
            engagement_id=engagement_id, persona_id=child.id,
            target_sku_reference=target_ref,
            # The same plan as the parent keeps the parent's picked plan.
            bundle_id=(parent_scenario.bundle_id
                       if target_ref == parent_scenario.target_sku_reference else None),
            target_unit_price_annual=price,
            target_discount_pct=parent_scenario.target_discount_pct,
            term_duration=parent_scenario.term_duration,
            billing_plan=parent_scenario.billing_plan,
            in_scope=parent_scenario.in_scope,
        )
        # A carve-out onto a DIFFERENT plan must be priced as that plan. Inheriting
        # the parent's $/seat would quote Business Premium at the E3 rate and put a
        # number on the readout that no one could buy — so requote from the catalog
        # at this engagement's basis, exactly as changing a target elsewhere does.
        # (An explicit price in the request always wins; an uncatalogued target
        # keeps the inherited figure rather than dropping to a silent zero.)
        if (target_unit_price_annual is None
                and target_ref != parent_scenario.target_sku_reference):
            db.add(child_scenario)
            _requote_scenario(db, db.get(models.Engagement, engagement_id), child_scenario)
        else:
            db.add(child_scenario)
        # Deliberately NOT copied: the parent's price override. That was a
        # negotiated rate for the parent's target, and this persona is on a
        # different one — carrying it over would quote a discount nobody agreed.

    db.flush()
    return child


# ---- Carve-out lineage --------------------------------------------------------
@router.get("/carve-out-drift")
def list_carve_out_drift(engagement_id: str, db: Session = Depends(get_db)):
    """Every carve-out whose licence or tool tags no longer match its parent's
    (services/lineage). A pure read."""
    _require_engagement(db, engagement_id)
    return lineage.drift(db, engagement_id)


@router.post("/personas/{persona_id}/match-parent", response_model=schemas.PersonaOut)
def match_parent(engagement_id: str, persona_id: str, db: Session = Depends(get_db)):
    """Give a carve-out exactly its parent's licence and tool tags — the person's
    answer to a drift warning, never applied on its own."""
    _require_engagement(db, engagement_id)
    child = db.get(models.Persona, persona_id)
    if child is None or child.engagement_id != engagement_id:
        raise HTTPException(404, "Persona not found")
    if child.parent_persona_id is None:
        raise HTTPException(422, "Only a carve-out has a parent to match.")
    lineage.match_parent(db, child)
    db.commit()
    db.refresh(child)
    return child


# ---- The Business seat cap split (TARGET_SCHEMA D27) -------------------------
@router.get("/seat-cap-splits")
def list_seat_cap_splits(engagement_id: str, db: Session = Depends(get_db)):
    """Every persona whose plan is over a seat cap's seats left in the future state, with
    its split offer (services/seat_cap). A pure read."""
    _require_engagement(db, engagement_id)
    return seat_cap.split_offers(db, engagement_id)


def _set_plan(db: Session, s: models.PersonaScenario, ref: str, price, addons: list[dict],
              bundle_id: str | None = None):
    """Put a scenario on a plan the recommender composed: base (with its library
    plan), its price, and the add-ons that close its gaps (replacing the old plan's
    add-ons)."""
    s.target_sku_reference = ref
    s.bundle_id = bundle_id
    if price is not None:
        s.target_unit_price_annual = price
    for a in list(s.addons):
        db.delete(a)
    db.flush()
    for a in addons:
        db.add(models.ScenarioAddon(scenario_id=s.id, bundle_id=a["bundle_id"],
                                    unit_price_annual=a["unit_price_annual"]))


@router.post("/personas/{persona_id}/seat-cap-split", response_model=schemas.PersonaOut,
             status_code=201)
def split_for_seat_cap(engagement_id: str, persona_id: str,
                       payload: schemas.SeatCapSplitIn, db: Session = Depends(get_db)):
    """Confirm a persona's seat-cap split: the seats left stay on the capped plan,
    the rest move to the recommender's best uncapped plan, and the larger side is
    the parent. The offer is recomputed here, never taken from the client, so it is
    the split the GUI showed only if nothing changed in between. Returns the
    carve-out."""
    _require_engagement(db, engagement_id)
    offer = seat_cap.split_offer(db, engagement_id, persona_id)
    if offer is None:
        raise HTTPException(409, "This persona now fits the capped seats left; nothing to split.")
    if offer["kind"] != "split":
        raise HTTPException(422, {
            "no_seats": f"{offer['cap_name']} has no seats left, so there is nothing to split: "
                        "choose another plan for this persona.",
            "nested": "This is a carve-out; split its parent instead.",
            "no_alternative": f"No priced plan outside {offer['cap_name']} covers what these "
                              "people need, so the rest can't be placed.",
        }[offer["kind"]])
    parent = db.get(models.Persona, persona_id)
    s = seat_cap._scenario(db, persona_id)
    name = (payload.name or "").strip() or offer["carve_out_name"]
    capped = {"ref": s.target_sku_reference, "bundle_id": s.bundle_id,
              "price": s.target_unit_price_annual,
              "addons": [{"bundle_id": a.bundle_id, "unit_price_annual": a.unit_price_annual}
                         for a in s.addons]}
    other = {"ref": offer["other_plan"], "bundle_id": offer.get("other_bundle_id"),
             "price": offer["other_price_annual"],
             "addons": offer["other_addons"]}
    if offer["parent_side"] == "capped":
        # The parent keeps its plan; the people over the cap are carved onto the other.
        child = _carve(db, engagement_id, parent, offer["other_seats"], name, other["ref"], other["price"])
        child_plan = other
    else:
        # The people over the cap are the larger side: the parent moves to the other
        # plan (its negotiated price was for the capped plan, so it no longer
        # applies), and the seats left are carved onto the capped plan.
        s.price_override = False
        _set_plan(db, s, other["ref"], other["price"], other["addons"], other["bundle_id"])
        child = _carve(db, engagement_id, parent, offer["capped_seats"], name, capped["ref"], capped["price"])
        child_plan = capped
    cs = seat_cap._scenario(db, child.id)
    if cs is not None:
        _set_plan(db, cs, child_plan["ref"], child_plan["price"], child_plan["addons"],
                  child_plan["bundle_id"])
    db.commit()
    db.refresh(child)
    return child


@router.delete("/personas/{persona_id}", status_code=204)
def delete_persona(engagement_id: str, persona_id: str, db: Session = Depends(get_db)):
    row = db.get(models.Persona, persona_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Persona not found")
    # Licences and tools held ONLY by this persona would become untagged — read as
    # Org-wide (D12) — and their cost would silently move onto everyone else. A
    # person decides where they go first (code never rewrites what was entered).
    only_lines = [l.sku_reference or "A licence line" for l in db.execute(
        select(models.CurrentMicrosoftLicense).where(
            models.CurrentMicrosoftLicense.engagement_id == engagement_id)).scalars()
        if l.persona_ids == [persona_id]]
    only_tools = [t.name or "A tool" for t in db.execute(
        select(models.ThirdPartyProduct).where(
            models.ThirdPartyProduct.engagement_id == engagement_id)).scalars()
        if t.persona_ids == [persona_id]]
    if only_lines or only_tools:
        raise HTTPException(409, f"{row.name} is the only group with "
                            f"{', '.join(only_lines + only_tools)}. Give "
                            f"{'it' if len(only_lines + only_tools) == 1 else 'them'} to another group, "
                            f"or remove {'it' if len(only_lines + only_tools) == 1 else 'them'}, first.")
    # Everything that hangs off the persona goes with it: its tags on shared
    # licences and tools, its plan (add-ons and services cascade) and its gap
    # answers. A stored narrative keeps its snapshotted name.
    for model in (models.CurrentLicensePersona, models.ThirdPartyPersona):
        for link in db.execute(select(model).where(model.persona_id == persona_id)).scalars():
            db.delete(link)
    for sc in db.execute(select(models.PersonaScenario).where(
            models.PersonaScenario.persona_id == persona_id)).scalars():
        db.delete(sc)
    for n in db.execute(select(models.ScenarioNarrative).where(
            models.ScenarioNarrative.persona_id == persona_id)).scalars():
        n.persona_id = None
    # Deleting a carve-out returns its seats to the persona they came from —
    # otherwise undoing a split would quietly shrink the customer's population.
    if row.parent_persona_id:
        parent = db.get(models.Persona, row.parent_persona_id)
        if parent is not None:
            parent.headcount += row.headcount
    # A parent's carve-outs are no longer attached to anything; keep them as
    # standalone personas (their seats are real people) rather than deleting
    # someone's population as a side effect.
    for child in db.execute(
        select(models.Persona).where(models.Persona.parent_persona_id == row.id)
    ).scalars().all():
        child.parent_persona_id = None
    db.delete(row)
    db.commit()


@router.post("/personas/{persona_id}/bundle-analysis")
def bundle_analysis(
    engagement_id: str, persona_id: str,
    payload: schemas.BundleAnalysisRequest | None = None,
    db: Session = Depends(get_db),
):
    """Evaluate every candidate Microsoft bundle as this persona's target and
    rank by TCO (best-bundle optimizer). Optional per-bundle price overrides."""
    _require_engagement(db, engagement_id)
    try:
        return compute.analyze_persona_bundles(
            db, engagement_id, persona_id, prices=(payload.prices if payload else None)
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))


# ---------- Outcomes ----------
@router.get("/outcomes", response_model=list[schemas.OutcomeOut])
def list_outcomes(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.Outcome).where(models.Outcome.engagement_id == engagement_id)
    ).scalars().all()


@router.post("/outcomes", response_model=schemas.OutcomeOut, status_code=201)
def create_outcome(engagement_id: str, payload: schemas.OutcomeIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    if not payload.name.strip():
        raise HTTPException(422, "Outcome name is required.")
    row = models.Outcome(engagement_id=engagement_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/outcomes/{outcome_id}", response_model=schemas.OutcomeOut)
def update_outcome(engagement_id: str, outcome_id: str, payload: schemas.OutcomeIn, db: Session = Depends(get_db)):
    row = db.get(models.Outcome, outcome_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Outcome not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if k == "area":
            v = (v or "").strip() or None   # blank = not grouped (NULL)
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/outcomes/{outcome_id}", status_code=204)
def delete_outcome(engagement_id: str, outcome_id: str, db: Session = Depends(get_db)):
    row = db.get(models.Outcome, outcome_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Outcome not found")
    # Answers about a capability that no longer exists answer nothing.
    for ans in db.execute(
        select(models.CoverageGapAnswer).where(models.CoverageGapAnswer.outcome_id == outcome_id)
    ).scalars().all():
        db.delete(ans)
    for tick in db.execute(
        select(models.CurrentLicenseOutcome).where(models.CurrentLicenseOutcome.outcome_id == outcome_id)
    ).scalars().all():
        db.delete(tick)
    library_updates.record_outcome_removed_by_hand(db, row.engagement, row)
    db.delete(row)
    db.commit()


# ---------- Current Microsoft licenses ----------
@router.get("/current-licenses", response_model=list[schemas.CurrentLicenseOut])
def list_licenses(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.CurrentMicrosoftLicense).where(
            models.CurrentMicrosoftLicense.engagement_id == engagement_id
        )
    ).scalars().all()


def _reconcile_persona_tags(links, persona_ids: list[str], make_link):
    """Reconcile a row's persona-tag links to the given set. Diffs (rather than
    clear-and-re-add) so an unchanged tag isn't re-inserted — which would trip the
    unique constraint before the delete of the old row flushes."""
    want = list(dict.fromkeys(persona_ids))
    have = {pl.persona_id: pl for pl in links}
    for pid, pl in list(have.items()):
        if pid not in want:
            links.remove(pl)
    for pid in want:
        if pid not in have:
            links.append(make_link(pid))


def _set_persona_tags(row: models.CurrentMicrosoftLicense, persona_ids: list[str]):
    _reconcile_persona_tags(
        row.persona_links, persona_ids,
        lambda pid: models.CurrentLicensePersona(persona_id=pid),
    )


# ---- Catalog picks and list prices, quoted on the server ---------------------
def _picked_sku(db: Session, sku_id: str | None) -> models.MicrosoftSku | None:
    if not sku_id:
        return None
    sku = db.get(models.MicrosoftSku, sku_id)
    if sku is None:
        raise HTTPException(422, "That catalog SKU is no longer in the price list; pick it again.")
    return sku


def _apply_pick(sku: models.MicrosoftSku, data: dict, *, ref: str, price: str,
                old_ref: str, old_price) -> None:
    """A pick from the catalog: the line takes the picked SKU's name and, when the
    pick is a different SKU than the line held or the line has no price yet, its
    list price (the annual ERP of the exact row picked, already at the line's
    basis). A different SKU also ends a negotiated price, which was for the old
    one. Anything sent in the same request wins."""
    changed = sku.sku_title != (old_ref or "")
    data.setdefault(ref, sku.sku_title)
    if price not in data and (changed or not old_price):
        data[price] = sku.annual_erp_price
        if changed:
            data.setdefault("price_override", False)
            data.setdefault("overridden_price_annual", None)


def _licence_basis(eng: models.Engagement, row: models.CurrentMicrosoftLicense) -> dict:
    """The line's quoting basis: its own segment/term/billing, else the engagement's."""
    basis = bundles.engagement_price_basis(eng)
    for key, attr in (("segment", "segment"), ("term", "term_duration"), ("billing", "billing_plan")):
        if getattr(row, attr):
            basis[key] = getattr(row, attr)
    return basis


def _requote_licence(db: Session, eng: models.Engagement, row: models.CurrentMicrosoftLicense) -> None:
    """Re-pull the line's list price from the catalog at its basis (the licence twin
    of _requote_scenario). Only a price the catalog finds (> 0) is written, so a
    hand-entered price on an uncatalogued licence survives."""
    if not (row.sku_reference or "").strip():
        return
    price = bundles.catalog_annual_erp(db, row.sku_reference, bundle_id=row.bundle_id,
                                       **_licence_basis(eng, row))
    if price > 0:
        row.unit_price_paid_annual = price
        row.source_tag = "ListPrice"


@router.post("/current-licenses", response_model=schemas.CurrentLicenseOut, status_code=201)
def create_license(engagement_id: str, payload: schemas.CurrentLicenseIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    data = payload.model_dump()
    sku = _picked_sku(db, data.pop("catalog_sku_id", None))
    if sku is not None:
        # A new line on a picked SKU: its name, and its list price unless one was typed.
        data["sku_reference"] = sku.sku_title
        if not data.get("unit_price_paid_annual"):
            data["unit_price_paid_annual"] = sku.annual_erp_price
            data["source_tag"] = "ListPrice"
    persona_ids = data.pop("persona_ids", [])
    row = models.CurrentMicrosoftLicense(engagement_id=engagement_id, **data)
    _set_persona_tags(row, persona_ids)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/current-licenses/{license_id}", response_model=schemas.CurrentLicenseOut)
def update_license(engagement_id: str, license_id: str, payload: schemas.CurrentLicenseIn, db: Session = Depends(get_db)):
    eng = _require_engagement(db, engagement_id)
    row = db.get(models.CurrentMicrosoftLicense, license_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "License not found")
    data = payload.model_dump(exclude_unset=True)
    sku = _picked_sku(db, data.pop("catalog_sku_id", None))
    if sku is not None:
        _apply_pick(sku, data, ref="sku_reference", price="unit_price_paid_annual",
                    old_ref=row.sku_reference, old_price=row.unit_price_paid_annual)
        if "unit_price_paid_annual" in data:
            data.setdefault("source_tag", "ListPrice")
    if "persona_ids" in data:
        _set_persona_tags(row, data.pop("persona_ids"))
    # Changing the line's segment, term or payment model is a request to requote it
    # (as for a plan); a price sent in the same request wins.
    basis_changed = any(k in data and data[k] != getattr(row, k)
                        for k in ("segment", "term_duration", "billing_plan"))
    for k, v in data.items():
        setattr(row, k, v)
    if basis_changed and "unit_price_paid_annual" not in data:
        _requote_licence(db, eng, row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/current-licenses/{license_id}/list-price", response_model=schemas.CurrentLicenseOut)
def revert_licence_to_list(engagement_id: str, license_id: str, db: Session = Depends(get_db)):
    """Stop paying a negotiated price and use the catalog list price at the line's
    basis. The negotiated price is kept for a later re-enable."""
    eng = _require_engagement(db, engagement_id)
    row = db.get(models.CurrentMicrosoftLicense, license_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "License not found")
    row.price_override = False
    _requote_licence(db, eng, row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/current-licenses/{license_id}", status_code=204)
def delete_license(engagement_id: str, license_id: str, db: Session = Depends(get_db)):
    row = db.get(models.CurrentMicrosoftLicense, license_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "License not found")
    # A "not for this customer" about this line answers nothing once it's gone.
    for d in db.execute(select(models.LibraryUpdateDecision).where(
            models.LibraryUpdateDecision.license_id == license_id)).scalars().all():
        db.delete(d)
    db.delete(row)
    db.commit()


# ---------- Licence names: the unknown-licence card (TARGET_SCHEMA §4.4, D23) ----------
@router.get("/licence-names")
def list_licence_names(engagement_id: str, db: Session = Depends(get_db)):
    """Each licence name that needs an answer (unread) or has a person's answer,
    once per name, with the groups holding it and how it is read. A pure read."""
    eng = _require_engagement(db, engagement_id)
    return licence_names.list_names(db, eng)


@router.put("/licence-names/answer")
def answer_licence_name(engagement_id: str, payload: schemas.LicenceNameAnswerIn,
                        db: Session = Depends(get_db)):
    """Answer a licence name for every line that carries it: the same as a library
    plan, the library's list, out of scope, or clear the answer."""
    eng = _require_engagement(db, engagement_id)
    try:
        return licence_names.answer(db, eng, payload.sku_reference, payload.answer, payload.bundle_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.put("/licence-names/outcome", status_code=204)
def set_licence_name_outcome(engagement_id: str, payload: schemas.LicenceNameOutcomeIn,
                             db: Session = Depends(get_db)):
    """Tick, confirm or untick one outcome a licence name delivers (every line of
    that name). The same controls as a tool's uses."""
    eng = _require_engagement(db, engagement_id)
    try:
        licence_names.set_outcome(db, eng, payload.sku_reference, payload.outcome_id, payload.action)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# ---------- Third-party products ----------
def _tool_out(row: models.ThirdPartyProduct, interp) -> dict:
    """A tool as entered, plus its derived figures from the interpreter (read-only,
    never stored on the row)."""
    it = interp.tool(row.id)
    return {
        "id": row.id, "name": row.name, "vendor": row.vendor, "raw_cost": row.raw_cost,
        "cost_period": row.cost_period, "covered_count_override": row.covered_count_override,
        "renewal_date": row.renewal_date, "is_managed": row.is_managed,
        "tooling_pct": row.tooling_pct, "source_tag": row.source_tag,
        "extra_seats_answer": row.extra_seats_answer, "contract_id": row.contract_id,
        "persona_ids": row.persona_ids,
        "annual_cost": it.annual_cost, "covered_count": it.people_covered,
        "persona_covered_count": it.personas_headcount,
        "per_unit_annual_cost": it.cost_per_person,
        "effective_annual_cost": it.effective_annual_cost,
        "effective_tooling_pct": it.tooling_pct, "tooling_source": it.tooling_source,
    }


def _delete_tool(db: Session, row: models.ThirdPartyProduct) -> None:
    """A tool and what hangs off it: its coverage rows (its persona tags cascade)."""
    for c in db.execute(select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.third_party_product_id == row.id)).scalars():
        db.delete(c)
    db.delete(row)


# ---------- Vendor contracts (GUIDED_PLAN G12) ----------
def _contract(db: Session, engagement_id: str, contract_id: str) -> models.ToolContract:
    row = db.get(models.ToolContract, contract_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Contract not found")
    return row


@router.get("/contracts", response_model=list[schemas.ContractOut])
def list_contracts(engagement_id: str, db: Session = Depends(get_db)):
    return _require_engagement(db, engagement_id).tool_contracts


@router.post("/contracts", response_model=schemas.ContractOut, status_code=201)
def create_contract(engagement_id: str, payload: schemas.ContractIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    if not payload.name.strip():
        raise HTTPException(422, "Contract name is required.")
    row = models.ToolContract(engagement_id=engagement_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/contracts/{contract_id}", response_model=schemas.ContractOut)
def update_contract(engagement_id: str, contract_id: str, payload: schemas.ContractIn,
                    db: Session = Depends(get_db)):
    row = _contract(db, engagement_id, contract_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/contracts/{contract_id}", status_code=204)
def delete_contract(engagement_id: str, contract_id: str, db: Session = Depends(get_db)):
    """Remove a contract and its cost lines (their coverage and tags with them)."""
    row = _contract(db, engagement_id, contract_id)
    for line in db.execute(select(models.ThirdPartyProduct).where(
            models.ThirdPartyProduct.contract_id == contract_id)).scalars():
        _delete_tool(db, line)
    db.delete(row)
    db.commit()


@router.post("/third-party/{tp_id}/break-down", response_model=schemas.ContractOut, status_code=201)
def break_down_tool(engagement_id: str, tp_id: str, db: Session = Depends(get_db)):
    """"Can we break that down?" (G12): the tool becomes a contract with its cost,
    period and renewal, and one $0 cost line per outcome it delivers, each held by
    the same personas with the same seat count. Until amounts are typed the
    contract's remainder line carries the whole cost, so no number changes."""
    _require_engagement(db, engagement_id)
    tool = db.get(models.ThirdPartyProduct, tp_id)
    if tool is None or tool.engagement_id != engagement_id:
        raise HTTPException(404, "Third-party product not found")
    if tool.contract_id:
        raise HTTPException(409, "This is already a line of a contract.")
    rows = db.execute(select(models.CoverageMapEntry).where(
        models.CoverageMapEntry.third_party_product_id == tp_id,
        models.CoverageMapEntry.ratified.is_(True))).scalars().all()
    if len(rows) < 2:
        raise HTTPException(422, "Only a tool used for two or more things can be broken down.")
    contract = models.ToolContract(
        engagement_id=engagement_id, name=tool.name, vendor=tool.vendor, total_cost=tool.raw_cost,
        cost_period=tool.cost_period, renewal_date=tool.renewal_date, source_tag=tool.source_tag)
    db.add(contract)
    db.flush()
    names = {o.id: o.name for o in db.execute(select(models.Outcome).where(
        models.Outcome.engagement_id == engagement_id)).scalars()}
    for r in rows:
        line = models.ThirdPartyProduct(
            engagement_id=engagement_id, name=f"{tool.name}: {names.get(r.outcome_id, 'line')}",
            vendor=tool.vendor, raw_cost=0, cost_period=tool.cost_period,
            covered_count_override=tool.covered_count_override, is_managed=tool.is_managed,
            tooling_pct=tool.tooling_pct, extra_seats_answer=tool.extra_seats_answer,
            contract_id=contract.id, source_tag=tool.source_tag)
        _set_tp_persona_tags(line, tool.persona_ids)
        db.add(line)
        db.flush()
        db.add(models.CoverageMapEntry(
            engagement_id=engagement_id, outcome_id=r.outcome_id, product_kind="ThirdParty",
            third_party_product_id=line.id, coverage="Full", ai_suggested=False, ratified=True))
    _delete_tool(db, tool)
    db.commit()
    db.refresh(contract)
    return contract


@router.get("/third-party", response_model=list[schemas.ThirdPartyOut])
def list_third_party(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    interp = raw.interpreted(db, engagement_id)
    rows = db.execute(
        select(models.ThirdPartyProduct).where(
            models.ThirdPartyProduct.engagement_id == engagement_id
        )
    ).scalars().all()
    return [_tool_out(r, interp) for r in rows]


def _set_tp_persona_tags(row: models.ThirdPartyProduct, persona_ids: list[str]):
    _reconcile_persona_tags(
        row.persona_links, persona_ids,
        lambda pid: models.ThirdPartyPersona(persona_id=pid),
    )


@router.post("/third-party", response_model=schemas.ThirdPartyOut, status_code=201)
def create_third_party(engagement_id: str, payload: schemas.ThirdPartyIn, db: Session = Depends(get_db)):
    eng = _require_engagement(db, engagement_id)
    if not payload.name.strip():
        raise HTTPException(422, "Product name is required.")
    data = payload.model_dump()
    persona_ids = data.pop("persona_ids", [])
    row = models.ThirdPartyProduct(engagement_id=engagement_id, **data)
    _set_tp_persona_tags(row, persona_ids)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _tool_out(row, raw.interpreted(db, engagement_id))


@router.patch("/third-party/{tp_id}", response_model=schemas.ThirdPartyOut)
def update_third_party(engagement_id: str, tp_id: str, payload: schemas.ThirdPartyIn, db: Session = Depends(get_db)):
    eng = _require_engagement(db, engagement_id)
    row = db.get(models.ThirdPartyProduct, tp_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Third-party product not found")
    data = payload.model_dump(exclude_unset=True)
    if "persona_ids" in data:
        _set_tp_persona_tags(row, data.pop("persona_ids"))
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return _tool_out(row, raw.interpreted(db, engagement_id))


@router.delete("/third-party/{tp_id}", status_code=204)
def delete_third_party(engagement_id: str, tp_id: str, db: Session = Depends(get_db)):
    row = db.get(models.ThirdPartyProduct, tp_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Third-party product not found")
    _delete_tool(db, row)
    db.commit()


# ---------- Coverage map ----------
@router.get("/coverage", response_model=list[schemas.CoverageOut])
def list_coverage(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.engagement_id == engagement_id
        )
    ).scalars().all()


@router.post("/coverage", response_model=schemas.CoverageOut, status_code=201)
def create_coverage(engagement_id: str, payload: schemas.CoverageIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    row = models.CoverageMapEntry(engagement_id=engagement_id, **payload.model_dump())
    row.source = "engagement"  # added by a person for this customer (§4.7)
    # Resolve Microsoft SKU coverage onto its bundle so it keys the same way the
    # seeded coverage does (the SKU → Bundle → Outcomes spine).
    if row.product_kind == "MicrosoftSku" and row.bundle_id is None:
        row.bundle_id = bundles.resolve_bundle(db, row.microsoft_sku_reference or "")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/coverage/{entry_id}", response_model=schemas.CoverageOut)
def update_coverage(engagement_id: str, entry_id: str, payload: schemas.CoverageIn, db: Session = Depends(get_db)):
    row = db.get(models.CoverageMapEntry, entry_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Coverage entry not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.post("/coverage/{entry_id}/ratify", response_model=schemas.CoverageOut)
def ratify_coverage(engagement_id: str, entry_id: str, db: Session = Depends(get_db)):
    """Human ratification gate (PRD 5.7): only ratified entries feed the math."""
    row = db.get(models.CoverageMapEntry, entry_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Coverage entry not found")
    row.ratified = True
    db.commit()
    db.refresh(row)
    return row


@router.delete("/coverage/{entry_id}", status_code=204)
def delete_coverage(engagement_id: str, entry_id: str, db: Session = Depends(get_db)):
    row = db.get(models.CoverageMapEntry, entry_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Coverage entry not found")
    # Removing a row the library lists is this engagement's answer: the Library
    # updates review must not offer it back (TARGET_SCHEMA §4.7).
    library_updates.record_removed_by_hand(db, row.engagement, row)
    db.delete(row)
    db.commit()


# ---------- Library updates review (TARGET_SCHEMA §4.7, D8) ----------
@router.get("/library-updates")
def list_library_updates(engagement_id: str, db: Session = Depends(get_db)):
    """Each difference between the shared library and this engagement's copy that
    isn't applied or declined yet, plus the remembered "not for this customer"
    answers. A pure read: nothing changes until a person applies an item."""
    eng = _require_engagement(db, engagement_id)
    return {"items": library_updates.pending(db, eng),
            "declined": library_updates.declined(eng, db)}


def _subject(payload: schemas.LibraryUpdateIn) -> dict:
    return payload.model_dump(exclude={"kind"})


@router.post("/library-updates/apply", status_code=204)
def apply_library_update(engagement_id: str, payload: schemas.LibraryUpdateIn,
                         db: Session = Depends(get_db)):
    eng = _require_engagement(db, engagement_id)
    try:
        library_updates.apply(db, eng, payload.kind, _subject(payload))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/library-updates/apply-all")
def apply_all_library_updates(engagement_id: str, db: Session = Depends(get_db)):
    eng = _require_engagement(db, engagement_id)
    return {"applied": library_updates.apply_all(db, eng)}


@router.post("/library-updates/decline", status_code=204)
def decline_library_update(engagement_id: str, payload: schemas.LibraryUpdateIn,
                           db: Session = Depends(get_db)):
    """"Not for this customer": remembered, so the item doesn't come back."""
    eng = _require_engagement(db, engagement_id)
    try:
        library_updates.decline(db, eng, payload.kind, _subject(payload))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/library-updates/decisions/{decision_id}", status_code=204)
def undo_library_update_decision(engagement_id: str, decision_id: str, db: Session = Depends(get_db)):
    """Undo a "not for this customer": the item is offered again."""
    eng = _require_engagement(db, engagement_id)
    try:
        library_updates.undo(db, eng, decision_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


# ---------- Coverage gap answers (TARGET_SCHEMA §4.8) ----------
@router.get("/coverage-gap-answers", response_model=list[schemas.GapAnswerOut])
def list_gap_answers(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.CoverageGapAnswer).where(
            models.CoverageGapAnswer.engagement_id == engagement_id
        )
    ).scalars().all()


@router.put("/coverage-gap-answers", response_model=schemas.GapAnswerOut)
def set_gap_answer(engagement_id: str, payload: schemas.GapAnswerIn, db: Session = Depends(get_db)):
    """Record the customer's answer for one (persona, capability) gap — one answer
    per pair, so a second answer replaces the first."""
    _require_engagement(db, engagement_id)
    persona = db.get(models.Persona, payload.persona_id)
    outcome = db.get(models.Outcome, payload.outcome_id)
    if persona is None or persona.engagement_id != engagement_id:
        raise HTTPException(404, "Persona not found")
    if outcome is None or outcome.engagement_id != engagement_id:
        raise HTTPException(404, "Outcome not found")
    row = db.execute(
        select(models.CoverageGapAnswer).where(
            models.CoverageGapAnswer.persona_id == payload.persona_id,
            models.CoverageGapAnswer.outcome_id == payload.outcome_id,
        )
    ).scalars().first()
    if row is None:
        row = models.CoverageGapAnswer(
            engagement_id=engagement_id, persona_id=payload.persona_id,
            outcome_id=payload.outcome_id)
        db.add(row)
    row.answer = payload.answer
    row.source_tag = payload.source_tag
    db.commit()
    db.refresh(row)
    return row


@router.delete("/coverage-gap-answers/{answer_id}", status_code=204)
def delete_gap_answer(engagement_id: str, answer_id: str, db: Session = Depends(get_db)):
    """Clear an answer: the gap goes back to unanswered (never claimed as new)."""
    row = db.get(models.CoverageGapAnswer, answer_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Answer not found")
    db.delete(row)
    db.commit()


# ---------- Persona scenarios ----------
@router.put("/scenarios/{scenario_id}/services", response_model=schemas.ScenarioOut)
def set_scenario_services(engagement_id: str, scenario_id: str, payload: schemas.ScenarioServicesIn,
                          db: Session = Depends(get_db)):
    """The practice services in a persona's future state (GUIDED_PLAN G13)."""
    row = db.get(models.PersonaScenario, scenario_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Scenario not found")
    wanted = list(dict.fromkeys(payload.service_ids))
    for sid in wanted:
        sv = db.get(models.PracticeService, sid)
        if sv is None:
            raise HTTPException(422, "Unknown practice service.")
    for link in list(row.service_links):
        if link.service_id not in wanted:
            row.service_links.remove(link)
    have = {l.service_id for l in row.service_links}
    for sid in wanted:
        if sid not in have:
            row.service_links.append(models.ScenarioService(service_id=sid))
    db.commit()
    db.refresh(row)
    return row


@router.get("/scenarios", response_model=list[schemas.ScenarioOut])
def list_scenarios(engagement_id: str, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    return db.execute(
        select(models.PersonaScenario).where(
            models.PersonaScenario.engagement_id == engagement_id
        )
    ).scalars().all()


def _scenario_base(db: Session, bundle_id: str | None, ref: str | None) -> str | None:
    """The library plan a scenario's base is: the plan a person picked, else the
    plan its typed name resolves to (TARGET_SCHEMA §5.2). The one place this is
    decided for writes; the interpreter's loader applies the same rule."""
    return bundle_id or bundles.resolve_bundle(db, ref or "")


def _check_bundle(db: Session, bundle_id: str | None) -> None:
    if bundle_id is not None and db.get(models.Bundle, bundle_id) is None:
        raise HTTPException(422, "That plan isn't in the library.")


def _validate_addon_eligibility(db: Session, base_id: str | None, addon_bundle_ids: list[str]):
    """Enforce the composition "logic layer": each add-on must be eligible for the
    scenario's base bundle (à-la-carte add-ons layer onto anything). Skips when the
    base isn't known yet (no target set) — there's nothing to check against."""
    if base_id is None:
        return
    elig_map = bundles.eligibility_map(db)
    for bid in addon_bundle_ids:
        if not bundles.addon_applies(bid, base_id, elig_map):
            addon = db.get(models.Bundle, bid)
            base = db.get(models.Bundle, base_id)
            raise HTTPException(
                422,
                f"'{addon.name if addon else bid}' cannot be added to "
                f"'{base.name if base else base_id}': it is not eligible for that base.",
            )


def _set_scenario_addons(db: Session, row: models.PersonaScenario, addons: list, base_id: str | None):
    """Reconcile a scenario's add-on bundles to the given set (by bundle_id), after
    validating each is eligible for the scenario's base plan."""
    want = {a["bundle_id"]: a for a in addons}
    _validate_addon_eligibility(db, base_id, list(want))
    have = {ad.bundle_id: ad for ad in row.addons}
    for bid, ad in list(have.items()):
        if bid not in want:
            row.addons.remove(ad)
    for bid, a in want.items():
        if bid in have:
            have[bid].unit_price_annual = a["unit_price_annual"]
        else:
            row.addons.append(models.ScenarioAddon(
                bundle_id=bid, unit_price_annual=a["unit_price_annual"]))


def _requote_scenario(db: Session, eng: models.Engagement, s: models.PersonaScenario) -> None:
    """Re-price the scenario's composed target (base + add-ons) from the catalog
    at its effective quoting basis — the scenario's term/billing when set, else
    the engagement defaults (the global → engagement → line hierarchy). Called
    when the operator changes the scenario's term/payment model; prices remain
    hand-editable afterward. Only prices a fresh quote can actually find (> 0)
    are written, so a hand-entered price on an uncatalogued bundle survives."""
    basis = bundles.engagement_price_basis(eng)
    if s.term_duration:
        basis["term"] = s.term_duration
    if s.billing_plan:
        basis["billing"] = s.billing_plan
    if s.target_sku_reference or s.bundle_id:
        base_id = _scenario_base(db, s.bundle_id, s.target_sku_reference)
        price = bundles.catalog_annual_erp(db, s.target_sku_reference, bundle_id=base_id, **basis)
        if price > 0:
            s.target_unit_price_annual = price
    for ad in s.addons:
        b = db.get(models.Bundle, ad.bundle_id)
        if b is None:
            continue
        price = bundles.catalog_annual_erp(db, b.name, bundle_id=b.id, **basis)
        if price > 0:
            ad.unit_price_annual = price


@router.post("/scenarios", response_model=schemas.ScenarioOut, status_code=201)
def create_scenario(engagement_id: str, payload: schemas.ScenarioIn, db: Session = Depends(get_db)):
    _require_engagement(db, engagement_id)
    data = payload.model_dump()
    addons = data.pop("addons", [])
    sku = _picked_sku(db, data.pop("catalog_sku_id", None))
    if sku is not None:
        # A plan on a picked SKU: its name and library plan, and its list price
        # unless one was typed.
        data["target_sku_reference"] = sku.sku_title
        data["bundle_id"] = data.get("bundle_id") or sku.bundle_id
        if not data.get("target_unit_price_annual"):
            data["target_unit_price_annual"] = sku.annual_erp_price
    _check_bundle(db, data.get("bundle_id"))
    # One plan per persona (TARGET_SCHEMA §5.2): a second would be costed too.
    if db.execute(select(models.PersonaScenario.id).where(
            models.PersonaScenario.engagement_id == engagement_id,
            models.PersonaScenario.persona_id == data["persona_id"])).first():
        raise HTTPException(409, "This persona already has a plan; change that one.")
    row = models.PersonaScenario(engagement_id=engagement_id, **data)
    _set_scenario_addons(db, row, addons, _scenario_base(db, row.bundle_id, row.target_sku_reference))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/scenarios/{scenario_id}", response_model=schemas.ScenarioOut)
def update_scenario(engagement_id: str, scenario_id: str, payload: schemas.ScenarioUpdate, db: Session = Depends(get_db)):
    eng = _require_engagement(db, engagement_id)
    row = db.get(models.PersonaScenario, scenario_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Scenario not found")
    data = payload.model_dump(exclude_unset=True)
    sku = _picked_sku(db, data.pop("catalog_sku_id", None))
    if sku is not None:
        # The pick records the library plan the SKU is (TARGET_SCHEMA §5.2).
        data.setdefault("bundle_id", sku.bundle_id)
        _apply_pick(sku, data, ref="target_sku_reference", price="target_unit_price_annual",
                    old_ref=row.target_sku_reference, old_price=row.target_unit_price_annual)
    _check_bundle(db, data.get("bundle_id"))
    # A renamed target keeps its picked plan only while the new name still reads as
    # that plan; typing a different plan's name drops the link (the name is then
    # resolved, as for any typed target).
    if ("target_sku_reference" in data and "bundle_id" not in data and row.bundle_id
            and bundles.resolve_bundle(db, data["target_sku_reference"] or "") != row.bundle_id):
        data["bundle_id"] = None
    if "addons" in data:
        # Validate against the base being set in this same PATCH if present, else
        # the scenario's existing base.
        base_id = _scenario_base(db, data.get("bundle_id", row.bundle_id),
                                 data.get("target_sku_reference", row.target_sku_reference))
        _set_scenario_addons(db, row, data.pop("addons") or [], base_id)
    basis_changed = any(
        k in data and data[k] != getattr(row, k) for k in ("term_duration", "billing_plan")
    )
    for k, v in data.items():
        setattr(row, k, v)
    if basis_changed:
        _requote_scenario(db, eng, row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(engagement_id: str, scenario_id: str, db: Session = Depends(get_db)):
    row = db.get(models.PersonaScenario, scenario_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(404, "Scenario not found")
    db.delete(row)
    db.commit()
