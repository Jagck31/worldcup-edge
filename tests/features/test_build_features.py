import unittest

import pandas as pd

from features.build_features import build_match_features
from features.elo import EloEngine


class BuildFeatureTests(unittest.TestCase):
    def test_build_features_uses_only_prior_matches_for_form_and_elo(self):
        matches = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2022-01-01"),
                    "home_team": "A",
                    "away_team": "B",
                    "home_score": 3,
                    "away_score": 0,
                    "tournament": "Friendly",
                    "neutral": False,
                },
                {
                    "date": pd.Timestamp("2022-01-10"),
                    "home_team": "A",
                    "away_team": "B",
                    "home_score": 0,
                    "away_score": 2,
                    "tournament": "Friendly",
                    "neutral": False,
                },
            ]
        )
        elo_history = EloEngine().process_matches(matches)
        rankings = pd.DataFrame(
            [
                {"rank_date": pd.Timestamp("2021-12-15"), "team": "A", "rank": 10, "points": 1600.0},
                {"rank_date": pd.Timestamp("2021-12-15"), "team": "B", "rank": 20, "points": 1500.0},
            ]
        )

        features = build_match_features(matches, elo_history, rankings)
        second = features.iloc[1]

        self.assertEqual(second["home_goals_for_last5"], 3.0)
        self.assertEqual(second["away_goals_for_last5"], 0.0)
        self.assertGreater(second["home_elo"], 1500.0)
        self.assertLess(second["away_elo"], 1500.0)
        self.assertEqual(second["target_1x2"], "A")


if __name__ == "__main__":
    unittest.main()
