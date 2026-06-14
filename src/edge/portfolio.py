"""Correlation-aware portfolio construction for the betting book.

The per-bet Kelly sizing in ``edge/kelly.py`` treats every bet as independent. It
isn't: "NO Switzerland win Group B" and "YES Canada win Group B" are largely the
same exposure, and the champion + group-winner markets for one team move together.
Independent sizing therefore double-counts correlated risk and over-concentrates.

This module fixes that by using the Monte Carlo: every candidate bet's payoff is
evaluated in each simulated tournament, giving an empirical joint distribution of
returns. From that we build a **growth-optimal (Kelly) allocation** — which handles
correlation and avoids over-betting by construction — under per-bet and per-group
concentration caps, and report mean/variance/**Sharpe** for the risk/reward view.

Pure numpy + scipy (both already dependencies via scikit-learn).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

try:
    from scipy.optimize import minimize

    _HAVE_SCIPY = True
except Exception:  # pragma: no cover - scipy ships with sklearn, but degrade gracefully
    _HAVE_SCIPY = False


@dataclass
class BetSpec:
    """One candidate bet. ``market_key`` indexes the simulated outcome it depends on
    ("champion" or "group:<letter>"); the bet wins when ``team`` (does/doesn't) win it."""

    bet_id: str
    market_key: str
    team: str
    side: str  # "YES" | "NO"
    price: float  # executable per-share cost, 0 < price < 1
    label: str = ""
    group: str = ""  # concentration bucket (e.g. "champion" or "B")
    max_fraction: float = 0.15  # per-bet cap as a fraction of bankroll
    naive_fraction: float = 0.0  # current independent-Kelly size, for comparison
    edge_pp: float = 0.0
    model_prob: float = 0.0


def build_payoff_matrix(bets: list[BetSpec], samples: list[dict[str, str]]) -> np.ndarray:
    """``R[i, j]`` = net per-dollar return of bet *j* in simulated tournament *i*
    (``1/price - 1`` if it wins, ``-1`` if it loses)."""
    n, k = len(samples), len(bets)
    R = np.empty((n, k), dtype=float)
    keys = {b.market_key for b in bets}
    winners = {key: np.array([s.get(key) for s in samples], dtype=object) for key in keys}
    for j, b in enumerate(bets):
        team_wins = winners[b.market_key] == b.team
        bet_wins = team_wins if b.side == "YES" else ~team_wins
        R[:, j] = np.where(bet_wins, (1.0 / b.price) - 1.0, -1.0)
    return R


def _group_index(bets: list[BetSpec]) -> dict[str, list[int]]:
    idx: dict[str, list[int]] = {}
    for j, b in enumerate(bets):
        idx.setdefault(b.group or "_", []).append(j)
    return idx


def _constraints(bets, deploy_cap, group_cap):
    cons = [{"type": "ineq", "fun": lambda w: deploy_cap - np.sum(w)}]
    for _g, idx in _group_index(bets).items():
        arr = np.array(idx)
        cons.append({"type": "ineq", "fun": (lambda w, a=arr, gc=group_cap: gc - np.sum(w[a]))})
    return cons


def optimize_growth(
    R: np.ndarray, bets: list[BetSpec], deploy_cap: float = 0.6, group_cap: float = 0.25
) -> np.ndarray:
    """Full-Kelly weights: maximise mean log-growth of the bankroll over the sims,
    long-only, under per-bet and per-group caps. Concave objective → global optimum."""
    n, k = R.shape
    caps = [max(1e-4, min(b.max_fraction, deploy_cap)) for b in bets]

    def neg_log_growth(w):
        port = R @ w
        return -np.mean(np.log1p(np.clip(port, -0.999, None)))

    if not _HAVE_SCIPY:
        # Fallback: proportional to per-bet edge, capped. Loses the correlation handling.
        w = np.array([max(b.edge_pp, 0.0) for b in bets], dtype=float)
        w = np.minimum(w / (w.sum() or 1.0) * deploy_cap, caps)
        return w

    bounds = [(0.0, c) for c in caps]
    w0 = np.minimum(np.full(k, deploy_cap / max(k, 1)), caps)
    res = minimize(
        neg_log_growth, w0, method="SLSQP", bounds=bounds,
        constraints=_constraints(bets, deploy_cap, group_cap),
        options={"maxiter": 500, "ftol": 1e-10},
    )
    return np.clip(res.x, 0.0, None)


def portfolio_stats(R: np.ndarray, w: np.ndarray) -> dict:
    """Risk/reward summary of a weight vector against the simulated payoff matrix."""
    port = R @ w
    invested = float(np.sum(w))
    mean = float(np.mean(port))
    std = float(np.std(port))
    weights_norm = w / invested if invested > 1e-9 else w
    eff_bets = float(1.0 / np.sum(weights_norm ** 2)) if invested > 1e-9 else 0.0
    return {
        "invested_fraction": invested,
        "exp_return_pct": mean * 100.0,
        "volatility_pct": std * 100.0,
        "sharpe": mean / std if std > 1e-9 else 0.0,
        "exp_log_growth_pct": float(np.mean(np.log1p(np.clip(port, -0.999, None)))) * 100.0,
        "prob_loss": float(np.mean(port < 0)),
        "p05_return_pct": float(np.percentile(port, 5)) * 100.0,
        "p95_return_pct": float(np.percentile(port, 95)) * 100.0,
        "max_weight": float(np.max(w)) if len(w) else 0.0,
        "effective_bets": eff_bets,
    }


def correlation_flags(R: np.ndarray, bets: list[BetSpec], threshold: float = 0.4) -> list[dict]:
    """Surface near-duplicate exposures (e.g. NO team-X vs YES team-Y in the same group)."""
    if R.shape[1] < 2:
        return []
    corr = np.corrcoef(R, rowvar=False)
    flags = []
    for i in range(len(bets)):
        for j in range(i + 1, len(bets)):
            c = corr[i, j]
            if abs(c) >= threshold:
                flags.append({
                    "a": bets[i].label or bets[i].bet_id,
                    "b": bets[j].label or bets[j].bet_id,
                    "corr": round(float(c), 2),
                })
    flags.sort(key=lambda f: -abs(f["corr"]))
    return flags[:12]


def build_portfolio(
    bets: list[BetSpec],
    samples: list[dict[str, str]],
    bankroll_usd: float,
    kelly_fraction: float = 0.34,
    deploy_cap: float = 0.6,
    group_cap: float = 0.25,
) -> dict:
    """End-to-end: payoff matrix -> growth-optimal allocation (fractional-Kelly scaled) +
    max-Sharpe tangency for comparison + the naive independent-Kelly book, all scored on the
    same simulated distribution so the diversification gain is measurable."""
    if not bets or not samples:
        return {"available": False, "reason": "no candidate bets or no simulation samples"}

    R = build_payoff_matrix(bets, samples)

    full_kelly = optimize_growth(R, bets, deploy_cap, group_cap)
    frac_kelly = full_kelly * kelly_fraction
    naive = np.array([min(max(b.naive_fraction, 0.0), b.max_fraction) for b in bets], dtype=float)

    def alloc_rows(w):
        rows = []
        for b, frac in zip(bets, w):
            if frac <= 1e-4:
                continue
            rows.append({
                "label": b.label or b.bet_id,
                "team": b.team,
                "side": b.side,
                "group": b.group,
                "price": round(b.price, 4),
                "weight_pct": round(float(frac) * 100.0, 2),
                "stake_usd": round(float(frac) * bankroll_usd, 2),
                "edge_pp": round(b.edge_pp, 1),
                "model_prob": round(b.model_prob, 4),
            })
        rows.sort(key=lambda r: -r["stake_usd"])
        return rows

    return {
        "available": True,
        "n_candidates": len(bets),
        "n_sims": len(samples),
        "bankroll_usd": round(bankroll_usd, 2),
        "kelly_fraction": kelly_fraction,
        "deploy_cap": deploy_cap,
        "group_cap": group_cap,
        "recommended": {
            "label": f"Growth-optimal (fractional Kelly x{kelly_fraction})",
            "allocation": alloc_rows(frac_kelly),
            "stats": portfolio_stats(R, frac_kelly),
        },
        "full_kelly": {"stats": portfolio_stats(R, full_kelly)},
        "naive_independent": {
            "allocation": alloc_rows(naive),
            "stats": portfolio_stats(R, naive) if naive.sum() > 1e-9 else {},
        },
        "correlations": correlation_flags(R, bets),
    }
