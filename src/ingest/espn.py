"""Second live-results source: ESPN's free soccer scoreboard.

TheSportsDB's free tier is incomplete -- it silently omits whole 2026 fixtures (Australia-
Turkiye, Netherlands-Japan, Sweden-Tunisia were never published), so the tracker stalls on
"a game a day". ESPN's public scoreboard endpoint carries the full slate, needs no key, and
returns the same ``LiveEvent`` shape, so the engine can merge it alongside TheSportsDB and stop
missing games.

    GET site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates=YYYYMMDD

``slug`` is ``fifa.world`` for the men's World Cup. We poll a date window (the scoreboard is
per-day) clamped to the tournament bounds, exactly like the TheSportsDB day-window client.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
from typing import Any

import requests

from ingest.livescores import LiveEvent, _to_int
from ingest.results import TeamNameNormalizer

DEFAULT_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
DEFAULT_SLUG = "fifa.world"  # FIFA men's World Cup


def _classify(state: str, completed: bool, status_name: str) -> str:
    name = (status_name or "").upper()
    if any(w in name for w in ("POSTPONED", "CANCEL", "ABANDON", "SUSPEND")):
        return "postponed"
    if completed or state == "post":
        return "finished"
    if state == "in":
        return "in_play"
    return "scheduled"


@dataclass(frozen=True)
class EspnScoreClient:
    base_url: str
    slug: str
    normalizer: TeamNameNormalizer
    timeout: float = 20.0
    window_start: str = "2026-06-11"
    window_end: str = "2026-07-19"
    day_lookback: int = 6
    day_lookahead: int = 2

    @classmethod
    def from_config(cls, config: dict, normalizer: TeamNameNormalizer) -> "EspnScoreClient":
        return cls(
            base_url=str(config.get("espn_api_base", DEFAULT_BASE)).rstrip("/"),
            slug=str(config.get("espn_league_slug", DEFAULT_SLUG)),
            normalizer=normalizer,
            timeout=float(config.get("live_timeout_sec", 20)),
            window_start=str(config.get("live_window_start", "2026-06-11")),
            window_end=str(config.get("live_window_end", "2026-07-19")),
            day_lookback=int(config.get("live_day_lookback", 6)),
            day_lookahead=int(config.get("live_day_lookahead", 2)),
        )

    def _day_window(self) -> list[str]:
        today = datetime.now(timezone.utc).date()
        days = [today + timedelta(days=d) for d in range(-self.day_lookback, self.day_lookahead + 1)]
        try:
            lo, hi = date.fromisoformat(self.window_start), date.fromisoformat(self.window_end)
            days = [d for d in days if lo <= d <= hi]
        except ValueError:
            pass
        return [d.strftime("%Y%m%d") for d in days]

    def _to_event(self, raw: dict) -> LiveEvent | None:
        comps = raw.get("competitions") or []
        if not comps:
            return None
        comp = comps[0]
        competitors = comp.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if home is None or away is None:
            return None
        home_name = (home.get("team") or {}).get("displayName") or (home.get("team") or {}).get("name")
        away_name = (away.get("team") or {}).get("displayName") or (away.get("team") or {}).get("name")
        if not home_name or not away_name:
            return None
        status = (comp.get("status") or raw.get("status") or {}).get("type", {})
        state = _classify(str(status.get("state", "")), bool(status.get("completed")), str(status.get("name", "")))
        event_date = str(raw.get("date", ""))[:10]
        return LiveEvent(
            event_id=f"espn:{raw.get('id', '')}",
            date=event_date,
            kickoff=str(raw.get("date", "")),
            home=self.normalizer.canonical(str(home_name)),
            away=self.normalizer.canonical(str(away_name)),
            home_score=_to_int(home.get("score")),
            away_score=_to_int(away.get("score")),
            status_raw=str(status.get("shortDetail") or status.get("detail") or status.get("name", "")),
            state=state,
        )

    def _get(self, day: str) -> dict[str, Any]:
        url = f"{self.base_url}/{self.slug}/scoreboard"
        response = requests.get(url, params={"dates": day}, timeout=self.timeout)
        response.raise_for_status()
        return response.json() or {}

    def fetch_events(self) -> list[LiveEvent]:
        """Every event ESPN lists across the tournament day-window. Tolerant per-day."""
        events: list[LiveEvent] = []
        for day in self._day_window():
            try:
                payload = self._get(day)
            except (requests.RequestException, ValueError):
                continue
            for raw in payload.get("events") or []:
                event = self._to_event(raw)
                if event is not None:
                    events.append(event)
        return events
