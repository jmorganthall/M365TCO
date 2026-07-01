"""Accessor for the single GlobalDefaults row (PRD 5.10).

Get-or-create the singleton so the operator-editable defaults are always
available without a migration/seed step.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models


def get_defaults(db: Session) -> models.GlobalDefaults:
    row = db.get(models.GlobalDefaults, "singleton")
    if row is None:
        row = models.GlobalDefaults(id="singleton")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row
