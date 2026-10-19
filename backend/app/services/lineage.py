"""Carve-out lineage checks: does a carve-out still hold what its parent holds?

A carve-out is the same people as its parent (TARGET_SCHEMA D11): it copies the
parent's current-licence and third-party tags when it is made, and only its target
differs. Tags added or removed afterwards are per persona, so they don't reach the
other side — a tool tagged to the parent later is simply missing from the
carve-out, and the recommender then plans the carve-out as if the tool weren't
there. This module says where the two sides differ. It only reads; the GUI's
"Match parent" action is what changes the carve-out's tags, when a person asks."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models


def _tagged(rows, persona_id: str) -> dict:
    return {r.id: r for r in rows if persona_id in r.persona_ids}


def _rows(db: Session, engagement_id: str):
    licences = db.execute(select(models.CurrentMicrosoftLicense).where(
        models.CurrentMicrosoftLicense.engagement_id == engagement_id)).scalars().all()
    tools = db.execute(select(models.ThirdPartyProduct).where(
        models.ThirdPartyProduct.engagement_id == engagement_id)).scalars().all()
    return licences, tools


def drift(db: Session, engagement_id: str) -> list[dict]:
    """Every carve-out whose licence or tool tags differ from its parent's: what the
    parent has that it lacks (`missing_*`) and what it has that the parent doesn't
    (`extra_*`), by name. Carve-outs that match are left out."""
    from . import personas as persona_order

    eng = db.get(models.Engagement, engagement_id)
    if eng is None:
        return []
    by_id = {p.id: p for p in eng.personas}
    licences, tools = _rows(db, engagement_id)
    out = []
    for child in persona_order.ordered(eng.personas):
        parent = by_id.get(child.parent_persona_id or "")
        if parent is None:
            continue
        row = {"persona_id": child.id, "persona_name": child.name,
               "parent_id": parent.id, "parent_name": parent.name}
        for kind, items, label in (("licenses", licences, lambda r: r.sku_reference or "(unnamed line)"),
                                   ("tools", tools, lambda r: r.name or "(unnamed tool)")):
            mine, theirs = _tagged(items, child.id), _tagged(items, parent.id)
            row[f"missing_{kind}"] = sorted(label(r) for i, r in theirs.items() if i not in mine)
            row[f"extra_{kind}"] = sorted(label(r) for i, r in mine.items() if i not in theirs)
        if any(row[k] for k in ("missing_licenses", "extra_licenses", "missing_tools", "extra_tools")):
            out.append(row)
    return out


def match_parent(db: Session, child: models.Persona) -> None:
    """Give a carve-out exactly its parent's licence and tool tags (adds what it
    lacks, removes what the parent doesn't have). Edits each row's tag collection,
    as the tag routes do, so derived covers see the change. Flushes, doesn't commit."""
    licences, tools = _rows(db, child.engagement_id)
    parent_id = child.parent_persona_id
    for rows, make in ((licences, lambda: models.CurrentLicensePersona(persona_id=child.id)),
                       (tools, lambda: models.ThirdPartyPersona(persona_id=child.id))):
        for row in rows:
            ids = set(row.persona_ids)
            if parent_id in ids and child.id not in ids:
                row.persona_links.append(make())
            elif child.id in ids and parent_id not in ids:
                for link in [l for l in row.persona_links if l.persona_id == child.id]:
                    row.persona_links.remove(link)
    db.flush()
