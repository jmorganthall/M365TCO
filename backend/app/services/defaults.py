"""Accessor for the single GlobalDefaults row (PRD 5.10).

Get-or-create the singleton so the operator-editable defaults are always
available without a migration/seed step.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models


def get_defaults(db: Session) -> models.GlobalDefaults:
    """The install settings. Before anyone saves them, an unsaved row with the
    built-in defaults — reading never creates it (viewing never writes)."""
    row = db.get(models.GlobalDefaults, "singleton")
    return row if row is not None else models.GlobalDefaults.unsaved()
