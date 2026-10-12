"""Routing and the yesterday cutoff, with a stubbed API."""
from datetime import datetime
from zoneinfo import ZoneInfo

from mlbasker.engine import Asker
from tests.conftest import FakeClient

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("America/New_York"))   # cutoff = Oct 11

SEASON = {"seasons": [{"seasonId": "2026", "regularSeasonStartDate": "2026-03-25",
                       "regularSeasonEndDate": "2026-09-27", "postSeasonEndDate": "2026-10-31"}]}
JUDGE = {"people": [{"id": 592450, "fullName": "Aaron Judge", "active": True,
                     "primaryPosition": {"abbreviation": "RF"}}]}
STATS = {"stats": [{"splits": [{"team": {"name": "New York Yankees"},
                                "stat": {"gamesPlayed": 66, "homeRuns": 18, "avg": ".241", "rbi": 41,
                                         "ops": ".871", "stolenBases": 5}}]}]}
TEAMS = {"teams": [{"id": 147, "name": "New York Yankees", "teamName": "Yankees"}]}


def asker(routes):
    client = FakeClient({"seasons": SEASON, "teams": TEAMS, **routes})
    return Asker(client=client, index=None, now=NOW), client


def test_player_stat_is_capped_at_yesterday():
    a, client = asker({"people/search": JUDGE, "people/592450/stats": STATS})
    ans = a.ask("How many home runs does Aaron Judge have this season?")
    assert "**18 home runs**" in ans.text and "Oct 11, 2026" in ans.footnote
    stat_calls = [p for path, p in client.calls if path.endswith("/stats")]
    assert stat_calls and all(p["endDate"] <= "2026-10-11" for p in stat_calls)


def test_never_answers_about_today():
    a, client = asker({})
    ans = a.ask("what's the score tonight")
    assert "nothing from today" in ans.text
    assert not [c for c in client.calls if c[0] == "schedule"]


def test_future_date_refused():
    a, _ = asker({})
    ans = a.ask("what was the Yankees score on 10/12/2026")
    assert "past my cutoff" in ans.text


def test_history_question_without_index_explains():
    a, _ = asker({})
    assert a.ask("last time a game ended on a pickoff at first base").kind == "needs_index"


def test_unknown_question_shows_help():
    a, _ = asker({"people/search": {"people": []}})
    assert a.ask("tell me a joke").kind == "help"
