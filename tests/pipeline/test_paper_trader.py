import unittest

from pipeline.paper_trader import PaperLedger


class PaperTraderTests(unittest.TestCase):
    def test_record_slate_preserves_no_side_from_recommendation_rows(self):
        ledger = PaperLedger(bankroll_usd=50, max_total_exposure_pct=1.0, paper_floor_usd=1.0)

        ledger.record_slate(
            [
                {
                    "market": "Advance",
                    "team": "Team B",
                    "side": "NO",
                    "action": "BUY NO",
                    "model_prob": 0.65,
                    "exec_price": 0.52,
                    "edge_pp": 13.0,
                    "uncapped_size_usd": 4.0,
                }
            ]
        )

        summary = ledger.summary()

        self.assertEqual(summary["n_positions"], 1)
        self.assertEqual(summary["positions"][0]["side"], "NO")


if __name__ == "__main__":
    unittest.main()
