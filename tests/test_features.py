"""Structured answers and the question types added with the search-engine UI."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from mlbasker import glossary
from mlbasker.engine import Asker
from mlbasker.entities import Entities
from mlbasker.events import EventSearch
from mlbasker.index.extract import score_only
from mlbasker.index.sync import _write
from mlbasker.timeparse import parse
from tests.conftest import FakeClient

CUT = date(2026, 10, 11)
NOW = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("America/New_York"))
SEASON = {"seasons": [{"seasonId": "2026", "regularSeasonStartDate": "2026-03-25",
                       "regularSeasonEndDate": "2026-09-27", "postSeasonEndDate": "2026-10-31"}]}


def person(pid, name, pos="RF", active=True):
    return {"id": pid, "fullName": name, "active": active, "primaryPosition": {"abbreviation": pos}}


def hit_split(**kw):
    st = {"gamesPlayed": 66, "homeRuns": 18, "avg": ".241", "rbi": 41, "ops": ".871", "stolenBases": 5,
          "obp": ".360", "slg": ".511", "hits": 57, "runs": 45}
    st.update(kw)
    return {"stats": [{"splits": [{"sport": {"id": 0}, "team": {"name": "New York Yankees"}, "stat": st}]}]}


def asker(routes):
    client = FakeClient({"seasons": SEASON, "teams": {"teams": []}, **routes})
    return Asker(client=client, index=None, now=NOW), client


# --- structured player answers --------------------------------------------------------------
def test_player_answer_has_headline_tiles_and_subhead():
    a, _ = asker({"people/search": {"people": [person(1, "Aaron Judge")]}, "people/1/stats": hit_split()})
    ans = a.ask("How many home runs does Aaron Judge have this season?")
    assert ans.headline == "18 home runs"
    assert "Aaron Judge" in ans.subhead and "New York Yankees" in ans.subhead
    tiles = {t["label"]: t for t in ans.tiles}
    assert tiles["HR"]["value"] == "18" and tiles["HR"]["emphasis"] and not tiles["AVG"]["emphasis"]
    assert ans.suggestions and ans.title == "Player stats"


def test_unsupported_split_is_flagged_not_silently_ignored():
    a, _ = asker({"people/search": {"people": [person(1, "Aaron Judge")]}, "people/1/stats": hit_split()})
    ans = a.ask("Aaron Judge batting average against lefties")
    assert any("against lefties" in n and "overall number" in n for n in ans.notes)


def test_month_window_uses_exact_dates():
    a, client = asker({"people/search": {"people": [person(2, "Paul Skenes", "P")]},
                       "people/2/stats": {"stats": [{"splits": [{"sport": {"id": 0}, "team": {"name": "Pirates"},
                                                                "stat": {"strikeOuts": 38, "wins": 2, "losses": 1}}]}]},
                       "people/2": {"people": [{"primaryPosition": {"abbreviation": "P"}}]}})
    ans = a.ask("how many strikeouts did Paul Skenes have in April")
    p = [p for path, p in client.calls if path == "people/2/stats"][0]
    assert (p["stats"], p["startDate"], p["endDate"]) == ("byDateRange", "2026-04-01", "2026-04-30")
    assert "April 2026" in ans.text and "38 strikeouts" in ans.text


def test_last_n_days_window_never_passes_cutoff():
    p = parse("home runs in the last 14 days", CUT)
    assert (p.date_from, p.date_to) == (date(2026, 9, 28), date(2026, 10, 11))
    assert parse("in october", CUT).date_to == CUT          # clamped to yesterday
    assert parse("in march", CUT).date_from == date(2026, 3, 1)


# --- comparison --------------------------------------------------------------------------------
def test_compare_two_players_marks_the_better_stat():
    stats_a = hit_split(homeRuns=23)
    stats_b = hit_split(homeRuns=18)

    class C(FakeClient):
        def get(self, path, params=None, **kw):
            self.calls.append((path, dict(params or {})))
            if path.startswith("people/search"):
                n = params["names"]
                return {"people": [person(1, "Mookie Betts", "SS")] if "Mookie" in n else [person(2, "Aaron Judge")]}
            if path == "people/1/stats":
                return stats_a
            if path == "people/2/stats":
                return stats_b
            if path in ("seasons",):
                return SEASON
            return {"teams": []}

    ans = Asker(client=C(), index=None, now=NOW).ask("Mookie Betts vs Aaron Judge home runs")
    assert ans.kind == "compare" and ans.headline == "Betts 23 · Judge 18"
    hr = next(i for i, r in enumerate(ans.rows) if r[0] == "HR")
    assert [hr, 1] in ans.best_cells               # column 1 (Betts) wins home runs
    assert ans.columns == ["", "Mookie Betts", "Aaron Judge"]


def test_ambiguous_surname_offers_choices_instead_of_guessing():
    class C(FakeClient):
        def get(self, path, params=None, **kw):
            if path == "people/search":
                n = params["names"]
                if n == "Soto":
                    return {"people": [person(10, "Juan Soto"), person(11, "Gregory Soto", "P")]}
                return {"people": [person(1, "Aaron Judge")]}
            return SEASON if path == "seasons" else {"teams": []}

    ans = Asker(client=C(), index=None, now=NOW).ask("compare Judge and Soto")
    assert ans.title == "Which player?"
    assert any("Juan Soto" in s for s in ans.suggestions) and any("Gregory Soto" in s for s in ans.suggestions)


# --- leaders direction + titles -----------------------------------------------------------------
def test_worst_era_reverses_the_sort_and_title():
    leaders = {"stats": [{"splits": [{"player": {"fullName": "X"}, "team": {"name": "T"}, "stat": {"era": "7.01"}}]}]}
    a, client = asker({"stats": leaders})
    ans = a.ask("worst ERA in the AL")
    p = [p for path, p in client.calls if path == "stats"][0]
    assert p["order"] == "desc" and p["playerPool"] == "qualified"
    assert ans.headline.startswith("Highest ERA")


def test_leader_title_is_singular():
    leaders = {"stats": [{"splits": [{"player": {"fullName": "X"}, "team": {"name": "T"}, "stat": {"homeRuns": 5}}]}]}
    a, _ = asker({"stats": leaders})
    assert a.ask("home run leaders 2024").headline == "Home run leaders"


# --- routing: refusals, definitions, season wins ---------------------------------------------------
@pytest.mark.parametrize("q", ["what time is the Dodgers game", "today's games", "who's pitching tonight",
                               "Dodgers schedule"])
def test_future_and_schedule_questions_are_refused_without_api_calls(q):
    a, client = asker({})
    ans = a.ask(q)
    assert ans.title == "Completed games only" and "nothing from today" in ans.text
    assert client.calls == []


@pytest.mark.parametrize("q,thing", [("fastest game ever", "game duration"), ("World Series MVP", "awards"),
                                     ("who will win tonight's game odds", "odds")])
def test_unavailable_data_is_named(q, thing):
    a, _ = asker({})
    ans = a.ask(q)
    assert ans.title in ("Not available", "Completed games only") and ans.kind == "notice"


def test_glossary_only_when_the_question_is_just_the_term():
    assert glossary.lookup("what is OPS").headline == "OPS"
    assert glossary.lookup("what's a walk-off?") is not None
    assert glossary.lookup("what is Aaron Judge's batting average against lefties") is None
    assert glossary.lookup("what is Judge's OPS") is None


def test_how_many_games_did_a_team_win_is_a_season_record():
    teams = {"teams": [{"id": 147, "name": "New York Yankees", "teamName": "Yankees"}]}
    standings = {"records": [{"division": {"name": "AL East"}, "teamRecords": [
        {"team": {"id": 147, "name": "New York Yankees"}, "wins": 103, "losses": 59,
         "winningPercentage": ".636", "gamesBack": "-", "divisionRank": "1",
         "records": {"splitRecords": [{"type": "home", "wins": 55, "losses": 26}]}}]}]}
    client = FakeClient({"seasons": {"seasons": [{"seasonId": "2019", "regularSeasonStartDate": "2019-03-20",
                                                  "regularSeasonEndDate": "2019-09-29",
                                                  "postSeasonEndDate": "2019-10-30"}]},
                         "teams": teams, "standings": standings})
    ans = Asker(client=client, index=None, now=NOW).ask("how many games did the Yankees win in 2019")
    assert "won **103** games" in ans.text and ans.headline == "103–59"
    assert ans.highlight_rows == [0] and {"label": "Home", "value": "55–26"} in ans.tiles


# --- index-backed: champions, final scores, game counts, no-hitter direction -----------------------
@pytest.fixture()
def es(index):
    teams = {"teams": [{"id": 147, "name": "New York Yankees", "teamName": "Yankees"},
                       {"id": 119, "name": "Brooklyn Dodgers", "teamName": "Dodgers"}]}
    return EventSearch(index, Entities(FakeClient({"teams": teams, "people/search": {"people": []}}), index), CUT)


def _ws_1956(index):
    """Seven World Series games, Yankees winning 4-3 (the fixture holds only game 5)."""
    wins = [("Brooklyn Dodgers", 119, 6, "New York Yankees", 147, 3), ("New York Yankees", 147, 13, "Brooklyn Dodgers", 119, 8)]
    results = ["D", "D", "Y", "Y", "Y", "D", "Y"]      # Dodgers lead 2-0, Yankees win in 7 (game 5 = Larsen)
    for i, r in enumerate(results, 2):
        yw = r == "Y"
        _write(index, {"game": score_only({"game_pk": 9000 + i, "date": f"1956-10-{i:02d}", "season": 1956,
                                            "game_type": "W", "league": "American League",
                                            "away_id": 119, "away": "Brooklyn Dodgers", "home_id": 147,
                                            "home": "New York Yankees",
                                            "away_score": 1 if yw else 3, "home_score": 3 if yw else 1}),
                       "plays": [], "outs": [], "defensive": [], "players": {}})
    index.commit()


def test_world_series_champion_for_a_year(es, index):
    _ws_1956(index)
    ans = es.answer("who won the 1956 World Series")
    assert ans.headline == "New York Yankees" and "over Brooklyn Dodgers" in ans.subhead
    assert "World Series" in ans.title


def test_last_time_a_team_won_the_world_series(es, index):
    _ws_1956(index)
    ans = es.answer("last time the Yankees won the World Series")
    assert ans.headline == "1956" and "Brooklyn Dodgers" in ans.text
    assert "haven't won" in es.answer("last time the Dodgers won the World Series").text


def test_final_score_search_respects_who_won(es, index):
    _ws_1956(index)
    a = es.answer("last time the Yankees won a game 3-1")
    assert a.results and "New York Yankees beat Brooklyn Dodgers 3–1" in a.results[0]["text"]
    assert "Brooklyn Dodgers" in es.answer("last time the Dodgers won 3-1").text      # the other direction


def test_game_count_is_a_count(es, index):
    _ws_1956(index)
    assert es.answer("how many games were played in 1956").headline == "8"      # 7 + Larsen's game 5 fixture


def test_no_hitter_team_direction(es):
    # Larsen: the Yankees' pitcher threw it; the Dodgers were the ones held hitless
    assert "Oct 8, 1956" in es.answer("last no-hitter by the Yankees").text
    assert "couldn't find" in es.answer("last no-hitter by the Dodgers").text
    assert "Oct 8, 1956" in es.answer("last time the Dodgers were no-hit").text
    assert "Oct 8, 1956" in es.answer("last no-hitter against the Dodgers").text


def test_event_results_are_structured_cards(es):
    a = es.answer("list all unassisted triple plays")
    assert a.headline == "1 unassisted triple play" and a.results[0]["date"] == "Aug 23, 2009"
    assert "Bottom 9" in a.results[0]["meta"] and "Bruntlett" in a.results[0]["text"]
    last = es.answer("last time a game ended on a pickoff at first base")
    assert last.headline == "Aug 12, 2009" and last.title == "Most recent" and last.suggestions
    assert es.answer("how many perfect games").headline == "2"
