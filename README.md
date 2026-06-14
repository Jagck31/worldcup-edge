# World Cup Edge

A Python research tool for 2026 World Cup sub-market edge detection. The project follows
the build spec in `../worldcup_model_build_spec.md`: calibration first, executable prices
second, bankroll protection always.

This is not an auto-trading bot. It writes a betting slate for manual review only. There
is no wallet code, no signing code, and no order placement in v1.

## What It Builds

- Historical international results ingestion from the public `martj42/international_results`
  CSV.
- Editable team-name normalization in `data/manual/team_aliases.yaml`.
- FIFA ranking loaders with strict point-in-time joins so future rankings do not leak into
  past matches.
- A football-tuned Elo engine with match-importance K factors, margin-of-victory damping,
  and neutral/home adjustment.
- Feature assembly for Elo, FIFA rank, rolling form, rest, host flags, context, and recent
  head-to-head.
- XGBoost 1X2 model training hooks with walk-forward validation and calibration reporting.
- A Poisson/Dixon-Coles-style goal model for scoreline distributions.
- 2026 group ranking and top-two-plus-best-eight-third qualification logic.
- Polymarket Gamma/CLOB read-only client for market metadata and executable order-book
  snapshots.
- Edge detection using buyable YES ask depth after fees, never midpoint prices.
- Quarter-Kelly sizing with single-bet, total-exposure, minimum-fill, and liquidity caps.
- A consistency scanner for sum-to-one market families such as group winners.
- Timestamped markdown reports that put calibration health before any slate.

## Bankroll Rules

Defaults live in `config.yaml`:

- Bankroll: `$75`
- Kelly fraction: `0.25`
- Max single bet: `20%` of bankroll
- Max total exposure: `80%` of bankroll
- Minimum edge: `5` percentage points
- Minimum fillable size: `$5`

With a $75 bankroll over one tournament, variance dominates short-run P&L. The point is to
learn whether the process is disciplined: calibrated probabilities, executable prices,
liquidity checks, and capped sizing. A lucky or unlucky tournament is not proof that the
model is good or bad.

## Installation

```powershell
cd "C:\Users\jackg\Desktop\World Cup ML\worldcup-edge"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `python` is not on PATH in Codex, the bundled runtime used during build was:

```powershell
$env:PYTHONPATH="$PWD\src"
& "C:\Users\jackg\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -p "test_*.py" -v
```

## Run the pipeline + live dashboard

The pipeline is wired end-to-end and writes a single state artifact the dashboard reads.

```powershell
# 1. Run the full pipeline: download results -> Elo -> calibrated model -> goal model
#    -> Monte Carlo -> edge slate. Writes data/processed/dashboard_state.json (~45s).
$env:PYTHONPATH="$PWD\src"
python -m pipeline.orchestrator            # add --refresh to re-download, --sims 5000 for more sims

# 2. Launch the interactive terminal dashboard (textual).
python run_dashboard.py                    # or:  python -m dashboard
```

Inside the dashboard:

- Tabs (←/→ or click): **Overview · Data & Pipeline · Elo · Model & Calibration · Simulation · Trades & Markets**.
- `r` re-runs the whole pipeline in the background and refreshes every panel; `g` jumps to Trades; `d` toggles theme; `q` quits.
- The **Model** tab shows the architecture as an input→trees→calibration→1X2 flow with permutation-importance bars. (The model is **gradient-boosted trees**, not a neural network — XGBoost when available, otherwise scikit-learn `HistGradientBoosting`.)

**Honesty labels in the UI:** data is `CACHED`/`DOWNLOADED`, the 2026 draw is `DEMO` (Elo-seeded until official groups are loaded into `data/manual/fixtures_2026.csv`), and slate prices are `SAMPLE` (priced around model probabilities) until a live CLOB feed is wired. Champion/Final edges are usually below the `$5` min-fill on a `$75` bankroll — that suppression is the point.

## Test Command

```powershell
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests -p "test_*.py" -v
```

The test suite focuses on the failure modes most likely to create fake edge:

- point-in-time ranking joins
- Elo pre-match ratings and neutral-site handling
- feature form windows without future leakage
- 2026 group ranking and best-third qualification
- executable ask-depth pricing
- Kelly caps and liquidity checks
- consistency-scanner sum checks
- report ordering that surfaces calibration before bets

## Data Inputs

`data/raw/` and `data/cache/` are gitignored. Use them for downloaded results, ranking
mirrors, and Polymarket snapshots.

`data/manual/fixtures_2026.csv` is a paste-friendly fixture template. Fill scores as group
matches complete and set `status` to `completed`; the simulator locks those results and
samples only remaining matches.

`data/manual/third_place_assignments.csv` is intentionally not prefilled. The exact
round-of-32 assignment for third-placed teams must come from FIFA's Annex C table. If that
table is missing, bracket-dependent simulations should block rather than guess.

## Typical Workflow

1. Refresh or load historical results and rankings.
2. Build Elo history and feature tables using only data known before each match.
3. Train and calibrate the 1X2 model with walk-forward splits.
4. Fit the goal model for scoreline distributions.
5. Load 2026 fixtures and locked results.
6. Simulate advancement and sub-market probabilities.
7. Pull Polymarket metadata and CLOB order books.
8. Compare model probabilities to executable prices after fees.
9. Size surviving edges with quarter-Kelly and hard caps.
10. Write a timestamped report for manual review.

## Current V1 Boundaries

- No private keys, wallets, USDC transfers, signatures, or auto-execution.
- No player-level top-scorer model; team-goal props are in scope, player props are not.
- Bracket-dependent championship markets require the official third-place assignment CSV.
- Historical odds backtesting is scaffolded because free international odds coverage is
  spotty; calibration is the primary trust gate until odds data is available.

## Key Modules

- `src/ingest/results.py`: results download/cache and team normalization.
- `src/ingest/rankings.py`: ranking history loading and point-in-time joins.
- `src/ingest/polymarket.py`: Gamma/CLOB public API client and snapshot caching.
- `src/features/elo.py`: custom Elo engine.
- `src/features/build_features.py`: match-level feature table.
- `src/model/train.py`: XGBoost training and validation hooks.
- `src/model/calibrate.py`: calibration metrics and reliability tables.
- `src/model/goal_model.py`: scoreline distribution model.
- `src/simulate/monte_carlo.py`: standings, qualifiers, and simulator primitives.
- `src/edge/detect.py`: executable price and edge detection.
- `src/edge/kelly.py`: fractional Kelly sizing.
- `src/edge/scanner.py`: cross-market consistency alerts.
- `src/pipeline/run_live.py`: report writing and live-run entry point.
