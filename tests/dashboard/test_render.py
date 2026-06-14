import io
import json
import tempfile
import unittest
from pathlib import Path

from rich.console import Console

from dashboard.app import DashboardApp
from dashboard.render import bar, load_state, pct


def _sample_state() -> dict:
    return {
        "generated_at": "2026-06-13T00:00:00Z",
        "config": {"bankroll_usd": 75, "kelly_fraction": 0.25, "max_single_bet_pct": 0.2, "min_edge_pp": 5.0},
        "data": {
            "source": "cached",
            "n_matches": 49409,
            "n_teams": 336,
            "date_min": "1872-11-30",
            "date_max": "2026-06-12",
            "path": "data/raw/results.csv",
        },
        "elo": {
            "config": {"base_rating": 1500.0},
            "n_teams_rated": 3,
            "leaderboard": [
                {"team": "Spain", "rating": 2167.0},
                {"team": "Argentina", "rating": 2103.2},
                {"team": "Brazil", "rating": 2034.4},
            ],
        },
        "model": {
            "kind": "hist_gradient_boosting",
            "n_train": 1000,
            "n_calibration": 300,
            "n_validation": 300,
            "feature_columns": ["elo_diff", "home_elo", "away_elo"],
            "feature_importance": [{"feature": "elo_diff", "importance": 0.23}],
            "calibration": {
                "uncalibrated": {"log_loss": 0.88, "brier": 0.52, "accuracy": 0.60, "verdict": "research_only"},
                "calibrated": {"log_loss": 0.89, "brier": 0.51, "accuracy": 0.60, "verdict": "research_only"},
            },
            "reliability": [
                {"class": "H", "bin": "(0.4, 0.5]", "mean_predicted": 0.45, "observed_rate": 0.47, "count": 120}
            ],
            "architecture": {"inputs": 3, "stages": []},
        },
        "goal_model": {"global_home_goals": 1.6, "global_away_goals": 1.1, "n_teams": 200},
        "simulation": {
            "n_sims": 3000,
            "draw_label": "DEMO",
            "groups": {"A": ["Spain", "Senegal", "USA", "Chile"]},
            "submarkets": [
                {
                    "team": "Spain",
                    "p_win_group": 0.6,
                    "p_advanced": 0.96,
                    "p_last_16": 0.7,
                    "p_last_8": 0.5,
                    "p_last_4": 0.4,
                    "p_finalist": 0.35,
                    "p_champion": 0.24,
                }
            ],
        },
        "markets": {
            "source": "SAMPLE",
            "note": "synthetic",
            "total_recommended_exposure_usd": 28.98,
            "slate": [
                {
                    "market": "Advance",
                    "team": "Spain",
                    "model_prob": 0.96,
                    "exec_price": 0.84,
                    "edge_pp": 12.0,
                    "ev_per_dollar": 0.14,
                    "kelly_fraction": 0.19,
                    "uncapped_size_usd": 14.18,
                    "capped_size_usd": 14.18,
                    "kelly_size_usd": 10.0,
                    "status": "ok",
                    "actionable": True,
                }
            ],
            "scanner_flags": [
                {"group": "Group J", "sum_implied": 1.096, "gap_pp": 9.6, "direction": "overpriced", "markets": 4}
            ],
        },
        "notes": ["demo note"],
        "pipeline_log": [{"step": "Load results", "status": "ok", "seconds": 0.1, "detail": "49,409 matches"}],
    }


class RenderHelperTests(unittest.TestCase):
    def test_bar_clamps_and_scales(self):
        self.assertEqual(len(str(bar(5, 10, width=10))), 10)
        self.assertEqual(len(str(bar(50, 10, width=10))), 10)  # over-max clamps, not overflow

    def test_pct_formats(self):
        self.assertEqual(pct(0.24), "24.0%")
        self.assertEqual(pct("bad"), "—")


class DashboardViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "state.json"
        path.write_text(json.dumps(_sample_state()), encoding="utf-8")
        self.app = DashboardApp(state_path=path)

    def tearDown(self):
        self.tmp.cleanup()

    def _render(self, renderable) -> str:
        console = Console(file=io.StringIO(), width=118, color_system=None)
        console.print(renderable)
        return console.file.getvalue()

    def test_all_views_render_without_error(self):
        builders = [
            self.app._overview,
            self.app._data,
            self.app._elo,
            self.app._model,
            self.app._sim,
            self.app._markets,
        ]
        for builder in builders:
            text = self._render(builder())
            self.assertGreater(len(text), 50)

    def test_key_content_present(self):
        self.assertIn("Spain", self._render(self.app._elo()))
        self.assertIn("Architecture", self._render(self.app._model()))
        self.assertIn("Advance", self._render(self.app._markets()))
        self.assertIn("Group Draw", self._render(self.app._sim()))

    def test_missing_state_shows_prompt(self):
        app = DashboardApp(state_path=Path(self.tmp.name) / "nope.json")
        self.assertEqual(app.state, {})


if __name__ == "__main__":
    unittest.main()
