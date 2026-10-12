"""Short definitions for stat and event names ("what is OPS?")."""
from __future__ import annotations

import re

from .answer import Answer

# term → (expanded name, definition, related search)
TERMS: dict[str, tuple[str, str, str]] = {
    "ops": ("On-base plus slugging", "On-base percentage plus slugging percentage. A single number for how often a hitter reaches base and how much power they have; .800 is good, .900+ is excellent.", "Who leads MLB in OPS?"),
    "obp": ("On-base percentage", "How often a hitter reaches base: (hits + walks + hit-by-pitches) ÷ (at-bats + walks + hit-by-pitches + sacrifice flies).", "Who leads MLB in OBP?"),
    "slg": ("Slugging percentage", "Total bases ÷ at-bats. Weights extra-base hits more heavily than batting average does.", "Who leads MLB in SLG?"),
    "avg": ("Batting average", "Hits ÷ at-bats.", "Who leads MLB in batting average?"),
    "batting average": ("Batting average", "Hits ÷ at-bats.", "Who leads MLB in batting average?"),
    "era": ("Earned run average", "Earned runs allowed per nine innings pitched. Lower is better; under 3.00 is excellent.", "Who leads MLB in ERA?"),
    "whip": ("Walks and hits per inning pitched", "(Walks + hits) ÷ innings pitched. Lower is better; under 1.10 is excellent.", "Who leads MLB in WHIP?"),
    "rbi": ("Runs batted in", "Runs that score because of a batter's plate appearance (hit, walk with bases loaded, sacrifice fly, groundout…), not counting errors or double-play grounders.", "Who leads MLB in RBI?"),
    "babip": ("Batting average on balls in play", "(Hits − home runs) ÷ (at-bats − strikeouts − home runs + sacrifice flies). Mostly measures luck and defense.", ""),
    "fip": ("Fielding independent pitching", "ERA-like estimate built only from what a pitcher controls: strikeouts, walks, hit batters and home runs.", ""),
    "war": ("Wins above replacement", "An estimate of how many wins a player adds over a freely available replacement. Not in this API's feed, so I can't look it up.", ""),
    "k/9": ("Strikeouts per nine innings", "Strikeouts × 9 ÷ innings pitched.", ""),
    "save": ("Save", "Credited to a reliever who finishes a win while protecting a lead of three runs or fewer (or pitching three effective innings).", "Who leads MLB in saves?"),
    "hold": ("Hold", "Credited to a reliever who enters in a save situation, records at least one out, leaves without giving up the lead, and isn't the finisher.", ""),
    "quality start": ("Quality start", "A start of at least six innings with three or fewer earned runs allowed.", ""),
    "walk-off": ("Walk-off", "A home team's winning run scoring in the bottom of the final inning, ending the game immediately.", "How many walk-offs in 2025?"),
    "walk off": ("Walk-off", "A home team's winning run scoring in the bottom of the final inning, ending the game immediately.", "How many walk-offs in 2025?"),
    "no-hitter": ("No-hitter", "A game in which one team gets no hits over at least nine innings. Walks, errors and hit batters can still put runners on.", "List all no-hitters since 2020"),
    "perfect game": ("Perfect game", "A no-hitter where no opposing batter reaches base at all: 27 up, 27 down.", "List all perfect games"),
    "cycle": ("Hitting for the cycle", "A single, double, triple and home run by the same player in one game.", "List all cycles since 2020"),
    "unassisted triple play": ("Unassisted triple play", "Three outs recorded on one play by a single fielder with no assists.", "List all unassisted triple plays"),
    "triple play": ("Triple play", "Three outs recorded on one continuous play.", "List all triple plays since 2020"),
    "balk": ("Balk", "An illegal move by the pitcher with runners on base; every runner advances one base.", ""),
    "wild pitch": ("Wild pitch", "A pitch so far off target that the catcher can't reasonably handle it, letting runners advance.", ""),
    "passed ball": ("Passed ball", "A catcher's mistake on a catchable pitch that lets a runner advance.", ""),
    "sac fly": ("Sacrifice fly", "A fly ball caught for an out that lets a runner score from third. Doesn't count as an at-bat.", ""),
    "sacrifice fly": ("Sacrifice fly", "A fly ball caught for an out that lets a runner score from third. Doesn't count as an at-bat.", ""),
    "fielder's choice": ("Fielder's choice", "The defense retires a different runner instead of the batter, who reaches base safely.", ""),
    "ghost runner": ("Automatic runner", "In extra innings (2020–2022 and since 2023 in the regular season) each half-inning starts with a runner on second base.", ""),
    "grand slam": ("Grand slam", "A home run with the bases loaded, scoring four runs.", "List all walk-off grand slams"),
}

_ASK = re.compile(r"\b(what is|what's|whats|what are|what does|define|definition of|meaning of|explain|stands? for)\b")


def lookup(question: str) -> Answer | None:
    q = question.lower().strip(" ?!.")
    if not _ASK.search(q):
        return None
    rest = _ASK.sub(" ", q)
    rest = re.sub(r"\b(a|an|the|mean|means|in baseball|baseball|stat|stats)\b", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip()
    for term in sorted(TERMS, key=len, reverse=True):
        # "what is OPS" yes; "what is Judge's batting average against lefties" no (that's a stat lookup)
        if re.fullmatch(rf"{re.escape(term)}s?", rest):
            name, definition, related = TERMS[term]
            label = term.upper() if len(term) <= 4 and term.isalpha() or "/" in term else name
            return Answer(f"{name}: {definition}", kind="define", title="Definition",
                          headline=label if label != name else name, subhead=name if label != name else "",
                          notes=[], footnote="", suggestions=[related] if related else [])
    return None
