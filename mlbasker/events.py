"""Answer history questions from the local index: plays, game endings, records, champions."""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

from .answer import Answer, pretty_date, through
from .entities import Entities
from .index import catalog
from .index.catalog import Filters
from .index.db import has_fts
from .index.sync import FIRST_PBP_SEASON, coverage
from .timeparse import MONTHS, Parsed, in_phrase, parse, season_of

SHOW = {"list": 10, "all": 40}
SCORE_LEVEL = {"game_extreme", "game_score", "game_count"}    # need only final scores, not plays
ROUNDS = {"F": "Wild Card", "D": "Division Series", "L": "League Championship Series", "W": "World Series"}
MLB_LEAGUES = ("American League", "National League")
CHAMP_RE = re.compile(r"world series")
CHAMP_WORDS = re.compile(r"\b(won|win|wins|winner|winners|champion|champions|champs|titles?|rings?|"
                         r"swept|beat)\b")


def _plural(w: str) -> str:
    if w.endswith("y") and not w.endswith(("ay", "ey")):
        return w[:-1] + "ies"
    return w + "s"


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
    if entry == "game_score":
        w = spec["label"]
        return w, _plural(w) if w != "shutout" else "shutouts"
    if entry == "game_count":
        return "game", "games"
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


def _round(game_type: str | None) -> str:
    return ROUNDS.get(game_type or "", "")


