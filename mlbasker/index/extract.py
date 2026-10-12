"""Turn one game feed into index rows."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

# Trimming the feed with `fields=` shrinks a ~750KB game to ~70KB.
FEED_FIELDS = (
    "gamePk,gameData,datetime,officialDate,status,abstractGameState,teams,away,home,id,name,venue,"
    "liveData,plays,allPlays,result,type,event,eventType,description,isOut,awayScore,homeScore,"
    "about,atBatIndex,halfInning,inning,isScoringPlay,matchup,batter,pitcher,fullName,runners,"
    "movement,originBase,start,end,outBase,outNumber,details,runner,movementReason,playIndex,"
    "credits,player,position,abbreviation,credit,linescore,innings,num"
)

HIT_EVENTS = {"single", "double", "triple", "home_run"}


def extract_game(feed: dict, meta: dict) -> dict[str, Any] | None:
    """`meta` comes from the schedule: game_pk, date, season, game_type."""
    gd = feed.get("gameData", {})
    teams = gd.get("teams", {})
    away, home = teams.get("away", {}), teams.get("home", {})
    plays_raw = feed.get("liveData", {}).get("plays", {}).get("allPlays", [])

    game = {
        "game_pk": meta["game_pk"], "date": meta["date"], "season": meta["season"],
        "game_type": meta["game_type"],
        "away_id": away.get("id"), "away": away.get("name"),
        "home_id": home.get("id"), "home": home.get("name"),
        "venue": (gd.get("venue") or {}).get("name"),
        "has_pbp": 1 if plays_raw else 0,
        "away_score": None, "home_score": None, "innings": None,
        "away_hits": 0, "home_hits": 0, "away_reached": 0, "home_reached": 0,
        "away_innings_batted": 0, "home_innings_batted": 0,
        "final_out_event": None, "final_out_desc": None,
        "walk_off": 0, "walk_off_event": None,
    }
    out = {"game": game, "plays": [], "outs": [], "defensive": [], "players": {}}
    if not plays_raw:
        return out

    innings_batted = {"top": set(), "bottom": set()}
    for p in plays_raw:
        res, about = p.get("result", {}), p.get("about", {})
        m = p.get("matchup", {})
        batter, pitcher = m.get("batter", {}), m.get("pitcher", {})
        half = about.get("halfInning")
        idx = about.get("atBatIndex", p.get("atBatIndex"))
        side = "away" if half == "top" else "home"
        if half in innings_batted and about.get("inning") is not None:
            innings_batted[half].add(about["inning"])
        for who in (batter, pitcher):
            if who.get("id"):
                out["players"][who["id"]] = who.get("fullName")

        out["plays"].append({
            "game_pk": meta["game_pk"], "idx": idx, "inning": about.get("inning"), "half": half,
            "event_type": res.get("eventType"), "event": res.get("event"),
            "description": res.get("description"),
            "batter_id": batter.get("id"), "batter": batter.get("fullName"),
            "pitcher_id": pitcher.get("id"), "pitcher": pitcher.get("fullName"),
            "away_score": res.get("awayScore"), "home_score": res.get("homeScore"),
            "is_scoring": 1 if about.get("isScoringPlay") else 0, "is_last": 0,
        })

        if res.get("eventType") in HIT_EVENTS:
            game[f"{side}_hits"] += 1

        # group outs by the on-field play that produced them
        by_play: dict[Any, list[dict]] = defaultdict(list)
        batter_reached = False
        for r in p.get("runners", []):
            mv, det = r.get("movement", {}), r.get("details", {})
            runner = det.get("runner", {})
            if (runner.get("id") == batter.get("id") and mv.get("originBase") is None
                    and not mv.get("isOut") and (mv.get("end") or mv.get("start"))):
                batter_reached = True
            if mv.get("isOut"):
                out["outs"].append({
                    "game_pk": meta["game_pk"], "play_idx": idx,
                    "runner_id": runner.get("id"), "runner": runner.get("fullName"),
                    "event_type": det.get("eventType"), "movement_reason": det.get("movementReason"),
                    "out_base": mv.get("outBase"), "out_number": mv.get("outNumber"),
                })
                by_play[det.get("playIndex")].append(r)
        if batter_reached:
            game[f"{side}_reached"] += 1

        for play_index, rs in by_play.items():
            if len(rs) < 2:
                continue
            credits = [c for r in rs for c in r.get("credits", [])]
            putouts = [c for c in credits if c.get("credit") == "f_putout"]
            assists = [c for c in credits if c.get("credit") == "f_assist"]
            fielders = {c.get("player", {}).get("id") for c in credits if c.get("player")}
            out["defensive"].append({
                "game_pk": meta["game_pk"], "play_idx": idx,
                "play_index": play_index if play_index is not None else -1,
                "outs": len(rs), "putouts": len(putouts), "assists": len(assists),
                "fielders": len(fielders),
                "unassisted": 1 if (not assists and len(putouts) == len(rs) and len(fielders) == 1) else 0,
            })

    last = out["plays"][-1]
    last["is_last"] = 1
    game["away_score"], game["home_score"] = last["away_score"], last["home_score"]
    game["away_innings_batted"] = len(innings_batted["top"])
    game["home_innings_batted"] = len(innings_batted["bottom"])
    li = feed.get("liveData", {}).get("linescore", {}).get("innings")
    game["innings"] = len(li) if li else max((p["inning"] or 0) for p in out["plays"])

    last_outs = [o for o in out["outs"] if o["play_idx"] == last["idx"]]
    if last_outs:
        final = max(last_outs, key=lambda o: o["out_number"] or 0)
        game["final_out_event"] = final["event_type"]
    else:
        game["final_out_event"] = last["event_type"]
    game["final_out_desc"] = last["description"]

    if (last["half"] == "bottom" and last["is_scoring"]
            and (game["home_score"] or 0) > (game["away_score"] or 0)):
        game["walk_off"] = 1
        game["walk_off_event"] = last["event_type"]
    return out
