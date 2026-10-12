"""Answer 'any information' questions from the play-by-play index."""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

from .answer import Answer, pretty_date, through
from .entities import Entities
from .index import catalog
from .index.catalog import Filters
from .index.db import has_fts
from .index.sync import coverage
from .timeparse import Parsed, parse

LABELS = {
    "walk_off": "walk-off", "game_end": "game-ending play", "no_hitter": "no-hitter",
    "cycle": "cycle", "multi_out": "multi-out play", "game_extreme": "game", "play_event": "play",
}
SHOW = {"list": 10, "all": 40, "count": 5, "last": 1, "first": 1}


def _plural(w: str) -> str:
    if w.endswith("y") and not w.endswith(("ay", "ey")):
        return w[:-1] + "ies"
    return w if w.endswith("s") and not w.endswith("ss") and False else w + "s"


def _event_phrase(entry: str, spec: dict) -> tuple[str, str]:
    """(singular, plural) noun phrase for the thing being searched."""
    if entry == "multi_out":
        w = {2: "double play", 3: "triple play"}[spec["outs"]]
        w = ("unassisted " if spec["unassisted"] else "") + w
        return w, w + "s"
    if entry == "game_end":
        fam, base = spec["family"], spec["base"]
        if fam == "pickoff":
            names = {"1b": "first base", "2b": "second base", "3b": "third base"}
            tail = "a pickoff" + (f" at {names[base]}" if base else "")
        elif fam:
            tail = ("an " if fam[0] in "aeiou" else "a ") + fam.replace("_", " ")
        else:
            return "game-ending play", "game-ending plays"
        return f"game that ended on {tail}", f"games that ended on {tail}"
    if entry in ("walk_off", "play_event"):
        w = spec["label"]
        return w, _plural(w)
    if entry == "no_hitter":
        w = "perfect game" if spec["perfect"] else "no-hitter"
        return w, w + "s"
    if entry == "cycle":
        return "cycle", "cycles"
    if entry == "game_extreme":
        return spec["label"], spec["label"]
    return entry, entry


def _clean_phrase(q: str) -> str | None:
    """A quoted phrase, or text after 'plays that say / description contains'."""
    m = re.search(r'["“]([^"”]{3,80})["”]', q)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:description|plays?|text)\s+(?:contains?|says?|mentions?|with)\s+(.{3,60})$", q.rstrip("?. "), re.I)
    return m.group(1).strip(" '\"") if m else None


