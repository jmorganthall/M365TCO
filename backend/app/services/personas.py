"""Persona order: every carve-out directly after the persona it was carved from.

A carve-out (Persona.parent_persona_id) is part of its parent's story, so every
list a person reads — the GUI steps, the engine's scenario order, the readout, the
PDF and the spreadsheet — shows it right under its parent, never at the end of the
list because it was created later. One definition, used everywhere."""

from __future__ import annotations

from typing import Iterable, TypeVar

T = TypeVar("T")


def ordered(personas: Iterable[T]) -> list[T]:
    """Parents in their own order, each followed by its carve-outs (in theirs). A
    carve-out whose parent isn't in the list keeps its place."""
    items = list(personas)
    ids = {p.id for p in items}
    children: dict[str, list[T]] = {}
    for p in items:
        if p.parent_persona_id in ids:
            children.setdefault(p.parent_persona_id, []).append(p)
    out: list[T] = []
    for p in items:
        if p.parent_persona_id in ids:
            continue
        out.append(p)
        out.extend(children.get(p.id, []))
    return out


def rank(personas: Iterable) -> dict[str, int]:
    """Each persona id's position in `ordered` — a sort key for rows keyed by persona."""
    return {p.id: i for i, p in enumerate(ordered(personas))}
