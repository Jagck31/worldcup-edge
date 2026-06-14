# Model

The calibrated 1X2 (home / draw / away) classifier. `src/model/train.py` + `src/model/calibrate.py`. Part of [[Architecture]].

## What it is
- **Classifier:** gradient-boosted trees (sklearn `HistGradientBoostingClassifier`) by default, or an MLP neural net (`model_type: mlp` in `config.yaml`). Python 3.14 has no xgboost wheel, so HGB is the workhorse.
- **Calibration:** after training, pick the **best of none / isotonic / temperature** on a time-forward holdout. Calibration is allowed to do *nothing* — see below.
- **Trained on the last 12 years** of `martj42/international_results`; Elo, by contrast, is built on full history (see [[Monte Carlo]]).

## Features (`features/build_features.py`)
- `elo_expected_home` — logistic of the Elo gap + home advantage. **The single best input.**
- `rank_points_diff` / `rank_diff` — FIFA ranking deltas, the **#2/#3** features and the only signal not derived from match results.
- Goal strengths (attack/defense), expected goals, `abs_elo_diff`, `elo_resid` momentum.
- Reliability: `matches_last_30d` / `matches_last_365d` — how much recent data backs the rating.

## The plateau (the key finding — see [[Findings & Decisions]])
- **~0.858 log loss** vs 1.099 uniform. Hyperparameter search and sample-weighting both **failed to beat it**. International football is heavily Elo-determined; more features give diminishing returns *for log loss*.
- **Calibration must be allowed to do nothing.** Forcing isotonic *worsened* log loss; the trainer now selects.
- **Trees: more ≠ better.** The curve overfits past ~100 trees; the trainer keeps the best iteration.
- FIFA rankings were the one feature add that genuinely moved it (0.876 → 0.868).

## How it's used
- The **Live Tracker** scores its calibrated probs vs real results (log loss / Brier / accuracy) — see [[Live Tracker]].
- The Monte Carlo uses Elo win-prob for knockouts and the goal model for group scorelines, *not* this classifier directly — this model's value is the per-match probability shown in the tracker and the calibration discipline.

## Open threads
- Per-player / squad-form input judged **low marginal value over Elo + FIFA rank** for free data. See [[Ideas & Open Questions]].
- Attack/Defense Elo split would most help the **goal model**, not this classifier.
