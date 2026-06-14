from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KellyConfig:
    bankroll_usd: float = 75.0
    kelly_fraction: float = 0.25
    max_single_bet_pct: float = 0.20
    max_total_exposure_pct: float = 0.80
    min_fillable_usd: float = 5.0


@dataclass(frozen=True)
class SizedBet:
    raw_kelly_fraction: float
    fractional_kelly_fraction: float
    uncapped_size_usd: float
    capped_size_usd: float
    fillable_size_usd: float
    reason: str


def size_bet(
    model_probability: float,
    executable_price: float,
    fillable_usd: float,
    config: KellyConfig,
    current_total_exposure_usd: float = 0.0,
) -> SizedBet:
    if executable_price <= 0 or executable_price >= 1:
        return _empty("invalid_price")
    edge = model_probability - executable_price
    if edge <= 0:
        return _empty("no_positive_edge")

    raw_fraction = edge / (1.0 - executable_price)
    fractional = max(0.0, raw_fraction * config.kelly_fraction)
    uncapped_size = config.bankroll_usd * fractional
    single_cap = config.bankroll_usd * config.max_single_bet_pct
    exposure_cap_remaining = max(
        0.0,
        config.bankroll_usd * config.max_total_exposure_pct - current_total_exposure_usd,
    )
    capped = min(uncapped_size, single_cap, exposure_cap_remaining)
    fillable = min(capped, fillable_usd)
    if fillable < config.min_fillable_usd:
        return SizedBet(
            raw_kelly_fraction=raw_fraction,
            fractional_kelly_fraction=fractional,
            uncapped_size_usd=round(uncapped_size, 2),
            capped_size_usd=round(capped, 2),
            fillable_size_usd=0.0,
            reason="below_min_fillable",
        )
    return SizedBet(
        raw_kelly_fraction=raw_fraction,
        fractional_kelly_fraction=fractional,
        uncapped_size_usd=round(uncapped_size, 2),
        capped_size_usd=round(capped, 2),
        fillable_size_usd=round(fillable, 2),
        reason="ok",
    )


def _empty(reason: str) -> SizedBet:
    return SizedBet(0.0, 0.0, 0.0, 0.0, 0.0, reason)
