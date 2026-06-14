"""Out-of-sample calibration backtest, sliced by competition type.

Scores the trained, calibrated model on its time-forward validation slice (genuinely
out-of-sample), and separately on the major-tournament subset (World Cup / Euro / Copa /
AFCON / Asian Cup) — the population we actually bet on. Cheap: no retraining, reuses the
fitted predictor on the held-out rows.
"""
from __future__ import annotations

from math import log

import numpy as np
import pandas as pd

from model.train import TARGET_CLASSES, three_way_time_split

MAJOR = ("world cup", "euro", "copa", "afcon", "asian cup", "nations league")


def _metrics(y_true: np.ndarray, probs: np.ndarray) -> dict:
    if len(y_true) == 0:
        return {"n": 0}
    index = {label: i for i, label in enumerate(TARGET_CLASSES)}
    targets = np.array([index[str(y)] for y in y_true])
    chosen = probs[np.arange(len(targets)), targets]
    log_loss = float(-np.mean(np.log(np.clip(chosen, 1e-12, 1.0))))
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(targets)), targets] = 1.0
    brier = float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))
    acc = float(np.mean(probs.argmax(axis=1) == targets))
    return {"n": int(len(y_true)), "log_loss": round(log_loss, 4), "brier": round(brier, 4), "accuracy": round(acc, 4)}


def tournament_backtest(labelled: pd.DataFrame, predictor: object) -> dict:
    """Predict the held-out validation slice and report calibration overall vs majors."""
    frame = labelled.dropna(subset=["target_1x2"]).sort_values("date").reset_index(drop=True)
    if len(frame) < 30:
        return {"available": False}
    _, _, valid_idx = three_way_time_split(len(frame))
    valid = frame.iloc[valid_idx].reset_index(drop=True)
    probs = predictor.predict_frame(valid)
    y = valid["target_1x2"].astype(str).to_numpy()

    overall = _metrics(y, probs)
    tournament = valid.get("tournament", pd.Series([""] * len(valid))).astype(str).str.lower()
    major_mask = tournament.apply(lambda t: any(name in t for name in MAJOR)).to_numpy()
    majors = _metrics(y[major_mask], probs[major_mask])
    friendlies_mask = tournament.str.contains("friendly").to_numpy()
    competitive = _metrics(y[~friendlies_mask], probs[~friendlies_mask])

    return {
        "available": True,
        "overall": overall,
        "major_tournaments": majors,
        "competitive_only": competitive,
        "uniform_log_loss": round(float(log(3)), 4),
        "window": f"{valid['date'].min().date()} → {valid['date'].max().date()}",
    }
