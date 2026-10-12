from datetime import date, datetime
from zoneinfo import ZoneInfo

from mlbasker.cutoff import cutoff_date, clamp

ET = ZoneInfo("America/New_York")


def test_cutoff_is_yesterday_eastern():
    assert cutoff_date(datetime(2026, 10, 12, 9, 0, tzinfo=ET)) == date(2026, 10, 11)


def test_cutoff_uses_eastern_not_utc():
    # 01:30 UTC on the 13th is still the evening of the 12th in New York
    now = datetime(2026, 10, 13, 1, 30, tzinfo=ZoneInfo("UTC"))
    assert cutoff_date(now) == date(2026, 10, 11)


def test_clamp_never_passes_cutoff():
    now = datetime(2026, 10, 12, 12, 0, tzinfo=ET)
    assert clamp(date(2026, 10, 12), now) == date(2026, 10, 11)
    assert clamp(date(2026, 5, 1), now) == date(2026, 5, 1)
