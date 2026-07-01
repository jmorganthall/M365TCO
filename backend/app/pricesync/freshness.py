"""Freshness classification (PRD §6.4) — pure, offline, no auth, no API call.

Two rules, config-driven:
  - Day rule: age from fetched_at vs AGE_AGING_DAYS / AGE_STALE_DAYS.
  - Month rule (optional): if the sheet's data month is not the current calendar
    month, classify at least Aging.
When both run, the stricter state wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

FRESH = "fresh"
AGING = "aging"
STALE = "stale"

_SEVERITY = {FRESH: 0, AGING: 1, STALE: 2}


def _stricter(a: str, b: str) -> str:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


@dataclass
class Freshness:
    state: str
    age_days: Optional[int]
    data_month: Optional[str]
    current_month: str
    day_state: Optional[str]
    month_state: Optional[str]
    reasons: list[str] = field(default_factory=list)

    @property
    def is_stale(self) -> bool:
        return self.state == STALE


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def classify(
    fetched_at,
    data_month: Optional[str],
    now: Optional[datetime] = None,
    aging_days: int = 25,
    stale_days: int = 30,
    use_month_rule: bool = True,
) -> Freshness:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current_month = now.strftime("%Y-%m")

    fetched = _parse_dt(fetched_at)
    if fetched is None:
        # No cached sheet at all -> Stale (PRD FR-AGE-2).
        return Freshness(
            state=STALE, age_days=None, data_month=data_month,
            current_month=current_month, day_state=None, month_state=None,
            reasons=["No cached price sheet exists."],
        )

    age_days = (now - fetched).days
    reasons: list[str] = []

    if age_days >= stale_days:
        day_state = STALE
        reasons.append(f"Sheet is {age_days} days old (>= {stale_days}).")
    elif age_days >= aging_days:
        day_state = AGING
        reasons.append(f"Sheet is {age_days} days old (>= {aging_days}).")
    else:
        day_state = FRESH

    month_state = None
    if use_month_rule and data_month:
        if data_month != current_month:
            month_state = AGING
            reasons.append(
                f"Sheet data month {data_month} is not the current month {current_month}."
            )
        else:
            month_state = FRESH

    state = day_state
    if month_state is not None:
        state = _stricter(state, month_state)

    return Freshness(
        state=state, age_days=age_days, data_month=data_month,
        current_month=current_month, day_state=day_state, month_state=month_state,
        reasons=reasons,
    )
