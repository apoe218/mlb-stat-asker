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
