"""Routes a question to the right answerer."""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime

from .answer import Answer, through
from .client import MlbClient
from .cutoff import cutoff_date
from .entities import Entities
from .events import EventSearch
from .stats import Stats
from .timeparse import parse

HELP = """I answer MLB stat questions from the public MLB Stats API, using games through **yesterday** only.

Try things like:
- *How many home runs does Aaron Judge have this season?*
- *Who leads MLB in ERA?* · *Top 5 in stolen bases in 2024*
- *Show me the standings* · *How are the Dodgers doing?*
- *What was the score of the Brewers game last night?*
- *Last time a game ended on a pickoff at first base*
- *List all unassisted triple plays* · *How many walk-off grand slams since 2015?*"""

RESULT_RE = re.compile(r"\b(score|scores|result|results|box score|who won|beat|did .{1,40}\b(win|lose)|final|"
                       r"game (yesterday|last night)|play(ed)? (yesterday|last night)|last game)\b")
STANDINGS_RE = re.compile(r"standings|games back|\bgb\b|division (lead|race)|wild[- ]card (race|standings)|"
                          r"how (are|is|have) (the )?.{1,30}(doing|been)|\brecord\b|\bwhat place\b")
LEADERS_RE = re.compile(r"\blead(s|er|ers|ing)?\b|\btop \d+\b|who (has|had) the (most|best|highest|lowest|fewest)|"
                        r"\bbest .{1,30} in (the )?(league|mlb|majors|baseball)|\bmost .{1,25} (in|across) (the )?(league|mlb|majors|baseball)")


class Asker:
    def __init__(self, client: MlbClient | None = None, index: sqlite3.Connection | None = None,
                 now: datetime | None = None):
        self.client = client or MlbClient()
        self.index = index
        self._now = now

    @property
    def cutoff(self) -> date:
        return cutoff_date(self._now)

    def ask(self, question: str) -> Answer:
        q = (question or "").strip()
        if not q:
            return Answer(HELP, kind="help")
        cutoff = self.cutoff
        entities = Entities(self.client, self.index)
        ql = q.lower()

        # 1. history / "any information" questions → play-by-play index
        if self.index is not None:
            events = EventSearch(self.index, entities, cutoff)
            if events.can_answer(q):
                return events.answer(q)
        elif EventSearchProbe.looks_historical(ql):
            return Answer("That needs the play-by-play index, which isn't built yet. "
                          "Run `python -m mlbasker sync` once.", kind="needs_index")

        stats = Stats(self.client, entities, cutoff)
        parsed = parse(q, cutoff)
        teams, _ = entities.find_teams(q)

        if RESULT_RE.search(ql) and (teams or re.search(r"yesterday|last night|tonight|today|tomorrow|scores", ql)
                                      or stats._target_date(q)):
            return stats.results(q, teams)
        if STANDINGS_RE.search(ql) and not LEADERS_RE.search(ql):
            return stats.standings(q, parsed, teams)
        if LEADERS_RE.search(ql):
            return stats.leaders(q, parsed, teams)

        players, ambiguous = entities.find_players(q)
        if players:
            return stats.player_stat(q, players[0], parsed)
        if ambiguous:
            return Answer(f"More than one player matches **{ambiguous[0]}**. Can you give me the full name?",
                          footnote=through(cutoff))
        if teams:
            if RESULT_RE.search(ql):
                return stats.results(q, teams)
            return stats.standings(q, parsed, teams)
        return Answer("I'm not sure what you're asking.\n\n" + HELP, kind="help", footnote=through(cutoff))


class EventSearchProbe:
    """Cheap check used when no index is loaded."""

    @staticmethod
    def looks_historical(ql: str) -> bool:
        from .index import catalog
        return catalog.match(ql) is not None
