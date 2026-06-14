from pathlib import Path
import tempfile
import unittest

from pipeline.run_live import write_markdown_report


class ReportTests(unittest.TestCase):
    def test_report_prominently_lists_calibration_before_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = write_markdown_report(
                output_dir=Path(tmp),
                calibration={"log_loss": 0.98, "brier": 0.21, "verdict": "research_only"},
                slate=[],
                scanner_flags=[],
                bankroll_exposure={"bankroll_usd": 75, "open_exposure_usd": 0},
            )

            text = report.read_text(encoding="utf-8")
            self.assertLess(text.index("## Calibration Health"), text.index("## Ranked Betting Slate"))
            self.assertIn("Manual execution only", text)
            self.assertIn("variance dominates", text)


if __name__ == "__main__":
    unittest.main()
