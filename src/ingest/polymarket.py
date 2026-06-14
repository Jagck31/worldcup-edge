from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Iterable, Mapping

from edge.detect import OrderBook, OrderLevel
from ingest.results import DEFAULT_ALIASES


GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


@dataclass(frozen=True)
class PolymarketMarket:
    market_id: str
    question: str
    slug: str
    outcomes: list[str]
    token_ids: list[str]
    end_date: str | None = None


@dataclass(frozen=True)
class WorldCupMarketMapping:
    market_id: str
    question: str
    market_name: str
    market_type: str
    team: str
    probability_column: str
    probability_key: str
    group: str | None = None
    yes_token_id: str | None = None
    no_token_id: str | None = None


class PolymarketClient:
    def __init__(
        self,
        gamma_base: str = GAMMA_BASE,
        clob_base: str = CLOB_BASE,
        cache_dir: Path | None = None,
        sleep_sec: float = 0.25,
    ) -> None:
        self.gamma_base = gamma_base.rstrip("/")
        self.clob_base = clob_base.rstrip("/")
        self.cache_dir = cache_dir
        self.sleep_sec = sleep_sec

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        import requests

        for attempt in range(4):
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 429 and attempt < 3:
                time.sleep((attempt + 1) * self.sleep_sec)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"Could not fetch {url}")

    def list_world_cup_markets(self, active: bool = True) -> list[PolymarketMarket]:
        params = {
            "active": str(active).lower(),
            "closed": "false",
            "limit": 500,
            "q": "World Cup",
        }
        payload = self._get_json(f"{self.gamma_base}/markets", params=params)
        records = payload if isinstance(payload, list) else payload.get("markets", [])
        markets: list[PolymarketMarket] = []
        for record in records:
            question = str(record.get("question", ""))
            if "world cup" not in question.lower() and "fifa" not in question.lower():
                continue
            outcomes = _decode_json_list(record.get("outcomes"))
            token_ids = _decode_json_list(record.get("clobTokenIds") or record.get("tokenIds"))
            markets.append(
                PolymarketMarket(
                    market_id=str(record.get("id") or record.get("conditionId") or record.get("slug")),
                    question=question,
                    slug=str(record.get("slug", "")),
                    outcomes=outcomes,
                    token_ids=token_ids,
                    end_date=record.get("endDate"),
                )
            )
        return markets

    def get_order_book(self, token_id: str, market_id: str | None = None) -> OrderBook:
        payload = self._get_json(f"{self.clob_base}/book", params={"token_id": token_id})
        asks = [
            OrderLevel(price=float(level["price"]), size_usd=float(level["size"]) * float(level["price"]))
            for level in payload.get("asks", [])
        ]
        bids = [
            OrderLevel(price=float(level["price"]), size_usd=float(level["size"]) * float(level["price"]))
            for level in payload.get("bids", [])
        ]
        return OrderBook(market_id=market_id or token_id, yes_asks=asks, yes_bids=bids)

    def get_yes_order_book(self, mapping: WorldCupMarketMapping) -> OrderBook:
        """Fetch the order book for the mapped YES token, regardless of outcome order."""
        if not mapping.yes_token_id:
            raise ValueError(f"Mapped market {mapping.market_id} has no YES token id")
        return self.get_order_book(mapping.yes_token_id, market_id=mapping.market_id)

    def cache_snapshot(self, name: str, payload: Any) -> Path:
        if self.cache_dir is None:
            raise ValueError("cache_dir is required to cache snapshots")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.cache_dir / f"{timestamp}_{name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def snapshot_world_cup_markets(self) -> Path:
        markets = self.list_world_cup_markets()
        return self.cache_snapshot("world_cup_markets", [asdict(market) for market in markets])


def _decode_json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [str(value)]
    if isinstance(loaded, list):
        return [str(item) for item in loaded]
    return [str(loaded)]


