import random
import unittest

import numpy as np
import pandas as pd

from model.calibrate import IsotonicCalibrator
from model.predict import CalibratedPredictor
from model.train import TARGET_CLASSES, train_1x2


def _synthetic_matches(n: int) -> pd.DataFrame:
    rng = random.Random(1)
    rows = []
    start = pd.Timestamp("2015-01-01")
    for i in range(n):
        elo_diff = rng.gauss(0, 200)
        # Stronger Elo edge -> more likely home win; noise keeps draws/away in play.
        roll = rng.random() + elo_diff / 600.0
        target = "H" if roll > 0.7 else "A" if roll < 0.2 else "D"
        rows.append(
            {
                "date": start + pd.Timedelta(days=i),
                "elo_diff": elo_diff,
                "home_elo": 1500 + elo_diff / 2,
                "away_elo": 1500 - elo_diff / 2,
                "home_rank": rng.randint(1, 50),
                "away_rank": rng.randint(1, 50),
                "target_1x2": target,
            }
        )
    return pd.DataFrame(rows)


class CalibrationTests(unittest.TestCase):
    def test_isotonic_calibrator_outputs_valid_simplex(self):
        rng = np.random.default_rng(0)
        raw = rng.dirichlet([1, 1, 1], size=200)
        y = np.array(["H", "D", "A"])[raw.argmax(axis=1)]
        calibrator = IsotonicCalibrator.fit(y, raw, TARGET_CLASSES)
        out = calibrator.transform(raw)
        self.assertTrue(np.all(out >= 0))
        np.testing.assert_allclose(out.sum(axis=1), np.ones(len(out)), atol=1e-6)

    def test_neural_net_model_type_trains_and_predicts(self):
        features = _synthetic_matches(400)
        feature_columns = ["elo_diff", "home_elo", "away_elo", "home_rank", "away_rank"]

        result = train_1x2(features, feature_columns, model_type="mlp")

        self.assertEqual(result.model_kind, "neural_net")
        self.assertTrue(np.isfinite(result.report.log_loss))
        predictor = CalibratedPredictor.from_train_result(result)
        probs = predictor.predict_row(
            {"elo_diff": 200, "home_elo": 1600, "away_elo": 1400, "home_rank": 8, "away_rank": 30}
        )
        self.assertAlmostEqual(probs.home_win + probs.draw + probs.away_win, 1.0, places=5)

    def test_train_1x2_returns_calibrated_predictor(self):
        features = _synthetic_matches(400)
        feature_columns = ["elo_diff", "home_elo", "away_elo", "home_rank", "away_rank"]

        result = train_1x2(features, feature_columns)

        self.assertIsNotNone(result.calibrator)
        self.assertGreater(result.n_train, 0)
        self.assertGreater(result.n_calibration, 0)
        self.assertGreater(result.n_validation, 0)
        self.assertTrue(np.isfinite(result.report.log_loss))

        predictor = CalibratedPredictor.from_train_result(result)
        probs = predictor.predict_row(
            {"elo_diff": 300, "home_elo": 1650, "away_elo": 1350, "home_rank": 5, "away_rank": 40}
        )
        total = probs.home_win + probs.draw + probs.away_win
        self.assertAlmostEqual(total, 1.0, places=5)
        self.assertGreaterEqual(min(probs.as_dict().values()), 0.0)


if __name__ == "__main__":
    unittest.main()