class EventSearch:
    def __init__(self, con: sqlite3.Connection, entities: Entities, cutoff: date):
        self.con, self.entities, self.cutoff = con, entities, cutoff

    # --- routing ----------------------------------------------------------------------
    def can_answer(self, q: str) -> bool:
        ql = q.lower()
        return (catalog.match(ql) is not None or _clean_phrase(q) is not None
                or self._champion_question(ql))

    @staticmethod
    def _champion_question(ql: str) -> bool:
        return bool(CHAMP_RE.search(ql) and CHAMP_WORDS.search(ql) and not re.search(r"\bmvp\b", ql))

    def _filters(self, q: str, parsed: Parsed, spec: dict | None = None
                 ) -> tuple[Filters, list[str], list[str], list]:
        teams, _ = self.entities.find_teams(q, seasons=(parsed.season_from, parsed.season_to))
        players, ambiguous = self.entities.find_players(q)
        date_to = min(parsed.date_to, self.cutoff) if parsed.date_to else self.cutoff
        f = Filters(season_from=parsed.season_from, season_to=parsed.season_to,
                    date_from=parsed.date_from.isoformat() if parsed.date_from else None,
                    date_to=date_to.isoformat(), game_types=parsed.game_types,
                    team_ids=[t.id for t in teams], player_ids=[p.id for p in players],
                    player_names=[p.name for p in players])
        if (len(teams) == 1 and re.search(r"walk[- ]?offs?|walked[- ]off", q.lower())
                and not re.search(r"\b(against|vs\.?|versus|off of|allowed|gave up|surrender)", q.lower())):
            f.home_only = True      # "the Yankees had a walk-off" = the Yankees won it
        if spec and spec.get("result") and len(teams) == 1:
            f.team_result = spec["result"]      # "the Red Sox won 1-0"
        m_ = catalog.match(q.lower())
        if len(teams) == 1 and m_ and m_[0] == "no_hitter":
            victim = re.search(r"\bagainst\b|(were|was|got|get|been|being) (no[- ]?hit|perfect)|held hitless", q.lower())
            f.team_role = "victim" if victim else "thrower"   # "a Dodgers no-hitter" = their pitchers threw it
        scope = [t.name for t in teams] + [p.name for p in players]
        return f, scope, ambiguous, players

    # --- main entry -------------------------------------------------------------------
    def answer(self, q: str) -> Answer:
        cov = coverage(self.con)
        ql = q.lower()
        if self._champion_question(ql) and catalog.match(ql) is None:
            return self.champions(q, cov)
        matched = catalog.match(ql)
        score_level = bool(matched and matched[0] in SCORE_LEVEL)
        if not (cov["all_games"] if score_level else cov["games"]):
            return Answer("The history index is empty, so I can't search it yet. Run "
                          "`python -m mlbasker sync` once to build it (about an hour for everything; "
                          "recent seasons first).", kind="needs_index", title="Index not built",
                          notes=["Run `python -m mlbasker sync` once. Recent seasons are indexed first."])
        parsed = parse(q, self.cutoff)
        spec = matched[1] if matched else None
        f, scope, ambiguous, players = self._filters(q, parsed, spec)
        blocked = (self._outside_score_coverage(parsed, cov) if score_level
                   else self._outside_play_coverage(parsed, cov))
        if blocked:
            return Answer(blocked, kind="notice", title="Out of range", footnote=through(self.cutoff),
                          suggestions=["Who led the league in home runs in 1927?", "1927 standings"]
                          if "1950" in blocked else [])

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
                if spec.get("min"):
                    one, many = f"game with {spec['min']}+ {spec['stat']} by one player", \
                                f"games with {spec['min']}+ {spec['stat']} by one player"
        else:
            text = _clean_phrase(q)
            sql, params = catalog.text_search(f, text, has_fts(self.con) and self._fts_ready())
            order, one, many = "", f'play mentioning "{text}"', f'plays mentioning "{text}"'
            entry = "text"

        mode = parsed.mode
        if entry == "game_count":
            mode = "count"
        total = self.con.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
        where = self._where_text(scope, parsed, f.home_only, f.team_result)
        foot = self._footnote(cov, score_level) + " " + through(self.cutoff)
        notes = []
        if ambiguous:
            notes.append(f"I couldn't tell which player you meant by: {', '.join(ambiguous)}.")
        hl = [n for p in players for n in (p.name, p.name.split()[-1])]
        if parsed.game_types is None and (parsed.season_from is not None or parsed.date_from) \
                and entry in ("play_event", "game_count", "walk_off", "game_score", "text"):
            notes.append("Includes postseason games; add “regular season” to leave them out.")
        pl = lambda n, o, m: o if n == 1 else m

        if total == 0:
            return Answer(f"I couldn't find any {many}{where} in the games I can search.", kind="notice",
                          title="No results", footnote=foot, notes=notes)

        if mode == "count" and not ranked:
            noun = pl(total, one, many)
            return Answer(f"There {'has' if total == 1 else 'have'} been **{total:,}** {noun}{where} "
                          f"in the games I can search.", footnote=foot, kind="answer", title="Count",
                          headline=f"{total:,}", subhead=f"{noun}{where}", notes=notes,
                          suggestions=self._suggest(one, many, where, "count", ranked))

        direction = "ASC" if mode == "first" else "DESC"
        order_by = order or f"date {direction}, game_pk {direction}, inning {direction}"
        limit = 1 if (mode in ("last", "first") and not ranked) else SHOW["all" if mode == "all" else "list"]
        rows = self.con.execute(f"SELECT * FROM ({sql}) ORDER BY {order_by} LIMIT {limit}", params).fetchall()
        table = [[pretty_date(r["date"]), self._matchup(r), self._inning(r), r["detail"] or ""] for r in rows]
        cols = ["Date", "Game", "Inning", "Play"]
        cards = [self._card(r, i + 1 if ranked else None) for i, r in enumerate(rows)]

        if mode in ("last", "first") and not ranked:
            r = rows[0]
            when = "most recent" if mode == "last" else "first"
            lead = (f"The {when} {one}{where} was on **{pretty_date(r['date'])}** — "
                    f"{self._matchup(r)}{self._inning(r, ' (', ')')}.")
            if r["detail"]:
                lead += f"\n\n> {r['detail']}"
            if total > 1:
                lead += f"\n\nThere {'are' if total != 2 else 'is'} {total - 1:,} other{'s' if total != 2 else ''} in the index."
            last_day = cov["all_to"] if score_level else cov["to"]
            if mode == "first" and not score_level and cov["first_season"] <= FIRST_PBP_SEASON:
                notes.append(f"The API has play-by-play only from {FIRST_PBP_SEASON}, so this is the first "
                             f"since then, not necessarily the first ever.")
            elif mode == "first" and cov["first_season"] > FIRST_PBP_SEASON and not score_level:
                notes.append(f"The index holds {cov['first_season']}–{cov['last_season']} so far, so an "
                             f"earlier one may exist.")
            if date.fromisoformat(last_day) < self.cutoff - timedelta(days=7):
                notes.append(f"The index is behind (it stops {pretty_date(last_day)}), so a more recent one may "
                             f"exist. Run `python -m mlbasker sync` to catch up.")
            return Answer(lead, cols, table, foot, title="Most recent" if mode == "last" else "First on record",
                          headline=pretty_date(r["date"]), subhead=f"{one[:1].upper() + one[1:]}{where}",
                          results=cards, notes=notes,
                          highlights=hl, suggestions=self._suggest(one, many, where, mode, ranked))

        if ranked:
            head = f"Top {len(rows)} {many}{where}:"
            headline = f"Top {len(rows)} {many}"
            sub = where.strip()
        else:
            shown = "all" if total == len(rows) else \
                f"the {'most recent' if direction == 'DESC' else 'earliest'} {len(rows)} of {total:,}"
            head = f"Found **{total:,}** {pl(total, one, many)}{where}; showing {shown}:"
            headline = f"{total:,} {pl(total, one, many)}"
            sub = f"{where.strip().capitalize()} · showing {shown}" if where else f"Showing {shown}"
        return Answer(head, cols, table, foot, title="Results", headline=headline, subhead=sub,
                      results=cards, notes=notes, highlights=hl,
                      suggestions=self._suggest(one, many, where, "list", ranked))

    # --- World Series champions (from final scores) -----------------------------------
    def champions(self, q: str, cov: dict) -> Answer:
        ql = q.lower()
        parsed = parse(q, self.cutoff)
        teams, _ = self.entities.find_teams(q, seasons=(parsed.season_from, parsed.season_to))
        rows = self.con.execute(
            "SELECT season, away_id, away, home_id, home, away_score, home_score FROM games "
            "WHERE game_type = 'W' AND league IN ('American League','National League') "
            "AND home_score IS NOT NULL AND away_score != home_score ORDER BY season, date").fetchall()
        if not rows:
            return Answer("I don't have World Series games indexed yet. Run `python -m mlbasker sync`.",
                          kind="needs_index", title="Index not built")
        series: dict[int, dict] = {}
        for r in rows:
            s = series.setdefault(r["season"], {})
            w_id, w_name = (r["away_id"], r["away"]) if r["away_score"] > r["home_score"] else (r["home_id"], r["home"])
            l_id, l_name = (r["home_id"], r["home"]) if w_id == r["away_id"] else (r["away_id"], r["away"])
            for tid, name in ((w_id, w_name), (l_id, l_name)):
                s.setdefault(tid, {"name": name, "wins": 0})["name"] = name
            s[w_id]["wins"] += 1
        decided = {}
        for season, teams_ in series.items():
            ranked_ = sorted(teams_.items(), key=lambda kv: -kv[1]["wins"])
            if len(ranked_) >= 2 and ranked_[0][1]["wins"] > ranked_[1][1]["wins"] and \
                    (ranked_[0][1]["wins"] >= 4 or season < season_of(self.cutoff)):
                (cid, c), (_, r_) = ranked_[0], ranked_[1]
                decided[season] = {"id": cid, "champ": c["name"], "runner": r_["name"],
                                   "score": f"{c['wins']}–{r_['wins']}"}
        first, last = min(series), max(series)
        foot = f"Based on World Series game scores, {first}–{last}. {through(self.cutoff)}"
        a, b = parsed.season_from, parsed.season_to

        if a is not None and a == b:   # "who won the 2019 World Series"
            d = decided.get(a)
            if d:
                return Answer(f"The **{d['champ']}** won the {a} World Series, {d['score']} over the {d['runner']}.",
                              footnote=foot, title="World Series champion", headline=d["champ"],
                              subhead=f"{a} World Series · {d['score']} over {d['runner']}",
                              suggestions=[f"Who won the {a - 1} World Series?", f"Who won the {a + 1} World Series?"
                                           if a + 1 <= season_of(self.cutoff) else "List all World Series winners"])
            if a == season_of(self.cutoff) and a in series:
                return Answer(f"The {a} World Series hasn't been decided yet — it's still under way as of "
                              f"{pretty_date(self.cutoff.isoformat())}.", kind="notice", title="In progress",
                              footnote=foot)
            if a < 1903:
                return Answer("The first modern World Series was in 1903.", kind="notice", title="Out of range",
                              footnote=foot)
            return Answer(f"I have no World Series result for {a} (none was played in {a}, or it isn't indexed).",
                          kind="notice", title="No World Series", footnote=foot)

        if teams:                      # "last time the Cubs won the World Series" / "how many has X won"
            t = teams[0]
            titles = sorted(s for s, d in decided.items() if d["id"] == t.id and (a is None or s >= a)
                            and (b is None or s <= b))
            if not titles:
                return Answer(f"The {t.short} haven't won a World Series in the games I can search "
                              f"({first}–{last}).", footnote=foot, title="World Series titles",
                              headline="0", subhead=f"{t.name} · {first}–{last}")
            mode = parsed.mode
            if mode in ("count",) or re.search(r"how many", ql):
                return Answer(f"The {t.short} have won **{len(titles)}** World Series ({first}–{last}): "
                              f"{', '.join(map(str, titles))}.", footnote=foot, title="World Series titles",
                              headline=str(len(titles)), subhead=f"{t.name} · titles since {first}",
                              notes=[f"Years: {', '.join(map(str, titles))}"],
                              suggestions=[f"Last time the {t.short} won the World Series"])
            yr = titles[0] if mode == "first" else titles[-1]
            d = decided[yr]
            when = "first" if mode == "first" else "last"
            return Answer(f"The {t.short} {when} won the World Series in **{yr}**, beating the {d['runner']} "
                          f"{d['score']}.", footnote=foot, title="World Series champion", headline=str(yr),
                          subhead=f"{d['champ']} · {d['score']} over {d['runner']}",
                          notes=[f"{len(titles)} title{'s' if len(titles) != 1 else ''} in the games I can search."],
                          suggestions=[f"How many World Series have the {t.short} won?"])

        years = sorted((s for s in decided if (a is None or s >= a) and (b is None or s <= b)), reverse=True)
        limit = 40 if parsed.mode == "all" else 10
        table = [[str(y), decided[y]["champ"], decided[y]["runner"], decided[y]["score"]] for y in years[:limit]]
        shown = "all" if len(years) <= limit else f"the {limit} most recent of {len(years)}"
        return Answer(f"World Series champions — showing {shown}:", ["Year", "Champion", "Runner-up", "Series"],
                      table, foot, title="World Series champions", headline="World Series champions",
                      subhead=f"Showing {shown}",
                      suggestions=["Who won the 2019 World Series?", "How many World Series have the Yankees won?"])

    # --- helpers ----------------------------------------------------------------------
    @staticmethod
    def _suggest(one: str, many: str, where: str, mode: str, ranked: bool) -> list[str]:
        if ranked:
            return []
        base = [(f"Last {one}{where}", "last"), (f"First {one}{where}", "first"),
                (f"List all {many}{where}", "list"),
                (f"How many {many}{where}".replace(" that ended", " ended"), "count")]
        return [t for t, m in base if m != mode][:3]

    def _card(self, r, rank: int | None) -> dict:
        meta = []
        if r["inning"] is not None:
            meta.append(f"{str(r['half']).capitalize()} {r['inning']}")
        rnd = _round(r["game_type"])
        if rnd:
            meta.append(rnd)
        if r["league"] and r["league"] not in MLB_LEAGUES:
            meta.append(r["league"])
        return {"rank": rank, "date": pretty_date(r["date"]), "title": self._matchup(r, tag=False),
                "meta": meta, "text": r["detail"] or ""}

    @staticmethod
    def _outside_play_coverage(parsed: Parsed, cov: dict) -> str | None:
        """Explain when the asked-about seasons predate or fall outside the play-by-play index."""
        a, b = parsed.season_from, parsed.season_to
        if a is None and b is None:
            return None
        lo, hi = cov["first_season"], cov["last_season"]
        rng = f"{a}" if a == b else f"{a or 'the start'}–{b or 'now'}"
        if b is not None and b < FIRST_PBP_SEASON:
            return (f"The API has no play-by-play before {FIRST_PBP_SEASON}, so I can't search plays "
                    f"in {rng}. I can still give scores, standings, season stats and leaders for those years.")
        if (b is not None and b < lo) or (a is not None and a > hi):
            return (f"My play-by-play index doesn't cover {rng} yet (it holds {lo}–{hi}). "
                    f"Run `python -m mlbasker sync` to fill it in.")
        return None

    @staticmethod
    def _outside_score_coverage(parsed: Parsed, cov: dict) -> str | None:
        b = parsed.season_to
        if b is not None and b < cov["all_first_season"]:
            return (f"The API has game scores only from {cov['all_first_season']}, so I can't search "
                    f"games before then. Standings, season stats and leaders go back to 1876.")
        return None

    @staticmethod
    def _footnote(cov: dict, score_level: bool) -> str:
        if score_level:
            return (f"Searched {cov['all_games']:,} games ({cov['all_first_season']}–{cov['all_last_season']}) "
                    f"using final scores; innings are only recorded from {FIRST_PBP_SEASON}.")
        if cov["first_season"] <= FIRST_PBP_SEASON:
            return (f"Searched {cov['games']:,} games with play-by-play "
                    f"({cov['first_season']}–{cov['last_season']}; the API has none earlier).")
        return (f"Searched {cov['games']:,} indexed games ({cov['first_season']}–{cov['last_season']}); "
                f"the index isn't fully built yet.")

    def _fts_ready(self) -> bool:
        try:
            self.con.execute("SELECT 1 FROM plays_fts LIMIT 1").fetchone()
            return self.con.execute("SELECT COUNT(*) FROM plays_fts WHERE plays_fts MATCH 'the'").fetchone()[0] > 0
        except sqlite3.DatabaseError:
            return False

    @staticmethod
    def _matchup(r, tag: bool = True) -> str:
        extra = f" [{r['league']}]" if tag and r["league"] and r["league"] not in MLB_LEAGUES else ""
        return f"{r['away']} {r['away_score']} @ {r['home']} {r['home_score']}{extra}"

    @staticmethod
    def _inning(r, pre: str = "", post: str = "") -> str:
        if r["inning"] is None:
            return ""
        return f"{pre}{r['half']} {r['inning']}{post}"

    @staticmethod
    def _where_text(scope: list[str], parsed: Parsed, home_only: bool = False, result: str | None = None) -> str:
        bits = []
        if scope:
            verb = {"win": "won by ", "loss": "lost by "}.get(result or "", "by " if home_only else "involving ")
            bits.append(verb + " / ".join(scope))
        if parsed.window_label:
            bits.append(in_phrase(parsed.window_label) if parsed.window_label.split()[0].lower() in MONTHS
                        else f"over the {parsed.window_label}")
        elif parsed.season_from is not None or parsed.season_to is not None:
            a, b = parsed.season_from, parsed.season_to
            bits.append(f"in {a}" if a == b else f"from {a or 'the start'} to {b or 'now'}")
        if parsed.game_types:
            bits.append("in the postseason" if len(parsed.game_types) > 1 else
                        {"W": "in the World Series", "L": "in a league championship series",
                         "D": "in a division series", "F": "in a wild-card game", "R": "in the regular season"}[parsed.game_types[0]])
        return (" " + " ".join(bits)) if bits else ""
