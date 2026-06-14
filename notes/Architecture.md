# Architecture

End-to-end pipeline (`src/pipeline/orchestrator.py` → `data/processed/dashboard_state.json` → `src/dashboard/`).

## The flow
1. **Data** — `martj42/international_results` (1872→now). [[Findings & Decisions|Elo uses all of it]]; the model trains on the **last 12 years** only.
2. **Elo** (`features/elo.py`) — football-tuned, point-in-time. Live WC results are fed in each run so ratings update per matchday. See [[Monte Carlo]] for why the Elo *spread* matters.
3. **Features** (`features/build_features.py`) — `elo_expected_home` (top input), opponent-adjusted momentum, goal strengths, FIFA rank (`rank_points_diff`/`rank_diff` are #2/#3), reliability. See [[Model]].
4. **Calibrated 1X2 model** (`model/train.py`, `model/calibrate.py`) — GBT or MLP, then pick best of none/isotonic/temperature.
5. **Goal model** (`model/goal_model.py`, Codex's) — Poisson/Dixon-Coles scorelines.
6. **Monte Carlo** (`simulate/monte_carlo.py`) — 30k tournaments on the official draw. See [[Monte Carlo]].
7. **Edges** — map sim probs to **live Polymarket CLOB** markets, `detect_edges` (YES+NO) → Codex's `edge/recommend.py`. See [[Trading & Paper Account]].
8. **Paper account** (`pipeline/paper_account.py`) — persistent, executes/settles/marks-to-market each run.
9. **Tracker** (`pipeline/tracker.py`) — predictions vs real results. See [[Live Tracker]].

## Live engine (always-on)
`src/pipeline/live_engine.py` (+ root launcher `live_engine.py`) is the **single writer** of `dashboard_state.json` — it folds the old three scripts (orchestrator / live_tracker / price_watcher) into one process so they can't race. It trains the model once (`build_context`), then runs staggered jobs: **prices** (~60s: live CLOB → edges → portfolio → paper account), **results** (~90s: live feed → re-score frozen picks → Elo → tracker), **simulate** (on every new result, else ~15m: Monte Carlo → champion/advance probabilities + cached outcomes), **retrain** (~6h: full `run_pipeline`). Cadences in `config.yaml` (`engine_*_interval_sec`). Writes are atomic (temp + `os.replace`). Single-threaded scheduler = no file races, no concurrency bugs. Maintains `state["equity_curve"]` for the live sparkline. A web "Refresh now" drops `engine_control.json`, which the engine consumes within ~1s to force a re-sim. Run it with `python live_engine.py` (rich live terminal) or `--no-ui` (systemd). The freeze-at-kickoff invariant is preserved.

## Dashboards
- **Web (`web_app.py`)** — real-time HTML over `dashboard_state.json`, pushed to the browser via **Server-Sent Events** (`/events`), no polling lag; KPI values flash on change, live equity sparkline, per-section freshness chips, engine-status bar. Binds localhost; Caddy fronts it with TLS + basic auth. **This is the deployed site.** SSE proxies cleanly through Caddy (unlike the old textual WebSocket).
- **Terminal (`live_engine.py`)** — the live rich console; all sections update continuously in place. (The older `src/dashboard/app.py` textual TUI still reads a snapshot with manual `r`.)

State schema (engine↔dashboards) is the contract between the two — owned on the Claude side per [[COLLABORATION_STATUS]]. Deploy: see [[Deploy & Access]] (`deploy/` = systemd units + Caddyfile + `deploy.sh`).

## Lanes
Claude: monte_carlo, train, orchestrator, build_features, dashboard. Codex: edge/, ingest/, goal_model, elo K-factor. See [[COLLABORATION_STATUS]].
