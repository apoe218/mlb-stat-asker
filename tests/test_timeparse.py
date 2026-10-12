from datetime import date

from mlbasker.timeparse import parse, season_of

CUT = date(2026, 10, 11)


def test_modes():
    assert parse("last time X happened", CUT).mode == "last"
    assert parse("first time ever", CUT).mode == "first"
    assert parse("how many times", CUT).mode == "count"
    assert parse("list all of them", CUT).mode == "all"
    assert parse("show some", CUT).mode == "list"


def test_years():
    assert (parse("in 2019", CUT).season_from, parse("in 2019", CUT).season_to) == (2019, 2019)
    p = parse("since 2015", CUT)
    assert (p.season_from, p.season_to) == (2015, None)
    p = parse("between 2010 and 2014", CUT)
    assert (p.season_from, p.season_to) == (2010, 2014)
    p = parse("before 2000", CUT)
    assert p.season_to == 1999


def test_relative_seasons():
    assert parse("this season", CUT).season_from == 2026
    assert parse("last year", CUT).season_from == 2025


def test_game_types():
    assert parse("in the world series", CUT).game_types == ["W"]
    assert parse("in the playoffs", CUT).game_types == ["F", "D", "L", "W"]
    assert parse("regular season", CUT).game_types == ["R"]


def test_season_of_offseason():
    assert season_of(date(2027, 1, 15)) == 2026


def test_bare_last_and_first():
    assert parse("last walk-off", CUT).mode == "last"
    assert parse("first no-hitter", CUT).mode == "first"
    assert parse("home runs last season", CUT).mode == "list"
    assert parse("game that ended on a pickoff at first base", CUT).mode == "list"
