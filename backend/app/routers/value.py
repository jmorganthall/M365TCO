"""Workshop value: delivered workshops rolled up (TARGET_SCHEMA §7.2, D30)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import workshop_value

router = APIRouter(prefix="/api/workshop-value", tags=["workshop-value"])


@router.get("")
def get_workshop_value(db: Session = Depends(get_db)):
    """Totals and headlines across every delivered workshop. Reads only, and
    never returns a customer's name or id — the page is built for screenshots."""
    return workshop_value.workshop_value(db)
