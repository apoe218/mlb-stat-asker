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


def test_traded_player_uses_combined_split_not_first_team():
    cole = {"people": [{"id": 543037, "fullName": "Gerrit Cole", "active": True,
                        "primaryPosition": {"abbreviation": "P"}}]}
    def split(team, sport, k):
        s = {"sport": {"id": sport}, "stat": {"gamesPlayed": 10, "strikeOuts": k, "wins": 1, "losses": 1,
                                              "era": "3.00", "inningsPitched": "50.0", "whip": "1.0"}}
        if team:
            s["team"] = {"name": team}
        return s
    stats = {"stats": [{"splits": [split("Pittsburgh Pirates", 1, 734), split("New York Yankees", 1, 1046),
                                   split(None, 0, 2382)]}]}
    a, _ = asker({"people/search": cole, "people/543037/stats": stats})
    ans = a.ask("Gerrit Cole career strikeouts")
    assert "**2382 strikeouts**" in ans.text
    assert ans.rows[0][5] == "2382"       # SO column of the pitching line


def test_index_resolved_player_gets_position_for_pitching_stats():
    # a pitcher found via the local index has no position; strikeouts must still be pitching
    from mlbasker.entities import Player
    a, client = asker({"people/543037": {"people": [{"primaryPosition": {"abbreviation": "P"}}]},
                       "people/543037/stats": {"stats": [{"splits": [{"sport": {"id": 0},
                                                          "stat": {"gamesPlayed": 1, "strikeOuts": 9}}]}]}})
    from mlbasker.entities import Entities
    from mlbasker.stats import Stats
    from mlbasker.timeparse import parse
    st = Stats(client, Entities(client), a.cutoff)
    st.player_stat("Cole strikeouts this season", Player(543037, "Gerrit Cole"), parse("this season", a.cutoff))
    group = [p["group"] for path, p in client.calls if path.endswith("543037/stats")][0]
    assert group == "pitching"


def test_league_filter_reaches_the_api():
    a, client = asker({"stats": {"stats": [{"splits": [
        {"player": {"fullName": "Pete Alonso"}, "team": {"name": "Baltimore Orioles"}, "stat": {"rbi": 113}}]}]}})
    ans = a.ask("who leads the AL in RBI")
    assert "Pete Alonso" in ans.text and "in the AL" in ans.text
    params = [p for path, p in client.calls if path == "stats"][0]
    assert params["leagueIds"] == 103 and params["endDate"] <= "2026-10-11"


def test_two_teams_means_head_to_head():
    teams = {"teams": [{"id": 147, "name": "New York Yankees", "teamName": "Yankees"},
                       {"id": 111, "name": "Boston Red Sox", "teamName": "Red Sox"}]}
    game = {"officialDate": "2026-08-30", "gameType": "R", "status": {"abstractGameState": "Final"},
            "teams": {"away": {"team": {"id": 111, "name": "Boston Red Sox", "teamName": "Red Sox"}, "score": 1},
                      "home": {"team": {"id": 147, "name": "New York Yankees", "teamName": "Yankees"},
                               "score": 16, "isWinner": True}}}
    client = FakeClient({"seasons": SEASON, "teams": teams,
                         "schedule": {"dates": [{"date": "2026-08-30", "games": [game]}]}})
    ans = Asker(client=client, index=None, now=NOW).ask("Yankees vs Red Sox last game")
    sched = [p for path, p in client.calls if path == "schedule"][0]
    assert sched["teamId"] == 147 and sched["opponentId"] == 111
    assert "16–1" in ans.text
