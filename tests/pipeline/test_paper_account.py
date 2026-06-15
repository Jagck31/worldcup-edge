import unittest

from pipeline.paper_account import update_account


def _row(market, market_id, side, edge_pp, target, price=0.5, model_prob=0.9):
    return {
        "market": market,
        "market_id": market_id,
        "side": side,
        "action": f"BUY {side}",
        "actionable": True,
        "edge_pp": edge_pp,
        "capped_size_usd": target,
        "kelly_size_usd": target,
        "exec_price": price,
        "model_prob": model_prob,
        "settle_date": "2026-06-27T00:00:00Z",
    }


class PerMarketCapTests(unittest.TestCase):
    def _fresh(self, bankroll=10000.0):
        return {
            "starting_bankroll": bankroll, "cash": bankroll, "realized_pnl": 0.0,
            "n_trades": 0, "positions": [], "history": [], "created_at": None,
            "updated_at": None, "summary": {},
        }

    def test_per_market_cap_bounds_correlated_contracts(self):
        # Two correlated contracts in the same market each "want" 30% of bankroll. With a 20%
        # market cap, their COMBINED stake must not exceed 20% — the second is clamped/skipped.
        slate = [
            _row("Win Group J", "J-fav-YES", "YES", edge_pp=20, target=3000),
            _row("Win Group J", "J-long-NO", "NO", edge_pp=15, target=3000),
        ]
        acct = update_account(
            self._fresh(), slate, market_prices={}, now_iso="2026-06-15T00:00:00Z",
            size_mode="kelly", max_total_exposure_pct=0.80, min_stake_usd=5.0,
            max_market_exposure_pct=0.20,
        )
        group_j = sum(p["stake"] for p in acct["positions"] if p["market"] == "Win Group J")
        self.assertLessEqual(group_j, 2000.0 + 1e-6)

    def test_disabled_cap_allows_old_concentration(self):
        slate = [
            _row("Win Group J", "J-fav-YES", "YES", edge_pp=20, target=3000),
            _row("Win Group J", "J-long-NO", "NO", edge_pp=15, target=3000),
        ]
        acct = update_account(
            self._fresh(), slate, market_prices={}, now_iso="2026-06-15T00:00:00Z",
            size_mode="kelly", max_total_exposure_pct=0.80, min_stake_usd=5.0,
            max_market_exposure_pct=1.0,  # disabled
        )
        group_j = sum(p["stake"] for p in acct["positions"] if p["market"] == "Win Group J")
        self.assertAlmostEqual(group_j, 6000.0, places=2)

    def test_cap_is_per_market_not_global(self):
        # Three different markets each get their full 20% — the cap is per-bucket, so the book
        # diversifies across events instead of piling into one.
        slate = [
            _row("Win Group J", "J-YES", "YES", edge_pp=20, target=3000),
            _row("Win Group K", "K-YES", "YES", edge_pp=18, target=3000),
            _row("Win Group L", "L-YES", "YES", edge_pp=16, target=3000),
        ]
        acct = update_account(
            self._fresh(), slate, market_prices={}, now_iso="2026-06-15T00:00:00Z",
            size_mode="kelly", max_total_exposure_pct=0.80, min_stake_usd=5.0,
            max_market_exposure_pct=0.20,
        )
        by_market = {}
        for p in acct["positions"]:
            by_market[p["market"]] = by_market.get(p["market"], 0.0) + p["stake"]
        self.assertEqual(len(by_market), 3)
        for stake in by_market.values():
            self.assertAlmostEqual(stake, 2000.0, places=2)

    def test_existing_positions_seed_the_bucket(self):
        # An existing $1,800 position in Group J leaves only $200 of headroom under a 20% cap.
        acct = self._fresh()
        acct["cash"] = 8200.0
        acct["n_trades"] = 1
        acct["positions"] = [{
            "trade_id": 1, "market": "Win Group J", "market_id": "J-fav-YES", "side": "YES",
            "entry_price": 0.5, "model_prob": 0.9, "shares": 3600.0, "stake": 1800.0,
            "current_price": 0.5, "current_value": 1800.0, "unrealized_pnl": 0.0,
            "settle_date": "2026-06-27T00:00:00Z", "status": "open",
        }]
        slate = [_row("Win Group J", "J-long-NO", "NO", edge_pp=15, target=3000)]
        out = update_account(
            acct, slate, market_prices={}, now_iso="2026-06-15T00:00:00Z",
            size_mode="kelly", max_total_exposure_pct=0.80, min_stake_usd=5.0,
            max_market_exposure_pct=0.20,
        )
        group_j = sum(p["stake"] for p in out["positions"] if p["market"] == "Win Group J")
        self.assertLessEqual(group_j, 2000.0 + 1e-6)
        self.assertGreater(group_j, 1800.0)  # the new bet added up to the cap, not zero


if __name__ == "__main__":
    unittest.main()