def map_world_cup_market(
    market: PolymarketMarket,
    known_teams: Iterable[str] | None = None,
    aliases: Mapping[str, str] | None = None,
) -> WorldCupMarketMapping | None:
    """Map clear binary 2026 FIFA World Cup markets to simulator probability columns.

    The parser is intentionally conservative: ambiguous questions return ``None`` so
    downstream edge detection does not compare a probability to the wrong contract.
    """
    yes_no_tokens = _yes_no_token_ids(market)
    if yes_no_tokens is None:
        return None

    parse_text = _market_text(market)
    filter_text = _market_filter_text(market)
    if not _looks_like_2026_fifa_world_cup(filter_text):
        return None

    group_match = _GROUP_WINNER_RE.match(parse_text)
    if group_match:
        team = _canonical_team(group_match.group("team"), known_teams, aliases)
        if team is None:
            return None
        group = group_match.group("group").upper()
        return _mapping(
            market=market,
            team=team,
            market_type="win_group",
            probability_column="p_win_group",
            label=f"Win Group {group}",
            yes_no_tokens=yes_no_tokens,
            group=group,
        )

    stage_match = _REACH_STAGE_RE.match(parse_text)
    if stage_match:
        stage = _stage_from_text(stage_match.group("stage"))
        if stage is None:
            return None
        team = _canonical_team(stage_match.group("team"), known_teams, aliases)
        if team is None:
            return None
        return _mapping(
            market=market,
            team=team,
            market_type="reach_stage",
            probability_column=stage["probability_column"],
            label=f"Reach {stage['label']}",
            yes_no_tokens=yes_no_tokens,
        )

    advance_match = _ADVANCE_RE.match(parse_text)
    if advance_match:
        team = _canonical_team(advance_match.group("team"), known_teams, aliases)
        if team is None:
            return None
        return _mapping(
            market=market,
            team=team,
            market_type="advance",
            probability_column="p_advanced",
            label="Advance",
            yes_no_tokens=yes_no_tokens,
        )

    champion_match = _CHAMPION_RE.match(parse_text)
    if champion_match:
        team = _canonical_team(champion_match.group("team"), known_teams, aliases)
        if team is None:
            return None
        return _mapping(
            market=market,
            team=team,
            market_type="champion",
            probability_column="p_champion",
            label="Champion",
            yes_no_tokens=yes_no_tokens,
        )

    return None


def map_world_cup_markets(
    markets: Iterable[PolymarketMarket],
    known_teams: Iterable[str] | None = None,
    aliases: Mapping[str, str] | None = None,
) -> list[WorldCupMarketMapping]:
    mappings: list[WorldCupMarketMapping] = []
    for market in markets:
        mapping = map_world_cup_market(market, known_teams=known_teams, aliases=aliases)
        if mapping is not None:
            mappings.append(mapping)
    return mappings


def build_market_probability_inputs(
    mappings: Iterable[WorldCupMarketMapping | None],
    simulation_rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, float], list[str]]:
    """Return ``detect_edges`` model probabilities from mapped markets and sim rows.

    Missing rows or probability columns are reported explicitly. They are not
    guessed, because a silent mismatch here would create fake edge.
    """
    row_by_team = {str(row.get("team", "")).casefold(): row for row in simulation_rows}
    probabilities: dict[str, float] = {}
    missing: list[str] = []

    for mapping in mappings:
        if mapping is None:
            continue
        row = row_by_team.get(mapping.team.casefold())
        if row is None:
            missing.append(f"{mapping.market_name}: missing simulation row for {mapping.team}")
            continue
        if mapping.probability_column not in row:
            missing.append(
                f"{mapping.market_name}: missing probability column {mapping.probability_column}"
            )
            continue
        value = row.get(mapping.probability_column)
        try:
            probability = float(value)
        except (TypeError, ValueError):
            missing.append(
                f"{mapping.market_name}: invalid probability value for {mapping.probability_column}"
            )
            continue
        if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
            missing.append(
                f"{mapping.market_name}: invalid probability value for {mapping.probability_column}"
            )
            continue
        probabilities[mapping.market_name] = probability
    return probabilities, missing


