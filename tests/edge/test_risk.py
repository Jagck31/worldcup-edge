import unittest

from edge.risk import position_risk


class PositionRiskTests(unittest.TestCase):
    def test_concentration_and_buckets(self):
        positions = [
            {"stake": 2000, "settle_date": "2026-06-27T00:00:00Z"},
            {"stake": 1500, "settle_date": "2026-06-27T00:00:00Z"},
            {"stake": 1000, "settle_date": "2026-06-27T00:00:00Z"},
            {"stake": 500, "settle_date": "2026-07-20T00:00:00Z"},
            {"stake": 10, "settle_date": "2026-07-20T00:00:00Z"},
        ]
        r = position_risk(positions, bankroll=10000)
        self.assertEqual(r["n_open"], 5)
        self.assertEqual(r["invested_usd"], 5010.0)
        self.assertEqual(r["max_position_pct"], 20.0)
        self.assertEqual(r["top3_pct"], 45.0)          # 2000+1500+1000 = 4500 / 10000
        self.assertEqual(r["top5_pct"], 50.1)
        self.assertEqual(r["n_settle_buckets"], 2)
        self.assertEqual(r["settle_buckets"]["2026-06-27"], 4500.0)

    def test_per_market_exposure(self):
        # Two correlated contracts in Group J = the real concentration the single-bet cap misses.
        positions = [
            {"stake": 2000, "market": "Win Group J", "settle_date": "2026-06-27T00:00:00Z"},
            {"stake": 1400, "market": "Win Group J", "settle_date": "2026-06-27T00:00:00Z"},
            {"stake": 1500, "market": "Win Group E", "settle_date": "2026-06-27T00:00:00Z"},
        ]
        r = position_risk(positions, bankroll=10000)
        self.assertEqual(r["max_market"], "Win Group J")
        self.assertEqual(r["max_market_pct"], 34.0)        # (2000+1400)/10000 > the 20% single-bet view
        self.assertEqual(r["market_exposure"]["Win Group J"], 3400.0)

    def test_empty_book(self):
        r = position_risk([], bankroll=10000)
        self.assertEqual(r["n_open"], 0)
        self.assertEqual(r["max_position_pct"], 0.0)
        self.assertEqual(r["invested_usd"], 0.0)

    def test_zero_bankroll_is_safe(self):
        r = position_risk([{"stake": 100, "settle_date": ""}], bankroll=0)
        self.assertIsNone(r["invested_pct"])
        self.assertEqual(r["n_open"], 1)


if __name__ == "__main__":
    unittest.main()