class EventSearch:
    def __init__(self, con: sqlite3.Connection, entities: Entities, cutoff: date):
        self.con, self.entities, self.cutoff = con, entities, cutoff

    def _filters(self, q: str, parsed: Parsed) -> tuple[Filters, list[str], list[str]]:
        teams, _ = self.entities.find_teams(q)
        players, ambiguous = self.entities.find_players(q)
        f = Filters(season_from=parsed.season_from, season_to=parsed.season_to,
                    date_to=self.cutoff.isoformat(), game_types=parsed.game_types,
                    team_ids=[t.id for t in teams], player_ids=[p.id for p in players],
                    player_names=[p.name for p in players])
        if (len(teams) == 1 and re.search(r"walk[- ]?offs?|walked[- ]off", q.lower())
                and not re.search(r"\b(against|vs\.?|versus|off of|allowed|gave up|surrender)", q.lower())):
            f.home_only = True      # "the Yankees had a walk-off" = the Yankees won it
        scope = [t.name for t in teams] + [p.name for p in players]
        return f, scope, ambiguous

    def can_answer(self, q: str) -> bool:
        ql = q.lower()
        return catalog.match(ql) is not None or _clean_phrase(q) is not None

    def answer(self, q: str) -> Answer:
        cov = coverage(self.con)
        if not cov["games"]:
            return Answer("The play-by-play index is empty, so I can't search history yet. "
                          "Run `python -m mlbasker sync` once to build it (a few minutes per decade).",
                          kind="needs_index")
        ql = q.lower()
        parsed = parse(q, self.cutoff)
        f, scope, ambiguous = self._filters(q, parsed)

        matched = catalog.match(ql)
        ranked = False
        if matched:
            entry, spec = matched
            sql, params, order = catalog.build(entry, spec, f)
            ranked = bool(order)
            one, many = _event_phrase(entry, spec)
            if entry == "game_extreme":
                one = many = {"innings": "longest games", "total runs": "highest-scoring games",
                              "run margin": "most lopsided games"}.get(spec["label"], "games")
            if entry == "single_game_record":
                stat = {"strikeouts": "strikeout", "walks": "walk", "home runs": "home run"}.get(spec["stat"], spec["stat"].rstrip("s"))
                one = many = f"single-game {stat} performances"
        else:
            text = _clean_phrase(q)
            sql, params = catalog.text_search(f, text, has_fts(self.con) and self._fts_ready())
            order, one, many = "", f'play mentioning "{text}"', f'plays mentioning "{text}"'

        mode = parsed.mode
        total = self.con.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
        where = self._where_text(scope, parsed)
        span = f"{cov['first_season']}–{cov['last_season']}"
        foot = (f"Searched {cov['games']:,} indexed games ({span}), {through(self.cutoff)}"
                + (f" Couldn't tell which player you meant by: {', '.join(ambiguous)}." if ambiguous else ""))

        if total == 0:
            return Answer(f"I couldn't find any {many}{where} in the indexed games.", footnote=foot)

        if mode == "count" and not ranked:
            return Answer(f"There {'has' if total == 1 else 'have'} been **{total:,}** "
                          f"{one if total == 1 else many}{where} in the indexed games.", footnote=foot)

        direction = "ASC" if mode == "first" else "DESC"
        order_by = order or f"date {direction}, game_pk {direction}, inning {direction}"
        limit = 1 if (mode in ("last", "first") and not ranked) else SHOW["all" if mode == "all" else "list"]
        rows = self.con.execute(f"SELECT * FROM ({sql}) ORDER BY {order_by} LIMIT {limit}", params).fetchall()
        table = [[pretty_date(r["date"]), self._matchup(r), self._inning(r), r["detail"] or ""] for r in rows]
        cols = ["Date", "Game", "Inning", "Play"]

        if mode in ("last", "first") and not ranked:
            r = rows[0]
            when = "most recent" if mode == "last" else "first"
            lead = (f"The {when} {one}{where} was on **{pretty_date(r['date'])}** — "
                    f"{self._matchup(r)}{self._inning(r, ' (', ')')}.")
            if r["detail"]:
                lead += f"\n\n> {r['detail']}"
            if total > 1:
                lead += f"\n\nThere {'are' if total != 2 else 'is'} {total - 1:,} other{'s' if total != 2 else ''} in the index."
            lead += f"\n\n*Based on indexed games from {pretty_date(cov['from'])} to {pretty_date(cov['to'])}.*"
            if date.fromisoformat(cov["to"]) < self.cutoff - timedelta(days=7):
                lead += (f" ⚠️ The index is behind (it stops {pretty_date(cov['to'])}), so a more recent "
                         f"one may exist. Run `python -m mlbasker sync` to catch up.")
            return Answer(lead, footnote=foot)

        if ranked:
            head = f"Top {len(rows)} {many}{where}:"
        else:
            shown = "all" if total == len(rows) else f"the {'most recent' if direction == 'DESC' else 'earliest'} {len(rows)} of {total:,}"
            head = f"Found **{total:,}** {one if total == 1 else many}{where}; showing {shown}:"
        return Answer(head, cols, table, foot)

    def _fts_ready(self) -> bool:
        try:
            self.con.execute("SELECT 1 FROM plays_fts LIMIT 1").fetchone()
            return self.con.execute("SELECT COUNT(*) FROM plays_fts WHERE plays_fts MATCH 'the'").fetchone()[0] > 0
        except sqlite3.DatabaseError:
            return False

    @staticmethod
    def _matchup(r) -> str:
        return f"{r['away']} {r['away_score']} @ {r['home']} {r['home_score']}"

    @staticmethod
    def _inning(r, pre: str = "", post: str = "") -> str:
        if r["inning"] is None:
            return ""
        return f"{pre}{r['half']} {r['inning']}{post}"

    @staticmethod
    def _where_text(scope: list[str], parsed: Parsed) -> str:
        bits = []
        if scope:
            bits.append("involving " + " / ".join(scope))
        if parsed.season_from is not None or parsed.season_to is not None:
            a, b = parsed.season_from, parsed.season_to
            bits.append(f"in {a}" if a == b else f"from {a or 'the start'} to {b or 'now'}")
        if parsed.game_types:
            bits.append("in the postseason" if len(parsed.game_types) > 1 else
                        {"W": "in the World Series", "L": "in a league championship series",
                         "D": "in a division series", "F": "in a wild-card game", "R": "in the regular season"}[parsed.game_types[0]])
        return (" " + " ".join(bits)) if bits else ""