_WORLD_CUP_SUFFIX = (
    r"(?:\s+(?:at|in|of|during|for)\s+(?:the\s+)?)?"
    r"(?:2026\s+)?(?:fifa\s+)?world cup(?:\s+2026)?"
)
_CHAMPION_RE = re.compile(
    rf"^(?:will\s+)?(?P<team>.+?)\s+(?:to\s+)?win\s+(?:the\s+)?"
    rf"(?:2026\s+)?(?:fifa\s+)?world cup(?:\s+2026)?$",
    re.IGNORECASE,
)
_GROUP_WINNER_RE = re.compile(
    rf"^(?:will\s+)?(?P<team>.+?)\s+(?:to\s+)?win\s+group\s+"
    rf"(?P<group>[A-L]){_WORLD_CUP_SUFFIX}?$",
    re.IGNORECASE,
)
_REACH_STAGE_RE = re.compile(
    rf"^(?:will\s+)?(?P<team>.+?)\s+(?:to\s+)?"
    rf"(?:reach|make|make\s+the|make\s+it\s+to|advance\s+to)\s+"
    rf"(?:the\s+)?(?P<stage>round\s+of\s+16|r16|last\s+16|quarter-?finals?|"
    rf"semi-?finals?|final){_WORLD_CUP_SUFFIX}?$",
    re.IGNORECASE,
)
_ADVANCE_RE = re.compile(
    rf"^(?:will\s+)?(?P<team>.+?)\s+(?:to\s+)?(?:"
    rf"advance\s+(?:from|out\s+of)\s+(?:the\s+)?group|"
    rf"qualify\s+from\s+(?:the\s+)?group|"
    rf"make\s+it\s+out\s+of\s+(?:the\s+)?group|"
    rf"reach\s+(?:the\s+)?knockout\s+stage)"
    rf"{_WORLD_CUP_SUFFIX}?$",
    re.IGNORECASE,
)
_STAGES = {
    "round of 16": {"probability_column": "p_last_16", "label": "Round of 16"},
    "r16": {"probability_column": "p_last_16", "label": "Round of 16"},
    "last 16": {"probability_column": "p_last_16", "label": "Round of 16"},
    "quarterfinal": {"probability_column": "p_last_8", "label": "Quarterfinal"},
    "quarterfinals": {"probability_column": "p_last_8", "label": "Quarterfinal"},
    "quarter-final": {"probability_column": "p_last_8", "label": "Quarterfinal"},
    "quarter-finals": {"probability_column": "p_last_8", "label": "Quarterfinal"},
    "semifinal": {"probability_column": "p_last_4", "label": "Semifinal"},
    "semifinals": {"probability_column": "p_last_4", "label": "Semifinal"},
    "semi-final": {"probability_column": "p_last_4", "label": "Semifinal"},
    "semi-finals": {"probability_column": "p_last_4", "label": "Semifinal"},
    "final": {"probability_column": "p_finalist", "label": "Final"},
}


def _mapping(
    market: PolymarketMarket,
    team: str,
    market_type: str,
    probability_column: str,
    label: str,
    yes_no_tokens: tuple[str | None, str | None],
    group: str | None = None,
) -> WorldCupMarketMapping:
    return WorldCupMarketMapping(
        market_id=market.market_id,
        question=market.question,
        market_name=f"{label} - {team}",
        market_type=market_type,
        team=team,
        group=group,
        probability_column=probability_column,
        probability_key=f"{probability_column}:{team}",
        yes_token_id=yes_no_tokens[0],
        no_token_id=yes_no_tokens[1],
    )


def _yes_no_token_ids(market: PolymarketMarket) -> tuple[str | None, str | None] | None:
    outcomes = [str(outcome).strip().casefold() for outcome in market.outcomes]
    if len(outcomes) != 2 or set(outcomes) != {"yes", "no"}:
        return None
    yes_token_id = None
    no_token_id = None
    for index, outcome in enumerate(outcomes):
        token_id = market.token_ids[index] if index < len(market.token_ids) else None
        if outcome == "yes":
            yes_token_id = token_id
        elif outcome == "no":
            no_token_id = token_id
    return yes_token_id, no_token_id


def _market_text(market: PolymarketMarket) -> str:
    source = market.question or market.slug.replace("-", " ")
    return _clean_question(source)


def _market_filter_text(market: PolymarketMarket) -> str:
    return _clean_question(f"{market.question} {market.slug.replace('-', ' ')}")


def _clean_question(value: str) -> str:
    text = str(value).replace("?", " ").replace("!", " ").replace(".", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _looks_like_2026_fifa_world_cup(text: str) -> bool:
    lowered = text.casefold()
    if "world cup" not in lowered:
        return False
    if "club world cup" in lowered or "women" in lowered:
        return False
    return "2026" in lowered or "fifa world cup" in lowered


def _canonical_team(
    raw_team: str,
    known_teams: Iterable[str] | None,
    aliases: Mapping[str, str] | None,
) -> str | None:
    team = _clean_team(raw_team)
    alias_map = {key.casefold(): value for key, value in DEFAULT_ALIASES.items()}
    if aliases:
        alias_map.update({str(key).casefold(): str(value) for key, value in aliases.items()})
    canonical = alias_map.get(team.casefold(), team)

    if known_teams is None:
        return canonical
    known_map = {str(item).casefold(): str(item) for item in known_teams}
    return known_map.get(canonical.casefold()) or known_map.get(team.casefold())


def _clean_team(value: str) -> str:
    team = _clean_question(value).strip(" '\"")
    if team.casefold().startswith("the "):
        team = team[4:].strip()
    return team


def _stage_from_text(value: str) -> dict[str, str] | None:
    key = re.sub(r"\s+", " ", str(value).casefold().strip())
    return _STAGES.get(key)
