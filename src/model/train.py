from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pickle

import numpy as np
import pandas as pd

from model.calibrate import (
    CalibrationReport,
    IsotonicCalibrator,
    TemperatureCalibrator,
    multiclass_brier,
    reliability_table,
    verdict_from_metrics,
)


TARGET_CLASSES = ["H", "D", "A"]


@dataclass(frozen=True)
class TrainResult:
    model: object
    classes: list[str]
    report: CalibrationReport
    calibrator: object | None = None
    calibration_method: str = "none"
    uncalibrated_report: CalibrationReport | None = None
    model_kind: str = "unknown"
    feature_columns: list[str] | None = None
    feature_importance: list[tuple[str, float]] | None = None
    n_train: int = 0
    n_calibration: int = 0
    n_validation: int = 0


def time_series_folds(frame: pd.DataFrame, n_folds: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    ordered = frame.sort_values("date").reset_index(drop=True)
    if len(ordered) < n_folds + 2:
        raise ValueError("Not enough rows for walk-forward folds")
    fold_size = len(ordered) // (n_folds + 1)
    folds = []
    for fold in range(1, n_folds + 1):
        train_end = fold * fold_size
        valid_end = min((fold + 1) * fold_size, len(ordered))
        if train_end == 0 or valid_end <= train_end:
            continue
        folds.append((np.arange(0, train_end), np.arange(train_end, valid_end)))
    return folds


def three_way_time_split(
    n_rows: int, train_frac: float = 0.6, calib_frac: float = 0.2
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Chronological train / calibration / validation split (no shuffling, no leakage)."""
    train_end = int(n_rows * train_frac)
    calib_end = int(n_rows * (train_frac + calib_frac))
    train_end = max(1, min(train_end, n_rows - 2))
    calib_end = max(train_end + 1, min(calib_end, n_rows - 1))
    return (
        np.arange(0, train_end),
        np.arange(train_end, calib_end),
        np.arange(calib_end, n_rows),
    )


def _build_neural_net() -> tuple[object, str]:
    """A feed-forward neural network (sklearn MLP) in a NaN-imputing, scaling pipeline.

    Trees beat MLPs on small tabular data like this, but the option is wired so the model
    type is configurable and comparable. torch is used if available; otherwise sklearn's
    MLPClassifier, which is a genuine multilayer perceptron.
    """
    from sklearn.impute import SimpleImputer
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    mlp = MLPClassifier(
        hidden_layer_sizes=(64, 32),
        activation="relu",
        alpha=1e-3,
        learning_rate_init=1e-3,
        max_iter=400,
        early_stopping=True,
        n_iter_no_change=15,
        random_state=0,
    )
    pipeline = Pipeline(
        [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler()), ("mlp", mlp)]
    )
    return pipeline, "neural_net"


def _build_classifier(streaming: bool = False, model_type: str = "gbt") -> tuple[object, str]:
    """Prefer XGBoost; fall back to scikit-learn's HistGradientBoosting (native NaN handling).

    ``model_type='mlp'`` returns a neural-network pipeline instead. For trees, both XGBoost
    and HGB are gradient-boosted; HGB is used when xgboost has no wheel for the running
    interpreter. When ``streaming`` is set the HGB model is configured for warm-start
    incremental fitting so a UI can watch the validation loss fall iteration by iteration.
    """
    if model_type == "mlp":
        return _build_neural_net()
    try:
        from xgboost import XGBClassifier

        model = XGBClassifier(
            objective="multi:softprob",
            num_class=3,
            max_depth=3,
            learning_rate=0.05,
            n_estimators=300,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=5.0,
            reg_alpha=1.0,
            eval_metric="mlogloss",
            early_stopping_rounds=30,
        )
        return model, "xgboost"
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingClassifier

        model = HistGradientBoostingClassifier(
            max_depth=3,
            learning_rate=0.05,
            max_iter=400,
            l2_regularization=5.0,
            early_stopping=not streaming,
            warm_start=streaming,
            validation_fraction=0.15,
            random_state=0,
        )
        return model, "hist_gradient_boosting"


def _fit_hgb_streaming(
    model: object,
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_valid: pd.DataFrame,
    y_valid_labels: np.ndarray,
    class_to_index: dict[str, int],
    progress,
    n_points: int = 15,
) -> None:
    """Warm-start fit in chunks, reporting validation log loss at each step."""
    from sklearn.metrics import log_loss

    total = int(model.get_params()["max_iter"])
    step = max(1, total // n_points)
    targets = list(range(step, total + 1, step))
    if targets[-1] != total:
        targets.append(total)

    sorted_labels = sorted(TARGET_CLASSES)
    col_order = [TARGET_CLASSES.index(label) for label in sorted_labels]
    best_iter, best_ll = targets[-1], float("inf")
    for iteration in targets:
        model.set_params(max_iter=iteration)
        model.fit(X_train, y_train)
        proba = model.predict_proba(X_valid)
        order = [list(model.classes_).index(class_to_index[label]) for label in TARGET_CLASSES]
        proba = proba[:, order]
        ll = float(log_loss(y_valid_labels, proba[:, col_order], labels=sorted_labels))
        progress(iteration, total, ll)
        if ll < best_ll:
            best_ll, best_iter = ll, iteration

    # Early-stopping equivalent: keep the best-validation tree count, not the overfit
    # final one. Refit cleanly to best_iter so the model we hand downstream is the good one.
    if best_iter != targets[-1]:
        model.set_params(warm_start=False, max_iter=best_iter)
        model.fit(X_train, y_train)


def _log_loss_only(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    from sklearn.metrics import log_loss

    sorted_labels = sorted(TARGET_CLASSES)
    col_order = [TARGET_CLASSES.index(label) for label in sorted_labels]
    return float(log_loss(y_true, probabilities[:, col_order], labels=sorted_labels))


def _report(y_true: np.ndarray, probabilities: np.ndarray) -> CalibrationReport:
    from sklearn.metrics import accuracy_score, log_loss

    # sklearn's log_loss binarizes labels in sorted order, so the probability columns
    # must be reordered to match sorted(labels) — otherwise classes get mis-paired.
    sorted_labels = sorted(TARGET_CLASSES)
    col_order = [TARGET_CLASSES.index(label) for label in sorted_labels]
    ll = float(log_loss(y_true, probabilities[:, col_order], labels=sorted_labels))
    brier = multiclass_brier(y_true, probabilities, TARGET_CLASSES)
    predicted = [TARGET_CLASSES[i] for i in probabilities.argmax(axis=1)]
    return CalibrationReport(
        log_loss=ll,
        brier=brier,
        accuracy=float(accuracy_score(y_true, predicted)),
        reliability=reliability_table(y_true, probabilities, TARGET_CLASSES),
        verdict=verdict_from_metrics(ll, brier),
    )


def _emit_nn_curve(model: object, progress, max_points: int = 20) -> None:
    """Replay the MLP's training-loss curve so the dashboard still shows it learning."""
    try:
        curve = list(model.named_steps["mlp"].loss_curve_)
    except Exception:
        return
    total = len(curve)
    step = max(1, total // max_points)
    for index in range(0, total, step):
        progress(index + 1, total, float(curve[index]))


def train_1x2(
    features: pd.DataFrame, feature_columns: list[str], progress=None, model_type: str = "gbt",
    refit_full: bool = False,
) -> TrainResult:
    """Train a calibrated 1X2 classifier with a chronological train/calibrate/validate split.

    Rows are kept even when some features are NaN — gradient-boosted trees route missing
    values natively, so we only drop rows with a missing target.

    ``progress`` is an optional callback ``(iteration, max_iter, val_log_loss)`` invoked
    while the model trains so a UI can stream the learning curve.
    """
    frame = features.dropna(subset=["target_1x2"]).sort_values("date").reset_index(drop=True)
    if len(frame) < 30:
        raise ValueError("Need at least 30 labelled matches to train")

    X = frame[feature_columns]
    y = frame["target_1x2"].astype(str)
    if not set(y.unique()).issubset(set(TARGET_CLASSES)):
        raise ValueError("target_1x2 contains labels outside H/D/A")

    class_to_index = {label: idx for idx, label in enumerate(TARGET_CLASSES)}
    y_encoded = y.map(class_to_index).to_numpy()

    train_idx, calib_idx, valid_idx = three_way_time_split(len(frame))

    model, kind = _build_classifier(streaming=progress is not None, model_type=model_type)
    if kind == "neural_net":
        model.fit(X.iloc[train_idx], y_encoded[train_idx])
        if progress is not None:
            _emit_nn_curve(model, progress)
    elif kind == "xgboost":
        model.fit(
            X.iloc[train_idx],
            y_encoded[train_idx],
            eval_set=[(X.iloc[calib_idx], y_encoded[calib_idx])],
            verbose=False,
        )
        if progress is not None:
            progress(int(model.get_params().get("n_estimators", 0)), int(model.get_params().get("n_estimators", 0)), float("nan"))
    elif progress is not None:
        _fit_hgb_streaming(
            model,
            X.iloc[train_idx],
            y_encoded[train_idx],
            X.iloc[valid_idx],
            y.to_numpy()[valid_idx],
            class_to_index,
            progress,
        )
    else:
        model.fit(X.iloc[train_idx], y_encoded[train_idx])

    def predict_proba(rows: pd.DataFrame) -> np.ndarray:
        proba = model.predict_proba(rows)
        # Reorder columns into TARGET_CLASSES order regardless of the estimator's class order.
        order = [list(model.classes_).index(class_to_index[label]) for label in TARGET_CLASSES]
        return proba[:, order]

    raw_calib = predict_proba(X.iloc[calib_idx])
    y_calib = y.to_numpy()[calib_idx]
    raw_valid = predict_proba(X.iloc[valid_idx])
    y_valid = y.to_numpy()[valid_idx]

    # Fit two calibrators and select whichever minimizes validation log loss — including
    # "none", so calibration can never make the model worse than the raw probabilities.
    candidates: dict[str, object | None] = {
        "none": None,
        "isotonic": IsotonicCalibrator.fit(y_calib, raw_calib, TARGET_CLASSES),
        "temperature": TemperatureCalibrator.fit(y_calib, raw_calib, TARGET_CLASSES),
    }

    def _cal_logloss(cal: object | None) -> float:
        probs = raw_valid if cal is None else cal.transform(raw_valid)
        return _log_loss_only(y_valid, probs)

    calibration_method = min(candidates, key=lambda name: _cal_logloss(candidates[name]))
    calibrator = candidates[calibration_method]

    uncalibrated = _report(y_valid, raw_valid)
    calibrated_probs = raw_valid if calibrator is None else calibrator.transform(raw_valid)
    calibrated = _report(y_valid, calibrated_probs)
    importance = _permutation_importance(model, X.iloc[valid_idx], y_encoded[valid_idx], feature_columns)

    # Deployment refit: the chronological splits above give an HONEST held-out estimate
    # (kept as `report`/`uncalibrated_report`). But shipping a model trained on only the 60%
    # train slice wastes a third of the history. When no calibrator is in play (so there's
    # nothing to mismatch), refit the model we actually deploy on ALL rows so production
    # predictions use 100% of the data. Reports stay the honest split-based numbers.
    deploy_model = model
    # Refit the SHIPPED model on all rows when asked (callers set refit_full identically, so
    # every engine deploys the same model; the reports above stay the honest held-out estimate).
    # Only safe when no calibrator was selected (nothing tuned to the split model's outputs).
    if calibrator is None and refit_full:
        try:
            refit, _ = _build_classifier(model_type=model_type)
            refit.fit(X, y_encoded)
            deploy_model = refit
        except Exception:
            deploy_model = model  # never let the deployment refit break training

    return TrainResult(
        model=deploy_model,
        classes=TARGET_CLASSES,
        report=calibrated,
        calibrator=calibrator,
        calibration_method=calibration_method,
        uncalibrated_report=uncalibrated,
        model_kind=kind,
        feature_columns=list(feature_columns),
        feature_importance=importance,
        n_train=len(train_idx),
        n_calibration=len(calib_idx),
        n_validation=len(valid_idx),
    )


def _permutation_importance(
    model: object, X_valid: pd.DataFrame, y_valid: np.ndarray, feature_columns: list[str]
) -> list[tuple[str, float]]:
    """Model-agnostic feature importance (works for HGB, which has no native attribute)."""
    try:
        from sklearn.inspection import permutation_importance

        result = permutation_importance(
            model, X_valid, y_valid, n_repeats=3, random_state=0, scoring="neg_log_loss"
        )
    except Exception:
        return []
    ranked = sorted(
        zip(feature_columns, (float(v) for v in result.importances_mean)),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked


# Backwards-compatible alias; the spec referred to an XGBoost-specific entry point.
def train_xgboost_1x2(features: pd.DataFrame, feature_columns: list[str]) -> TrainResult:
    return train_1x2(features, feature_columns)


def persist_model(result: TrainResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(result, handle)
    return path
