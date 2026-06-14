# Codex to Claude Handoff

Project: `C:\Users\jackg\Desktop\World Cup ML\worldcup-edge`

Source brief: `C:\Users\jackg\Desktop\World Cup ML\worldcup_model_build_spec.md`

Status as of 2026-06-13: Codex built a tested v1 scaffold from the markdown spec. This is not a finished live betting system yet; it is the foundation for calibrated modeling, executable-price edge detection, and a manual-review report flow.

## What Exists

- `src/ingest/results.py`: downloads/caches the public international results CSV and applies editable team aliases.
- `src/ingest/rankings.py`: loads FIFA ranking history and joins it point-in-time, using only rankings known on or before each match date.
- `src/ingest/fixtures.py`: creates/loads a manual 2026 fixture template and overlays completed results.
- `src/ingest/polymarket.py`: read-only Gamma/CLOB client for World Cup market discovery, order books, and JSON snapshots.
- `src/features/elo.py`: custom football Elo with importance-based K, margin-of-victory damping, and home/neutral adjustment.
- `src/features/build_features.py`: Elo, ranking, form, rest, context, host, H2H, and target feature assembly.
- `src/model/train.py`: XGBoost 1X2 training hooks with walk-forward splits.
- `src/model/calibrate.py`: log-loss, Brier, reliability table, and calibration verdict helpers.
- `src/model/predict.py`: small prediction wrapper for calibrated 1X2 probabilities.
- `src/model/goal_model.py`: Poisson/Dixon-Coles-style scoreline distribution model.
- `src/simulate/monte_carlo.py`: group standings, 2026 top-two-plus-best-eight-third qualification, simulator primitives, and strict third-place bracket assignment input handling.
- `src/edge/detect.py`: executable YES ask-depth pricing and edge detection after fees.
- `src/edge/kelly.py`: quarter-Kelly sizing with bankroll, single-bet, total-exposure, minimum-fill, and liquidity caps.
- `src/edge/scanner.py`: sum-to-one consistency scanner for related market families.
- `src/backtest/validate.py`: calibration backtest readiness checks.
- `src/pipeline/run_live.py`: timestamped markdown report writer and scaffold live entry point.

## Verification Already Run

Use the same command from project root:

```powershell
$env:PYTHONPATH="$PWD\src"
& "C:\Users\jackg\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -p "test_*.py" -v
```

Last result: 16 tests ran and passed.

Syntax pass:

```powershell
& "C:\Users\jackg\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m compileall -q src
```

Last result: exit code 0.

Report smoke test:

```powershell
$env:PYTHONPATH="$PWD\src"
& "C:\Users\jackg\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m pipeline.run_live
```

Last output:

```text
C:\Users\jackg\Desktop\World Cup ML\worldcup-edge\reports\20260613T063850Z_worldcup_edge_report.md
```

## Important Guardrails

- Do not add wallet code, private keys, signing, or auto-execution in v1. Reports are manual-review only.
- Edge calculations must use executable order-book prices after configured fees, never midpoint prices.
- Never recommend a bet that is below minimum fill size or cannot be filled at the assumed price.
- Calibration health must appear before betting recommendations.
- With a $75 bankroll, variance dominates. Keep language conservative.
- Bracket-dependent tournament probabilities require the official FIFA Annex C third-place assignment table. The current code intentionally blocks exact bracket assignment if `data/manual/third_place_assignments.csv` is missing.

## Known Gaps Worth Reviewing

1. The model training module is a scaffold, not a fully wired training pipeline. It assumes dependencies from `requirements.txt` and a completed feature table.
2. The simulator has group and qualification primitives, but not a full end-to-end 50,000-run bracket aggregation yet.
3. The Polymarket market-to-team/submarket mapper is not built yet; `polymarket.py` can fetch metadata/order books but does not fully normalize all market semantics.
4. Historical odds value backtesting is only readiness-scaffolded because free international odds coverage is spotty.
5. The live pipeline currently writes a scaffold report; it does not yet run ingestion, training, simulation, Polymarket pulls, edge detection, Kelly sizing, and scanner in one command.
6. There are generated cache folders/files from verification (`__pycache__`, `.uv-cache`) that are ignored by `.gitignore`. Cleanup is optional.

## Review Requests For Claude

Please review broadly for:

- bugs in point-in-time logic, Elo math, Kelly sizing, executable pricing, and 2026 qualification rules
- places where the implementation quietly overclaims live-readiness
- missing tests for high-risk behavior
- better module boundaries before we wire the monitoring terminal
- whether the README is honest enough about variance, calibration, and manual execution

Leave notes in:

`C:\Users\jackg\Desktop\World Cup ML\worldcup-edge\CLAUDE_REVIEW_NOTES.md`

## Next Build Target: Monitoring Terminal

After review, the next feature should be a terminal monitoring surface, not a trading bot. Proposed scope:

- A command such as `python -m terminal.monitor`.
- Show latest report path, calibration status, data freshness, bankroll/exposure, edge slate count, scanner flag count, and blocked prerequisites.
- Read from existing artifacts first: `reports/*.md`, `config.yaml`, cache snapshots, fixture CSV, and future pipeline JSON summaries.
- Include explicit warnings when calibration is missing, third-place assignment table is absent, Polymarket snapshots are stale, or no executable liquidity exists.
- Prefer a dependency-light implementation first. A plain standard-library terminal view is acceptable; `rich` or `textual` can be considered later if useful.
- Tests should cover parsing/report status and blocked-prerequisite display before UI polish.

## Suggested Fix Order After Review

1. Address any correctness bugs Claude finds.
2. Add missing tests for those bugs.
3. Add a machine-readable pipeline summary JSON next to each markdown report.
4. Build the monitoring terminal against that summary JSON and current config/data freshness.
5. Only then wire more live pipeline steps.

