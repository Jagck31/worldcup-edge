import unittest

import pandas as pd

from model.goal_model import PoissonGoalModel


class GoalModelTests(unittest.TestCase):
    def test_goal_model_learns_higher_expected_goals_for_stronger_attack(self):
        matches = pd.DataFrame(
            [
                {"home_team": "A", "away_team": "B", "home_score": 3, "away_score": 0},
                {"home_team": "A", "away_team": "C", "home_score": 2, "away_score": 0},
                {"home_team": "B", "away_team": "C", "home_score": 1, "away_score": 1},
                {"home_team": "C", "away_team": "A", "home_score": 0, "away_score": 2},
            ]
        )

        model = PoissonGoalModel().fit(matches)
        strong_home, weak_away = model.expected_goals("A", "B")
        weak_home, strong_away = model.expected_goals("B", "A")

        self.assertGreater(strong_home, weak_home)
        self.assertGreater(strong_away, weak_away)

    def test_scoreline_distribution_sums_close_to_one_after_truncation(self):
        matches = pd.DataFrame(
            [
                {"home_team": "A", "away_team": "B", "home_score": 1, "away_score": 1},
                {"home_team": "B", "away_team": "A", "home_score": 0, "away_score": 2},
            ]
        )

        model = PoissonGoalModel(max_goals=8).fit(matches)
        distribution = model.scoreline_distribution("A", "B")

        self.assertGreater(sum(distribution.values()), 0.99)
        self.assertIn((1, 0), distribution)

    def test_tiny_sample_blowout_is_shrunk_toward_average(self):
        matches = pd.DataFrame(
            [
                {"home_team": "Tiny", "away_team": "Victim", "home_score": 8, "away_score": 0},
                {"home_team": "BaselineA", "away_team": "BaselineB", "home_score": 1, "away_score": 1},
                {"home_team": "BaselineC", "away_team": "BaselineD", "home_score": 1, "away_score": 1},
                {"home_team": "BaselineE", "away_team": "BaselineF", "home_score": 1, "away_score": 1},
            ]
        )

        # Raw path: a blowout inflates attack, and shrinkage pulls a tiny sample back toward 1.0.
        raw_model = PoissonGoalModel(opponent_adjusted=False, shrinkage_matches=0).fit(matches)
        shrunk_model = PoissonGoalModel(opponent_adjusted=False, shrinkage_matches=8).fit(matches)

        self.assertLess(shrunk_model.attack_strength["Tiny"], raw_model.attack_strength["Tiny"])
        self.assertGreater(shrunk_model.attack_strength["Tiny"], 1.0)

    def test_opponent_adjustment_credits_goals_by_defence_faced(self):
        # ScorerA and ScorerB both score 2, but A does it against a strong defence and B against
        # a weak one. Opponent adjustment should rate A's attack above B's; the raw method ties them.
        matches = pd.DataFrame(
            [
                {"home_team": "StrongDef", "away_team": "X1", "home_score": 0, "away_score": 0},
                {"home_team": "StrongDef", "away_team": "X2", "home_score": 0, "away_score": 0},
                {"home_team": "WeakDef", "away_team": "Y1", "home_score": 0, "away_score": 4},
                {"home_team": "WeakDef", "away_team": "Y2", "home_score": 0, "away_score": 4},
                {"home_team": "ScorerA", "away_team": "StrongDef", "home_score": 2, "away_score": 0},
                {"home_team": "ScorerB", "away_team": "WeakDef", "home_score": 2, "away_score": 0},
            ]
        )

        adjusted = PoissonGoalModel(opponent_adjusted=True, shrinkage_matches=0).fit(matches)
        self.assertGreater(adjusted.attack_strength["ScorerA"], adjusted.attack_strength["ScorerB"])

    def test_time_decay_weights_recent_goal_form_more_heavily(self):
        matches = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2020-01-01"),
                    "home_team": "Trend",
                    "away_team": "Peer",
                    "home_score": 0,
                    "away_score": 1,
                },
                {
                    "date": pd.Timestamp("2024-01-01"),
                    "home_team": "Trend",
                    "away_team": "Peer",
                    "home_score": 4,
                    "away_score": 1,
                },
                {
                    "date": pd.Timestamp("2024-01-01"),
                    "home_team": "BaselineA",
                    "away_team": "BaselineB",
                    "home_score": 1,
                    "away_score": 1,
                },
            ]
        )

        even_model = PoissonGoalModel(shrinkage_matches=0).fit(matches)
        decay_model = PoissonGoalModel(shrinkage_matches=0, half_life_days=365).fit(
            matches,
            as_of_date=pd.Timestamp("2024-01-02"),
        )

        self.assertGreater(decay_model.attack_strength["Trend"], even_model.attack_strength["Trend"])


if __name__ == "__main__":
    unittest.main()
