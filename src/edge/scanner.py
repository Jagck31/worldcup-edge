from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class ConsistencyMarket:
    market_name: str
    group_key: str
    executable_yes_price: float
    fillable_usd: float


@dataclass(frozen=True)
class ConsistencyFlag:
    group_key: str
    sum_implied_probability: float
    expected_probability: float
    gap_pp: float
    direction: str
    market_count: int
    min_fillable_usd: float


def scan_sum_to_one(
    markets: list[ConsistencyMarket],
    tolerance_pp: float,
    min_fillable_usd: float = 0.0,
    expected_total: float = 1.0,
    alert_overpriced: bool = True,
    expected_market_count: int | None = None,
) -> list[ConsistencyFlag]:
    if expected_total <= 0:
        raise ValueError("expected_total must be positive")
    if expected_market_count is not None and expected_market_count <= 0:
        raise ValueError("expected_market_count must be positive")
    grouped: dict[str, list[ConsistencyMarket]] = defaultdict(list)
    for market in markets:
        if market.fillable_usd >= min_fillable_usd:
            grouped[market.group_key].append(market)

    flags: list[ConsistencyFlag] = []
    for group_key, group_markets in grouped.items():
        if expected_market_count is not None and len(group_markets) != expected_market_count:
            continue
        implied = round(sum(item.executable_yes_price for item in group_markets), 6)
        gap_pp = (implied - expected_total) * 100.0
        if abs(gap_pp) <= tolerance_pp:
            continue
        if gap_pp > 0 and not alert_overpriced:
            continue
        flags.append(
            ConsistencyFlag(
                group_key=group_key,
                sum_implied_probability=implied,
                expected_probability=expected_total,
                gap_pp=gap_pp,
                direction="overpriced" if gap_pp > 0 else "underpriced",
                market_count=len(group_markets),
                min_fillable_usd=min(item.fillable_usd for item in group_markets),
            )
        )
    return sorted(flags, key=lambda item: abs(item.gap_pp), reverse=True)
