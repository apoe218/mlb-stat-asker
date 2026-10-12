"""Live lookups against the Stats API: player stats, comparisons, leaders, standings, game results.

Everything is capped at the cutoff date (yesterday, US Eastern): requests use explicit
start/end dates, so a game played today never leaks into an answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .answer import Answer, pretty_date, through
from .client import MlbClient, TTL_LONG, TTL_SHORT
from .entities import Entities, Player, Team
from .timeparse import Parsed, in_phrase, season_of

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

# richer sets for the stat tiles and for side-by-side comparisons: (field, label, higher_is_better)
HIT_TILES = [("avg", "AVG", True), ("obp", "OBP", True), ("slg", "SLG", True), ("ops", "OPS", True),
             ("homeRuns", "HR", True), ("rbi", "RBI", True), ("hits", "H", True), ("runs", "R", True),
             ("stolenBases", "SB", True), ("gamesPlayed", "G", None)]
PIT_TILES = [("era", "ERA", False), ("whip", "WHIP", False), ("wins", "W", True), ("losses", "L", False),
             ("saves", "SV", True), ("inningsPitched", "IP", True), ("strikeOuts", "SO", True),
             ("baseOnBalls", "BB", False), ("gamesStarted", "GS", None), ("gamesPlayed", "G", None)]

LEGACY_LEAGUES = {103: "American League", 104: "National League"}
PBP_START = 1950

# things people ask that this app can't split a stat by; say so instead of silently ignoring them
SPLIT_RE = re.compile(
    r"\b(against|vs\.?|versus)\s+(lefties|righties|left[- ]handed|right[- ]handed|lefty|righty|the\s+\w+)|"
    r"\blefties\b|\brighties\b|\bat home\b|\bhome games\b|\bon the road\b|\broad games\b|\baway games\b|"
    r"\bday games\b|\bnight games\b|\bwith runners\b|\brisp\b|\bbases loaded\b|\btwo outs\b|\bclutch\b|"
    r"\bwith two strikes\b|\bin the (first|second|third|fourth|fifth|sixth|seventh|eighth|ninth) inning\b")


def era_note(first_season: int | None) -> str:
    """A caveat for seasons whose records are thinner than the modern game's."""
    if first_season is not None and first_season < PBP_START:
        return (f"Before {PBP_START} there is no play-by-play, and some stats weren't officially "
                f"tracked, so totals can be missing or incomplete.")
    return ""


def find_stat(q: str):
    for row in STATS:
        if re.search(row[0], q):
            return row
    return None


def _singular(label: str) -> str:
    if label.isupper() or label in ("batting average", "innings pitched"):
        return label
    return label[:-1] if label.endswith("s") else label


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


