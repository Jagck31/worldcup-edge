# Autonomous improvement loop

This is the spec Claude follows when improving the World Cup edge system on its own. The
loop's job: **make the real-time trading, tracking, data, and website measurably better,
one safe change at a time, on a live-money-adjacent box.** Read this top-to-bottom at the
start of every iteration.

Related: `IMPROVEMENTS.md` (human-readable change log), [[worldcup-edge-project]],
[[worldcup-deploy-hetzner]], [[worldcup-resume-state]] (memory).

---

## 1. The objective function

`python evaluate.py` is the scorecard. **It is the source of truth for "better."** Every
change is judged by what it does to these numbers:

| Metric | Where | Goal | Baseline 2026-06-15 |
|---|---|---|---|
| Held-out 1X2 log-loss (major tournaments) | `model.major_tournaments.log_loss` | ↓ (uniform = 1.0986) | **0.8092** |
| Held-out 1X2 Brier (majors) | `model.major_tournaments.brier` | ↓ | 0.4727 |
| Live tracker log-loss (games played) | `tracker.completed.log_loss` | ↓ below 1.0986 | **1.155 ⚠ worse than coin-flip** |
| Live tracker accuracy | `tracker.completed.accuracy` | ↑ | 0.50 (n=10) |
| Paper ROI % | `trading.roi_pct` | ↑ toward 0+ | **−15.5% ⚠** |
| Extreme-disagreement edges | `strategy.n_extreme_disagreement` | low / 0 | 0 (was 4 before shrinkage) |
| Market-mapping resolution rate | `data.market_mapping_rate` | 1.0 | 1.0 |

Modes:
- `python evaluate.py` — fast, reads on-disk state (monitoring).
- `python evaluate.py --retrain` — **retrains the model now** → fresh honest held-out
  numbers. **Use this to gate any model/feature/data change.**
- `python evaluate.py --live` — fetches live Polymarket markets → fresh strategy/data diag.
  **Use this to gate any strategy/pricing change.**

Each run appends a line to `data/processed/scorecard_history.jsonl` so trends are visible.

---

## 2. Guardrails (hard rules — never break these)

1. **Test-gated.** `PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"` must
   be 100% green before any commit. Add a test for every new behaviour.
2. **Metric-gated, measure-then-keep.** Keep a change only if the scorecard shows it helps
   (or is a clearly-correct risk control that doesn't regress measurable metrics). A change
   that can't be measured yet ships **conservative + reversible** and says so in the log.
   This is the rule the on-box auto-implementer lacked (it only checked "tests pass").
3. **Never touch the Stock Claude project.** It is a live money book on the same box. No edits
   under `/opt/stock-claude`, its crontab, its container, or its tailscale serve. Coexist.
4. **Engine stays the single writer** of `dashboard_state.json`. Don't add a second writer;
   sidecars (ops/improver) write their own small files that the engine folds in.
5. **Degrade gracefully.** Live code paths (Polymarket fetch, feed) must be wrapped so a
   failure falls back, never crashes the engine. (`job_prices` already try/excepts
   `_build_live_markets` → SAMPLE fallback.)
6. **Atomic + revertible.** One logical change per commit, descriptive message, easy
   `git revert`. Push to `origin/main` → box auto-deploys in ~2 min.
7. **Config over code for tunables.** Risk knobs live in `config.yaml` so they can be moved
   without a deploy. ⚠ `config.yaml` is git-tracked → it overwrites the box's hand-set values
   on deploy (notably `engine_sim_n`; keep the 2 GB box in mind).
8. **Honesty in the UI.** Anything synthetic stays labelled (SAMPLE/DEMO). Never present a
   modelled number as a market number.

---

## 3. Iteration protocol

1. `python evaluate.py --retrain --live` → record the headline. Read
   `scorecard_history.jsonl` for the trend.
2. Pick the **single highest-leverage** open item from the backlog (§4) given the current
   numbers (e.g. tracker log-loss worse than coin-flip ⇒ model/feature work outranks a UI
   polish).
3. Implement the smallest version that could move the metric. Add/adjust tests.
4. Run the suite (green) **and** the relevant scorecard mode. Compare to the recorded headline.
5. **Keep only if it helped** (or is a documented, reversible risk control). Otherwise revert
   and write down what was measured (a measured-worse result is a real result — log it).
6. Append to `IMPROVEMENTS.md` (what / file / metric before→after / verdict). Commit + push.
7. Confirm green, update memory if state changed, loop.

---

## 4. Backlog (highest leverage first — re-rank each iteration against the scorecard)

**A. Model accuracy on live games is the #1 problem.** Tracker log-loss 1.155 > coin-flip.
   - Audit the live feature pipeline used for *upcoming* fixtures vs the training features —
     mismatch (neutral-site flag, host advantage for USA/Canada/Mexico, tournament tag,
     missing in-tournament Elo updates) silently degrades live predictions.
   - Check Elo cold-start / regression-to-mean at tournament start; confirm group-stage Elo
     k-factor and the home/neutral handling for 2026 (all matches neutral except hosts).
   - Re-examine the goal model (Poisson) inputs the same way.
   - Gate every change with `evaluate.py --retrain` (held-out) AND watch tracker log-loss.

**B. Strategy / bankroll (ROI −15.5%).**
   - ✅ done: derived-market shrinkage toward de-vigged market (`edge/shrink.py`,
     `market_blend_weight`). Tune the weight as group/champion markets resolve: compare raw
     model vs de-vigged market vs blend calibration on settled markets, set weight to the
     realised optimum.
   - Reconsider deploying ~80% of bankroll into long-dated derived markets (champion/group
     resolve weeks out, capital locked, marked-to-market drawdowns). Consider favouring
     near-dated match markets, an EV-per-day-locked ranking, or a lower exposure cap.
   - Add a real two-sided/vig sanity gate: if both YES and NO "show edge," that's model-vs-
     whole-market, not arb — treat with suspicion.

**C. Data flowing in.**
   - TheSportsDB free feed coverage: 48-team tournament, feed sometimes lags/omits games
     (Australia–Türkiye historically absent). Add a second results source or a manual-entry
     fallback that the engine merges; surface per-fixture feed coverage on the dashboard.
   - Validate the official 2026 group/schedule CSVs against a public source each retrain.
   - Cache + rate-limit Polymarket calls; detect stale/empty books explicitly.

**D. Website performance & UX.**
   - Measure SSE latency and payload size; `dashboard_state.json` is ~180 KB — trim what the
     client doesn't need, or gzip. Confirm the freshness chips reflect real job timestamps.
   - Make the strategy honesty visible: show raw-model vs blended prob and the blend weight
     on the edges tab (the data is already in `markets.comparison`).

**E. Calibration.** As live games accumulate, re-test whether isotonic/temperature beats
   `none` on the tournament population specifically (currently `none` wins on the broad
   validation slice). Recalibrate on majors if it helps live log-loss.

---

## 5. Change log

Substantive changes are recorded in `IMPROVEMENTS.md` with before→after metrics. The scorecard
history is machine-readable in `data/processed/scorecard_history.jsonl`.
