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
    away_id INTEGER, away TEXT, home_id INTEGER, home TEXT,
    away_score INTEGER, home_score INTEGER,
    innings INTEGER,
    venue TEXT,
    has_pbp INTEGER NOT NULL DEFAULT 1,
    away_hits INTEGER, home_hits INTEGER,
    away_reached INTEGER, home_reached INTEGER,       -- batters who reached base
    away_innings_batted INTEGER, home_innings_batted INTEGER,
    final_out_event TEXT,            -- eventType of the game's last out
    final_out_desc TEXT,
    walk_off INTEGER NOT NULL DEFAULT 0,
    walk_off_event TEXT
);
CREATE INDEX IF NOT EXISTS games_date ON games(date);

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
CREATE INDEX IF NOT EXISTS plays_batter ON plays(batter_id);
CREATE INDEX IF NOT EXISTS plays_pitcher ON plays(pitcher_id);

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

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    season INTEGER PRIMARY KEY, synced_through TEXT NOT NULL
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
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
