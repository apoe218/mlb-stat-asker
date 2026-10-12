"""Pull time ranges, game types and answer modes out of a question."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta


def season_of(d: date) -> int:
    """Baseball season a date belongs to (Jan–Feb count toward the season just finished)."""
    return d.year if d.month >= 3 else d.year - 1


@dataclass
class Parsed:
    season_from: int | None = None
    season_to: int | None = None
    game_types: list[str] | None = None
    mode: str = "list"            # last | first | count | list | all
    notes: list[str] = field(default_factory=list)
    date_from: date | None = None   # a month or "last N days" window; always ends on/before the cutoff
    date_to: date | None = None
    window_label: str = ""


MODE_PATTERNS = [
    ("last", r"\blast time\b|\bmost recent(ly)?\b|\blatest\b|\bwhen (was|did) (the |it )?(last|latest)\b|\bwhen did .{0,40}\blast\b|"
                  r"\blast\b(?!\s+(season|year|night|week|month|\d))"),
    ("first", r"\bfirst time\b|\bfirst\b(?!\s+(base|inning|pitch|half))|\bearliest\b|\bwhen was the first\b|\bfirst ever\b|\bwhen did .{0,40}\bfirst\b"),
    ("all", r"\blist (all|every)\b|\ball (the )?(occurrences|instances|times|of them)\b|\bevery (time|single)\b|\bshow (me )?all\b|\bfull list\b"),
    ("count", r"\bhow many\b|\bnumber of\b|\bcount\b|\bhow often\b|\btotal (number|times)\b"),
]


def parse(q: str, cutoff: date) -> Parsed:
    q = q.lower()
    out = Parsed()
    cur = season_of(cutoff)

    for mode, pat in MODE_PATTERNS:
        if re.search(pat, q):
            out.mode = mode
            break

    years = [int(y) for y in re.findall(r"\b(18[89]\d|19\d\d|20\d\d)\b", q)]
    if re.search(r"\bbetween\s+(\d{4})\s+and\s+(\d{4})|\bfrom\s+(\d{4})\s+(to|through|until)\s+(\d{4})", q) and len(years) >= 2:
        out.season_from, out.season_to = min(years[:2]), max(years[:2])
    elif re.search(r"\b(since|starting (in|from)|from)\s+\d{4}", q) and years:
        out.season_from = years[0]
    elif re.search(r"\b(after)\s+\d{4}", q) and years:
        out.season_from = years[0] + 1
    elif re.search(r"\b(before|prior to|until)\s+\d{4}", q) and years:
        out.season_to = years[0] - 1 if re.search(r"before|prior", q) else years[0]
    elif years:
        out.season_from = out.season_to = years[0]
    elif re.search(r"\b(this|current) (season|year)\b|\bso far this\b", q):
        out.season_from = out.season_to = cur
    elif re.search(r"\blast (season|year)\b", q):
        out.season_from = out.season_to = cur - 1

    _date_window(q, cutoff, out)

    if re.search(r"world series", q):
        out.game_types = ["W"]
    elif re.search(r"\b(alcs|nlcs|championship series|league championship)\b", q):
        out.game_types = ["L"]
    elif re.search(r"\b(alds|nlds|division series)\b", q):
        out.game_types = ["D"]
    elif re.search(r"wild[- ]card (game|round|series)", q):
        out.game_types = ["F"]
    elif re.search(r"post[- ]?season|playoff|october baseball", q):
        out.game_types = ["F", "D", "L", "W"]
    elif re.search(r"regular season", q):
        out.game_types = ["R"]
    return out


MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}


def _date_window(q: str, cutoff: date, out: Parsed) -> None:
    """'in April' / 'during June 2024' / 'last 14 days' → an exact date window."""
    m = re.search(r"\b(?:in|during|for|of)\s+(" + "|".join(MONTHS) + r")\b(?:\s+(\d{4}))?", q)
    if m:
        month = MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else (out.season_from or cutoff.year)
        if date(year, month, 1) > cutoff:          # "in October" in March means last October
            year -= 1
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        out.date_from, out.date_to = first, min(last, cutoff)
        out.window_label = f"{calendar.month_name[month]} {year}"
        if out.season_from is None:
            out.season_from = out.season_to = year
        return
    m = re.search(r"\b(?:last|past)\s+(\d{1,3})\s+(day|week|month)s?\b", q)
    n = None
    if m:
        n = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
        label = f"last {m.group(1)} {m.group(2)}s" if m.group(1) != "1" else f"last {m.group(2)}"
    elif re.search(r"\b(?:last|past) week\b", q):
        n, label = 7, "last 7 days"
    elif re.search(r"\b(?:last|past) month\b", q):
        n, label = 30, "last 30 days"
    if n:
        out.date_from, out.date_to = cutoff - timedelta(days=n - 1), cutoff
        out.window_label = label


def in_phrase(label: str) -> str:
    """'in April 2026' / 'in the 2026 season' / 'in the last 14 days' / 'in career'."""
    if label and label.split()[0].lower() in MONTHS:
        return f"in {label}"
    return f"in the {label}"
