from datetime import date

import pytest

from mlbasker.entities import Entities
from mlbasker.events import EventSearch
from tests.conftest import FakeClient

CUTOFF = date(2026, 10, 11)


@pytest.fixture()
def es(index):
    teams = {"teams": [{"id": 120, "name": "Washington Nationals", "teamName": "Nationals"},
                       {"id": 121, "name": "New York Mets", "teamName": "Mets"}]}
    return EventSearch(index, Entities(FakeClient({"teams": teams, "people/search": {"people": []}}), index), CUTOFF)


def test_last_game_ended_on_pickoff_at_first(es):
    a = es.answer("last time a game ended on a pickoff at first base")
    assert "Aug 12, 2009" in a.text and "picks off Nyjer Morgan" in a.text


def test_pickoff_at_third_finds_nothing(es):
    assert "couldn't find" in es.answer("last time a game ended on a pickoff at third base").text


def test_list_unassisted_triple_plays(es):
    a = es.answer("list all occurrences of an unassisted triple play")
    assert len(a.rows) == 1 and "Bruntlett" in a.rows[0][3]


def test_count_walk_off_grand_slams(es):
    assert "**1**" in es.answer("how many walk-off grand slams were there").text


def test_walk_off_is_not_read_as_a_walk(es):
    a = es.answer("last walk-off")
    assert "walk-off win" in a.text and "Sep 30, 2009" in a.text   # not filtered to base-on-balls


def test_perfect_game_vs_no_hitter(es):
    assert "perfect game" in es.answer("when was the last perfect game").text
    assert es.answer("how many no-hitters").text.startswith("There have been **2**")   # Buehrle + Larsen


def test_team_with_walk_off_means_that_team_won_it(es):
    assert "Sep 30, 2009" in es.answer("last time the Nationals had a walk-off").text
    # the Mets were on the losing end of that one: "had a walk-off" must not match
    assert "couldn't find" in es.answer("last time the Mets had a walk-off").text
    assert "Sep 30, 2009" in es.answer("last time the Mets suffered a walk-off against the Nationals").text


def test_season_filter(es):
    # the fixture index only holds 2009, so a 2012 question is "not indexed yet", not "never happened"
    assert "doesn't cover 2012" in es.answer("how many triple plays in 2012").text
    assert "couldn't find" in es.answer("how many triple plays in 2009 involving the Nationals").text


def test_before_play_by_play_explains_the_limit(es):
    a = es.answer("last time a game ended on a pickoff in 1927")
    assert "no play-by-play before 1950" in a.text and "scores, standings, season stats" in a.text
    assert "1927" in a.text


def test_score_level_rows_answer_game_extremes(es, index):
    # a 1927 Negro National League game exists only as a schedule row
    from mlbasker.index.extract import score_only
    from mlbasker.index.sync import _write
    _write(index, {"game": score_only({"game_pk": 1, "date": "1927-04-26", "season": 1927, "game_type": "R",
                                       "league": "Negro National League (I)", "away_id": 1501,
                                       "away": "Cleveland Hornets", "home_id": 1541, "home": "St. Louis Stars",
                                       "away_score": 25, "home_score": 9}),
                   "plays": [], "outs": [], "defensive": [], "players": {}})
    index.commit()
    a = es.answer("highest scoring game ever")
    assert a.rows[0][0] == "Apr 26, 1927" and "Negro National League (I)" in a.rows[0][1]
    assert "using final scores" in a.footnote
    # but a play-level question still only searches play-by-play games
    assert "Apr 26, 1927" not in es.answer("list all walk-offs").text



def test_empty_index_is_explained():
    from mlbasker.index.db import connect
    con = connect(":memory:")
    e = EventSearch(con, Entities(FakeClient({"teams": {"teams": []}}), con), CUTOFF)
    assert e.answer("last time a game ended on a pickoff").kind == "needs_index"


def test_1950s_play_by_play_finds_larsens_perfect_game(es):
    a = es.answer("first perfect game")
    assert "Oct 8, 1956" in a.text and "Brooklyn Dodgers" in a.text and "held hitless" in a.text
    a = es.answer("how many perfect games in the world series")
    assert "**1**" in a.text


def test_pre_1950_notice_even_with_index_present(es):
    assert "no play-by-play before 1950" in es.answer("how many triple plays between 1920 and 1940").text


def test_score_level_search_before_first_game_row_explains(es, index):
    from mlbasker.index.extract import score_only
    from mlbasker.index.sync import _write
    _write(index, {"game": score_only({"game_pk": 1, "date": "1927-04-26", "season": 1927, "game_type": "R",
                                       "away_id": 1, "away": "A", "home_id": 2, "home": "B",
                                       "away_score": 1, "home_score": 2}),
                   "plays": [], "outs": [], "defensive": [], "players": {}})
    index.commit()
    a = es.answer("highest scoring game before 1900")
    assert "only from 1927" in a.text and "back to 1876" in a.text


def test_empty_result_on_a_shallow_index_says_how_to_go_deeper(es):
    # the fixture index starts in 1956, i.e. it was built with a shallow --history
    a = es.answer("how many triple plays in 2009 involving the Nationals")
    assert a.title == "No results" and any("--history full" in n for n in a.notes)
    assert "--history full" in es.answer("how many no-hitters").footnote


def test_parallel_schedule_windows_keep_order_and_dedupe():
    from datetime import date
    from mlbasker.index.sync import final_games
    from tests.test_sync import game
    g = lambda pk, day: game(pk, {"id": 1, "name": "A"}, {"id": 2, "name": "B"}, officialDate=day)
    pages = iter([{"dates": [{"date": "2019-04-02", "games": [g(2, "2019-04-02"), g(1, "2019-04-01")]}]},
                  {"dates": [{"date": "2019-05-02", "games": [g(2, "2019-04-02"), g(3, "2019-05-02")]}]}])

    class C(FakeClient):
        def get(self, path, params=None, **kw):       # windows come back in request order
            self.calls.append((path, dict(params or {})))
            return PAGES[params["startDate"]]
    PAGES = {"2019-04-01": next(pages), "2019-05-01": next(pages)}
    out = final_games(C(), date(2019, 4, 1), date(2019, 5, 30))
    assert [x["game_pk"] for x in out] == [2, 1, 3]
