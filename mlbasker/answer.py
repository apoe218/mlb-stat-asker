from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class Answer:
    """What the UI renders. `text` is always a complete one-sentence answer (it is also the
    plain-text fallback); everything after `kind` is optional structure for richer display."""

    text: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    footnote: str = ""
    kind: str = "answer"

    title: str = ""                      # small label above the card: "Player stats", "Standings"…
    headline: str = ""                   # the answer itself, large: "18 home runs"
    subhead: str = ""                    # who/when/where: "Aaron Judge · 2026 season · Yankees"
    tiles: list[dict] = field(default_factory=list)          # [{"label": "AVG", "value": ".241"}]
    results: list[dict] = field(default_factory=list)        # [{"date","title","meta","text"}]
    notes: list[str] = field(default_factory=list)           # caveats, shown as callouts
    suggestions: list[str] = field(default_factory=list)     # related searches
    highlight_rows: list[int] = field(default_factory=list)  # table rows to emphasise
    best_cells: list[list[int]] = field(default_factory=list)  # [[row, col]] winning cells in a comparison
    highlights: list[str] = field(default_factory=list)      # terms to bold inside result text

    def to_dict(self) -> dict:
        return asdict(self)


def pretty_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def through(cutoff: date) -> str:
    return f"Data through {pretty_date(cutoff.isoformat())}."
