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
    assert es.answer("how many no-hitters").text.startswith("There has been **1**")


def test_team_with_walk_off_means_that_team_won_it(es):
    assert "Sep 30, 2009" in es.answer("last time the Nationals had a walk-off").text
    # the Mets were on the losing end of that one: "had a walk-off" must not match
    assert "couldn't find" in es.answer("last time the Mets had a walk-off").text
    assert "Sep 30, 2009" in es.answer("last time the Mets suffered a walk-off against the Nationals").text


def test_season_filter(es):
    assert "couldn't find" in es.answer("how many triple plays in 2012").text


def test_empty_index_is_explained():
    from mlbasker.index.db import connect
    con = connect(":memory:")
    e = EventSearch(con, Entities(FakeClient({"teams": {"teams": []}}), con), CUTOFF)
    assert e.answer("last time a game ended on a pickoff").kind == "needs_index"
