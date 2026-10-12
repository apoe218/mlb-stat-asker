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
    a, _ = asker({"people/search": cole, "people/543037/stats": stats,
                  "people/543037": {"people": [{"active": True, "mlbDebutDate": "2011-04-11"}]}})
    ans = a.ask("Gerrit Cole career strikeouts")
    assert "**2382 strikeouts**" in ans.text
    assert ans.rows[0][5] == "2382"       # SO column of the pitching line


def test_index_resolved_player_gets_position_for_pitching_stats():
    # a pitcher found via the local index has no position; strikeouts must still be pitching
    from mlbasker.entities import Player
    a, client = asker({"people/543037/stats": {"stats": [{"splits": [{"sport": {"id": 0},
                                                          "stat": {"gamesPlayed": 1, "strikeOuts": 9}}]}]},
                       "people/543037": {"people": [{"primaryPosition": {"abbreviation": "P"}}]}})
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


def test_old_dates_with_explicit_year():
    a, _ = asker({})
    from mlbasker.entities import Entities
    from mlbasker.stats import Stats
    from datetime import date
    st = Stats(FakeClient(), Entities(FakeClient()), a.cutoff)
    assert st._target_date("score on October 8, 1956") == date(1956, 10, 8)
    assert st._target_date("score on 10/8/1956") == date(1956, 10, 8)
    assert st._target_date("score on 1927-05-01") == date(1927, 5, 1)
    assert st._target_date("score on Oct 3") == date(2026, 10, 3)


def test_standings_before_divisions_use_league_name():
    teams = {"teams": [{"id": 147, "name": "New York Yankees", "teamName": "Yankees"}]}
    standings = {"records": [{"league": {"id": 103}, "teamRecords": [
        {"team": {"id": 147, "name": "New York Yankees"}, "wins": 110, "losses": 44,
         "winningPercentage": ".714", "gamesBack": "-", "leagueRank": "1"}]}]}
    client = FakeClient({"seasons": {"seasons": [{"seasonId": "1927", "regularSeasonStartDate": "1927-04-12",
                                                  "regularSeasonEndDate": "1927-10-02",
                                                  "postSeasonEndDate": "1927-10-08"}]},
                         "teams": teams, "standings": standings})
    ans = Asker(client=client, index=None, now=NOW).ask("How did the Yankees finish in 1927?")
    assert "**110–44**" in ans.text and "American League" in ans.text and "#1" in ans.text


def test_historic_team_names_resolve_by_season():
    from mlbasker.entities import Entities
    cur = {"teams": [{"id": 119, "name": "Los Angeles Dodgers", "teamName": "Dodgers"}]}
    era = {"teams": [{"id": 119, "name": "Brooklyn Dodgers", "teamName": "Dodgers"}]}

    class Client(FakeClient):
        def get(self, path, params=None, **kw):
            self.calls.append((path, dict(params or {})))
            return era if (params or {}).get("season") == 1955 else cur

    teams, _ = Entities(Client()).find_teams("how did the Brooklyn Dodgers do in 1955", seasons=(1955, 1955))
    assert [(t.id, t.name) for t in teams] == [(119, "Brooklyn Dodgers")]
    # without a season, the static era-name table still finds the franchise
    teams, _ = Entities(Client()).find_teams("Brooklyn Dodgers")
    assert teams and teams[0].id == 119


def test_led_the_league_is_a_leaders_question():
    a, client = asker({"stats": {"stats": [{"splits": [
        {"player": {"fullName": "Roger Maris"}, "team": {"name": "New York Yankees"}, "stat": {"homeRuns": 61}}]}]}})
    ans = a.ask("who led the league in home runs in 1961")
    assert "Roger Maris" in ans.text and "61" in ans.text
    assert not any("Before 1950" in n for n in ans.notes)
    # a 1927 season question carries the era caveat
    a, _ = asker({"stats": {"stats": [{"splits": [
        {"player": {"fullName": "Babe Ruth"}, "team": {"name": "New York Yankees"}, "stat": {"homeRuns": 60}}]}]}})
    assert any("Before 1950" in n for n in a.ask("who led the league in home runs in 1927").notes)


def _stat_params(client, suffix="/stats"):
    return [p for path, p in client.calls if path.endswith(suffix)]


def test_finished_season_uses_exact_season_totals_not_by_date():
    ruth = {"people": [{"id": 121578, "fullName": "Babe Ruth", "active": False,
                        "primaryPosition": {"abbreviation": "RF"}}]}
    stats = {"stats": [{"splits": [{"sport": {"id": 1}, "team": {"name": "New York Yankees"},
                                    "stat": {"gamesPlayed": 151, "homeRuns": 60}}]}]}
    a, client = asker({"people/search": ruth, "people/121578/stats": stats})
    ans = a.ask("How many home runs did Babe Ruth hit in 1927?")
    p = _stat_params(client)[0]
    assert p["stats"] == "season" and p["season"] == 1927 and "startDate" not in p
    assert "had **60 home runs**" in ans.text and any("Before 1950" in n for n in ans.notes)


def test_retired_or_19th_century_career_uses_career_totals():
    anson = {"people": [{"id": 1, "fullName": "Cap Anson", "active": False,
                         "primaryPosition": {"abbreviation": "1B"}}]}
    stats = {"stats": [{"splits": [{"sport": {"id": 1}, "stat": {"gamesPlayed": 2276, "hits": 3435}}]}]}
    a, client = asker({"people/search": anson, "people/1/stats": stats,
                       "people/1": {"people": [{"active": False, "mlbDebutDate": "1876-05-06"}]}})
    ans = a.ask("Cap Anson career hits")
    assert _stat_params(client)[0]["stats"] == "career" and "3435" in ans.text


def test_active_players_career_stays_capped_at_yesterday():
    judge = {"people": [{"id": 592450, "fullName": "Aaron Judge", "active": True,
                         "primaryPosition": {"abbreviation": "RF"}}]}
    stats = {"stats": [{"splits": [{"sport": {"id": 0}, "stat": {"homeRuns": 386}}]}]}
    a, client = asker({"people/search": judge, "people/592450/stats": stats,
                       "people/592450": {"people": [{"active": True, "mlbDebutDate": "2016-08-13"}]}})
    a.ask("Aaron Judge career home runs")
    p = _stat_params(client)[0]
    assert p["stats"] == "byDateRange" and p["endDate"] == "2026-10-11"   # never includes today


def test_game_scores_before_1901_explain_the_limit():
    a, client = asker({})
    ans = a.ask("score of the Giants game on June 1, 1890")
    assert "only from 1901" in ans.text and not [c for c in client.calls if c[0] == "schedule"]
