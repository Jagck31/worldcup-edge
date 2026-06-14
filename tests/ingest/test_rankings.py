from datetime import date
import unittest

import pandas as pd

from ingest.rankings import add_point_in_time_rankings


class RankingTests(unittest.TestCase):
    def test_rankings_join_uses_latest_prior_snapshot_without_future_leakage(self):
        matches = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2022-11-20"),
                    "home_team": "Qatar",
                    "away_team": "Ecuador",
                }
            ]
        )
        rankings = pd.DataFrame(
            [
                {
                    "rank_date": pd.Timestamp("2022-10-06"),
                    "team": "Qatar",
                    "rank": 50,
                    "points": 1441.41,
                },
                {
                    "rank_date": pd.Timestamp("2022-12-22"),
                    "team": "Qatar",
                    "rank": 60,
                    "points": 1390.00,
                },
                {
                    "rank_date": pd.Timestamp("2022-10-06"),
                    "team": "Ecuador",
                    "rank": 44,
                    "points": 1464.39,
                },
            ]
        )

        enriched = add_point_in_time_rankings(matches, rankings)

        row = enriched.iloc[0]
        self.assertEqual(row["home_rank"], 50)
        self.assertEqual(row["home_rank_points"], 1441.41)
        self.assertEqual(row["away_rank"], 44)
        self.assertEqual(row["rank_diff"], 6)

    def test_rankings_join_leaves_missing_values_when_no_prior_ranking_exists(self):
        matches = pd.DataFrame(
            [{"date": pd.Timestamp(date(1900, 1, 1)), "home_team": "A", "away_team": "B"}]
        )
        rankings = pd.DataFrame(
            [
                {
                    "rank_date": pd.Timestamp("1900-01-02"),
                    "team": "A",
                    "rank": 1,
                    "points": 10.0,
                }
            ]
        )

        enriched = add_point_in_time_rankings(matches, rankings)

        self.assertTrue(pd.isna(enriched.loc[0, "home_rank"]))
        self.assertTrue(pd.isna(enriched.loc[0, "away_rank"]))


if __name__ == "__main__":
    unittest.main()
