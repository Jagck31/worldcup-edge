import unittest
from pathlib import Path
import tempfile

import pandas as pd

from ingest.fixtures import load_fixtures, merge_live_results


class FixtureTests(unittest.TestCase):
    def test_load_fixtures_parses_manual_yes_no_neutral_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixtures.csv"
            path.write_text(
                "\n".join(
                    [
                        "match_id,date,group,stage,home_team,away_team,venue,country,neutral,home_score,away_score,status",
                        "A1,2026-06-11,A,Group,A,B,Stadium,USA,No,,,scheduled",
                        "A2,2026-06-12,A,Group,C,D,Stadium,USA,Yes,,,scheduled",
                    ]
                ),
                encoding="utf-8",
            )

            state = load_fixtures(path)

        self.assertFalse(bool(state.fixtures.loc[0, "neutral"]))
        self.assertTrue(bool(state.fixtures.loc[1, "neutral"]))

    def test_merge_live_results_rejects_duplicate_match_ids_clearly(self):
        fixtures = pd.DataFrame(
            [
                {
                    "match_id": "A1",
                    "home_team": "A",
                    "away_team": "B",
                    "home_score": pd.NA,
                    "away_score": pd.NA,
                    "status": "scheduled",
                }
            ]
        )
        completed = pd.DataFrame(
            [
                {"match_id": "A1", "home_score": 1, "away_score": 0},
                {"match_id": "A1", "home_score": 2, "away_score": 0},
            ]
        )

        with self.assertRaisesRegex(ValueError, "Duplicate completed result match_id"):
            merge_live_results(fixtures, completed)

    def test_merge_live_results_rejects_missing_scores_clearly(self):
        fixtures = pd.DataFrame(
            [
                {
                    "match_id": "A1",
                    "home_team": "A",
                    "away_team": "B",
                    "home_score": pd.NA,
                    "away_score": pd.NA,
                    "status": "scheduled",
                }
            ]
        )
        completed = pd.DataFrame(
            [
                {"match_id": "A1", "home_score": pd.NA, "away_score": 0},
            ]
        )

        with self.assertRaisesRegex(ValueError, "Missing completed score for match_id A1"):
            merge_live_results(fixtures, completed)


if __name__ == "__main__":
    unittest.main()
