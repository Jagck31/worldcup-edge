import unittest

from edge.kelly import KellyConfig, size_bet


class KellyTests(unittest.TestCase):
    def test_fractional_kelly_size_is_clamped_by_single_bet_cap_and_liquidity(self):
        config = KellyConfig(
            bankroll_usd=75,
            kelly_fraction=0.25,
            max_single_bet_pct=0.20,
            max_total_exposure_pct=0.80,
            min_fillable_usd=5,
        )

        sized = size_bet(model_probability=0.90, executable_price=0.50, fillable_usd=30, config=config)

        self.assertGreater(sized.raw_kelly_fraction, 0)
        self.assertEqual(sized.capped_size_usd, 15.0)
        self.assertEqual(sized.fillable_size_usd, 15.0)

    def test_kelly_rejects_bet_below_minimum_fillable_size(self):
        config = KellyConfig(bankroll_usd=75, min_fillable_usd=5)

        sized = size_bet(model_probability=0.62, executable_price=0.50, fillable_usd=4.99, config=config)

        self.assertEqual(sized.fillable_size_usd, 0.0)
        self.assertEqual(sized.reason, "below_min_fillable")


if __name__ == "__main__":
    unittest.main()
