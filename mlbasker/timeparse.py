"""Pull time ranges, game types and answer modes out of a question."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


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
