from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Answer:
    text: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    footnote: str = ""
    kind: str = "answer"

    def to_dict(self) -> dict:
        return {"text": self.text, "columns": self.columns, "rows": self.rows,
                "footnote": self.footnote, "kind": self.kind}


def pretty_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def through(cutoff: date) -> str:
    return f"Data through {pretty_date(cutoff.isoformat())}."
