"""Routes a question to the right answerer."""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime

from . import glossary
from .answer import Answer, through
from .client import MlbClient
from .cutoff import cutoff_date
from .entities import Entities
from .events import EventSearch
from .stats import Stats
from .timeparse import parse

EXAMPLES = [
    "How many home runs does Aaron Judge have this season?",
    "Who leads MLB in ERA?",
    "Judge vs Ohtani",
    "How are the Dodgers doing?",
    "What was the score of the Brewers game last night?",
    "Last time a game ended on a pickoff at first base",
    "List all unassisted triple plays",
    "Who won the 1956 World Series?",
]

HELP = ("I answer MLB stat questions from the public MLB Stats API, using games through **yesterday** only: "
        "player stats and comparisons, leaders, standings, scores, World Series champions, and history "
        "search (game endings, triple plays, walk-offs, no-hitters, records…).")

RESULT_RE = re.compile(r"\b(score|scores|result|results|box score|who won|beat|did .{1,40}\b(win|lose)|final|"
                       r"what happened|how did (the )?.{1,30} do|recap|"
                       r"game (yesterday|last night)|play(ed)? (yesterday|last night)|last game)\b")
STANDINGS_RE = re.compile(r"standings|games back|\bgb\b|division (lead|race)|wild[- ]card (race|standings)|"
                          r"how (are|is|have) (the )?.{1,30}(doing|been)|\brecord\b|\bwhat place\b")
SEASON_RECAP_RE = re.compile(r"how (did|have|do) (the )?.{1,40}?\b(do|finish|fare|perform|end up)\b")
WINS_RE = re.compile(r"how many (games|wins)\b.*\b(win|won|wins)\b|\bhow many wins\b")
LEADERS_RE = re.compile(r"\bled?\b|\blead(s|er|ers|ing)?\b|\btop \d+\b|who (has|had) the (most|best|highest|lowest|fewest|worst)|"
                        r"\bbest .{1,30} in (the )?(al|nl|american league|national league|league|mlb|majors|baseball)|"
                        r"\b(in|for) the (al|nl|american league|national league)\b|"
                        r"\bmost .{1,25} (in|across) (the )?(league|mlb|majors|baseball)|"
                        r"\b(worst|highest|lowest|fewest)\b .{0,20}\b(era|whip|avg|average|ops|obp|slg)\b")

# asking about games that haven't finished, or the schedule: the cutoff is yesterday
FUTURE_RE = re.compile(
    r"\bnext game\b|\bwhat time\b|\bschedule\b|\bupcoming\b|\bwho(?:'s| is) pitching\b|\bfirst pitch\b|"
    r"\b(today|tonight|tomorrow)(?:'s)?\s+(games?|scores?|schedule|lineups?|matchups?|starters?)\b|"
    r"\b(games?|score|scores|play|playing|lineup)\b.*\b(today|tonight|tomorrow|live|right now)\b|"
    r"\b(today|tonight|tomorrow|live|right now)\b.*\b(game|games|score|scores|play|playing)\b")

# data this app doesn't have: say so plainly instead of a generic "not sure"
UNSUPPORTED = [
    (r"\b(how long|duration|length of (the )?game|time of game|fastest game|shortest game|quickest game|longest delay)\b",
     "game duration"),
    (r"\battendance\b", "attendance"),
    (r"\b(mvp|cy young|gold glove|silver slugger|rookie of the year|hall of fame|awards?)\b", "awards and honors"),
    (r"\b(salary|salaries|contract|payroll|traded?|injur\w*|free agen\w*|draft|prospects?|roster|depth chart)\b",
     "contracts, trades, injuries or rosters"),
    (r"\b(odds|betting|point spread|predict\w*|projection|who will win|chances of)\b", "odds or predictions"),
    (r"\b(xwoba|exit velocity|launch angle|spin rate|statcast|barrel rate|war|fip)\b", "advanced metrics"),
]


