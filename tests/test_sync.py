from datetime import date

from mlbasker.index.db import connect
from mlbasker.index.sync import _schedule_meta, final_games, sync_range
from tests.conftest import FakeClient


def game(pk, away, home, **kw):
    g = {"gamePk": pk, "officialDate": "1939-08-07", "season": "1939", "gameType": "R",
         "status": {"abstractGameState": "Final"},
         "teams": {"away": {"team": away, "score": 3}, "home": {"team": home, "score": 5}}}
    g.update(kw)
    return g


def test_team_without_a_name_does_not_crash():
    nameless = {"id": 6234}
    meta = _schedule_meta(game(1, {"id": 1, "name": "A"}, nameless), "1939-08-07")
    assert meta["home"] == "Team 6234" and meta["away"] == "A"


def test_malformed_schedule_row_is_skipped_not_fatal():
    ok = game(1, {"id": 1, "name": "A"}, {"id": 2, "name": "B", "league": {"name": "American League"}})
    broken = {"gamePk": 2, "season": "1939", "gameType": "R", "status": {"abstractGameState": "Final"}}
    client = FakeClient({"schedule": {"dates": [{"date": "1939-08-07", "games": [broken, ok]}]}})
    games = final_games(client, date(1939, 8, 1), date(1939, 8, 30))
    assert [g["game_pk"] for g in games] == [1] and games[0]["league"] == "American League"


def test_pre_1950_seasons_become_score_only_rows_without_fetching_feeds():
    ok = game(1, {"id": 1, "name": "A"}, {"id": 2, "name": "B"})
    client = FakeClient({"schedule": {"dates": [{"date": "1939-08-07", "games": [ok]}]}})   # any feed call would raise
    con = connect(":memory:")
    stats = sync_range(con, client, date(1939, 8, 1), date(1939, 8, 30), progress=lambda m: None)
    assert stats["score_only"] == 1 and stats["indexed"] == 0
    row = con.execute("SELECT has_pbp, away_score, home_score, home FROM games").fetchone()
    assert tuple(row) == (0, 3, 5, "B")
    assert not [c for c in client.calls if c[0].startswith("game/")]


# --- history depth: scores for every year, plays only as deep as asked ----------------------------
import json
from pathlib import Path

import pytest

from mlbasker.index.sync import pbp_depth, sync_seasons

FEED = json.loads((Path(__file__).parent / "fixtures" / "game_walkoff_gs.json").read_text())
CUT = date(2026, 10, 11)


@pytest.mark.parametrize("profile,expected", [
    ("recent", 2017), ("modern", 1990), ("full", 1950), ("2005", 2005), ("1900", 1950),
    ("3000", 2026), ("nonsense", 2017), (None, 2017)])
def test_pbp_depth_profiles(profile, expected, monkeypatch):
    monkeypatch.delenv("MLBASKER_HISTORY", raising=False)
    assert pbp_depth(profile, CUT) == expected


def test_pbp_depth_reads_the_environment(monkeypatch):
    monkeypatch.setenv("MLBASKER_HISTORY", "modern")
    assert pbp_depth(None, CUT) == 1990


def test_deeper_history_upgrades_score_only_games_in_place():
    g = game(246561, {"id": 1, "name": "Away"}, {"id": 2, "name": "Home"}, officialDate="2020-08-05", season="2020")
    seasons = {"seasons": [{"seasonId": "2020", "regularSeasonStartDate": "2020-07-23",
                            "regularSeasonEndDate": "2020-09-27", "postSeasonEndDate": "2020-10-28"}]}
    client = FakeClient({"seasons": seasons, "schedule": {"dates": [{"date": "2020-08-05", "games": [g]}]},
                         "game/": FEED})
    con = connect(":memory:")
    feeds = lambda: len([c for c in client.calls if c[0].startswith("game/")])
    q = lambda sql: tuple(con.execute(sql).fetchone())

    sync_seasons(con, client, 2020, 2020, pbp_from=2021, progress=lambda m: None)   # too shallow for 2020
    assert q("SELECT has_pbp, away_score FROM games") == (0, 3) and feeds() == 0
    assert q("SELECT pbp FROM sync_state") == (0,)

    sync_seasons(con, client, 2020, 2020, pbp_from=2020, progress=lambda m: None)   # now go deeper
    assert q("SELECT has_pbp FROM games") == (1,) and q("SELECT COUNT(*) > 50 FROM plays") == (1,)
    assert q("SELECT COUNT(*) FROM games") == (1,) and feeds() == 1                  # upgraded, not duplicated
    assert q("SELECT pbp FROM sync_state") == (1,)

    sync_seasons(con, client, 2020, 2020, pbp_from=2020, progress=lambda m: None)   # nothing left to fetch
    assert feeds() == 1


def test_a_shallower_profile_later_never_discards_plays():
    g = game(246561, {"id": 1, "name": "Away"}, {"id": 2, "name": "Home"}, officialDate="2020-08-05", season="2020")
    seasons = {"seasons": [{"seasonId": "2020", "regularSeasonStartDate": "2020-07-23",
                            "regularSeasonEndDate": "2020-09-27", "postSeasonEndDate": "2020-10-28"}]}
    client = FakeClient({"seasons": seasons, "schedule": {"dates": [{"date": "2020-08-05", "games": [g]}]},
                         "game/": FEED})
    con = connect(":memory:")
    sync_seasons(con, client, 2020, 2020, pbp_from=2020, progress=lambda m: None)
    sync_seasons(con, client, 2020, 2020, pbp_from=2026, progress=lambda m: None)
    assert tuple(con.execute("SELECT has_pbp FROM games").fetchone()) == (1,)
    assert tuple(con.execute("SELECT pbp FROM sync_state").fetchone()) == (1,)
