from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CalibrationReport:
    log_loss: float
    brier: float
    accuracy: float
    reliability: pd.DataFrame
    verdict: str


@dataclass
class IsotonicCalibrator:
    """Per-class one-vs-rest isotonic calibration with row renormalization.

    Fit on a *time-forward* holdout slice (never the training rows): for each class
    we learn a monotone map from the model's raw probability to the observed
    frequency, then renormalize each row back to a simplex so the three 1X2
    probabilities sum to 1. This is the calibration layer the build spec makes the
    #1 priority; without it XGBoost/GBT softprob outputs are typically over-confident.
    """

    classes: list[str]
    maps: dict[str, object] = field(default_factory=dict)

    @classmethod
    def fit(cls, y_true: np.ndarray, probabilities: np.ndarray, classes: list[str]) -> "IsotonicCalibrator":
        from sklearn.isotonic import IsotonicRegression

        y_true = np.asarray([str(item) for item in y_true])
        maps: dict[str, object] = {}
        for index, label in enumerate(classes):
            target = (y_true == label).astype(float)
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(probabilities[:, index], target)
            maps[label] = iso
        return cls(classes=classes, maps=maps)

    def transform(self, probabilities: np.ndarray) -> np.ndarray:
        probabilities = np.asarray(probabilities, dtype=float)
        out = np.zeros_like(probabilities, dtype=float)
        for index, label in enumerate(self.classes):
            out[:, index] = self.maps[label].predict(probabilities[:, index])
        out = np.clip(out, 1e-9, None)
        row_sums = out.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        return out / row_sums


@dataclass
class TemperatureCalibrator:
    """Single-parameter temperature scaling: p -> softmax(log(p) / T).

    T > 1 softens over-confident probabilities, T < 1 sharpens them. One parameter fit
    by minimizing log loss, so it is far less prone to overfitting a small calibration
    slice than per-class isotonic — it usually helps or is neutral, rarely hurts.
    """

    temperature: float
    classes: list[str]

    @classmethod
    def fit(cls, y_true: np.ndarray, probabilities: np.ndarray, classes: list[str]) -> "TemperatureCalibrator":
        from scipy.optimize import minimize_scalar

        y_true = np.asarray([str(item) for item in y_true])
        index = {label: idx for idx, label in enumerate(classes)}
        targets = np.array([index[label] for label in y_true])
        logits = np.log(np.clip(probabilities, 1e-12, 1.0))

        def neg_log_likelihood(temperature: float) -> float:
            scaled = logits / max(temperature, 1e-3)
            scaled = scaled - scaled.max(axis=1, keepdims=True)
            exp = np.exp(scaled)
            probs = exp / exp.sum(axis=1, keepdims=True)
            chosen = probs[np.arange(len(targets)), targets]
            return float(-np.mean(np.log(np.clip(chosen, 1e-12, 1.0))))

        result = minimize_scalar(neg_log_likelihood, bounds=(0.25, 5.0), method="bounded")
        return cls(temperature=float(result.x), classes=classes)

    def transform(self, probabilities: np.ndarray) -> np.ndarray:
        probabilities = np.asarray(probabilities, dtype=float)
        logits = np.log(np.clip(probabilities, 1e-12, 1.0)) / max(self.temperature, 1e-3)
        logits = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(logits)
        return exp / exp.sum(axis=1, keepdims=True)


def multiclass_brier(y_true: np.ndarray, probabilities: np.ndarray, classes: list[str]) -> float:
    encoded = np.zeros_like(probabilities, dtype=float)
    index = {label: idx for idx, label in enumerate(classes)}
    for row_index, label in enumerate(y_true):
        encoded[row_index, index[str(label)]] = 1.0
    return float(np.mean(np.sum((probabilities - encoded) ** 2, axis=1)))


def reliability_table(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    classes: list[str],
    bins: int = 10,
) -> pd.DataFrame:
    rows: list[dict] = []
    for class_index, label in enumerate(classes):
        class_prob = probabilities[:, class_index]
        class_true = np.array([1.0 if str(item) == label else 0.0 for item in y_true])
        cut = pd.cut(class_prob, bins=np.linspace(0, 1, bins + 1), include_lowest=True)
        grouped = pd.DataFrame({"predicted": class_prob, "observed": class_true, "bin": cut}).groupby(
            "bin", observed=False
        )
        for interval, group in grouped:
            if group.empty:
                continue
            rows.append(
                {
                    "class": label,
                    "bin": str(interval),
                    "mean_predicted": float(group["predicted"].mean()),
                    "observed_rate": float(group["observed"].mean()),
                    "count": int(len(group)),
                }
            )
    return pd.DataFrame(rows)


def save_reliability_csv(report: CalibrationReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    report.reliability.to_csv(path, index=False)
    return path


def verdict_from_metrics(log_loss: float, brier: float) -> str:
    if log_loss <= 0.95 and brier <= 0.50:
        return "usable_with_caution"
    if log_loss <= 1.10:
        return "research_only"
    return "do_not_bet"
