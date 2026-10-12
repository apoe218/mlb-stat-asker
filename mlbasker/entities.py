"""Find teams and players mentioned in free text."""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass

from .client import MlbClient, TTL_LONG

# Older / alternate names → franchise id (ids are stable per franchise in the API)
HISTORIC_TEAMS = {   # era names → franchise id (ids never change for a franchise)
    "brooklyn dodgers": 119, "brooklyn robins": 119, "brooklyn superbas": 119, "new york giants": 137,
    "boston braves": 144, "milwaukee braves": 144, "philadelphia athletics": 133,
    "kansas city athletics": 133, "st louis browns": 110, "montreal expos": 120, "seattle pilots": 158,
    "houston colt 45s": 117, "california angels": 108, "anaheim angels": 108,
    "los angeles angels of anaheim": 108, "cincinnati redlegs": 113, "florida marlins": 146,
    "tampa bay devil rays": 139, "cleveland indians": 114, "cleveland naps": 114,
}

TEAM_EXTRAS = {
    "indians": 114, "cleveland indians": 114, "devil rays": 139, "tampa bay devil rays": 139,
    "expos": 120, "montreal expos": 120, "florida marlins": 146, "athletics": 133, "a's": 133,
    "oakland a's": 133, "d-backs": 109, "dbacks": 109, "diamondbacks": 109, "sox": None,
    "bo sox": 111, "yanks": 147, "halos": 108, "bucs": 134, "cards": 138, "stros": 117,
    "mariners": 136, "jays": 141, "nats": 120, "padres": 135,
}

