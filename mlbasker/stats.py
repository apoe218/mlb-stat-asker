"""Live lookups against the Stats API: player stats, leaders, standings, game results.

Everything is capped at the cutoff date (yesterday, US Eastern): requests use explicit
start/end dates, so a game played today never leaks into an answer.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .answer import Answer, pretty_date, through
from .client import MlbClient, TTL_LONG, TTL_SHORT
from .entities import Entities, Player, Team
from .timeparse import Parsed, parse, season_of

# phrase → (stat field, label, group or None=ambiguous, leaderboard sortStat, ascending, rate)
STATS = [
    (r"home ?runs?|homers?|\bhrs?\b", "homeRuns", "home runs", "hitting", "homeRuns", False, False),
    (r"batting average|batting avg|\bavg\b|average", "avg", "batting average", "hitting", "avg", False, True),
    (r"\brbis?\b|runs batted in", "rbi", "RBI", "hitting", "rbi", False, False),
    (r"on[- ]base percentage|\bobp\b", "obp", "OBP", "hitting", "obp", False, True),
    (r"slugging|\bslg\b", "slg", "SLG", "hitting", "slg", False, True),
    (r"\bops\b", "ops", "OPS", "hitting", "ops", False, True),
    (r"stolen bases?|steals|\bsb\b", "stolenBases", "stolen bases", "hitting", "stolenBases", False, False),
    (r"doubles", "doubles", "doubles", "hitting", "doubles", False, False),
    (r"triples", "triples", "triples", "hitting", "triples", False, False),
    (r"\bhits\b", "hits", "hits", "hitting", "hits", False, False),
    (r"\bruns scored\b|\bruns\b", "runs", "runs", "hitting", "runs", False, False),
    (r"\bera\b|earned run average", "era", "ERA", "pitching", "earnedRunAverage", True, True),
    (r"\bwhip\b", "whip", "WHIP", "pitching", "walksAndHitsPerInningPitched", True, True),
    (r"\bwins\b|\bwon\b|win-loss|\brecord\b.*pitch", "wins", "wins", "pitching", "wins", False, False),
    (r"\bsaves?\b", "saves", "saves", "pitching", "saves", False, False),
    (r"innings pitched|\binnings\b", "inningsPitched", "innings pitched", "pitching", "inningsPitched", False, False),
    (r"strike ?outs?|\bks\b|\bso\b", "strikeOuts", "strikeouts", None, "strikeOuts", False, False),
    (r"walks|bases on balls|\bbb\b", "baseOnBalls", "walks", None, "baseOnBalls", False, False),
]

HIT_LINE = [("gamesPlayed", "G"), ("avg", "AVG"), ("homeRuns", "HR"), ("rbi", "RBI"),
            ("ops", "OPS"), ("stolenBases", "SB")]
PIT_LINE = [("gamesPlayed", "G"), ("wins", "W"), ("losses", "L"), ("era", "ERA"),
            ("inningsPitched", "IP"), ("strikeOuts", "SO"), ("whip", "WHIP")]


def find_stat(q: str):
    for row in STATS:
        if re.search(row[0], q):
            return row
    return None


class Stats:
    def __init__(self, client: MlbClient, entities: Entities, cutoff: date):
        self.client, self.entities, self.cutoff = client, entities, cutoff

    # --- helpers ----------------------------------------------------------------------
    def season_bounds(self, season: int) -> tuple[date, date, date]:
        """(start, regular-season end, postseason end) for a season."""
        s = self.client.get("seasons", {"sportId": 1, "season": season}, ttl=TTL_LONG)["seasons"][0]
        start = date.fromisoformat(s["regularSeasonStartDate"])
        reg_end = date.fromisoformat(s["regularSeasonEndDate"])
        post_end = date.fromisoformat(s.get("postSeasonEndDate") or s["regularSeasonEndDate"])
        return start, reg_end, post_end

    def window(self, parsed: Parsed, career: bool = False) -> tuple[date, date, str]:
        """(start, end, human label) — the end never passes the cutoff."""
        if career:
            return date(1876, 1, 1), self.cutoff, "career"
        cur = season_of(self.cutoff)
        a = parsed.season_from if parsed.season_from is not None else cur
        b = parsed.season_to if parsed.season_to is not None else a
        start, _, _ = self.season_bounds(a)
        _, _, end = self.season_bounds(b)
        label = str(a) if a == b else f"{a}–{b}"
        return start - timedelta(days=45), min(end, self.cutoff), f"{label} season" + ("s" if a != b else "")

    def _ttl(self, end: date) -> int:
        return TTL_SHORT if end >= self.cutoff - timedelta(days=2) else TTL_LONG

    def _game_types(self, parsed: Parsed) -> str:
        return ",".join(parsed.game_types) if parsed.game_types else "R"

    # --- player stats -----------------------------------------------------------------
    def _split(self, data: dict) -> dict | None:
        splits = (data.get("stats") or [{}])[0].get("splits", [])
        return splits[0] if splits else None

    def player_stat(self, q: str, player: Player, parsed: Parsed) -> Answer:
        ql = q.lower()
        career = bool(re.search(r"career|lifetime|all[- ]time|ever", ql)) and parsed.season_from is None
        start, end, label = self.window(parsed, career)
        stat = find_stat(ql)
        pitching_words = bool(re.search(r"pitch|\bera\b|\bwhip\b|\bsaves?\b|innings|\bwins\b|started", ql))
        group = stat[3] if stat and stat[3] else None
        if group is None:
            pos = player.position or ""
            if pos == "P" or (pos == "TWP" and pitching_words):
                group = "pitching"
            elif pitching_words and pos in ("", "TWP"):
                group = "pitching"
            else:
                group = "hitting"
        if stat is None:
            group = "pitching" if (player.position == "P" or pitching_words) else "hitting"

        params = {"stats": "byDateRange", "group": group, "startDate": start.isoformat(),
                  "endDate": end.isoformat(), "gameType": self._game_types(parsed)}
        if not career:
            params["season"] = parsed.season_from if parsed.season_from is not None else season_of(self.cutoff)
        data = self.client.get(f"people/{player.id}/stats", params, ttl=self._ttl(end))
        split = self._split(data)
        gt = "" if self._game_types(parsed) == "R" else " (postseason)"
        if not split:
            return Answer(f"I don't have {group} stats for {player.name} in the {label}{gt}.",
                          footnote=through(self.cutoff))
        st = split["stat"]
        line = HIT_LINE if group == "hitting" else PIT_LINE
        cols = [h for _, h in line]
        row = [str(st.get(k, "—")) for k, _ in line]
        team = (split.get("team") or {}).get("name")
        where = f" with the {team}" if team and not career else ""
        if career:
            gt = " (regular season)" if self._game_types(parsed) == "R" else gt

        if stat and stat[1] in st:
            val = st[stat[1]]
            if stat[1] == "wins":
                val = f"{st['wins']}–{st['losses']}"
                text = f"{player.name} is **{val}** in the {label}{gt}{where}."
            else:
                text = f"{player.name} has **{val} {stat[2]}** in the {label}{gt}{where}."
                if stat[1] in ("avg", "obp", "slg", "ops", "era", "whip"):
                    text = f"{player.name}'s {stat[2]} is **{val}** in the {label}{gt}{where}."
                if career:
                    text = f"{player.name}'s career total{gt}: **{val} {stat[2]}**."
        else:
            text = f"Here's {player.name}'s {group} line for the {label}{gt}{where}:"
        return Answer(text, cols, [row], through(self.cutoff))

    # --- leaders ----------------------------------------------------------------------
    def leaders(self, q: str, parsed: Parsed, teams: list[Team]) -> Answer:
        ql = q.lower()
        stat = find_stat(ql)
        if stat is None:
            return Answer("Which stat? For example: “who leads MLB in home runs” or "
                          "“top 5 in ERA this season”.", footnote=through(self.cutoff))
        _, field, label, group, sort, asc, rate = stat
        if group is None:
            group = "pitching" if re.search(r"pitch", ql) else "hitting"
        n = 10 if re.search(r"top (10|ten)|leaders", ql) else 5
        m = re.search(r"top (\d+)", ql)
        if m:
            n = max(1, min(int(m.group(1)), 25))
        if re.search(r"\bwho (leads|has the most|is leading|has the best)|\bleader\b", ql) and not m and \
                not re.search(r"leaders|top", ql):
            n = 1 if not rate else 1
        start, end, wlabel = self.window(parsed)
        params = {"stats": "byDateRange", "group": group, "playerPool": "qualified" if rate else "all",
                  "sportId": 1, "startDate": start.isoformat(), "endDate": end.isoformat(),
                  "season": parsed.season_from if parsed.season_from is not None else season_of(self.cutoff),
                  "sortStat": sort, "order": "asc" if asc else "desc", "limit": n * 3 if teams else n,
                  "gameType": self._game_types(parsed)}
        if teams:
            params["teamId"] = ",".join(str(t.id) for t in teams)
        data = self.client.get("stats", params, ttl=self._ttl(end))
        splits = (data.get("stats") or [{}])[0].get("splits", [])[:n]
        if not splits:
            return Answer(f"I couldn't find leaders for {label} in the {wlabel}.", footnote=through(self.cutoff))
        rows = [[i + 1, s["player"]["fullName"], (s.get("team") or {}).get("name", ""), str(s["stat"].get(field, "—"))]
                for i, s in enumerate(splits)]
        top = rows[0]
        scope = f" for the {teams[0].short}" if teams else ""
        if n == 1:
            return Answer(f"**{top[1]}** ({top[2]}) leads{scope} in {label} in the {wlabel} with **{top[3]}**.",
                          ["#", "Player", "Team", label[:1].upper() + label[1:]], rows, through(self.cutoff) +
                          (" Rate stats use qualified players only." if rate else ""))
        return Answer(f"{label[:1].upper() + label[1:]} leaders{scope}, {wlabel}:", ["#", "Player", "Team", label[:1].upper() + label[1:]], rows,
                      through(self.cutoff) + (" Rate stats use qualified players only." if rate else ""))

    # --- standings --------------------------------------------------------------------
    def standings(self, q: str, parsed: Parsed, teams: list[Team]) -> Answer:
        season = parsed.season_from if parsed.season_from is not None else season_of(self.cutoff)
        _, reg_end, _ = self.season_bounds(season)
        as_of = min(self.cutoff, reg_end)
        data = self.client.get("standings", {"leagueId": "103,104", "season": season, "date": as_of.isoformat(),
                                             "standingsTypes": "regularSeason"}, ttl=self._ttl(as_of))
        if not data.get("records"):
            data = self.client.get("standings", {"leagueId": "103,104", "season": season}, ttl=self._ttl(as_of))
        divs = {101: "NL East"}  # placeholder; names come from the API below
        recs = data.get("records", [])
        final = as_of >= reg_end
        suffix = "final regular-season standings" if final else f"standings through {pretty_date(as_of.isoformat())}"
        if teams:
            t = teams[0]
            for r in recs:
                for tr in r["teamRecords"]:
                    if tr["team"]["id"] == t.id:
                        name = self._division_name(r)
                        gb = tr.get("gamesBack", "-")
                        rank = tr.get("divisionRank", "?")
                        extra = "" if gb in ("-", "0.0") else f", {gb} GB"
                        text = (f"The {t.short} {'finished' if final else 'are'} **{tr['wins']}–{tr['losses']}** "
                                f"({tr.get('winningPercentage', '')}), #{rank} in the {name}{extra} — {season} {suffix}.")
                        l10 = next((x for x in tr.get("records", {}).get("splitRecords", []) if x["type"] == "lastTen"), None)
                        if l10:
                            text += f" Last 10: {l10['wins']}–{l10['losses']}."
                        return Answer(text, footnote=through(self.cutoff))
            return Answer(f"I couldn't find {t.name} in the {season} standings.", footnote=through(self.cutoff))
        rows = []
        for r in recs:
            name = self._division_name(r)
            for tr in r["teamRecords"]:
                rows.append([name, tr["team"]["name"], tr["wins"], tr["losses"],
                             tr.get("winningPercentage", ""), tr.get("gamesBack", "-")])
        return Answer(f"{season} {suffix}:", ["Division", "Team", "W", "L", "PCT", "GB"], rows, through(self.cutoff))

    def _division_name(self, record: dict) -> str:
        div = record.get("division", {})
        if "name" in div:
            return div["name"]
        d = self.client.get(f"divisions/{div.get('id')}", ttl=TTL_LONG)["divisions"][0]
        return d["name"]

    # --- game results -----------------------------------------------------------------
    def _target_date(self, q: str) -> date | None:
        ql = q.lower()
        if re.search(r"yesterday|last night", ql):
            return self.cutoff
        m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", q)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        months = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d\d))?", ql)
        if m:
            mon, day = months[m[1]], int(m[2])
            year = int(m[3]) if m[3] else self.cutoff.year
            d = date(year, mon, day)
            if d > self.cutoff and not m[3]:
                d = date(year - 1, mon, day)
            return d
        m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", ql)
        if m:
            year = int(m[3]) if m[3] else self.cutoff.year
            year += 2000 if year < 100 else 0
            d = date(year, int(m[1]), int(m[2]))
            if d > self.cutoff and not m[3]:
                d = date(year - 1, int(m[1]), int(m[2]))
            return d
        return None

    def results(self, q: str, teams: list[Team]) -> Answer:
        ql = q.lower()
        if re.search(r"\b(tonight|tomorrow|today|next game|upcoming|will .* play)\b", ql):
            return Answer(f"I only use completed games through {pretty_date(self.cutoff.isoformat())} — "
                          f"nothing from today or the future.", footnote=through(self.cutoff))
        d = self._target_date(q)
        if d and d > self.cutoff:
            return Answer(f"That date is past my cutoff. I only have games through "
                          f"{pretty_date(self.cutoff.isoformat())}.", footnote=through(self.cutoff))
        params = {"sportId": 1, "hydrate": "decisions,linescore,team"}
        if teams:
            params["teamId"] = ",".join(str(t.id) for t in teams[:2])
        if d:
            params["date"] = d.isoformat()
        else:                                   # "last game"
            params["startDate"] = (self.cutoff - timedelta(days=30)).isoformat()
            params["endDate"] = self.cutoff.isoformat()
        data = self.client.get("schedule", params, ttl=TTL_SHORT)
        games = [g for dt in data.get("dates", []) for g in dt["games"]
                 if g["status"]["abstractGameState"] == "Final"]
        if not d and games:
            last_day = max(g["officialDate"] for g in games)
            games = [g for g in games if g["officialDate"] == last_day]
        if not games:
            who = f" for {', '.join(t.name for t in teams)}" if teams else ""
            when = f" on {pretty_date(d.isoformat())}" if d else " in the last 30 days"
            return Answer(f"I didn't find a completed game{who}{when}.", footnote=through(self.cutoff))
        rows, lines = [], []
        for g in games:
            a, h = g["teams"]["away"], g["teams"]["home"]
            dec = g.get("decisions", {})
            note = "; ".join(f"{k}: {dec[v]['fullName']}" for k, v in
                             (("W", "winner"), ("L", "loser"), ("SV", "save")) if v in dec)
            rows.append([pretty_date(g["officialDate"]), f"{a['team']['name']} {a.get('score', '')} @ "
                         f"{h['team']['name']} {h.get('score', '')}", self._round(g), note])
            winner = a if a.get("isWinner") else h
            loser = h if winner is a else a
            lines.append(f"{winner['team']['teamName']} beat the {loser['team']['teamName']} "
                         f"{winner.get('score')}–{loser.get('score')}")
        if len(games) == 1:
            g = games[0]
            away, home = g["teams"]["away"], g["teams"]["home"]
            win, lose = (away, home) if away.get("isWinner") else (home, away)
            score = f"{win.get('score')}–{lose.get('score')}"
            subject = next((t for t in teams if t.id in (win["team"]["id"], lose["team"]["id"])), None)
            if subject and subject.id == lose["team"]["id"]:
                text = f"The {lose['team']['teamName']} lost to the {win['team']['teamName']} {score}"
            else:
                text = f"The {win['team']['teamName']} beat the {lose['team']['teamName']} {score}"
            text += f" on {rows[0][0]} ({rows[0][2]})."
            if rows[0][3]:
                text += f" {rows[0][3]}."
            return Answer(text, footnote=through(self.cutoff))
        return Answer(f"{len(games)} games on {rows[0][0]}:", ["Date", "Game", "Round", "Decisions"], rows,
                      through(self.cutoff))

    @staticmethod
    def _round(g: dict) -> str:
        return {"R": "Regular season", "F": "Wild Card", "D": "Division Series", "L": "LCS",
                "W": "World Series"}.get(g.get("gameType", "R"), g.get("gameType", ""))
