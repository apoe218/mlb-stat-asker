def test_unassisted_triple_play_detected(extracted):
    ex = extracted["unassisted_tp"]
    tp = [d for d in ex["defensive"] if d["outs"] == 3]
    assert len(tp) == 1 and tp[0]["unassisted"] == 1 and tp[0]["assists"] == 0


def test_game_ended_on_pickoff_at_first(extracted):
    g = extracted["pickoff_1b"]["game"]
    assert g["final_out_event"] == "pickoff_1b"
    assert "picks off" in g["final_out_desc"]


def test_walk_off_grand_slam(extracted):
    g = extracted["walkoff_gs"]["game"]
    assert g["walk_off"] == 1 and g["walk_off_event"] == "home_run"
    assert "grand slam" in g["final_out_desc"]


def test_perfect_game_has_no_baserunners(extracted):
    g = extracted["perfect"]["game"]
    assert g["away_hits"] == 0 and g["away_reached"] == 0 and g["away_innings_batted"] >= 9
    assert g["home_hits"] > 0


def test_last_play_flagged_once(extracted):
    for ex in extracted.values():
        assert sum(p["is_last"] for p in ex["plays"]) == 1


def test_larsen_perfect_game_from_1956_feed(extracted):
    g = extracted["larsen_1956"]["game"]
    assert (g["away_hits"], g["away_reached"], g["away_innings_batted"]) == (0, 0, 9)
    assert g["final_out_event"] == "strikeout" and "Dale Mitchell" in g["final_out_desc"]
    assert (g["away_score"], g["home_score"]) == (0, 2)      # from the schedule meta
