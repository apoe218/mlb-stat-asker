"""Crawl the Stats API into the local index. Resumable; never indexes past the cutoff."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Callable

from ..client import BASE_11, MlbClient, TTL_LONG
from ..cutoff import cutoff_date
from .db import has_fts
from .extract import FEED_FIELDS, extract_game, score_only

GAME_TYPES = "R,F,D,L,W"   # regular season + postseason rounds; no spring training / All-Star
FIRST_SEASON = 1876        # earliest season in the API: score-level rows from here
FIRST_PBP_SEASON = 1950    # earliest season with play-by-play (1949 and before: none; checked)


def _chunks(start: date, end: date, days: int = 30):
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=days - 1), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def season_bounds(client: MlbClient, season: int) -> tuple[date, date] | None:
    data = client.get("seasons", {"sportId": 1, "season": season}, ttl=TTL_LONG)
    seasons = data.get("seasons", [])
    if not seasons:
        return None
    s = seasons[0]
    start = date.fromisoformat(s["regularSeasonStartDate"])
    end = date.fromisoformat(s.get("postSeasonEndDate") or s["regularSeasonEndDate"])
    return start, end


def _team_name(team: dict) -> str:
    # a few early Negro-league teams come back with an id but no name
    return team.get("name") or f"Team {team['id']}"


def _schedule_meta(g: dict, day: str) -> dict:
    away, home = g["teams"]["away"], g["teams"]["home"]
    innings = len((g.get("linescore") or {}).get("innings") or []) or None
    return {"game_pk": g["gamePk"], "date": g.get("officialDate", day),
            "season": int(g["season"]), "game_type": g["gameType"],
            "league": (home["team"].get("league") or {}).get("name"),
            "away_id": away["team"]["id"], "away": _team_name(away["team"]),
            "home_id": home["team"]["id"], "home": _team_name(home["team"]),
            "away_score": away.get("score"), "home_score": home.get("score"),
            "innings": innings, "venue": (g.get("venue") or {}).get("name")}


def final_games(client: MlbClient, start: date, end: date) -> list[dict]:
    games: list[dict] = []
    for a, b in _chunks(start, end):
        data = client.get("schedule", {"sportId": 1, "startDate": a.isoformat(), "endDate": b.isoformat(),
                                       "gameType": GAME_TYPES, "hydrate": "team,linescore"},
                          ttl=TTL_LONG if b < cutoff_date() - timedelta(days=3) else 300)
        for d in data.get("dates", []):
            for g in d.get("games", []):
                if g.get("status", {}).get("abstractGameState") != "Final":
                    continue
                if g.get("gameType") not in GAME_TYPES.split(","):
                    continue
                try:
                    games.append(_schedule_meta(g, d["date"]))
                except (KeyError, TypeError):
                    continue    # one malformed schedule row must not abort a whole backfill
    seen: set[int] = set()
    return [g for g in games if not (g["game_pk"] in seen or seen.add(g["game_pk"]))]


def _fetch(client: MlbClient, meta: dict) -> tuple[dict, dict | None]:
    try:
        feed = client.get(f"game/{meta['game_pk']}/feed/live", {"fields": FEED_FIELDS},
                          base=BASE_11, cache_result=False)
        return meta, feed
    except Exception:
        return meta, None


def _write(con: sqlite3.Connection, ex: dict) -> None:
    g = ex["game"]
    con.execute(f"INSERT OR REPLACE INTO games ({','.join(g)}) VALUES ({','.join('?' * len(g))})",
                list(g.values()))
    for table, rows in (("plays", ex["plays"]), ("outs", ex["outs"]),
                        ("defensive_plays", ex["defensive"])):
        for r in rows:
            verb = "INSERT OR REPLACE" if table != "outs" else "INSERT"
            con.execute(f"{verb} INTO {table} ({','.join(r)}) VALUES ({','.join('?' * len(r))})",
                        list(r.values()))
    for pid, name in ex["players"].items():
        con.execute("INSERT OR REPLACE INTO players (id, name) VALUES (?, ?)", (pid, name))


def _clear_game(con: sqlite3.Connection, game_pk: int) -> None:
    for t in ("plays", "outs", "defensive_plays"):
        con.execute(f"DELETE FROM {t} WHERE game_pk = ?", (game_pk,))


def sync_range(con: sqlite3.Connection, client: MlbClient, start: date, end: date, *,
               workers: int = 8, progress: Callable[[str], None] = print,
               play_by_play: bool = True) -> dict:
    """Index every finished game in [start, end] (capped at the cutoff) not yet indexed.

    Seasons before FIRST_PBP_SEASON have no play-by-play, so they get score-level rows only.
    """
    end = min(end, cutoff_date())
    if start > end:
        return {"indexed": 0, "score_only": 0, "skipped": 0, "failed": 0}
    known = {r[0]: r[1] for r in con.execute("SELECT game_pk, league IS NULL FROM games")}
    games = final_games(client, start, end)
    stats = {"indexed": 0, "score_only": 0, "skipped": 0, "failed": 0}

    todo = []
    for g in games:
        if g["game_pk"] in known:
            stats["skipped"] += 1
            if known[g["game_pk"]]:       # indexed before leagues were recorded: fill it in
                con.execute("UPDATE games SET league = ? WHERE game_pk = ?", (g["league"], g["game_pk"]))
            continue
        if play_by_play and g["season"] >= FIRST_PBP_SEASON:
            todo.append(g)
        else:
            _write(con, {"game": score_only(g), "plays": [], "outs": [], "defensive": [], "players": {}})
            stats["score_only"] += 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (meta, feed) in enumerate(pool.map(lambda m: _fetch(client, m), todo), 1):
            if feed is None:
                stats["failed"] += 1
                continue
            ex = extract_game(feed, meta)
            _clear_game(con, meta["game_pk"])
            _write(con, ex)
            stats["indexed"] += 1
            if i % 100 == 0:
                con.commit()
                progress(f"  {i}/{len(todo)} games ({meta['date']})")
    con.commit()
    return stats


def rebuild_fts(con: sqlite3.Connection) -> None:
    if has_fts(con):
        con.execute("INSERT INTO plays_fts(plays_fts) VALUES('rebuild')")
        con.commit()


def sync_seasons(con: sqlite3.Connection, client: MlbClient, first: int, last: int, *,
                 workers: int = 8, progress: Callable[[str], None] = print) -> None:
    for season in range(last, first - 1, -1):      # newest first: recent questions work early
        b = season_bounds(client, season)
        if not b:
            continue
        start, end = b
        row = con.execute("SELECT synced_through FROM sync_state WHERE season=?", (season,)).fetchone()
        needs_backfill = con.execute("SELECT 1 FROM games WHERE season = ? AND league IS NULL LIMIT 1",
                                     (season,)).fetchone()
        if row and not needs_backfill:  # resume after what was already indexed
            start = max(start, date.fromisoformat(row[0]) - timedelta(days=2))
        if start > cutoff_date():
            continue
        progress(f"Season {season}: {start} → {min(end, cutoff_date())}")
        stats = sync_range(con, client, start, end, workers=workers, progress=progress)
        through = min(end, cutoff_date())
        if stats["failed"] == 0:
            con.execute("INSERT OR REPLACE INTO sync_state VALUES (?, ?)", (season, through.isoformat()))
            con.commit()
        progress(f"  done: {stats}")
    rebuild_fts(con)


def sync_recent(con: sqlite3.Connection, client: MlbClient, days: int = 7, **kw) -> dict:
    end = cutoff_date()
    stats = sync_range(con, client, end - timedelta(days=days), end, **kw)
    rebuild_fts(con)
    return stats


def coverage(con: sqlite3.Connection) -> dict:
    """`games`/`from`/`to` describe play-by-play rows; `all_*` include score-level rows."""
    row = con.execute("SELECT COUNT(*) n, MIN(date) a, MAX(date) b, MIN(season) s0, MAX(season) s1 "
                      "FROM games WHERE has_pbp = 1").fetchone()
    allr = con.execute("SELECT COUNT(*) n, MIN(season) s0, MAX(season) s1, MAX(date) b FROM games").fetchone()
    return {"games": row["n"], "from": row["a"], "to": row["b"],
            "first_season": row["s0"], "last_season": row["s1"],
            "all_games": allr["n"], "all_first_season": allr["s0"], "all_last_season": allr["s1"],
            "all_to": allr["b"]}
