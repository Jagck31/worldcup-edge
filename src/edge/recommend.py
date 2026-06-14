from __future__ import annotations

from dataclasses import dataclass

from edge.detect import EdgeCandidate
from edge.kelly import KellyConfig, SizedBet, size_bet


@dataclass(frozen=True)
class EdgeRecommendation:
    rank: int
    market_name: str
    market_id: str
    side: str
    action: str
    model_probability: float
    executable_price: float
    edge_pp: float
    ev_per_dollar: float
    fillable_usd: float
    raw_kelly_fraction: float
    kelly_fraction: float
    uncapped_size_usd: float
    capped_size_usd: float
    kelly_size_usd: float
    status: str
    risk_label: str
    summary: str


def build_recommendations(
    edges: list[EdgeCandidate],
    config: KellyConfig,
    current_total_exposure_usd: float = 0.0,
) -> list[EdgeRecommendation]:
    """Rank and size edges for terminal/dashboard presentation.

    Ranking by fractional Kelly avoids over-promoting high edge-point bets that
    have little bankroll impact because they are priced near certainty.
    """
    ranked_edges = sorted(
        edges,
        key=lambda edge: _ranking_key(edge, config),
        reverse=True,
    )

    exposure = float(current_total_exposure_usd)
    recommendations: list[EdgeRecommendation] = []
    for rank, edge in enumerate(ranked_edges, start=1):
        sized = size_bet(
            model_probability=edge.model_probability,
            executable_price=edge.executable_price,
            fillable_usd=edge.fillable_usd,
            config=config,
            current_total_exposure_usd=exposure,
        )
        status = _display_status(sized, config, exposure)
        if status == "ok":
            exposure += sized.fillable_size_usd

        action = f"BUY {edge.side.upper()}"
        risk_label = _risk_label(edge, sized, status, config)
        summary = _summary(edge, action, sized, status)
        recommendations.append(
            EdgeRecommendation(
                rank=rank,
                market_name=edge.market_name,
                market_id=edge.market_id,
                side=edge.side.upper(),
                action=action,
                model_probability=edge.model_probability,
                executable_price=edge.executable_price,
                edge_pp=edge.edge_pp,
                ev_per_dollar=edge.ev_per_dollar,
                fillable_usd=edge.fillable_usd,
                raw_kelly_fraction=sized.raw_kelly_fraction,
                kelly_fraction=sized.fractional_kelly_fraction,
                uncapped_size_usd=sized.uncapped_size_usd,
                capped_size_usd=sized.capped_size_usd,
                kelly_size_usd=sized.fillable_size_usd,
                status=status,
                risk_label=risk_label,
                summary=summary,
            )
        )
    return recommendations


def recommendations_to_state_rows(recommendations: list[EdgeRecommendation]) -> list[dict]:
    rows: list[dict] = []
    for recommendation in recommendations:
        market, team = _split_market_name(recommendation.market_name)
        rows.append(
            {
                "rank": recommendation.rank,
                "market": market,
                "team": team,
                "market_id": recommendation.market_id,
                "side": recommendation.side,
                "action": recommendation.action,
                "model_prob": round(recommendation.model_probability, 4),
                "exec_price": round(recommendation.executable_price, 4),
                "edge_pp": round(recommendation.edge_pp, 2),
                "ev_per_dollar": round(recommendation.ev_per_dollar, 4),
                "kelly_fraction": round(recommendation.kelly_fraction, 4),
                "uncapped_size_usd": recommendation.uncapped_size_usd,
                "capped_size_usd": recommendation.capped_size_usd,
                "kelly_size_usd": recommendation.kelly_size_usd,
                "fillable_usd": round(recommendation.fillable_usd, 2),
                "status": recommendation.status,
                "risk_label": recommendation.risk_label,
                "summary": recommendation.summary,
                "actionable": recommendation.status == "ok",
            }
        )
    return rows


def summarize_recommendations(
    recommendations: list[EdgeRecommendation],
    config: KellyConfig,
    current_total_exposure_usd: float = 0.0,
) -> dict:
    recommended_exposure = round(
        sum(item.kelly_size_usd for item in recommendations if item.status == "ok"),
        2,
    )
    current_exposure = round(max(0.0, float(current_total_exposure_usd)), 2)
    projected_exposure = round(current_exposure + recommended_exposure, 2)
    exposure_cap = config.bankroll_usd * config.max_total_exposure_pct
    side_counts: dict[str, int] = {}
    for recommendation in recommendations:
        side_counts[recommendation.side] = side_counts.get(recommendation.side, 0) + 1
    actionable_count = sum(1 for item in recommendations if item.status == "ok")
    return {
        "count": len(recommendations),
        "actionable_count": actionable_count,
        "watchlist_count": len(recommendations) - actionable_count,
        "side_counts": side_counts,
        "current_exposure_usd": current_exposure,
        "total_recommended_exposure_usd": recommended_exposure,
        "total_projected_exposure_usd": projected_exposure,
        "exposure_pct_bankroll": (
            round((projected_exposure / config.bankroll_usd) * 100.0, 2)
            if config.bankroll_usd
            else 0.0
        ),
        "exposure_cap_usd": round(exposure_cap, 2),
        "exposure_cap_remaining_usd": round(max(0.0, exposure_cap - projected_exposure), 2),
    }


def _ranking_key(edge: EdgeCandidate, config: KellyConfig) -> tuple[float, float, float, float, float]:
    sized = size_bet(
        model_probability=edge.model_probability,
        executable_price=edge.executable_price,
        fillable_usd=edge.fillable_usd,
        config=config,
        current_total_exposure_usd=0.0,
    )
    actionable = 1.0 if sized.fillable_size_usd > 0 else 0.0
    return (
        actionable,
        sized.fractional_kelly_fraction,
        edge.ev_per_dollar,
        edge.edge_pp,
        edge.fillable_usd,
    )


def _display_status(sized: SizedBet, config: KellyConfig, current_exposure_usd: float) -> str:
    exposure_cap = config.bankroll_usd * config.max_total_exposure_pct
    remaining_exposure = max(0.0, exposure_cap - current_exposure_usd)
    if sized.fillable_size_usd <= 0 and remaining_exposure < config.min_fillable_usd:
        return "portfolio_cap_reached"
    return sized.reason


def _risk_label(edge: EdgeCandidate, sized: SizedBet, status: str, config: KellyConfig) -> str:
    if status != "ok":
        return "watchlist"
    if sized.fillable_size_usd >= config.bankroll_usd * 0.15 or edge.edge_pp >= 15:
        return "high_conviction"
    if edge.edge_pp >= 8:
        return "standard"
    return "thin"


def _summary(edge: EdgeCandidate, action: str, sized: SizedBet, status: str) -> str:
    return (
        f"{action}: {edge.market_name} | "
        f"model {_pct(edge.model_probability)} | "
        f"price {_pct(edge.executable_price)} | "
        f"edge {edge.edge_pp:+.1f}pp | "
        f"size ${sized.fillable_size_usd:.2f} | "
        f"{status}"
    )


def _pct(value: float) -> str:
    return f"{value * 100.0:.1f}%"


def _split_market_name(market_name: str) -> tuple[str, str]:
    if " - " not in market_name:
        return market_name, ""
    market, team = market_name.split(" - ", 1)
    return market, team
