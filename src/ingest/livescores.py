"""Live football-results feed (TheSportsDB) for the 2026 World Cup tracker.

Pulls the FIFA World Cup season schedule + scores from TheSportsDB's free API and
normalises team names to the model's canonical spelling. One season call returns every
event the provider currently knows about (finished, in-play, not-started), so a single
request is enough to drive the live tracker. The provider/key/league/season are all
config-driven; the public free key "3" needs no registration.

The feed is the *source of results only* — the fixture list still comes from the local
schedule CSV, so a sparse or lagging feed never loses an upcoming match.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
from typing import Iterable

import requests

from ingest.results import TeamNameNormalizer


DEFAULT_BASE = "https://www.thesportsdb.com/api/v1/json"
DEFAULT_KEY = "3"  # public free test key; no registration required
WORLD_CUP_LEAGUE_ID = "4429"  # TheSportsDB "FIFA World Cup"
DEFAULT_SEASON = "2026"

# TheSportsDB strStatus vocabulary is loose; bucket it into our three states.
_FINISHED = {"FT", "AET", "AP", "PEN", "MATCH FINISHED", "AFTER EXTRA TIME", "AFTER PENALTIES", "FT_PEN"}
_NOT_STARTED = {"", "NS", "NOT STARTED", "TBD", "SCHEDULED", "TIMED"}
_POSTPONED = {"PPD", "POSTP.", "POSTPONED", "CANC", "CANCELLED", "CANCELED", "ABD", "ABANDONED", "SUSP"}
_LIVE = {"1H", "2H", "HT", "ET", "BT", "P", "PEN LIVE", "LIVE", "IN PLAY", "INPLAY", "BREAK"}


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"null", "none", "-"}:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return None


def _classify(status_raw: str, home_score: int | None, away_score: int | None) -> str:
    status = (status_raw or "").strip().upper()
    if status in _POSTPONED:
        return "postponed"
    if status in _FINISHED:
        return "finished"
    if status in _LIVE or status.endswith("'") or status.rstrip("'").isdigit():
        return "in_play"
    if status in _NOT_STARTED:
        return "scheduled"
    # Unknown label: infer from scores. Both scores present with a non-NS label ~ finished.
    if home_score is not None and away_score is not None:
        return "finished"
    return "scheduled"


@dataclass(frozen=True)
class LiveEvent:
    event_id: str
    date: str            # "YYYY-MM-DD"
    kickoff: str         # provider timestamp, e.g. "2026-06-11T19:00:00" (may be "")
    home: str            # canonical team name
    away: str            # canonical team name
    home_score: int | None
    away_score: int | None
    status_raw: str
    state: str           # finished | in_play | scheduled | postponed

    @property
    def pair(self) -> frozenset:
        return frozenset((self.home, self.away))

    @property
    def finished(self) -> bool:
        return self.state == "finished" and self.home_score is not None and self.away_score is not None

    @property
    def in_play(self) -> bool:
        return self.state == "in_play"


@dataclass(frozen=True)
class LiveScoreClient:
    base_url: str
    api_key: str
    league_id: str
    season: str
    normalizer: TeamNameNormalizer
    timeout: float = 20.0
    window_start: str = "2026-06-11"   # tournament bounds — don't poll days outside them
    window_end: str = "2026-07-19"
    day_lookback: int = 6              # eventsday window around "today" (the season feed is sparse)
    day_lookahead: int = 2

    @classmethod
    def from_config(cls, config: dict, normalizer: TeamNameNormalizer) -> "LiveScoreClient":
        # Env overrides config so a private key never has to live in the repo.
        key = os.environ.get("LIVE_API_KEY") or os.environ.get("THESPORTSDB_KEY") or config.get("live_api_key") or DEFAULT_KEY
        return cls(
            base_url=str(config.get("live_api_base", DEFAULT_BASE)).rstrip("/"),
            api_key=str(key),
            league_id=str(config.get("live_league_id", WORLD_CUP_LEAGUE_ID)),
            season=str(config.get("live_season", DEFAULT_SEASON)),
            normalizer=normalizer,
            timeout=float(config.get("live_timeout_sec", 20)),
            window_start=str(config.get("live_window_start", "2026-06-11")),
            window_end=str(config.get("live_window_end", "2026-07-19")),
            day_lookback=int(config.get("live_day_lookback", 6)),
            day_lookahead=int(config.get("live_day_lookahead", 2)),
        )

    def _get(self, endpoint: str, params: dict) -> dict:
        url = f"{self.base_url}/{self.api_key}/{endpoint}"
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json() or {}

    def _to_event(self, raw: dict) -> LiveEvent | None:
        home_raw = raw.get("strHomeTeam")
        away_raw = raw.get("strAwayTeam")
        if not home_raw or not away_raw:
            return None
        home_score = _to_int(raw.get("intHomeScore"))
        away_score = _to_int(raw.get("intAwayScore"))
        state = _classify(str(raw.get("strStatus", "")), home_score, away_score)
        return LiveEvent(
            event_id=str(raw.get("idEvent", "")),
            date=str(raw.get("dateEvent", "") or ""),
            kickoff=str(raw.get("strTimestamp", "") or ""),
            home=self.normalizer.canonical(str(home_raw)),
            away=self.normalizer.canonical(str(away_raw)),
            home_score=home_score,
            away_score=away_score,
            status_raw=str(raw.get("strStatus", "")),
            state=state,
        )

    _STATE_RANK = {"finished": 3, "in_play": 2, "scheduled": 1, "postponed": 0}

    def _key(self, e: "LiveEvent") -> str:
        return e.event_id or f"{e.date}:{e.home}:{e.away}"

    def _merge(self, events: dict[str, "LiveEvent"], raw: dict) -> None:
        """Insert/replace an event, preferring the most-advanced state (FT > live > NS).

        The free season feed is sparse and often shows a played game as 'NS'; the per-day
        feed has the real FT result. Whichever source reports the more-advanced state (and,
        on a tie, the one carrying scores) wins."""
        event = self._to_event(raw)
        if event is None:
            return
        key = self._key(event)
        cur = events.get(key)
        if cur is None:
            events[key] = event
            return
        new_rank = self._STATE_RANK.get(event.state, 0)
        cur_rank = self._STATE_RANK.get(cur.state, 0)
        if new_rank > cur_rank:
            events[key] = event
        elif new_rank == cur_rank and event.home_score is not None and cur.home_score is None:
            events[key] = event

    def _day_window(self) -> list[str]:
        """Dates (YYYY-MM-DD) to poll via eventsday, clamped to the tournament window."""
        today = datetime.now(timezone.utc).date()
        days = [today + timedelta(days=d) for d in range(-self.day_lookback, self.day_lookahead + 1)]
        try:
            lo = date.fromisoformat(self.window_start)
            hi = date.fromisoformat(self.window_end)
            days = [d for d in days if lo <= d <= hi]
        except ValueError:
            pass
        return [d.isoformat() for d in days]

    def fetch_events(self) -> list[LiveEvent]:
        """Every event the provider knows for the league, from three sources, merged.

        The free `eventsseason` feed is incomplete (it omits finished games and lags scores),
        so we also poll `eventsday` for a window of recent/near dates — that feed carries the
        real FT results the season feed misses — plus `eventsnextleague` for imminent kickoffs.
        Each game keeps its most-advanced state across the sources. A reachable-but-empty feed
        returns []; network failures on supplementary calls are tolerated per-cycle."""
        events: dict[str, LiveEvent] = {}
        try:
            payload = self._get("eventsseason.php", {"id": self.league_id, "s": self.season})
            for raw in payload.get("events") or []:
                self._merge(events, raw)
        except requests.RequestException:
            pass  # day-window below is the more reliable source anyway

        for day in self._day_window():
            try:
                payload = self._get("eventsday.php", {"d": day, "l": self.league_id})
            except requests.RequestException:
                continue
            for raw in payload.get("events") or []:
                self._merge(events, raw)

        try:
            upcoming = self._get("eventsnextleague.php", {"id": self.league_id})
            for raw in upcoming.get("events") or []:
                self._merge(events, raw)
        except requests.RequestException:
            pass
        return list(events.values())


_MERGE_STATE_RANK = {"finished": 3, "in_play": 2, "scheduled": 1, "postponed": 0}


def merge_event_lists(*sources: Iterable[LiveEvent]) -> list[LiveEvent]:
    """Combine events from several providers (TheSportsDB + ESPN), de-duped by team pair + date.

    The same game seen by two sources collapses to one, keeping the most-advanced state (a FT
    from one provider beats an NS from the other) and, on a tie, the copy that carries scores.
    Games only one source has are all kept -- that's the whole point of running two feeds. Date
    is bucketed to the day so a kickoff straddling midnight UTC across sources still matches; a
    later knockout rematch (different date) stays a separate event."""
    merged: dict[tuple, LiveEvent] = {}
    for source in sources:
        for event in source:
            key = (event.pair, str(event.date)[:10])
            current = merged.get(key)
            if current is None:
                merged[key] = event
                continue
            new_rank = _MERGE_STATE_RANK.get(event.state, 0)
            cur_rank = _MERGE_STATE_RANK.get(current.state, 0)
            if new_rank > cur_rank:
                merged[key] = event
            elif new_rank == cur_rank and event.home_score is not None and current.home_score is None:
                merged[key] = event
    return list(merged.values())


def finished_events(events: Iterable[LiveEvent]) -> list[LiveEvent]:
    return [e for e in events if e.finished]


def in_play_events(events: Iterable[LiveEvent]) -> list[LiveEvent]:
    return [e for e in events if e.in_play]
