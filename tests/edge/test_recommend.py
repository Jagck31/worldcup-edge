import unittest

from edge.detect import EdgeCandidate
from edge.kelly import KellyConfig
from edge.recommend import build_recommendations, recommendations_to_state_rows, summarize_recommendations


class RecommendationTests(unittest.TestCase):
    def test_recommendations_rank_by_kelly_impact_not_edge_points_only(self):
        config = KellyConfig(
            bankroll_usd=100,
            kelly_fraction=0.25,
            max_single_bet_pct=0.50,
            max_total_exposure_pct=1.0,
            min_fillable_usd=1,
        )
        cheaper_longshot = EdgeCandidate(
            market_name="Reach Final - USA",
            market_id="final_usa",
            model_probability=0.98,
            executable_price=0.90,
            edge_pp=8.0,
            ev_per_dollar=0.0889,
            fillable_usd=50,
            side="YES",
        )
        prettier_edge_points = EdgeCandidate(
            market_name="Advance - Mexico",
            market_id="advance_mexico",
            model_probability=0.60,
            executable_price=0.50,
            edge_pp=10.0,
            ev_per_dollar=0.20,
            fillable_usd=50,
            side="YES",
        )

        recommendations = build_recommendations([prettier_edge_points, cheaper_longshot], config)

        self.assertEqual(recommendations[0].market_name, "Reach Final - USA")
        self.assertGreater(recommendations[0].kelly_fraction, recommendations[1].kelly_fraction)
        self.assertEqual([item.rank for item in recommendations], [1, 2])

    def test_recommendations_apply_total_exposure_cap_in_ranked_order(self):
        config = KellyConfig(
            bankroll_usd=100,
            kelly_fraction=0.25,
            max_single_bet_pct=0.50,
            max_total_exposure_pct=0.10,
            min_fillable_usd=1,
        )
        first = EdgeCandidate(
            market_name="Champion - Brazil",
            market_id="champ_brazil",
            model_probability=0.80,
            executable_price=0.40,
            edge_pp=40.0,
            ev_per_dollar=1.0,
            fillable_usd=50,
            side="YES",
        )
        second = EdgeCandidate(
            market_name="Champion - France",
            market_id="champ_france",
            model_probability=0.75,
            executable_price=0.42,
            edge_pp=33.0,
            ev_per_dollar=0.7857,
            fillable_usd=50,
            side="YES",
        )

        recommendations = build_recommendations([first, second], config)

        self.assertEqual(recommendations[0].kelly_size_usd, 10.0)
        self.assertEqual(recommendations[0].status, "ok")
        self.assertEqual(recommendations[1].kelly_size_usd, 0.0)
        self.assertEqual(recommendations[1].status, "portfolio_cap_reached")

    def test_no_side_recommendation_has_terminal_ready_summary(self):
        config = KellyConfig(bankroll_usd=75, min_fillable_usd=1)
        edge = EdgeCandidate(
            market_name="Advance - Team A",
            market_id="advance_team_a",
            model_probability=0.65,
            executable_price=0.52,
            edge_pp=13.0,
            ev_per_dollar=0.25,
            fillable_usd=25,
            side="NO",
        )

        recommendation = build_recommendations([edge], config)[0]

        self.assertEqual(recommendation.action, "BUY NO")
        self.assertIn("BUY NO", recommendation.summary)
        self.assertIn("model 65.0%", recommendation.summary)
        self.assertIn("price 52.0%", recommendation.summary)
        self.assertIn("+13.0pp", recommendation.summary)

    def test_recommendations_export_dashboard_compatible_rows_with_action_badges(self):
        config = KellyConfig(bankroll_usd=75, min_fillable_usd=1)
        edge = EdgeCandidate(
            market_name="Reach Final - Argentina",
            market_id="final_argentina",
            model_probability=0.42,
            executable_price=0.31,
            edge_pp=11.0,
            ev_per_dollar=0.3548,
            fillable_usd=25,
            side="YES",
        )
        recommendation = build_recommendations([edge], config)[0]

        row = recommendations_to_state_rows([recommendation])[0]

        self.assertEqual(row["rank"], 1)
        self.assertEqual(row["market"], "Reach Final")
        self.assertEqual(row["team"], "Argentina")
        self.assertEqual(row["side"], "YES")
        self.assertEqual(row["action"], "BUY YES")
        self.assertEqual(row["risk_label"], "standard")
        self.assertTrue(row["actionable"])
        self.assertIn("BUY YES", row["summary"])
        self.assertEqual(row["model_prob"], 0.42)
        self.assertEqual(row["exec_price"], 0.31)

    def test_portfolio_summary_counts_actionable_watchlist_and_side_mix(self):
        config = KellyConfig(bankroll_usd=100, max_total_exposure_pct=0.20, min_fillable_usd=1)
        yes_edge = EdgeCandidate(
            market_name="Champion - Brazil",
            market_id="champ_brazil",
            model_probability=0.90,
            executable_price=0.10,
            edge_pp=80.0,
            ev_per_dollar=8.0,
            fillable_usd=50,
            side="YES",
        )
        no_edge = EdgeCandidate(
            market_name="Advance - Team B",
            market_id="advance_team_b",
            model_probability=0.65,
            executable_price=0.52,
            edge_pp=13.0,
            ev_per_dollar=0.25,
            fillable_usd=25,
            side="NO",
        )
        recommendations = build_recommendations([yes_edge, no_edge], config)

        summary = summarize_recommendations(recommendations, config)

        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["actionable_count"], 1)
        self.assertEqual(summary["watchlist_count"], 1)
        self.assertEqual(summary["side_counts"], {"YES": 1, "NO": 1})
        self.assertEqual(summary["total_recommended_exposure_usd"], 20.0)
        self.assertEqual(summary["exposure_pct_bankroll"], 20.0)
        self.assertEqual(summary["exposure_cap_remaining_usd"], 0.0)

    def test_portfolio_summary_accounts_for_existing_exposure_when_reporting_cap_remaining(self):
        config = KellyConfig(
            bankroll_usd=100,
            kelly_fraction=0.25,
            max_single_bet_pct=0.20,
            max_total_exposure_pct=0.50,
            min_fillable_usd=1,
        )
        edge = EdgeCandidate(
            market_name="Champion - Spain",
            market_id="champ_spain",
            model_probability=0.90,
            executable_price=0.10,
            edge_pp=80.0,
            ev_per_dollar=8.0,
            fillable_usd=50,
            side="YES",
        )
        recommendations = build_recommendations(
            [edge],
            config,
            current_total_exposure_usd=30.0,
        )

        summary = summarize_recommendations(
            recommendations,
            config,
            current_total_exposure_usd=30.0,
        )

        self.assertEqual(summary["total_recommended_exposure_usd"], 20.0)
        self.assertEqual(summary["current_exposure_usd"], 30.0)
        self.assertEqual(summary["total_projected_exposure_usd"], 50.0)
        self.assertEqual(summary["exposure_cap_remaining_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