STOP_CAPS = {
    "I", "What", "Who", "When", "Where", "Which", "How", "Why", "Is", "Are", "Was", "Were", "Do",
    "Does", "Did", "Has", "Have", "Had", "Can", "Could", "Show", "Tell", "List", "Give", "Find",
    "The", "A", "An", "In", "On", "At", "Of", "For", "And", "Or", "MLB", "World", "Series",
    "National", "American", "League", "Opening", "Day", "All", "Star", "Game", "Games", "Last",
    "First", "Most", "Home", "Run", "Runs", "Hit", "Hits", "Walk", "Wild", "Card", "Division",
    "East", "West", "Central", "NL", "AL", "ERA", "RBI", "OPS", "WHIP", "April", "May", "June",
    "July", "August", "September", "October", "March", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday", "Sunday", "Yesterday", "Today", "This", "That", "These",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower().replace(".", "").replace("’", "'")).strip()


@dataclass(frozen=True)
class Team:
    id: int
    name: str
    short: str


@dataclass(frozen=True)
class Player:
    id: int
    name: str
    position: str | None = None
    active: bool | None = None


class Entities:
    def __init__(self, client: MlbClient, index: sqlite3.Connection | None = None):
        self.client, self.index = client, index
        self._teams: list[Team] | None = None
        self._aliases: list[tuple[str, Team]] | None = None
        self._pcache: dict[str, tuple[int, str]] | None = None
        self._era: dict[int, list[Team]] = {}

    # --- teams ------------------------------------------------------------------------
    def teams(self) -> list[Team]:
        if self._teams is None:
            data = self.client.get("teams", {"sportId": 1}, ttl=TTL_LONG)
            self._teams = [Team(t["id"], t["name"], t.get("teamName", t["name"]))
                           for t in data.get("teams", [])]
        return self._teams

    def team_by_id(self, tid: int) -> Team | None:
        return next((t for t in self.teams() if t.id == tid), None)

    def _team_aliases(self) -> list[tuple[str, Team]]:
        if self._aliases is None:
            out: list[tuple[str, Team]] = []
            for t in self.teams():
                out.append((norm(t.name), t))
                out.append((norm(t.short), t))
            for alias, tid in TEAM_EXTRAS.items():
                team = self.team_by_id(tid) if tid else None
                if team:
                    out.append((norm(alias), team))
            for alias, tid in HISTORIC_TEAMS.items():
                cur = self.team_by_id(tid)
                if cur:   # keep the era name the user typed, on the franchise's stable id
                    out.append((alias, Team(tid, alias.title().replace("St ", "St. "), alias.split()[-1].title())))
            self._aliases = sorted(out, key=lambda x: -len(x[0]))
        return self._aliases

    def _era_teams(self, season: int) -> list[Team]:
        """Teams as they were named in `season` (Brooklyn Dodgers 1955, Negro leagues 1927...)."""
        if season not in self._era:
            data = self.client.get("teams", {"sportId": 1, "season": season}, ttl=TTL_LONG)
            self._era[season] = [Team(t["id"], t["name"], t.get("teamName", t["name"]))
                                 for t in data.get("teams", [])]
        return self._era[season]

    def find_teams(self, text: str, seasons: tuple[int | None, int | None] | None = None
                   ) -> tuple[list[Team], str]:
        """Teams named in `text`, in order of appearance, plus the text with them blanked out.

        `seasons` (first, last) adds the team names used in those years.
        """
        aliases = list(self._team_aliases())
        if seasons and any(seasons):
            a = seasons[0] or seasons[1]
            b = seasons[1] or seasons[0]
            for yr in sorted({a, b, (a + b) // 2}):
                if 1876 <= yr <= 2100:
                    try:
                        for t in self._era_teams(yr):
                            aliases += [(norm(t.name), t), (norm(t.short), t)]
                    except Exception:
                        pass
            aliases.sort(key=lambda x: -len(x[0]))
        t = " " + norm(text) + " "
        hits: list[tuple[int, Team]] = []
        for alias, team in aliases:
            for m in re.finditer(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", t):
                hits.append((m.start(), team))
                t = t[:m.start()] + " " * (m.end() - m.start()) + t[m.end():]
        seen, ordered = set(), []
        for _, team in sorted(hits, key=lambda x: x[0]):
            if team.id not in seen:
                seen.add(team.id)
                ordered.append(team)
        return ordered, t.strip()

    # --- players ----------------------------------------------------------------------
    def _candidates(self, text: str) -> list[str]:
        text = re.sub(r"['’]s\b", "", text)
        toks = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.\-]*", text)
        cands: list[str] = []
        for n in (3, 2):
            for i in range(len(toks) - n + 1):
                gram = toks[i:i + n]
                if all(w[0].isupper() and w not in STOP_CAPS for w in gram):
                    cands.append(" ".join(gram))
        # lower-case chat input: also try plain bigrams that aren't obviously words
        if not cands:
            low = [w for w in toks if w.lower() not in _COMMON]
            for i in range(len(low) - 1):
                cands.append(f"{low[i]} {low[i + 1]}")
        return cands

    def _from_index(self, name: str) -> Player | None:
        if self.index is None:
            return None
        if self._pcache is None:
            self._pcache = {norm(r["name"]): (r["id"], r["name"])
                            for r in self.index.execute("SELECT id, name FROM players")}
        hit = self._pcache.get(norm(name))
        return Player(hit[0], hit[1]) if hit else None

    def _from_api(self, name: str) -> list[Player]:
        data = self.client.get("people/search", {"names": name}, ttl=TTL_LONG)
        return [Player(p["id"], p["fullName"], (p.get("primaryPosition") or {}).get("abbreviation"),
                       p.get("active")) for p in data.get("people", [])]

    def profile(self, player: Player) -> dict:
        """Position, whether still active, and debut year. Index-resolved players carry none."""
        data = self.client.get(f"people/{player.id}", ttl=TTL_LONG)
        p = (data.get("people") or [{}])[0]
        debut = p.get("mlbDebutDate")
        return {"position": player.position or (p.get("primaryPosition") or {}).get("abbreviation"),
                "active": p.get("active"), "debut_year": int(debut[:4]) if debut else None}

    def position(self, player: Player) -> str | None:
        """Primary position code (P, TWP, SS...)."""
        return player.position or self.profile(player)["position"]

    def find_players(self, text: str, limit: int = 3, lenient: bool = False) -> tuple[list[Player], list[str]]:
        """Players named in `text`. Second value lists ambiguous names we could not pin down."""
        found: list[Player] = []
        ambiguous: list[str] = []
        used = norm(text)
        api_calls = 0
        for cand in self._candidates(text):
            c = norm(cand)
            if c not in used:     # overlapped by an earlier, longer match
                continue
            p = self._from_index(cand)
            if p is None and api_calls < 6:
                api_calls += 1
                matches = [m for m in self._from_api(cand) if norm(m.name) == c]
                if len(matches) > 1:
                    matches = sorted(matches, key=lambda m: not m.active)[:1] if \
                        sum(1 for m in matches if m.active) == 1 else matches
                if len(matches) == 1:
                    p = matches[0]
                elif len(matches) > 1:
                    ambiguous.append(cand)
                    used = used.replace(c, " ")
                    continue
            if p and all(p.id != f.id for f in found):
                found.append(p)
                used = used.replace(c, " ")
            if len(found) >= limit:
                break
        if not found:  # single surname ("how many homers does Judge have", "skubal wins")
            plain = re.sub(r"['’]s\b", "", text)
            toks = re.findall(r"\b[A-Z][a-zA-Z'\-]{3,}\b", plain)
            if lenient and not toks:   # lower-case chat input: any unfamiliar word could be a surname
                toks = [t for t in re.findall(r"[a-z][a-z'\-]{3,}", plain) if t not in _COMMON][:3]
            for tok in toks:
                if tok in STOP_CAPS or tok.lower() in _COMMON or self.find_teams(tok)[0] or api_calls >= 6:
                    continue
                api_calls += 1
                actives = [m for m in self._from_api(tok) if m.active and
                           norm(m.name).split(" ")[-1] == norm(tok)]
                if len(actives) == 1:
                    found.append(actives[0])
                    break
                if len(actives) > 1:
                    ambiguous.append(tok)
        return found, ambiguous


_COMMON = set("""a an the of in on at for to by with and or is are was were do does did has have had
what who when where which how why many much last first most time times list all every ever
game games play plays player players team teams season seasons year years home run runs hit hits
strikeout strikeouts walk walks batting average stats stat number total career lifetime league
mlb baseball yesterday today this that these those their his her they it as from than over under
did ended end ending era whip avg obp slg ops rbi saves save wins win losses loss innings
average pitching hitting pitched batted record standings longest highest lowest biggest best worst""".split())
