from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from model.train import time_series_folds


@dataclass(frozen=True)
class BacktestVerdict:
    calibration_verdict: str
    folds: int
    notes: list[str]


def validate_calibration_inputs(features: pd.DataFrame) -> BacktestVerdict:
    notes: list[str] = []
    required = {"date", "target_1x2"}
    missing = required - set(features.columns)
    if missing:
        return BacktestVerdict("blocked", 0, [f"Missing columns: {sorted(missing)}"])
    tournaments = set(str(value) for value in features.get("tournament", pd.Series(dtype=str)).dropna())
    major = [item for item in tournaments if any(name in item.lower() for name in ("world cup", "euro", "copa"))]
    if not major:
        notes.append("No major-tournament rows found; calibration verdict should remain research-only.")
    try:
        folds = time_series_folds(features, n_folds=5)
    except ValueError as exc:
        return BacktestVerdict("blocked", 0, [str(exc)])
    return BacktestVerdict("ready_for_walk_forward", len(folds), notes)
