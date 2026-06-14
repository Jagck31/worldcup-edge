import unittest

from edge.scanner import ConsistencyMarket, scan_sum_to_one


class ScannerTests(unittest.TestCase):
    def test_scanner_flags_group_winner_sum_incoherence_above_buffer(self):
        markets = [
            ConsistencyMarket("Group A winner - A", "A", executable_yes_price=0.40, fillable_usd=15),
            ConsistencyMarket("Group A winner - B", "A", executable_yes_price=0.35, fillable_usd=15),
            ConsistencyMarket("Group A winner - C", "A", executable_yes_price=0.30, fillable_usd=15),
            ConsistencyMarket("Group A winner - D", "A", executable_yes_price=0.20, fillable_usd=15),
        ]

        flags = scan_sum_to_one(markets, tolerance_pp=10.0)

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].group_key, "A")
        self.assertEqual(flags[0].sum_implied_probability, 1.25)
        self.assertEqual(flags[0].direction, "overpriced")

    def test_scanner_can_ignore_normal_vig_on_buyable_asks(self):
        markets = [
            ConsistencyMarket("Group A winner - A", "A", executable_yes_price=0.30, fillable_usd=15),
            ConsistencyMarket("Group A winner - B", "A", executable_yes_price=0.27, fillable_usd=15),
            ConsistencyMarket("Group A winner - C", "A", executable_yes_price=0.26, fillable_usd=15),
            ConsistencyMarket("Group A winner - D", "A", executable_yes_price=0.24, fillable_usd=15),
        ]

        flags = scan_sum_to_one(markets, tolerance_pp=5.0, alert_overpriced=False)

        self.assertEqual(flags, [])

    def test_scanner_parameterizes_expected_total_for_advancement_families(self):
        markets = [
            ConsistencyMarket("Group A advance - A", "A", executable_yes_price=0.54, fillable_usd=15),
            ConsistencyMarket("Group A advance - B", "A", executable_yes_price=0.51, fillable_usd=15),
            ConsistencyMarket("Group A advance - C", "A", executable_yes_price=0.49, fillable_usd=15),
            ConsistencyMarket("Group A advance - D", "A", executable_yes_price=0.44, fillable_usd=15),
        ]

        flags = scan_sum_to_one(markets, tolerance_pp=5.0, expected_total=2.0)

        self.assertEqual(flags, [])

    def test_scanner_can_require_complete_market_family_before_flagging(self):
        markets = [
            ConsistencyMarket("Group A winner - A", "A", executable_yes_price=0.30, fillable_usd=15),
            ConsistencyMarket("Group A winner - B", "A", executable_yes_price=0.30, fillable_usd=15),
            ConsistencyMarket("Group A winner - C", "A", executable_yes_price=0.30, fillable_usd=15),
            ConsistencyMarket("Group A winner - D", "A", executable_yes_price=0.05, fillable_usd=1),
        ]

        flags = scan_sum_to_one(
            markets,
            tolerance_pp=5.0,
            min_fillable_usd=5.0,
            expected_market_count=4,
        )

        self.assertEqual(flags, [])


if __name__ == "__main__":
    unittest.main()