@dataclass
class Line:
    player: Player
    group: str
    stat: dict
    team: str | None
    label: str          # "2026 season", "career", "April 2026"
    gt: str             # " (postseason)" or ""
    past: bool          # a finished period: read it in past tense
    career: bool


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
        if parsed.date_from:
            return parsed.date_from, min(parsed.date_to or self.cutoff, self.cutoff), parsed.window_label
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
        gt = parsed.game_types
        if not gt:
            return "R"
        return "P" if len(gt) > 1 else gt[0]     # P = every postseason round combined

    def _split(self, data: dict) -> dict | None:
        """One split per team stint, plus a combined 'All' split (sport id 0) — prefer the combined."""
        splits = (data.get("stats") or [{}])[0].get("splits", [])
        total = next((s for s in splits if (s.get("sport") or {}).get("id") == 0), None)
        return total or (splits[0] if splits else None)

    def _notes(self, ql: str, first_season: int | None) -> list[str]:
        notes = []
        m = SPLIT_RE.search(ql)
        if m:
            notes.append(f"I can't split stats by “{m.group(0)}” yet, so this is the overall number.")
        e = era_note(first_season)
        if e:
            notes.append(e)
        return notes

    # --- player lines -----------------------------------------------------------------
    def _group(self, player: Player, stat, ql: str) -> str:
        pitching_words = bool(re.search(r"pitch|\bera\b|\bwhip\b|\bsaves?\b|innings|\bwins\b|started", ql))
        pos = self.entities.position(player) or ""
        if stat and stat[3]:
            return stat[3]
        if pos == "P" or (pitching_words and pos in ("", "TWP", "P")):
            return "pitching"
        return "hitting"

    def _line(self, player: Player, parsed: Parsed, ql: str, group: str, career: bool) -> Line | None:
        start, end, label = self.window(parsed, career)
        cur = season_of(self.cutoff)
        season = parsed.season_from if parsed.season_from is not None else cur
        windowed = bool(parsed.date_from)
        single_past = (not career and not windowed
                       and season == (parsed.season_to if parsed.season_to is not None else season)
                       and season < cur)
        retired = False
        if career:
            prof = self.entities.profile(player)
            retired = prof["active"] is False or (prof["debut_year"] or 9999) < 1901
        if single_past or retired:
            # a finished season (or a retired player's career) can't change, and the by-date endpoint
            # has nothing before 1901; season/career totals are exact and cover all of history
            params = {"stats": "career" if career else "season", "group": group,
                      "gameType": self._game_types(parsed)}
            if not career:
                params["season"] = season
            ttl = TTL_LONG
        else:
            params = {"stats": "byDateRange", "group": group, "startDate": start.isoformat(),
                      "endDate": end.isoformat(), "gameType": self._game_types(parsed)}
            if not career:
                params["season"] = season_of(start) if windowed else season
            ttl = self._ttl(end)
        data = self.client.get(f"people/{player.id}/stats", params, ttl=ttl)
        split = self._split(data)
        if not split:
            return None
        gt = "" if self._game_types(parsed) == "R" else " (postseason)"
        past = single_past or (windowed and end < self.cutoff - timedelta(days=1)) or retired
        return Line(player, group, split["stat"], (split.get("team") or {}).get("name"), label, gt,
                    bool(past), career)

    def _is_career(self, ql: str, parsed: Parsed) -> bool:
        return bool(re.search(r"career|lifetime|all[- ]time|ever", ql)) and parsed.season_from is None \
            and not parsed.date_from

    # --- single player ----------------------------------------------------------------
    def player_stat(self, q: str, player: Player, parsed: Parsed) -> Answer:
        ql = q.lower()
        career = self._is_career(ql, parsed)
        stat = find_stat(ql)
        group = self._group(player, stat, ql)
        line = self._line(player, parsed, ql, group, career)
        _, _, label = self.window(parsed, career)
        gt = "" if self._game_types(parsed) == "R" else " (postseason)"
        if line is None:
            return Answer(f"I don't see any {group} stats for {player.name} {in_phrase(label)}{gt} — "
                          f"{player.name} may not have appeared in it.", kind="notice", title="No stats found",
                          footnote=through(self.cutoff),
                          suggestions=[f"{player.name} career stats", f"{player.name} stats last season"])
        st = line.stat
        has, is_ = ("had", "was") if line.past else ("has", "is")
        where = f" with the {line.team}" if line.team and not line.career else ""
        scope = "career" if career else label
        scope_txt = "career" if career else in_phrase(label).removeprefix("in ")

        tiles_def = HIT_TILES if group == "hitting" else PIT_TILES
        tiles = []
        for key, lab, _ in tiles_def:
            if key in st:
                tiles.append({"label": lab, "value": str(st[key]),
                              "emphasis": bool(stat and stat[1] == key)})
        if group == "pitching" and "wins" in st and "losses" in st:
            tiles.insert(0, {"label": "W–L", "value": f"{st['wins']}–{st['losses']}",
                             "emphasis": bool(stat and stat[1] == "wins")})
            tiles = [t for t in tiles if t["label"] not in ("W", "L")]

        if stat and stat[1] in st:
            val = st[stat[1]]
            if stat[1] == "wins":
                val = f"{st['wins']}–{st['losses']}"
                text = f"{player.name} {is_} **{val}** in {scope_txt}{gt}{where}."
                headline = f"{val} record"
            else:
                text = f"{player.name} {has} **{val} {stat[2]}** in {scope_txt}{gt}{where}."
                headline = f"{val} {stat[2]}"
                if stat[1] in ("avg", "obp", "slg", "ops", "era", "whip"):
                    text = f"{player.name}'s {stat[2]} {is_} **{val}** in {scope_txt}{gt}{where}."
                if career:
                    text = f"{player.name}'s career total{gt}: **{val} {stat[2]}**."
        else:
            text = f"Here's {player.name}'s {group} line for {scope_txt}{gt}{where}:"
            headline = f"{player.name}"

        line_def = HIT_LINE if group == "hitting" else PIT_LINE
        cols = [h for _, h in line_def]
        row = [str(st.get(k, "—")) for k, _ in line_def]
        scope_label = "Career" if career else label[:1].upper() + label[1:]
        parts = ([player.name] if stat else []) + [scope_label] + (["Postseason"] if gt else []) \
            + ([line.team] if line.team and not career else [])
        sub = " · ".join(parts)
        first = 1876 if career else (parsed.season_from if parsed.season_from is not None else season_of(self.cutoff))
        title = "Career stats" if career else "Player stats"
        sug = []
        if stat:
            sug = [f"Who leads MLB in {stat[2]}?"]
            sug.append(f"{player.name} career {stat[2]}" if not career else f"{player.name} {stat[2]} this season")
            if not career:
                sug.append(f"{player.name} {stat[2]} in {season_of(self.cutoff) - 1}"
                           if (parsed.season_from in (None, season_of(self.cutoff))) else
                           f"{player.name} {stat[2]} this season")
        else:
            sug = [f"{player.name} career stats", f"{player.name} home runs", f"{player.name} stats last season"]
        return Answer(text, cols, [row], through(self.cutoff), title=title, headline=headline, subhead=sub,
                      tiles=tiles, notes=self._notes(ql, first), suggestions=sug[:4])

    # --- comparison -------------------------------------------------------------------
    def compare(self, q: str, players: list[Player], parsed: Parsed) -> Answer:
        ql = q.lower()
        a, b = players[:2]
        career = self._is_career(ql, parsed)
        stat = find_stat(ql)
        groups = {self._group(p, stat, ql) for p in (a, b)}
        group = groups.pop() if len(groups) == 1 else "hitting"
        la, lb = (self._line(p, parsed, ql, group, career) for p in (a, b))
        _, _, label = self.window(parsed, career)
        if la is None or lb is None:
            who = a.name if la is None else b.name
            return Answer(f"I couldn't find {group} stats for {who} {in_phrase(label)}.", kind="notice",
                          title="No stats found", footnote=through(self.cutoff))
        metrics = HIT_TILES if group == "hitting" else PIT_TILES
        rows, best = [], []
        for key, lab, higher in metrics:
            va, vb = la.stat.get(key), lb.stat.get(key)
            if va is None and vb is None:
                continue
            rows.append([lab, str(va if va is not None else "—"), str(vb if vb is not None else "—")])
            na, nb = _num(va), _num(vb)
            if higher is not None and na is not None and nb is not None and na != nb:
                win = 1 if (na > nb) == higher else 2
                best.append([len(rows) - 1, win])
        last = lambda p: p.name.split()[-1]
        if stat and stat[1] in la.stat and stat[1] in lb.stat:
            va, vb = la.stat[stat[1]], lb.stat[stat[1]]
            headline = f"{last(a)} {va} · {last(b)} {vb}"
            sub = f"{stat[2].capitalize() if stat[2].islower() else stat[2]} · {label}{la.gt}"
            na, nb = _num(va), _num(vb)
            lower_is_better = bool(stat[5])
            if na is not None and nb is not None and na != nb:
                lead = a if ((na > nb) != lower_is_better) else b
                text = f"{lead.name} leads in {stat[2]}: {last(a)} {va}, {last(b)} {vb} ({label}{la.gt})."
            else:
                text = f"{a.name} and {b.name} are even in {stat[2]}: {va} each ({label}{la.gt})."
        else:
            headline = f"{last(a)} vs {last(b)}"
            sub = f"{group.capitalize()} · {label}{la.gt}"
            text = f"{a.name} vs {b.name} — {group} stats, {label}{la.gt}."
        notes = self._notes(ql, None if career else (parsed.season_from or season_of(self.cutoff)))
        if len(players) > 2:
            notes.append(f"Comparing the first two players I found ({a.name} and {b.name}).")
        return Answer(text, ["", a.name, b.name], rows, through(self.cutoff), kind="compare", title="Comparison",
                      headline=headline, subhead=sub, best_cells=best, notes=notes,
                      suggestions=[f"{a.name} career stats", f"{b.name} career stats"])

    # --- leaders ----------------------------------------------------------------------
    def leaders(self, q: str, parsed: Parsed, teams: list[Team]) -> Answer:
        ql = q.lower()
        stat = find_stat(ql)
        if stat is None:
            return Answer("Tell me which stat, e.g. “who leads MLB in home runs” or “top 5 in ERA this season”.",
                          kind="notice", title="Which stat?", footnote=through(self.cutoff),
                          suggestions=["Who leads MLB in home runs?", "Top 5 in ERA this season",
                                       "Who leads the AL in RBI?"])
        _, field, label, group, sort, asc, rate = stat
        if group is None:
            group = "pitching" if re.search(r"pitch", ql) else "hitting"
        league = 103 if re.search(r"\b(al|american league)\b", ql) else 104 if re.search(r"\b(nl|national league)\b", ql) else None
        # direction: "worst"/"highest ERA" flips a rate stat; "fewest"/"lowest" flips a counting stat
        flip = bool(re.search(r"\bworst\b|\bhighest\b", ql)) if asc else bool(re.search(r"\bfewest\b|\blowest\b|\bleast\b|\bworst\b", ql))
        ascending = (not asc) if flip else asc
        n = 10 if re.search(r"top (10|ten)|leaders", ql) else 5
        m = re.search(r"top (\d+)", ql)
        if m:
            n = max(1, min(int(m.group(1)), 25))
        if re.search(r"\bwho (leads|led|has the most|had the most|is leading|was leading|has the best|had the best)"
                     r"|\bwho'?s leading|\b(the )?leader\b|\bwho has the (worst|highest|fewest|lowest)", ql) \
                and not m and not re.search(r"leaders|top", ql):
            n = 1
        start, end, wlabel = self.window(parsed)
        cur = season_of(self.cutoff)
        season = parsed.season_from if parsed.season_from is not None else cur
        windowed = bool(parsed.date_from)
        finished = (not windowed) and season < cur and (parsed.season_to in (None, season))
        pool = "qualified" if (rate or flip) else "all"
        params = {"stats": "season" if finished else "byDateRange", "group": group, "playerPool": pool,
                  "sportId": 1, "season": season_of(start) if windowed else season,
                  "sortStat": sort, "order": "asc" if ascending else "desc", "limit": n * 3 if teams else n,
                  "gameType": self._game_types(parsed)}
        if not finished:
            params.update({"startDate": start.isoformat(), "endDate": end.isoformat()})
        if teams:
            params["teamId"] = ",".join(str(t.id) for t in teams)
        if league:
            params["leagueIds"] = league
        data = self.client.get("stats", params, ttl=TTL_LONG if finished else self._ttl(end))
        splits = (data.get("stats") or [{}])[0].get("splits", [])[:n]
        if not splits:
            return Answer(f"I couldn't find leaders for {label} {in_phrase(wlabel)}.", kind="notice",
                          title="No results", footnote=through(self.cutoff))
        colname = label[:1].upper() + label[1:]
        rows = [[i + 1, s["player"]["fullName"], (s.get("team") or {}).get("name", ""),
                 str(s["stat"].get(field, "—"))] for i, s in enumerate(splits)]
        notes = self._notes(ql, season)
        if rate or flip:
            notes.insert(0, "Rate stats use qualified players only." if rate else
                         "Counting stats in reverse order use qualified players only.")
        scope = (f" for the {teams[0].short}" if teams else
                 " in the AL" if league == 103 else " in the NL" if league == 104 else "")
        past = (season < cur) and not windowed
        top = rows[0]
        verb = "led" if past else "leads"
        word = "lowest" if (ascending and not asc) else "highest" if (not ascending and asc) else ""
        if n == 1:
            sup = {"": "", "lowest": "the lowest ", "highest": "the highest "}[word]
            text = (f"**{top[1]}** ({top[2]}) {'had' if past else 'has'} {sup}{label}{scope} {in_phrase(wlabel)} with **{top[3]}**."
                    if word else f"**{top[1]}** ({top[2]}) {verb}{scope} in {label} {in_phrase(wlabel)} with **{top[3]}**.")
            return Answer(text, ["#", "Player", "Team", colname], rows, through(self.cutoff), title="League leader",
                          headline=top[1], subhead=f"{top[3]} {label} · {top[2]} · {wlabel}", notes=notes,
                          suggestions=[f"Top 10 in {label}", f"Who led MLB in {label} in {season - 1}?",
                                       f"{label[:1].upper() + label[1:]} leaders in the AL"])
        ttl = f"{('Lowest ' if word == 'lowest' else 'Highest ') if word else ''}{_singular(label)}"
        ttl = ttl[:1].upper() + ttl[1:] + " leaders" if not word else ttl + " leaders"
        return Answer(f"{ttl}{scope}, {wlabel}:", ["#", "Player", "Team", colname], rows, through(self.cutoff),
                      title="Leaders", headline=f"{ttl}{scope}", subhead=wlabel[:1].upper() + wlabel[1:],
                      notes=notes, suggestions=[f"Who {'led' if past else 'leads'} MLB in {label}?",
                                                f"Top 5 {_singular(label)} leaders in the NL" if league != 104
                                                else f"Top 5 {_singular(label)} leaders in the AL",
                                                f"{_singular(label)[:1].upper() + _singular(label)[1:]} leaders in {season - 1}"])

    # --- standings --------------------------------------------------------------------
    def standings(self, q: str, parsed: Parsed, teams: list[Team], wins_focus: bool = False) -> Answer:
        season = parsed.season_from if parsed.season_from is not None else season_of(self.cutoff)
        _, reg_end, _ = self.season_bounds(season)
        as_of = min(self.cutoff, reg_end)
        data = self.client.get("standings", {"leagueId": "103,104", "season": season, "date": as_of.isoformat(),
                                             "standingsTypes": "regularSeason"}, ttl=self._ttl(as_of))
        if not data.get("records"):
            data = self.client.get("standings", {"leagueId": "103,104", "season": season}, ttl=self._ttl(as_of))
        recs = data.get("records", [])
        final = as_of >= reg_end
        suffix = "final regular-season standings" if final else f"standings through {pretty_date(as_of.isoformat())}"
        notes = [era_note(season)] if era_note(season) else []
        if teams:
            t = teams[0]
            for r in recs:
                for tr in r["teamRecords"]:
                    if tr["team"]["id"] != t.id:
                        continue
                    name = self._division_name(r)
                    gb = tr.get("gamesBack", "-")
                    rank = tr.get("divisionRank") or tr.get("leagueRank", "?")
                    extra = "" if gb in ("-", "0.0") else f", {gb} GB"
                    if wins_focus:
                        text = (f"The {t.short} {'won' if final else 'have won'} **{tr['wins']}** games in {season} "
                                f"({tr['wins']}–{tr['losses']}).")
                    else:
                        text = (f"The {t.short} {'finished' if final else 'are'} **{tr['wins']}–{tr['losses']}** "
                                f"({tr.get('winningPercentage', '')}), #{rank} in the {name}{extra} — {season} {suffix}.")
                    splits = {x["type"]: x for x in tr.get("records", {}).get("splitRecords", [])}
                    l10 = splits.get("lastTen")
                    if l10 and not wins_focus:
                        text += f" Last 10: {l10['wins']}–{l10['losses']}."
                    tiles = [{"label": "PCT", "value": str(tr.get("winningPercentage", "—"))},
                             {"label": "GB", "value": str(gb)}]
                    for key, lab in (("home", "Home"), ("away", "Away"), ("lastTen", "Last 10")):
                        if key in splits:
                            tiles.append({"label": lab, "value": f"{splits[key]['wins']}–{splits[key]['losses']}"})
                    if tr.get("runDifferential") is not None:
                        tiles.append({"label": "Run diff", "value": f"{tr['runDifferential']:+d}"})
                    streak = (tr.get("streak") or {}).get("streakCode")
                    if streak and final is False:
                        tiles.append({"label": "Streak", "value": streak})
                    rows = [[x["team"]["name"], x["wins"], x["losses"], x.get("winningPercentage", ""),
                             x.get("gamesBack", "-")] for x in r["teamRecords"]]
                    hi = [i for i, x in enumerate(r["teamRecords"]) if x["team"]["id"] == t.id]
                    return Answer(text, ["Team", "W", "L", "PCT", "GB"], rows, through(self.cutoff),
                                  title=f"{season} standings", headline=f"{tr['wins']}–{tr['losses']}",
                                  subhead=f"{tr['team']['name']} · #{rank} in the {name} · {season} "
                                          f"{'final' if final else 'through ' + pretty_date(as_of.isoformat())}",
                                  tiles=tiles, highlight_rows=hi, notes=notes,
                                  suggestions=[f"{t.short} last game", f"{t.short} home run leaders",
                                               f"{season} standings"])
            return Answer(f"I couldn't find {t.name} in the {season} standings.", kind="notice",
                          title="No results", footnote=through(self.cutoff), notes=notes)
        rows = []
        for r in recs:
            name = self._division_name(r)
            for tr in r["teamRecords"]:
                rows.append([name, tr["team"]["name"], tr["wins"], tr["losses"],
                             tr.get("winningPercentage", ""), tr.get("gamesBack", "-")])
        return Answer(f"{season} {suffix}:", ["Division", "Team", "W", "L", "PCT", "GB"], rows, through(self.cutoff),
                      title="Standings", headline=f"{season} standings",
                      subhead=suffix[:1].upper() + suffix[1:], notes=notes,
                      suggestions=[f"{season - 1} standings", "How are the Dodgers doing?"])

    def _division_name(self, record: dict) -> str:
        """Division name; before 1969 there were none, so fall back to the league."""
        div = record.get("division") or {}
        if div.get("name"):
            return div["name"]
        try:
            if div.get("id"):
                return self.client.get(f"divisions/{div['id']}", ttl=TTL_LONG)["divisions"][0]["name"]
        except Exception:
            pass
        return LEGACY_LEAGUES.get((record.get("league") or {}).get("id"), "League")

    # --- game results -----------------------------------------------------------------
    MONTH_ABBR = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

    def _target_date(self, q: str) -> date | None:
        ql = q.lower()
        if re.search(r"yesterday|last night", ql):
            return self.cutoff
        m = re.search(r"\b(1[89]\d\d|20\d\d)-(\d\d)-(\d\d)\b", q)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        months = {m: i for i, m in enumerate(self.MONTH_ABBR, 1)}
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                      r"(?:,?\s+(1[89]\d\d|20\d\d))?", ql)
        if m:
            mon, day = months[m[1]], int(m[2])
            year = int(m[3]) if m[3] else self.cutoff.year
            d = date(year, mon, day)
            if d > self.cutoff and not m[3]:
                d = date(year - 1, mon, day)
            return d
        m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b", ql)
        if m:
            year = int(m[3]) if m[3] else self.cutoff.year
            year += 2000 if year < 100 else 0
            d = date(year, int(m[1]), int(m[2]))
            if d > self.cutoff and not m[3]:
                d = date(year - 1, int(m[1]), int(m[2]))
            return d
        return None

    @staticmethod
    def _round(g: dict) -> str:
        return {"R": "Regular season", "F": "Wild Card", "D": "Division Series", "L": "League Championship Series",
                "W": "World Series"}.get(g.get("gameType", "R"), g.get("gameType", ""))

    def _final_games(self, params: dict) -> list[dict]:
        data = self.client.get("schedule", params, ttl=TTL_SHORT)
        return [g for dt in data.get("dates", []) for g in dt["games"]
                if g["status"]["abstractGameState"] == "Final"]

    def _game_card(self, g: dict, teams: list[Team], notes: list[str] | None = None) -> Answer:
        away, home = g["teams"]["away"], g["teams"]["home"]
        win, lose = (away, home) if away.get("isWinner") else (home, away)
        score = f"{win.get('score')}–{lose.get('score')}"
        subject = next((t for t in teams if t.id in (win["team"]["id"], lose["team"]["id"])), None)
        wn, ln = win["team"].get("teamName", win["team"]["name"]), lose["team"].get("teamName", lose["team"]["name"])
        day = pretty_date(g["officialDate"])
        if subject and subject.id == lose["team"]["id"]:
            text = f"The {ln} lost to the {wn} {score} on {day} ({self._round(g)})."
        else:
            text = f"The {wn} beat the {ln} {score} on {day} ({self._round(g)})."
        dec = g.get("decisions", {})
        tiles = [{"label": lab, "value": dec[k]["fullName"]}
                 for lab, k in (("Win", "winner"), ("Loss", "loser"), ("Save", "save")) if k in dec]
        if tiles:
            text += " " + "; ".join(f"{t['label'][0] if t['label'] != 'Save' else 'SV'}: {t['value']}" for t in tiles) + "."
        cols, rows = [], []
        ls = g.get("linescore") or {}
        inns = ls.get("innings") or []
        if inns:
            cols = [""] + [str(i.get("num")) for i in inns] + ["R", "H", "E"]
            for side in ("away", "home"):
                t = g["teams"][side]["team"]
                tot = (ls.get("teams") or {}).get(side, {})
                rows.append([t.get("teamName", t["name"])] + [str((i.get(side) or {}).get("runs", "")) if (i.get(side) or {}).get("runs") is not None
                                                             else "X" for i in inns]
                            + [str(tot.get("runs", "")), str(tot.get("hits", "")), str(tot.get("errors", ""))])
        return Answer(text, cols, rows, through(self.cutoff), title="Game result",
                      headline=f"{wn} {win.get('score')}, {ln} {lose.get('score')}",
                      subhead=f"{self._round(g)} · {day} · Final", tiles=tiles, notes=notes or [],
                      suggestions=[f"{wn} record", f"{ln} record"])

    def results(self, q: str, teams: list[Team], parsed: Parsed | None = None) -> Answer:
        ql = q.lower()
        d = self._target_date(q)
        if d and d.year < 1901:
            return Answer("The API has game scores only from 1901. For earlier years I can give "
                          "standings, season stats and leaders.", kind="notice", title="Out of range",
                          footnote=through(self.cutoff))
        if d and d > self.cutoff:
            return Answer(f"That date is past my cutoff. I only have games through "
                          f"{pretty_date(self.cutoff.isoformat())}.", kind="notice", title="Out of range",
                          footnote=through(self.cutoff))
        params = {"sportId": 1, "hydrate": "decisions,linescore,team"}
        head_to_head = len(teams) >= 2
        if teams:
            params["teamId"] = teams[0].id
            if head_to_head:
                params["opponentId"] = teams[1].id
        if d:
            params["date"] = d.isoformat()
        else:                                   # "last game"
            params["startDate"] = (self.cutoff - timedelta(days=400 if head_to_head else 30)).isoformat()
            params["endDate"] = self.cutoff.isoformat()
        games = self._final_games(params)
        games.sort(key=lambda g: (g["officialDate"], g.get("gameNumber", 1)))

        if not games:
            who = f" for {' vs '.join(t.name for t in teams[:2])}" if teams else ""
            if d and teams and not head_to_head:
                # the team just didn't play that day: show their most recent game instead
                recent = self._final_games({"sportId": 1, "teamId": teams[0].id, "hydrate": "decisions,linescore,team",
                                            "startDate": (min(d, self.cutoff) - timedelta(days=30)).isoformat(),
                                            "endDate": min(d, self.cutoff).isoformat()})
                recent.sort(key=lambda g: (g["officialDate"], g.get("gameNumber", 1)))
                if recent:
                    ans = self._game_card(recent[-1], teams, notes=[
                        f"The {teams[0].short} didn't play on {pretty_date(d.isoformat())}. "
                        f"This is their most recent game before that."])
                    return ans
            when = f" on {pretty_date(d.isoformat())}" if d else (" in the last 13 months" if head_to_head
                                                                  else " in the last 30 days")
            return Answer(f"I didn't find a completed game{who}{when}.", kind="notice", title="No game found",
                          footnote=through(self.cutoff))

        if head_to_head and not d:
            return self._head_to_head(games, teams[:2])
        if not d:
            last_day = games[-1]["officialDate"]
            games = [g for g in games if g["officialDate"] == last_day]
        if len(games) == 1:
            return self._game_card(games[0], teams)
        rows, cards = [], []
        for g in games:
            a, h = g["teams"]["away"], g["teams"]["home"]
            dec = g.get("decisions", {})
            note = "; ".join(f"{k}: {dec[v]['fullName']}" for k, v in
                             (("W", "winner"), ("L", "loser"), ("SV", "save")) if v in dec)
            rows.append([pretty_date(g["officialDate"]), f"{a['team']['name']} {a.get('score', '')} @ "
                         f"{h['team']['name']} {h.get('score', '')}", self._round(g), note])
        day = rows[0][0]
        return Answer(f"{len(games)} games on {day}:", ["Date", "Game", "Round", "Decisions"], rows,
                      through(self.cutoff), title="Scoreboard", headline=f"{len(games)} games",
                      subhead=day)

    def _head_to_head(self, games: list[dict], teams: list[Team]) -> Answer:
        a, b = teams
        wins = {a.id: 0, b.id: 0}
        for g in games:
            for side in ("away", "home"):
                t = g["teams"][side]
                if t.get("isWinner") and t["team"]["id"] in wins:
                    wins[t["team"]["id"]] += 1
        latest = self._game_card(games[-1], teams)
        rows = []
        for g in reversed(games[-8:]):
            aw, ho = g["teams"]["away"], g["teams"]["home"]
            dec = g.get("decisions", {})
            rows.append([pretty_date(g["officialDate"]),
                         f"{aw['team'].get('teamName', aw['team']['name'])} {aw.get('score', '')} @ "
                         f"{ho['team'].get('teamName', ho['team']['name'])} {ho.get('score', '')}",
                         self._round(g), "; ".join(f"{k}: {dec[v]['fullName']}" for k, v in
                                                   (("W", "winner"), ("L", "loser")) if v in dec)])
        record = f"{a.short} {wins[a.id]}–{wins[b.id]} {b.short} over the last {len(games)} meetings"
        latest.columns, latest.rows = ["Date", "Game", "Round", "Decisions"], rows
        latest.title, latest.kind = "Head to head", "answer"
        latest.subhead = f"{latest.subhead} · most recent of {len(games)} meetings in 13 months"
        latest.notes = [record + " (last 13 months)."]
        latest.tiles = [{"label": a.short, "value": str(wins[a.id])}, {"label": b.short, "value": str(wins[b.id])}]
        latest.suggestions = [f"{a.short} record", f"{b.short} record", f"{a.short} vs {b.short} 2025"]
        return latest
