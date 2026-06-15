import unittest

import pandas as pd

from features.elo import EloConfig, EloEngine


class EloTests(unittest.TestCase):
    def test_elo_updates_winner_up_and_loser_down_with_pre_match_history(self):
        matches = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2022-11-20"),
                    "home_team": "Qatar",
                    "away_team": "Ecuador",
                    "home_score": 0,
                    "away_score": 2,
                    "tournament": "FIFA World Cup",
                    "neutral": False,
                }
            ]
        )
        engine = EloEngine(EloConfig(base_rating=1500.0, home_advantage=75.0))

        history = engine.process_matches(matches)

        self.assertEqual(history.pre_match_rating("Qatar", pd.Timestamp("2022-11-20")), 1500.0)
        self.assertEqual(history.pre_match_rating("Ecuador", pd.Timestamp("2022-11-20")), 1500.0)
        self.assertGreater(history.current_rating("Ecuador"), 1500.0)
        self.assertLess(history.current_rating("Qatar"), 1500.0)

    def test_match_delta_reports_per_match_swing(self):
        matches = pd.DataFrame(
            [{"date": pd.Timestamp("2026-06-15"), "home_team": "Spain", "away_team": "Cape Verde",
              "home_score": 0, "away_score": 0, "tournament": "FIFA World Cup", "neutral": True}]
        )
        # Spain starts well above Cape Verde, so a draw should cost Spain rating.
        engine = EloEngine(EloConfig(base_rating=1500.0))
        hist = engine.process_matches(matches)
        # seed unequal priors by processing a prior match isn't needed; equal priors -> draw delta 0.
        d_home = hist.match_delta("Spain", pd.Timestamp("2026-06-15"))
        d_away = hist.match_delta("Cape Verde", pd.Timestamp("2026-06-15"))
        self.assertIsNotNone(d_home)
        self.assertAlmostEqual(d_home, -d_away, places=6)  # symmetric swing
        # tolerance window: a result dated a day off the query still matches
        self.assertIsNotNone(hist.match_delta("Spain", pd.Timestamp("2026-06-16")))
        # a team with no match in range -> None
        self.assertIsNone(hist.match_delta("Brazil", pd.Timestamp("2026-06-15")))

    def test_match_delta_draw_vs_weaker_team_is_negative(self):
        matches = pd.DataFrame(
            [
                {"date": pd.Timestamp("2026-06-01"), "home_team": "Spain", "away_team": "Spain B",
                 "home_score": 5, "away_score": 0, "tournament": "Friendly", "neutral": True},
                {"date": pd.Timestamp("2026-06-15"), "home_team": "Spain", "away_team": "Minnow",
                 "home_score": 1, "away_score": 1, "tournament": "FIFA World Cup", "neutral": True},
            ]
        )
        hist = EloEngine(EloConfig()).process_matches(matches)
        self.assertLess(hist.match_delta("Spain", pd.Timestamp("2026-06-15")), 0)  # favourite drops on a draw

    def test_elo_neutral_matches_do_not_apply_home_advantage(self):
        neutral_matches = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2022-01-01"),
                    "home_team": "A",
                    "away_team": "B",
                    "home_score": 1,
                    "away_score": 0,
                    "tournament": "Friendly",
                    "neutral": True,
                }
            ]
        )
        home_matches = neutral_matches.assign(neutral=False)
        config = EloConfig(base_rating=1500.0, home_advantage=100.0)

        neutral_history = EloEngine(config).process_matches(neutral_matches)
        home_history = EloEngine(config).process_matches(home_matches)

        neutral_gain = neutral_history.current_rating("A") - 1500.0
        home_gain = home_history.current_rating("A") - 1500.0
        self.assertGreater(neutral_gain, home_gain)

    def test_pre_match_rating_for_future_date_reflects_last_result(self):
        matches = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2024-03-01"),
                    "home_team": "A",
                    "away_team": "B",
                    "home_score": 5,
                    "away_score": 0,
                    "tournament": "Friendly",
                    "neutral": True,
                }
            ]
        )
        history = EloEngine().process_matches(matches)
        # A future fixture must use A's rating AFTER the 5-0 win, not the stale pre-match 1500.
        future = history.pre_match_rating("A", pd.Timestamp("2026-06-15"))
        self.assertGreater(future, 1500.0)
        self.assertEqual(future, history.current_rating("A"))

    def test_elo_margin_of_victory_is_log_dampened(self):
        engine = EloEngine(EloConfig(base_rating=1500.0))

        one_goal = engine.margin_multiplier(1)
        five_goals = engine.margin_multiplier(5)

        self.assertEqual(one_goal, 1.0)
        self.assertGreater(five_goals, one_goal)
        self.assertLess(five_goals, 5.0)

    def test_k_factor_does_not_treat_finalissima_as_knockout_final(self):
        engine = EloEngine(EloConfig(friendly_k=10.0, knockout_k=50.0))

        self.assertEqual(engine.k_factor("CONMEBOL-UEFA Cup of Champions", "Finalissima"), 10.0)

    def test_k_factor_uses_structured_knockout_stage_names(self):
        engine = EloEngine(EloConfig(tournament_k=35.0, knockout_k=50.0))

        self.assertEqual(engine.k_factor("FIFA World Cup", "Quarter-finals"), 50.0)
        self.assertEqual(engine.k_factor("FIFA World Cup", "Group stage"), 35.0)


if __name__ == "__main__":
    unittest.main()
