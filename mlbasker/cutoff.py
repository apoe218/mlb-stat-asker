"""The data cutoff: answers only ever use games through yesterday (US Eastern)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def today_et(now: datetime | None = None) -> date:
    now = now or datetime.now(ET)
    return now.astimezone(ET).date()


def cutoff_date(now: datetime | None = None) -> date:
    """Last date whose games may be used: yesterday, Eastern time."""
    return today_et(now) - timedelta(days=1)


def clamp(d: date, now: datetime | None = None) -> date:
    return min(d, cutoff_date(now))
