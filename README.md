# mlb-stat-asker

A personal, single-user web chat for asking MLB questions in plain English. Answers come from the
public **MLB Stats API** (`statsapi.mlb.com`) and are always cut off at **yesterday** (US Eastern) —
a game played today never shows up in an answer.

No API key and no LLM: questions are parsed locally by rules, and answers are written in a short
conversational style. Nothing is hard-coded to a team or player; whoever you name in the question is
looked up from the API.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

python -m mlbasker serve          # http://127.0.0.1:8000
```

On start, `serve` also feeds the history index in the background (see below): a one-time backfill
(roughly 15–30 minutes, ~1 GB on disk), then a top-up every 6 hours. Use `--no-feed` to disable.

Other commands:

```bash
python -m mlbasker ask "last time a game ended on a pickoff at first base"
python -m mlbasker sync --from 2015          # build/resume the index for a range of seasons
python -m mlbasker update                    # index just the last week
python -m mlbasker status                    # cutoff date + index coverage
```

Files live under `data/` (git-ignored): `plays.sqlite` (index) and `cache/` (short-lived API cache).
Override with `MLBASKER_DB` and `MLBASKER_CACHE`.

## What you can ask

**Live lookups** (straight from the API, capped at yesterday)

- *How many home runs does Aaron Judge have this season?* · *Shohei Ohtani's ERA* · *Judge career home runs*
- *Who leads MLB in ERA?* · *Top 5 in stolen bases in 2025* · *Home run leaders for the Cubs*
- *Show me the standings* · *How are the Dodgers doing?*
- *What was the score of the Brewers game last night?* · *Did the Dodgers win on Oct 5?*

**Anything else — history search over play-by-play.** The API has no search endpoint, so the app
indexes every finished game's play-by-play into SQLite and queries that:

- *Last time a game ended on a pickoff at first base*
- *List all unassisted triple plays* · *How many triple plays since 2009?*
- *Last walk-off grand slam* · *Last time the Yankees had a walk-off in the postseason*
- *When was the last perfect game?* · *How many no-hitters in 2008?* · *Who hit for the cycle in 2009?*
- *Longest game ever* · *Most strikeouts in a single game* · *How many inside-the-park home runs in 2008?*
- Quote any phrase to search play descriptions: `plays that say "bunt single"`

Add filters in the question: a team, a player, a year (`in 2019`, `since 2015`, `between 2010 and
2014`), or a round (`in the postseason`, `in the World Series`).

## How it works

```
question ─► engine.Asker ─┬─► events.EventSearch ─► index/catalog.py ─► SQLite play index
                          └─► stats.Stats ───────► MLB Stats API (startDate/endDate ≤ cutoff)
```

- `cutoff.py` – yesterday in Eastern time; every API call passes explicit end dates, and the index
  only ever ingests games on or before it.
- `entities.py` – finds teams and players in free text (player names are resolved with the API's
  people search, plus the local index).
- `index/` – `sync.py` crawls the schedule and each game feed (trimmed with `fields=`), `extract.py`
  turns it into rows (plays, every out with its cause, multi-out defensive plays with fielder
  credits, per-game hits/baserunners, final out, walk-offs), `catalog.py` maps phrases to SQL.
- Unassisted triple plays are detected from fielder credits (three outs on one play, one fielder,
  no assists), not from the description text.

### Honest limits

- **History starts in 2008** — the earliest season with reliable play-by-play in the live feed.
  "First time ever" means first in the index. Answers say which seasons they searched, and warn
  if the index is behind.
- **The question parser is rule-based.** It covers the event types in `index/catalog.py` plus
  quoted-phrase text search. Unrecognised questions get a help message rather than a guess. Adding
  an event is one matcher plus one SQL builder.
- No-hitters count teams held hitless over 9+ innings (including combined no-hitters); rain-shortened
  and 7-inning games don't count.
- Statcast metrics (exit velocity, spin rate, …) aren't in this API and aren't covered.
- The Stats API is unofficial and undocumented; endpoints can change. MLB's data is copyrighted —
  this project is for private, personal use only.

## Tests

```bash
pytest
```

Index tests run against recorded real game feeds in `tests/fixtures/` (Bruntlett's unassisted
triple play, a game-ending pickoff, a walk-off grand slam, Buehrle's perfect game), so they need no
network.
