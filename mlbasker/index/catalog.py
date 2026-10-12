"""Named baseball events and how to find them in the index.

Each matcher inspects the (lower-cased) question and returns a spec dict, or None.
Each builder turns a spec + Filters into SQL returning a uniform row shape:
    date, game_pk, away, home, away_score, home_score, inning, half, detail
(ranked entries also return `metric`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Filters:
    season_from: int | None = None
    season_to: int | None = None
    date_from: str | None = None          # inclusive window start, YYYY-MM-DD
    date_to: str | None = None            # inclusive cutoff, YYYY-MM-DD
    game_types: list[str] | None = None   # None = everything indexed
    team_ids: list[int] = field(default_factory=list)
    home_only: bool = False               # teams must be the home side (walk-off winners)
    team_result: str | None = None        # "win" / "loss": the named team won / lost the game
    team_role: str | None = None          # no-hitters: "victim" (was no-hit) / "thrower" (its pitchers did it)
    player_ids: list[int] = field(default_factory=list)
    player_names: list[str] = field(default_factory=list)

    def player_clause(self, a: str = "p", use_names: bool = False) -> tuple[str, list]:
        """Plays the player took part in as batter/pitcher, or is named in the play text.

        Written as a UNION of two index lookups: `batter_id IN (..) OR pitcher_id IN (..)` makes
        SQLite scan every play (minutes on a 12M-row table).
        """
        if not self.player_ids:
            return "", []
        ph = ",".join("?" * len(self.player_ids))
        sql = (f" AND {a}.rowid IN (SELECT rowid FROM plays WHERE batter_id IN ({ph}) "
               f"UNION SELECT rowid FROM plays WHERE pitcher_id IN ({ph})")
        params: list = self.player_ids * 2
        for n in (self.player_names if use_names else []):
            sql += f" UNION SELECT rowid FROM plays WHERE description LIKE ?"
            params.append(f"%{n}%")
        return sql + ")", params

    def game_clause(self, a: str = "g") -> tuple[str, list]:
        c, p = ["1=1"], []
        if self.season_from is not None:
            c.append(f"{a}.season >= ?"); p.append(self.season_from)
        if self.season_to is not None:
            c.append(f"{a}.season <= ?"); p.append(self.season_to)
        if self.date_from:
            c.append(f"{a}.date >= ?"); p.append(self.date_from)
        if self.date_to:
            c.append(f"{a}.date <= ?"); p.append(self.date_to)
        if self.game_types:
            c.append(f"{a}.game_type IN ({','.join('?' * len(self.game_types))})")
            p += self.game_types
        if self.team_ids:
            ph = ",".join("?" * len(self.team_ids))
            if self.team_result in ("win", "loss"):
                cmp_h, cmp_a = (">", "<") if self.team_result == "win" else ("<", ">")
                c.append(f"(({a}.home_id IN ({ph}) AND {a}.home_score {cmp_h} {a}.away_score) OR "
                         f"({a}.away_id IN ({ph}) AND {a}.away_score {cmp_h} {a}.home_score))")
                p += self.team_ids * 2
            elif self.home_only:
                c.append(f"{a}.home_id IN ({ph})"); p += self.team_ids
            else:
                c.append(f"({a}.away_id IN ({ph}) OR {a}.home_id IN ({ph}))")
                p += self.team_ids * 2
        return " AND ".join(c), p


BASE_WORDS = {
    "1b": r"(first base|1st base|first|1st|1b)", "2b": r"(second base|2nd base|second|2nd|2b)",
    "3b": r"(third base|3rd base|third|3rd|3b)", "home": r"(home plate|home|at the plate)",
}


def _base_in(q: str) -> str | None:
    for key in ("1b", "2b", "3b"):
        if re.search(rf"\b(at|on|to|by|from|picked off)?\s*{BASE_WORDS[key]}\b", q):
            return key
    return None


# --- event phrase families --------------------------------------------------------------
FINAL_OUT_FAMILIES = [
    (r"pick(ed)?[- ]?off", "pickoff"),
    (r"strike ?out|struck out|punch ?out", "strikeout"),
    (r"triple play", "triple_play"),
    (r"double play|\bdp\b|\bgidp\b", "double_play"),
    (r"caught stealing|thrown out stealing", "caught_stealing"),
    (r"force ?out", "force_out"),
    (r"fielder'?s choice", "fielders_choice"),
    (r"sac(rifice)? fly", "sac_fly"),
    (r"sac(rifice)? bunt", "sac_bunt"),
    (r"fly ?out|fly ball|flies out", "flyout"),
    (r"ground ?out|grounder|grounds out", "groundout"),
    (r"line ?out|line drive", "lineout"),
    (r"pop ?out|pop[- ]?up|pops out", "popout"),
]

FINAL_OUT_TYPES = {
    "strikeout": ["strikeout", "strike_out", "strikeout_double_play", "strikeout_triple_play"],
    "triple_play": ["triple_play", "grounded_into_triple_play", "strikeout_triple_play"],
    "double_play": ["double_play", "grounded_into_double_play", "strikeout_double_play",
                    "sac_bunt_double_play", "sac_fly_double_play", "cs_double_play",
                    "runner_double_play"],
    "caught_stealing": ["caught_stealing", "caught_stealing_2b", "caught_stealing_3b",
                        "caught_stealing_home", "cs_double_play"],
    "force_out": ["force_out"], "fielders_choice": ["fielders_choice", "fielders_choice_out"],
    "sac_fly": ["sac_fly", "sac_fly_double_play"],
    "sac_bunt": ["sac_bunt", "sac_bunt_double_play"],
    "flyout": ["field_out"], "groundout": ["field_out"], "lineout": ["field_out"],
    "popout": ["field_out"],
}
FINAL_OUT_DESC = {"flyout": "%flies out%", "groundout": "%grounds out%",
                  "lineout": "%lines out%", "popout": "%pops out%"}

WALKOFF_FAMILIES = [
    (r"grand slam", ("desc", "%grand slam%"), "walk-off grand slam"),
    (r"inside[- ]the[- ]park", ("desc", "%inside-the-park%"), "walk-off inside-the-park home run"),
    (r"home run|homer|\bhr\b", ("types", ["home_run"]), "walk-off home run"),
    (r"single", ("types", ["single"]), "walk-off single"),
    (r"double", ("types", ["double"]), "walk-off double"),
    (r"triple", ("types", ["triple"]), "walk-off triple"),
    (r"hit by pitch|\bhbp\b|hit-by-pitch", ("types", ["hit_by_pitch"]), "walk-off hit by pitch"),
    (r"\bwalk|base on balls", ("types", ["walk", "intent_walk"]), "walk-off walk"),
    (r"balk", ("types", ["balk"]), "walk-off balk"),
    (r"wild pitch", ("types", ["wild_pitch"]), "walk-off wild pitch"),
    (r"passed ball", ("types", ["passed_ball"]), "walk-off passed ball"),
    (r"sac(rifice)? fly", ("types", ["sac_fly"]), "walk-off sacrifice fly"),
    (r"error", ("types", ["field_error", "error"]), "walk-off error"),
    (r"fielder'?s choice", ("types", ["fielders_choice"]), "walk-off fielder's choice"),
]

# single plays that can be listed by their event type
PLAY_EVENTS = [
    (r"inside[- ]the[- ]park", ("desc", "%inside-the-park%"), "inside-the-park home run"),
    (r"grand slam", ("desc", "%grand slam%"), "grand slam"),
    (r"catcher'?s? interference", ("types", ["catcher_interf"]), "catcher's interference"),
    (r"\bbalk", ("types", ["balk"]), "balk"),
    (r"steal(s|ing)? (of )?home|stolen base of home", ("types", ["stolen_base_home"]), "steal of home"),
    (r"wild pitch", ("types", ["wild_pitch"]), "wild pitch"),
    (r"passed ball", ("types", ["passed_ball"]), "passed ball"),
    (r"hit by (a )?pitch|\bhbp\b", ("types", ["hit_by_pitch"]), "hit by pitch"),
    (r"intentional walk", ("types", ["intent_walk"]), "intentional walk"),
    (r"home runs?|homers?", ("types", ["home_run"]), "home run"),
    (r"triples?", ("types", ["triple"]), "triple"),
    (r"fan interference", ("types", ["fan_interference"]), "fan interference play"),
    (r"ejection", ("types", ["ejection"]), "ejection"),
]

COMMON_EVENTS = {"home run", "triple", "hit by pitch", "intentional walk", "balk", "wild pitch", "passed ball"}
HISTORY_CUES = re.compile(r"last time|first time|most recent|latest|earliest|\bwhen\b|\blist\b|\ball\b|"
                          r"every|occurrences?|instances?|\bwhich\b|games? (in which|where)|\bplays?\b|"
                          r"were hit|\btotal\b|combined|league[- ]?wide|in (all of )?(mlb|the majors|baseball)")

ORDINAL_OUTS = {"double": 2, "triple": 3}


def _score_spec(q: str) -> dict | None:
    """'1-0 game', 'won 5-3', 'shutouts' → winner/loser run totals (None = any)."""
    m = re.search(r"(?<![\d/.-])(\d{1,2})\s*[-–]\s*(\d{1,2})(?![\d/-])", q)
    if m and re.search(r"\b(game|games|win|won|beat|lose|lost|score|scored|final|ended|shutout)\b", q) \
            and not re.search(r"\b(19|20)\d\d\b\s*$", q):
        a, b = int(m.group(1)), int(m.group(2))
        spec = {"hi": max(a, b), "lo": min(a, b), "label": f"{max(a, b)}–{min(a, b)} game"}
        spec["result"] = "win" if re.search(r"\b(win|won|wins|beat|defeat)", q) else \
            "loss" if re.search(r"\b(lose|lost|loss|losses|fell|dropped)\b", q) else None
        return spec
    if re.search(r"\bshut[- ]?outs?\b", q) and not re.search(r"\b(era|pitcher|pitchers|leaders?)\b", q):
        return {"hi": None, "lo": 0, "label": "shutout",
                "result": "win" if re.search(r"\b(threw|pitched|won|win|wins)\b", q) else
                "loss" if re.search(r"\b(lost|loss|losses|were shut out|got shut out)\b", q) else None}
    return None


def _threshold_record(q: str) -> dict | None:
    """'a pitcher struck out 20', 'hit 4 home runs in a game' → {'stat': ..., 'min': N}."""
    if not re.search(r"in a game|in one game|single[- ]game|\ba (pitcher|batter|player|hitter)\b|\bgame\b", q):
        return None
    for pat, stat in ((r"struck out (\d+)|(\d+)\+? (strikeouts|k's)|(\d+)[- ]strikeout", "strikeouts"),
                      (r"(\d+)[- ]?(home runs?|homers?)|hit (\d+) (home runs?|homers?)|(\d+)[- ]homer game", "home runs"),
                      (r"(\d+) hits in a game|(\d+)[- ]hit game", "hits")):
        m = re.search(pat, q)
        if m:
            n = next((int(g) for g in m.groups() if g and g.isdigit()), None)
            if n and n >= 2:
                return {"stat": stat, "min": n}
    return None


# --- matchers ---------------------------------------------------------------------------
def match(q: str) -> tuple[str, dict] | None:
    """Return (entry_key, spec) for the first catalog entry that fits the question."""
    # multi-out defensive plays: "unassisted triple play", "triple plays", "unassisted double play"
    m = re.search(r"\b(double|triple)[- ]plays?\b", q)
    if m and not re.search(r"(game|games)\s+(end|ended|ending|finish|finished)|ended (on|with)|game[- ]?(ending|ender)|walk[- ]?off", q):
        return "multi_out", {"outs": ORDINAL_OUTS[m.group(1)],
                             "unassisted": bool(re.search(r"unassisted", q))}

    # no-hitters / perfect games
    if re.search(r"perfect game", q):
        return "no_hitter", {"perfect": True}
    if re.search(r"no[- ]?hit(ter|s|ters)?\b", q):
        return "no_hitter", {"perfect": False}
    if re.search(r"hit(s|ting)? for the cycle|\bcycles?\b", q):
        return "cycle", {}

    # games decided/ended by something
    if re.search(r"walk[- ]?offs?|walked[- ]off", q):
        rest = re.sub(r"walk[- ]?offs?|walked[- ]off", " ", q)
        for pat, how, label in WALKOFF_FAMILIES:
            if re.search(pat, rest):
                return "walk_off", {"how": how, "label": label}
        return "walk_off", {"how": None, "label": "walk-off win"}
    if re.search(r"(game|games|contest)\s+(end|ended|ends|ending|finish|finished|was over|concluded)|"
                 r"ended (the game|a game|on)|final out|game[- ]ending|game[- ]ender", q):
        for pat, fam in FINAL_OUT_FAMILIES:          # outs first: "ended on a double play"
            if re.search(pat, q):
                return "game_end", {"family": fam, "base": _base_in(q) if fam == "pickoff" else None}
        rest = re.sub(r"game[- ]ending|game[- ]ender", " ", q)
        for pat, how, label in WALKOFF_FAMILIES:     # ...then ways a home team ends it by scoring
            if re.search(pat, rest):
                return "walk_off", {"how": how, "label": label}
        return None   # "ended on <something we don't know>": say so rather than guess

    # final scores: "last time a game ended 1-0", "Red Sox won 1-0", "shutouts"
    sc = _score_spec(q)
    if sc:
        return "game_score", sc

    # how many games were played
    if re.search(r"how many (regular[- ]season |postseason |playoff )?games\b", q) and \
            not re.search(r"\b(win|won|wins|lose|lost|loss|losses|ended|end|hit|strike|home run|homer|walk|no[- ]?hit|"
                          r"back|ago|behind|ahead|over|under)\b", q):
        return "game_count", {}

    # extremes
    if re.search(r"longest game", q):
        return "game_extreme", {"metric": "innings", "order": "DESC", "label": "innings"}
    if re.search(r"(highest|most)[- ]scoring (game|contest)", q):
        return "game_extreme", {"metric": "away_score + home_score", "order": "DESC", "label": "total runs"}
    if re.search(r"(lowest|least)[- ]scoring (game|contest)", q):
        return "game_extreme", {"metric": "away_score + home_score", "order": "ASC", "label": "total runs"}
    if re.search(r"biggest (blowout|win|margin|rout)|largest margin|most lopsided", q):
        return "game_extreme", {"metric": "ABS(away_score - home_score)", "order": "DESC", "label": "run margin"}
    if re.search(r"extra[- ]inning", q):
        return "game_extreme", {"metric": "innings", "order": "DESC", "label": "innings", "min": 10}

    # single-game individual records
    th = _threshold_record(q)
    if th:
        return "single_game_record", th
    # "in a single game" is a game record; "in a single season" is a leaders question
    m = re.search(r"most (strikeouts|home runs|homers|hits|walks)\b.*\bgame\b", q)
    if m:
        what = m.group(1)
        return "single_game_record", {"stat": {"homers": "home runs"}.get(what, what)}

    for pat, how, label in PLAY_EVENTS:
        if re.search(pat, q):
            # Everyday events (home runs, triples...) are normal player stats; only treat them
            # as a history search when the question asks for specific occurrences.
            if label in COMMON_EVENTS and not HISTORY_CUES.search(q):
                continue
            return "play_event", {"how": how, "label": label}
    return None


# --- builders ---------------------------------------------------------------------------
GAME_COLS = ("g.date AS date, g.game_pk AS game_pk, g.away AS away, g.home AS home, "
             "g.away_score AS away_score, g.home_score AS home_score, g.league AS league, "
             "g.game_type AS game_type")


HR_TEXT = ("%grand slam%", "%inside-the-park%")     # these only ever occur on home runs


def _play_cond(how: tuple, alias: str = "p") -> tuple[str, list]:
    kind, val = how
    if kind == "types":
        ph = ",".join("?" * len(val))
        return f"{alias}.event_type IN ({ph})", list(val)
    if val in HR_TEXT:   # lets SQLite use the event_type index instead of scanning 12M descriptions
        return f"{alias}.event_type = 'home_run' AND {alias}.description LIKE ?", [val]
    return f"{alias}.description LIKE ?", [val]


def _in(values: list[str]) -> tuple[str, list]:
    return f"({','.join('?' * len(values))})", list(values)


def build(entry: str, spec: dict, f: Filters) -> tuple[str, list, str]:
    """Return (sql, params, order) — `order` is a SQL ORDER BY fragment for 'ranked' entries or ''."""
    gc, gp = f.game_clause("g")
    # fielders (e.g. who turned a triple play) only appear in the play text
    player_sql, player_p = f.player_clause("p", use_names=entry == "multi_out")

    if entry == "multi_out":
        extra = " AND d.unassisted = 1" if spec["unassisted"] else ""
        sql = (f"SELECT {GAME_COLS}, p.inning AS inning, p.half AS half, p.description AS detail "
               "FROM defensive_plays d JOIN games g ON g.game_pk = d.game_pk "
               "JOIN plays p ON p.game_pk = d.game_pk AND p.idx = d.play_idx "
               f"WHERE d.outs = ?{extra} AND {gc}{player_sql}")
        return sql, [spec["outs"]] + gp + player_p, ""

    if entry == "game_end":
        conds, params = ["g.has_pbp = 1", gc], list(gp)
        fam = spec["family"]
        if fam == "pickoff":
            base = spec["base"]
            types = ([f"pickoff_{base}", f"pickoff_caught_stealing_{base}"] if base
                     else ["pickoff_1b", "pickoff_2b", "pickoff_3b", "pickoff_caught_stealing_2b",
                           "pickoff_caught_stealing_3b", "pickoff_caught_stealing_home"])
            ph, tp = _in(types)
            conds.append(f"g.final_out_event IN {ph}"); params += tp
        elif fam:
            ph, tp = _in(FINAL_OUT_TYPES[fam])
            conds.append(f"g.final_out_event IN {ph}"); params += tp
            if fam in FINAL_OUT_DESC:
                conds.append("g.final_out_desc LIKE ?"); params.append(FINAL_OUT_DESC[fam])
        sql = (f"SELECT {GAME_COLS}, p.inning AS inning, p.half AS half, g.final_out_desc AS detail "
               "FROM games g JOIN plays p ON p.game_pk = g.game_pk AND p.is_last = 1 "
               f"WHERE {' AND '.join(conds)}")
        sql += player_sql
        params += player_p
        return sql, params, ""

    if entry == "walk_off":
        conds, params = ["g.walk_off = 1", gc], list(gp)
        how = spec["how"]
        if how:
            kind, val = how
            if kind == "types":
                ph, tp = _in(val); conds.append(f"g.walk_off_event IN {ph}"); params += tp
            else:
                conds.append("g.final_out_desc LIKE ?"); params.append(val)
        sql = (f"SELECT {GAME_COLS}, p.inning AS inning, p.half AS half, g.final_out_desc AS detail "
               "FROM games g JOIN plays p ON p.game_pk = g.game_pk AND p.is_last = 1 "
               f"WHERE {' AND '.join(conds)}")
        sql += player_sql
        params += player_p
        return sql, params, ""

    if entry == "no_hitter":
        reach = "AND {r} = 0" if spec["perfect"] else ""
        zero_home = f"g.home_hits = 0 AND g.home_innings_batted >= 9 {reach.format(r='g.home_reached')}"
        zero_away = f"g.away_hits = 0 AND g.away_innings_batted >= 9 {reach.format(r='g.away_reached')}"
        word = "perfect game" if spec["perfect"] else "no-hitter"
        extra, ep = "", []
        if f.team_ids and f.team_role in ("victim", "thrower"):
            ph = ",".join("?" * len(f.team_ids))
            # the side held hitless is the victim; the other side's pitchers are the throwers
            vh, va = ("home_id", "away_id") if f.team_role == "victim" else ("away_id", "home_id")
            extra = f" AND (({zero_home} AND g.{vh} IN ({ph})) OR ({zero_away} AND g.{va} IN ({ph})))"
            ep = f.team_ids * 2
        sql = (f"SELECT {GAME_COLS}, NULL AS inning, NULL AS half, "
               f"CASE WHEN {zero_home} THEN g.home || ' were held hitless by ' || g.away "
               f"ELSE g.away || ' were held hitless by ' || g.home END || ' ({word})' AS detail "
               f"FROM games g WHERE g.has_pbp = 1 AND (({zero_home}) OR ({zero_away})) AND {gc}{extra}")
        return sql, gp + ep, ""

    if entry == "cycle":
        pid, pp = "", []
        if f.player_ids:
            pid = f" AND b.batter_id IN ({','.join('?' * len(f.player_ids))})"
            pp = list(f.player_ids)
        sql = (f"SELECT {GAME_COLS}, NULL AS inning, NULL AS half, b.batter || ' hit for the cycle' AS detail "
               "FROM batter_games b JOIN games g ON g.game_pk = b.game_pk "
               f"WHERE b.singles >= 1 AND b.doubles >= 1 AND b.triples >= 1 AND b.homers >= 1 AND {gc}{pid}")
        return sql, gp + pp, ""

    if entry == "game_count":
        sql = (f"SELECT {GAME_COLS}, NULL AS inning, NULL AS half, '' AS detail FROM games g "
               f"WHERE g.home_score IS NOT NULL AND {gc}")
        return sql, gp, ""

    if entry == "game_score":
        conds, params = ["g.home_score IS NOT NULL", gc], list(gp)
        if spec["hi"] is not None:
            conds.append("MAX(g.away_score, g.home_score) = ? AND MIN(g.away_score, g.home_score) = ?")
            params += [spec["hi"], spec["lo"]]
        else:
            conds.append("MIN(g.away_score, g.home_score) = ? AND g.away_score != g.home_score")
            params.append(spec["lo"])
        sql = (f"SELECT {GAME_COLS}, NULL AS inning, NULL AS half, "
               "CASE WHEN g.away_score > g.home_score THEN g.away ELSE g.home END || ' beat ' || "
               "CASE WHEN g.away_score > g.home_score THEN g.home ELSE g.away END || ' ' || "
               "MAX(g.away_score, g.home_score) || '–' || MIN(g.away_score, g.home_score) AS detail "
               f"FROM games g WHERE {' AND '.join(conds)}")
        return sql, params, ""

    if entry == "game_extreme":
        mn = f" AND g.innings >= {int(spec['min'])}" if spec.get("min") else ""
        needs = " AND g.innings IS NOT NULL" if "innings" in spec["metric"] else " AND g.home_score IS NOT NULL"
        # score-level rows (no play-by-play) count here: runs and innings come from the schedule
        sql = (f"SELECT {GAME_COLS}, NULL AS inning, NULL AS half, "
               f"({spec['metric']}) AS metric, '{spec['label']}: ' || ({spec['metric']}) AS detail "
               f"FROM games g WHERE 1=1{needs}{mn} AND {gc}")
        return sql, gp, f"metric {spec['order']}, date DESC"

    if entry == "single_game_record":
        stat = spec["stat"]
        table, idcol, namecol, col, noun = {
            "strikeouts": ("pitcher_games", "pitcher_id", "pitcher", "strikeouts", "struck out"),
            "walks": ("pitcher_games", "pitcher_id", "pitcher", "walks", "walked"),
            "home runs": ("batter_games", "batter_id", "batter", "homers", "home runs"),
            "hits": ("batter_games", "batter_id", "batter", "hits", "hits"),
        }[stat]
        pid, pp = "", []
        if f.player_ids:
            pid = f" AND t.{idcol} IN ({','.join('?' * len(f.player_ids))})"
            pp = list(f.player_ids)
        floor = spec.get("min") or 2
        sql = (f"SELECT {GAME_COLS}, NULL AS inning, NULL AS half, t.{col} AS metric, "
               f"t.{namecol} || ': ' || t.{col} || ' {noun}' AS detail "
               f"FROM {table} t JOIN games g ON g.game_pk = t.game_pk "
               f"WHERE t.{col} >= ? AND {gc}{pid}")
        params = [floor] + gp + pp
        if spec.get("min"):   # "a pitcher struck out 20" → chronological, so last/first/count work
            return sql, params, ""
        return sql, params, "metric DESC, date DESC"

    if entry == "play_event":
        cond, cp = _play_cond(spec["how"])
        sql = (f"SELECT {GAME_COLS}, p.inning AS inning, p.half AS half, p.description AS detail "
               "FROM plays p JOIN games g ON g.game_pk = p.game_pk "
               f"WHERE {cond} AND {gc}{player_sql}")
        return sql, cp + gp + player_p, ""

    raise KeyError(entry)


def text_search(f: Filters, phrase: str, use_fts: bool) -> tuple[str, list]:
    gc, gp = f.game_clause("g")
    player_sql, player_p = f.player_clause("p")
    if use_fts:
        quoted = '"' + phrase.replace('"', '""') + '"'
        sql = (f"SELECT {GAME_COLS}, p.inning AS inning, p.half AS half, p.description AS detail "
               "FROM plays_fts f JOIN plays p ON p.rowid = f.rowid JOIN games g ON g.game_pk = p.game_pk "
               f"WHERE plays_fts MATCH ? AND {gc}{player_sql}")
        return sql, [quoted] + gp + player_p
    sql = (f"SELECT {GAME_COLS}, p.inning AS inning, p.half AS half, p.description AS detail "
           "FROM plays p JOIN games g ON g.game_pk = p.game_pk "
           f"WHERE p.description LIKE ? AND {gc}{player_sql}")
    return sql, [f"%{phrase}%"] + gp + player_p
