"""SQLite schema for the play-by-play index."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("MLBASKER_DB", "data/plays.sqlite"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_pk INTEGER PRIMARY KEY,
    date TEXT NOT NULL,              -- official date, YYYY-MM-DD
    season INTEGER NOT NULL,
    game_type TEXT NOT NULL,         -- R regular, F/D/L/W postseason rounds
    league TEXT,                     -- home team's league (AL/NL, or a Negro league pre-1949)
    away_id INTEGER, away TEXT, home_id INTEGER, home TEXT,
    away_score INTEGER, home_score INTEGER,
    innings INTEGER,
    venue TEXT,
    has_pbp INTEGER NOT NULL DEFAULT 1,   -- 0 = score-level row only (no play-by-play exists)
    away_hits INTEGER, home_hits INTEGER,
    away_reached INTEGER, home_reached INTEGER,       -- batters who reached base
    away_innings_batted INTEGER, home_innings_batted INTEGER,
    final_out_event TEXT,            -- eventType of the game's last out
    final_out_desc TEXT,
    walk_off INTEGER NOT NULL DEFAULT 0,
    walk_off_event TEXT
);
CREATE INDEX IF NOT EXISTS games_date ON games(date);
CREATE INDEX IF NOT EXISTS games_season ON games(season, game_type);
CREATE INDEX IF NOT EXISTS games_home ON games(home_id);
CREATE INDEX IF NOT EXISTS games_away ON games(away_id);

CREATE TABLE IF NOT EXISTS plays (
    game_pk INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    inning INTEGER, half TEXT,
    event_type TEXT, event TEXT, description TEXT,
    batter_id INTEGER, batter TEXT, pitcher_id INTEGER, pitcher TEXT,
    away_score INTEGER, home_score INTEGER,
    is_scoring INTEGER, is_last INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_pk, idx)
);
CREATE INDEX IF NOT EXISTS plays_event ON plays(event_type);
CREATE INDEX IF NOT EXISTS plays_batter_ev ON plays(batter_id, event_type);
CREATE INDEX IF NOT EXISTS plays_pitcher_ev ON plays(pitcher_id, event_type);
CREATE INDEX IF NOT EXISTS plays_last ON plays(game_pk) WHERE is_last = 1;

-- one row per multi-out defensive play (double plays and up)
CREATE TABLE IF NOT EXISTS defensive_plays (
    game_pk INTEGER NOT NULL,
    play_idx INTEGER NOT NULL,
    play_index INTEGER NOT NULL,
    outs INTEGER NOT NULL,
    putouts INTEGER NOT NULL,
    assists INTEGER NOT NULL,
    fielders INTEGER NOT NULL,       -- distinct fielders credited
    unassisted INTEGER NOT NULL,
    PRIMARY KEY (game_pk, play_idx, play_index)
);

-- every out recorded, with the event that caused it (pickoffs, caught stealing, ...)
CREATE TABLE IF NOT EXISTS outs (
    game_pk INTEGER NOT NULL,
    play_idx INTEGER NOT NULL,
    runner_id INTEGER, runner TEXT,
    event_type TEXT, movement_reason TEXT,
    out_base TEXT, out_number INTEGER
);
CREATE INDEX IF NOT EXISTS outs_event ON outs(event_type);
CREATE INDEX IF NOT EXISTS outs_game ON outs(game_pk, play_idx);

-- per-player-per-game totals, derived from `plays` (cycles and single-game records read these)
CREATE TABLE IF NOT EXISTS batter_games (
    game_pk INTEGER NOT NULL, batter_id INTEGER NOT NULL, batter TEXT,
    singles INTEGER NOT NULL, doubles INTEGER NOT NULL, triples INTEGER NOT NULL,
    homers INTEGER NOT NULL, hits INTEGER NOT NULL,
    PRIMARY KEY (game_pk, batter_id)
);
CREATE INDEX IF NOT EXISTS batter_games_hr ON batter_games(homers) WHERE homers >= 2;
CREATE INDEX IF NOT EXISTS batter_games_hits ON batter_games(hits) WHERE hits >= 4;
CREATE TABLE IF NOT EXISTS pitcher_games (
    game_pk INTEGER NOT NULL, pitcher_id INTEGER NOT NULL, pitcher TEXT,
    strikeouts INTEGER NOT NULL, walks INTEGER NOT NULL,
    PRIMARY KEY (game_pk, pitcher_id)
);
CREATE INDEX IF NOT EXISTS pitcher_games_so ON pitcher_games(strikeouts) WHERE strikeouts >= 8;

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    season INTEGER PRIMARY KEY, synced_through TEXT NOT NULL
);
"""


STRIKEOUTS = "('strikeout','strike_out','strikeout_double_play','strikeout_triple_play')"

_BATTER_SQL = f"""
    INSERT OR REPLACE INTO batter_games
    SELECT game_pk, batter_id, MAX(batter),
           COALESCE(SUM(event_type='single'), 0), COALESCE(SUM(event_type='double'), 0),
           COALESCE(SUM(event_type='triple'), 0), COALESCE(SUM(event_type='home_run'), 0),
           COALESCE(SUM(event_type IN ('single','double','triple','home_run')), 0)
    FROM plays WHERE batter_id IS NOT NULL {{where}} GROUP BY game_pk, batter_id"""
_PITCHER_SQL = f"""
    INSERT OR REPLACE INTO pitcher_games
    SELECT game_pk, pitcher_id, MAX(pitcher),
           COALESCE(SUM(event_type IN {STRIKEOUTS}), 0), COALESCE(SUM(event_type IN ('walk','intent_walk')), 0)
    FROM plays WHERE pitcher_id IS NOT NULL {{where}} GROUP BY game_pk, pitcher_id"""


def refresh_game_totals(con: sqlite3.Connection, game_pk: int | None = None) -> None:
    """(Re)build per-game batter/pitcher totals for one game, or for everything."""
    where, params = ("AND game_pk = ?", (game_pk,)) if game_pk is not None else ("", ())
    if game_pk is not None:
        con.execute("DELETE FROM batter_games WHERE game_pk = ?", params)
        con.execute("DELETE FROM pitcher_games WHERE game_pk = ?", params)
    con.execute(_BATTER_SQL.format(where=where), params)
    con.execute(_PITCHER_SQL.format(where=where), params)


def _migrate(con: sqlite3.Connection) -> None:
    """Bring a database built by an older version up to date (columns, derived tables)."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(games)")}
    if cols and "league" not in cols:
        con.execute("ALTER TABLE games ADD COLUMN league TEXT")
    con.execute("DROP INDEX IF EXISTS plays_batter")      # superseded by plays_batter_ev
    con.execute("DROP INDEX IF EXISTS plays_pitcher")
    have_plays = con.execute("SELECT 1 FROM plays LIMIT 1").fetchone()
    if have_plays and not con.execute("SELECT 1 FROM batter_games LIMIT 1").fetchone():
        refresh_game_totals(con)       # one-time, from the plays already stored (no re-crawl)
        con.commit()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-131072")          # ~128 MB page cache
    con.execute("PRAGMA mmap_size=536870912")
    con.executescript(SCHEMA)
    _migrate(con)
    try:
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS plays_fts USING fts5("
            "description, content='plays', content_rowid='rowid')"
        )
    except sqlite3.OperationalError:
        pass  # FTS5 missing: search falls back to LIKE
    return con


def has_fts(con: sqlite3.Connection) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE name='plays_fts'").fetchone()
    return row is not None
