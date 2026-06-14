# World Cup Edge V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-pass Python project for calibrated World Cup 2026 sub-market probabilities, executable Polymarket edge detection, and bankroll-capped manual betting reports.

**Architecture:** Keep each responsibility in a focused module under `src`: ingestion, features, model, simulation, edge, pipeline, and backtest. Treat external data as cached/manual inputs, and block exact bracket claims unless official third-place assignment data is loaded.

**Tech Stack:** Python, pandas, numpy, scikit-learn, xgboost, requests/httpx, pyyaml, unittest.

---

### Task 1: Project Skeleton

**Files:**
- Create: `README.md`
- Create: `requirements.txt`
- Create: `config.yaml`
- Create: `pyproject.toml`
- Create: `src/*/__init__.py`
- Create: `tests/*/__init__.py`

- [x] Create the folder structure from the markdown build spec.
- [x] Add config defaults for the $75 bankroll, quarter Kelly, caps, fees, and polling.
- [x] Add test discovery configuration and package path markers.

### Task 2: Leakage-Prone Data Tests

**Files:**
- Create: `tests/ingest/test_rankings.py`
- Create: `tests/features/test_elo.py`
- Create: `tests/features/test_build_features.py`

- [x] Write tests proving FIFA rankings are joined as-of match date and future snapshots do not leak.
- [x] Write tests proving Elo exposes pre-match ratings and handles neutral sites.
- [x] Write tests proving rolling form uses only prior matches.
- [x] Run tests and confirm red failures before implementing modules.

### Task 3: Ingestion and Features

**Files:**
- Create: `src/ingest/results.py`
- Create: `src/ingest/rankings.py`
- Create: `src/ingest/fixtures.py`
- Create: `src/features/elo.py`
- Create: `src/features/build_features.py`

- [x] Implement results cache/download and team alias normalization.
- [x] Implement ranking CSV loading and point-in-time joins.
- [x] Implement fixture template loading and completed-result overlays.
- [x] Implement Elo with K-factor, MOV damping, and home/neutral adjustment.
- [x] Implement feature generation for Elo, form, rest, context, H2H, and targets.

### Task 4: Model and Goal Distribution

**Files:**
- Create: `src/model/train.py`
- Create: `src/model/calibrate.py`
- Create: `src/model/predict.py`
- Create: `src/model/goal_model.py`
- Create: `tests/model/test_goal_model.py`

- [x] Implement walk-forward split helpers and XGBoost training hooks.
- [x] Implement Brier, reliability, and calibration verdict helpers.
- [x] Implement prediction wrapper for calibrated 1X2 probabilities.
- [x] Write red tests for scoreline distribution behavior.
- [x] Implement Poisson/Dixon-Coles-style scoreline distribution.

### Task 5: 2026 Tournament Rules

**Files:**
- Create: `src/simulate/monte_carlo.py`
- Create: `tests/simulate/test_qualification.py`
- Create: `data/manual/third_place_assignments.README.md`

- [x] Test group ordering and top-two-plus-best-eight-third qualification.
- [x] Implement standings, qualification selection, scoreline sampling primitives, and knockout winner selection.
- [x] Add a strict official Annex C input contract for third-place bracket assignment.

### Task 6: Edge, Kelly, and Scanner

**Files:**
- Create: `src/ingest/polymarket.py`
- Create: `src/edge/detect.py`
- Create: `src/edge/kelly.py`
- Create: `src/edge/scanner.py`
- Create: `tests/edge/test_detect.py`
- Create: `tests/edge/test_kelly.py`
- Create: `tests/edge/test_scanner.py`

- [x] Test executable ask-depth pricing, not midpoint pricing.
- [x] Test edge threshold after configured fees and liquidity.
- [x] Test quarter-Kelly sizing with bankroll and liquidity caps.
- [x] Test sum-to-one consistency alerts.
- [x] Implement public Polymarket metadata/order-book client with cache snapshots.

### Task 7: Reporting and Validation

**Files:**
- Create: `src/backtest/validate.py`
- Create: `src/pipeline/run_live.py`
- Create: `tests/pipeline/test_report.py`

- [x] Implement calibration-validation readiness checks.
- [x] Implement timestamped markdown reports.
- [x] Test that calibration appears before the betting slate.
- [x] State manual execution, variance, and executable-price honesty constraints in reports.

### Task 8: Verification

**Files:**
- All source and test files.

- [x] Run the full unittest suite with `PYTHONPATH=src`.
- [x] Fix path shadowing, missing optional YAML dependency behavior, and goal-model coverage.
- [x] Re-run the suite and confirm all tests pass.
