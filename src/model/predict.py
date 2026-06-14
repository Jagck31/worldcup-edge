from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pickle

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MatchProbabilities:
    home_win: float
    draw: float
    away_win: float

    def as_dict(self) -> dict[str, float]:
        return {"H": self.home_win, "D": self.draw, "A": self.away_win}


class CalibratedPredictor:
    """Wraps a trained classifier and applies the fitted isotonic calibrator.

    If ``calibrator`` is None the raw model probabilities are returned, but the
    standard path supplies the calibrator from ``TrainResult`` so the output is
    actually calibrated (the spec's #1 priority).
    """

    def __init__(
        self,
        model: object,
        classes: list[str],
        feature_columns: list[str],
        calibrator: object | None = None,
    ) -> None:
        self.model = model
        self.classes = classes
        self.feature_columns = feature_columns
        self.calibrator = calibrator

    @classmethod
    def from_train_result(cls, result: object) -> "CalibratedPredictor":
        return cls(
            model=result.model,
            classes=result.classes,
            feature_columns=result.feature_columns,
            calibrator=result.calibrator,
        )

    def _raw_proba(self, frame: pd.DataFrame) -> np.ndarray:
        proba = self.model.predict_proba(frame)
        class_to_index = {label: idx for idx, label in enumerate(self.classes)}
        order = [list(self.model.classes_).index(class_to_index[label]) for label in self.classes]
        return proba[:, order]

    def predict_frame(self, frame: pd.DataFrame) -> np.ndarray:
        """Calibrated probabilities for many rows at once, columns in ``self.classes`` order."""
        probabilities = self._raw_proba(frame[self.feature_columns])
        if self.calibrator is not None:
            probabilities = self.calibrator.transform(probabilities)
        return probabilities

    def predict_row(self, row: pd.Series | dict) -> MatchProbabilities:
        frame = pd.DataFrame([row])[self.feature_columns]
        probabilities = self._raw_proba(frame)
        if self.calibrator is not None:
            probabilities = self.calibrator.transform(probabilities)
        values = probabilities[0]
        by_class = {label: float(values[index]) for index, label in enumerate(self.classes)}
        return MatchProbabilities(
            home_win=by_class.get("H", 0.0),
            draw=by_class.get("D", 0.0),
            away_win=by_class.get("A", 0.0),
        )


def load_train_result(path: Path) -> object:
    with path.open("rb") as handle:
        return pickle.load(handle)
