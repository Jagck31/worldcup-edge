"""Paper-trading ledger and a gated live-execution interface.

Turns the edge slate into a paper portfolio with expected value — no real money, no
wallet, no signing. Live execution is intentionally walled off behind explicit, multi-flag
opt-in (and is a no-op stub here): the spec mandates manual execution and bankroll
protection. This module exists so the capability is *ready* and the economics are visible,
not so the bot trades on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PaperPosition:
    market: str
    team: str
    side: str          # "YES" or "NO"
    model_prob: float
    price: float
    stake_usd: float
    shares: float
    expected_value_usd: float  # stake * (model_prob/price - 1)
    edge_pp: float


@dataclass
class PaperLedger:
    """A hypothetical portfolio built from positive-edge opportunities.

    Sizes each position at uncapped fractional-Kelly with a small paper floor (so the
    portfolio is non-empty even when real min-fill suppresses the bet), capped by total
    exposure. Reports total stake, expected profit, and expected ROI *assuming the model
    is right* — which is exactly the open question the live tracker answers over time.
    """

    bankroll_usd: float = 75.0
    max_total_exposure_pct: float = 0.80
    paper_floor_usd: float = 1.0
    positions: list[PaperPosition] = field(default_factory=list)

    def record_slate(self, slate: list[dict]) -> None:
        exposure_cap = self.bankroll_usd * self.max_total_exposure_pct
        spent = 0.0
        for row in slate:
            price = float(row.get("exec_price", 0.0))
            model_prob = float(row.get("model_prob", 0.0))
            if price <= 0.0 or price >= 1.0 or model_prob <= price:
                continue
            kelly_stake = float(row.get("uncapped_size_usd", 0.0))
            stake = max(self.paper_floor_usd, kelly_stake)
            stake = min(stake, max(0.0, exposure_cap - spent))
            if stake <= 0.0:
                continue
            side = str(row.get("side", "YES")).strip().upper()
            if side not in {"YES", "NO"}:
                side = "YES"
            spent += stake
            shares = stake / price
            ev = stake * (model_prob / price - 1.0)
            self.positions.append(
                PaperPosition(
                    market=str(row.get("market", "")),
                    team=str(row.get("team", "")),
                    side=side,
                    model_prob=round(model_prob, 4),
                    price=round(price, 4),
                    stake_usd=round(stake, 2),
                    shares=round(shares, 1),
                    expected_value_usd=round(ev, 2),
                    edge_pp=float(row.get("edge_pp", 0.0)),
                )
            )

    def summary(self) -> dict:
        total_stake = round(sum(p.stake_usd for p in self.positions), 2)
        total_ev = round(sum(p.expected_value_usd for p in self.positions), 2)
        return {
            "n_positions": len(self.positions),
            "total_stake_usd": total_stake,
            "expected_profit_usd": total_ev,
            "expected_roi_pct": round(100 * total_ev / total_stake, 1) if total_stake > 0 else 0.0,
            "bankroll_usd": self.bankroll_usd,
            "positions": [p.__dict__ for p in self.positions],
        }


def live_execution_status(enabled: bool = False, has_credentials: bool = False) -> dict:
    """Report the live-execution gate. Disabled by default and a no-op stub.

    Enabling real trading would require: a funded Polygon wallet, USDC, the Polymarket
    CLOB API credentials, EIP-712 order signing, and explicit per-order confirmation —
    none of which are wired here on purpose.
    """
    requirements = [
        "Funded Polygon wallet + USDC balance",
        "Polymarket CLOB API key/secret",
        "EIP-712 order signing (not implemented)",
        "Explicit per-order confirmation + bankroll/exposure caps",
        "Two opt-in flags: config use_live_execution AND a runtime --i-understand confirmation",
    ]
    armed = bool(enabled and has_credentials)
    return {
        "enabled": False if not armed else True,
        "mode": "ARMED (stub)" if armed else "DISABLED — manual execution only",
        "note": "Live order placement is intentionally not wired. The slate is for manual review.",
        "requirements_to_enable": requirements,
    }