class Asker:
    def __init__(self, client: MlbClient | None = None, index: sqlite3.Connection | None = None,
                 now: datetime | None = None):
        self.client = client or MlbClient()
        self.index = index
        self._now = now

    @property
    def cutoff(self) -> date:
        return cutoff_date(self._now)

    @staticmethod
    def help(prefix: str = "") -> Answer:
        return Answer((prefix + "\n\n" if prefix else "") + HELP, kind="help", title="Try asking",
                      headline=prefix or "What would you like to know?", suggestions=EXAMPLES)

    def ask(self, question: str) -> Answer:
        q = (question or "").strip()
        if not q:
            return self.help()
        cutoff = self.cutoff
        ql = q.lower()
        foot = through(cutoff)

        # 0. definitions ("what is OPS")
        defined = glossary.lookup(q)
        if defined:
            return defined

        # 1. games that haven't finished / the schedule
        if FUTURE_RE.search(ql):
            return Answer(f"I only use completed games through {cutoff.strftime('%b')} {cutoff.day}, {cutoff.year} — "
                          f"nothing from today or the future, so no live scores, schedules or lineups.",
                          kind="notice", title="Completed games only", footnote=foot,
                          suggestions=["What was the score of the Dodgers game last night?", "Show me the standings"])

        # 2. things this app has no data for
        for pat, thing in UNSUPPORTED:
            if re.search(pat, ql):
                return Answer(f"I don't have {thing} in my data — I work from game results, play-by-play and "
                              f"official season stats.", kind="notice", title="Not available", footnote=foot,
                              suggestions=EXAMPLES[:4])

        entities = Entities(self.client, self.index)
        events = EventSearch(self.index, entities, cutoff) if self.index is not None else None

        # 3. history: game endings, records, champions, … from the local index
        if events is not None and events.can_answer(q):
            return events.answer(q)
        if events is None:
            from .index import catalog
            if catalog.match(ql) is not None:
                return Answer("That needs the play-by-play index, which isn't built yet. "
                              "Run `python -m mlbasker sync` once.", kind="needs_index", title="Index not built")

        # 4. live lookups
        stats = Stats(self.client, entities, cutoff)
        parsed = parse(q, cutoff)
        teams, _ = entities.find_teams(q, seasons=(parsed.season_from, parsed.season_to))
        has_date = stats._target_date(q) is not None

        if WINS_RE.search(ql) and teams:                     # "how many games did the Yankees win in 2019"
            return stats.standings(q, parsed, teams, wins_focus=True)
        if SEASON_RECAP_RE.search(ql) and teams and not has_date and not re.search(r"game|last night|yesterday", ql):
            return stats.standings(q, parsed, teams)         # "how did the 1955 Dodgers do"
        if len(teams) >= 2 and not STANDINGS_RE.search(ql) and not LEADERS_RE.search(ql):
            return stats.results(q, teams, parsed)           # "Dodgers vs Giants": head to head
        if RESULT_RE.search(ql) and (teams or re.search(r"yesterday|last night|scores", ql) or has_date):
            return stats.results(q, teams, parsed)
        if STANDINGS_RE.search(ql) and not LEADERS_RE.search(ql):
            return stats.standings(q, parsed, teams)
        if LEADERS_RE.search(ql):
            return stats.leaders(q, parsed, teams)

        players, ambiguous = entities.find_players(q, lenient=True)
        if ambiguous:     # never silently answer for a different player than the one meant
            tok = ambiguous[0]
            opts = entities.options.get(tok, [])
            return Answer(f"More than one player matches **{tok}** — which one do you mean?", kind="notice",
                          title="Which player?", footnote=foot,
                          headline=f"Which {tok}?",
                          suggestions=[re.sub(re.escape(tok), p.name, q, count=1, flags=re.I) for p in opts])
        if len(players) >= 2:
            return stats.compare(q, players, parsed)
        if players:
            return stats.player_stat(q, players[0], parsed)
        if teams:
            return stats.standings(q, parsed, teams)
        return self.help("I'm not sure what you're asking.")
