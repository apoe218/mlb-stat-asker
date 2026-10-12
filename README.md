# mlb-stat-asker

A personal, single-user, search-engine-style page for asking MLB questions in plain English. Answers come from the
public **MLB Stats API** (`statsapi.mlb.com`) and are always cut off at **yesterday** (US Eastern) —
a game played today never shows up in an answer.

No API key and no LLM: questions are parsed locally by rules, and answers are written in a short
conversational style. Nothing is hard-coded to a team or player; whoever you name in the question is
looked up from the API.

## Run it

You need Python 3.11+ and nothing else: no compiler, no database server, no API key.

```bash
git clone https://github.com/apoe218/mlb-stat-asker && cd mlb-stat-asker
./run.sh                 # macOS / Linux          (Windows: run.bat)
```

That one command creates a private environment (first time only, a few seconds: every dependency is
a prebuilt wheel, so nothing compiles), starts the app and opens it in your browser at
http://127.0.0.1:8000.

**Live questions work immediately**: player stats, comparisons, leaders, standings and scores
come straight from the MLB API. **History search** (game endings, triple plays, walk-offs,
champions, records…) is answered from a local index that builds in the background while you use the app:

| `--history` | Play-by-play indexed | First-run build | Disk |
|---|---|---|---|
| `recent` (default) | last 10 seasons | **~6.5 minutes** (measured, incl. all-time scores) | ~0.7 GB |
| `modern` | 1990 on | ~20 minutes (estimated) | ~2 GB |
| `full` | 1950 on | ~40–60 minutes (estimated) | ~4 GB |
| a year, e.g. `2005` | that year on | in between | |

Final scores for **every** year (1901 on) are always included (about a minute) and power champions,
records and "highest-scoring game". The newest season is searchable within about 30 seconds, and
the page footer shows how far back the plays go. If a search comes up empty because it predates what's
indexed, the answer says so and how to go deeper. Go deeper whenever you like; it fills in what's
missing without re-downloading what you have:

```bash
./run.sh --history full          # or: python -m mlbasker serve --history full
```

The index tops itself up every 6 hours while the app is running. Use `--no-feed` to stop all
background downloading.

Other commands (activate `.venv` first, or use `.venv/bin/python`):

```bash
python -m mlbasker ask "last time a game ended on a pickoff at first base"
python -m mlbasker sync --history modern     # build the index without starting the web app
python -m mlbasker update                    # index just the last week
python -m mlbasker status                    # cutoff date, history depth, index coverage
```

Files live under `data/` (git-ignored): `plays.sqlite` (index) and `cache/` (short-lived API cache).
Override with `MLBASKER_DB` and `MLBASKER_CACHE`. Set `MLBASKER_HISTORY` to make a depth the default.

### In GitHub Codespaces

Open the repo → **Code → Codespaces → Create codespace**. The included `.devcontainer` installs
everything and starts the app; open the forwarded port 8000 (private to you). A codespace sleeps
when idle, which pauses the background index build until you return; live questions always work.

### Upgrading an older index

The first start after upgrading an index built by an earlier version runs a one-time migration
(about 30 s on a 4 GB index) that adds indexes and small per-game tables; history questions then
answer in well under a second.

## The interface

A single search box, like a search engine. Answers appear as a card: the answer itself in large
type, a one-line description, stat tiles or a table, and any caveats as a highlighted note. History
searches list each matching game as a result with the date, matchup and the play. **Related
searches** under each answer let you pivot (last → first → list all → how many).

- Every search has its own URL (`/?q=…`), so back/forward and bookmarks work.
- Autocomplete mixes your recent searches with example questions; `↑ ↓ Enter` to pick, `/` to
  focus the box from anywhere, `Esc` to clear.
- Light and dark themes follow your system; it works on a phone.

## What you can ask

**Live lookups** (straight from the API, capped at yesterday)

- *How many home runs does Aaron Judge have this season?* · *Shohei Ohtani's ERA* · *Judge career home runs*
- *Judge vs Ohtani* (side-by-side, better stat marked) · *Paul Skenes strikeouts in April* · *Judge HR last 14 days*
- *Who leads MLB in ERA?* · *Top 5 in stolen bases in 2025* · *Home run leaders for the Cubs*
- *Show me the standings* · *How are the Dodgers doing?*
- *What was the score of the Brewers game last night?* · *Did the Dodgers win on Oct 5?* · *Yankees vs Red Sox* (last meetings)
- *Who won the 1956 World Series?* · *Last time the Cubs won the World Series* · *How many games did the Yankees win in 2019?*
- *What is OPS?* (short definitions of common stats and terms)

**Anything else — history search.** The API has no search endpoint, so the app indexes every
finished game into SQLite and queries that:

- *Last time a game ended on a pickoff at first base*
- *List all unassisted triple plays* · *How many triple plays since 1990?*
- *Last walk-off grand slam* · *Last time the Yankees had a walk-off in the postseason*
- *When was the last perfect game?* · *How many no-hitters in 2012?* · *Who hit for the cycle in 2009?*
- *Longest game since 1950* · *Highest-scoring game ever* · *Most strikeouts in a single game*
- *Last time the Red Sox won a game 1-0* · *How many shutouts in 2019?* · *How many games were played in 1981?*
- Quote any phrase to search play descriptions: `plays that say "bunt single"`

Add filters in the question: a team (including old names like *Brooklyn Dodgers*), a player, a year
(`in 1961`, `since 1990`, `between 1950 and 1960`), or a round (`in the postseason`, `in the World Series`).

### What the API has, by era

| Era | What I can answer |
|---|---|
| **1876 → today** | Standings, season and career stats, and leaders (the API's 19th-century records). |
| **1901 → today** | Plus game scores and schedules, and score-level history: highest-scoring game, biggest blowout… This includes the Negro Leagues (1920–48), which MLB counts as major leagues since 2020; those games are labelled with their league. There is no game-level data before 1901. |
| **1950 → today** | Everything above plus play-by-play: how games ended, triple plays, walk-offs, no-hitters and perfect games, cycles, single-game records, phrase search. (About 85% of 1950–51 games have plays; later years are essentially complete.) |
| **Before 1950** | No play-by-play exists in the API, so play-level questions are answered with an explanation of that limit instead of a guess. Some stats weren't officially tracked in early eras and totals can be incomplete; those answers say so. |

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

- Questions I don't have data for (game duration, awards, contracts, trades, injuries, odds, advanced
  metrics like WAR) get a plain "not available" card, and "today / tonight / schedule" questions are
  declined because the cutoff is yesterday. Splits (vs lefties, at home, with runners…) aren't
  supported: you get the overall number plus a note saying so.

- **Play-by-play starts in 1950** (verified by sampling: 1949 and earlier have none). "First time
  ever" for a play-level event therefore means first since 1950, and the answer says so. Every
  answer states which seasons it searched and warns if the index is behind or still building.
- Longest-game questions need innings, which the API records only from 1950; earlier games count
  for runs-based questions only.
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
pip install -r requirements-dev.txt
pytest
```

Index tests run against recorded real game feeds in `tests/fixtures/` (Bruntlett's unassisted
triple play, a game-ending pickoff, a walk-off grand slam, Buehrle's perfect game, plus 1950s
feeds), so they need no network.
