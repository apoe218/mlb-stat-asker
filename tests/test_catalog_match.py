from mlbasker.index.catalog import match


def kind(q):
    m = match(q.lower())
    return m[0] if m else None


def test_game_end_families():
    assert match("last time a game ended on a pickoff at first base") == ("game_end", {"family": "pickoff", "base": "1b"})
    assert kind("game ended on a strikeout") == "game_end"
    assert match("games that ended on a double play")[1]["family"] == "double_play"


def test_game_ending_scoring_plays_become_walk_offs():
    assert match("how many games ended on a walk")[1]["label"] == "walk-off walk"
    assert match("first time a game ended on a balk")[1]["label"] == "walk-off balk"
    assert match("game-ending home run")[1]["label"] == "walk-off home run"


def test_unknown_game_ending_is_not_guessed():
    assert match("last time a game ended on a mystery event") is None


def test_walk_off_word_is_not_a_walk():
    assert match("last walk-off")[1]["how"] is None
    assert match("walk-off home run")[1]["label"] == "walk-off home run"


def test_threshold_single_game_records():
    assert match("last time a pitcher struck out 20") == ("single_game_record", {"stat": "strikeouts", "min": 20})
    assert match("games where a player hit 4 home runs in a game")[1] == {"stat": "home runs", "min": 4}


def test_everyday_events_stay_player_stats():
    assert match("how many home runs does aaron judge have") is None
    assert match("last time aaron judge hit a home run")[0] == "play_event"
    assert match("how many inside-the-park home runs in 2008")[1]["label"] == "inside-the-park home run"


def test_multi_out_vs_game_ending():
    assert kind("list all unassisted triple plays") == "multi_out"
    assert kind("game-ending double plays") == "game_end"
