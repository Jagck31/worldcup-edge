# Claude to Codex Handoff

Date: 2026-06-13 (later same day — the project has moved a long way past your `CODEx_TO_CLAUDE.md` scaffold note)

## TL;DR — the world changed since your handoff

Your handoff proposed building a **monitoring terminal** next. **That's already built** — don't build it. Since your note I've shipped a lot. Read this before picking up work so we don't collide.

### What's now live (mostly my lane — please don't rewrite these)
- **One-command pipeline**: `python run_pipeline.py` runs ingest → Elo → features → calibrated 1X2 → goal model → **Monte Carlo (default 1,000,000 sims, full bracket aggregation)** → live Polymarket edges → portfolio → paper account, and writes `data/processed/dashboard_state.json`. (Your gap #2 and #5 are closed.)
- **Monitoring surface**: `run_dashboard.py` (Textual TUI) **and** `web_app.py` (responsive HTML over the state JSON, served to phone via Tailscale). This is your proposed "next target" — done.
- **Polymarket mapper is wired and LIVE** (`map_world_cup_markets` + champion & group-winner markets, executable asks after fees). Your gap #3 is closed.
- **Goal model**: I replaced the flat raw-ratio strengths with **opponent-adjusted iterative Poisson MLE** (`PoissonGoalModel.opponent_adjusted=True`). Measured: 1X2 log loss 0.906→0.847 on a 2025+ holdout. **I touched `goal_model.py` — coordinate before editing it.**
- **Elo**: added a 538-style **margin-of-victory autocorrection** (`mov_autocorrection=0.0018`), measured on a holdout. `elo.py` is shared — ping me before changing K-factor logic.
- **Portfolio**: new `edge/portfolio.py` — correlation-aware growth-optimal (Kelly) book sizing off the sim's joint payoff distribution. Replaced naive per-bet Kelly for the recommended book.
- **Live price-watcher**: `price_watcher.py` re-fetches CLOB + re-optimizes the book every N min without retraining.

Lane split still holds (see `COLLABORATION_STATUS.md`): **mine** = monte_carlo, train, orchestrator, build_features, dashboard/web_app, portfolio, goal_model (just claimed). **Yours** = ingest/, edge/detect+kelly+scanner, backtest, data loaders.

## Your next build — in priority order

### 1. (TOP) Player / squad-strength ingestion — FBref + Transfermarkt
The user greenlit a **scoped squad-strength backtest** (his words: player data over the last 2y incl. injuries, "look at everything — playtime, coming off winning a tournament"). We agreed to **measure one hypothesis first** before a big build. This is pure ingest — your lane. Build:
- `src/ingest/players.py`: pull **FBref** via the `soccerdata` library (player minutes + xG/90 for club seasons, ~2014+). Add **Transfermarkt** scrape for squad market values + injury/availability (accept the ToS/maintenance risk — user OK'd it).
- A **squad-strength feature** per national team per match date: aggregate the squad's recent club minutes×performance (and a "key-players-out" proxy from injuries). **MUST be point-in-time** (only data known on/before the match date — same discipline as `rankings.py`). Leakage here invalidates the whole test.
- Hand me a clean `team, date -> squad_strength, squad_availability` table; I'll wire it into `build_features.py` and run the backtest (does it beat Elo+FIFA-rank OOS log loss?). If it doesn't move 0.858, we stop there — that's the point of measuring first.

### 2. FIFA Annex C third-place assignment table
Your guardrail #6 + sim gap: the bracket still uses a **non-official** third-place resolver. Build the data loader for the official Annex C table → `data/manual/third_place_assignments.csv`, conforming to the resolver hook already in `monte_carlo.py` (`official_third_place_resolver`). Self-contained, your lane, unblocks exact bracket geometry.

### 3. Dixon-Coles ρ calibration (goal model) — COORDINATE FIRST
`goal_model.py` now has opponent-adjusted strengths (mine) but `dixon_coles_rho=0.0` (unused). Fitting ρ (the low-score correlation correction) is a natural complement — but I just rewrote the fit, so **message me before editing** so we don't clobber each other.

## Guardrails (unchanged, still enforced)
- No wallet/keys/signing/auto-execution. Manual-review + paper only. (`paper_account.py` is gated; the live-exec stub stays disabled.)
- Edges use executable order-book prices after fees, never mid.
- Never recommend below min-fill / unfillable.
- Point-in-time everything. The squad-strength feature is the next place a leak could sneak in — guard it like `rankings.py`.

## Tests
`PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"` → currently **59 pass**. Add tests for the player ingestion's point-in-time join before wiring it.

— Claude
