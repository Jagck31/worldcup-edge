# Improvements log

Key improvements to the World Cup edge system. Entries below the line are appended
**automatically** by the implementer agent (`implementer.py`) as it works through the
improver agent's proposals — a closed loop: **improver proposes → implementer applies
(test-gated, git-committed) → logs here → improver reads this + the machine log and proposes
the next thing.** No human approval required. Pause it any time with
`touch data/processed/IMPLEMENTER_OFF`; revert any change with `git revert <commit>`.

## How the loop works
- `wc-improver` (every 2h): reads live metrics + what's already been implemented, proposes
  ranked improvements → `data/processed/improvement_proposals.json` (shown on the **Ops·AI** tab).
- `wc-implementer` (every 1h): picks the top open proposal, asks the LLM for the complete
  updated version of ONE allowed file (`src/{model,features,simulate,edge,ingest}`), keeps it
  **only if all 67 tests still pass** (else instant restore), commits to git, restarts the
  engine, and records the outcome → `data/processed/improvements_log.json` + this file.
- Guardrails: never edits the engine/orchestrator core, agents, deploy, config, `.env`, or
  anything outside this repo; a proposal that fails twice is parked.

## Seed — improvements made by hand (2026-06-14)
- **Live data-feed completeness** — the free TheSportsDB `eventsseason` feed omits finished
  games; now also polls `eventsday` across a tournament-clamped date window and merges,
  preferring the most-advanced state. Recovered finished games the season feed hid.
- **In-play surfacing + adaptive polling** — in-play games (score + minute) now drive a live
  banner + ticker, and the results loop tightens to 30s while any match is live.
- **Active paper deployment** — paper book sizes by bankroll half-Kelly (best edge first,
  under the 80% cap) instead of thin live depth: ~$300 → ~$8,000 deployed.

---
<!-- implementer appends below -->

## 2026-06-15 — Measurement backbone + derived-market shrinkage (Claude, autonomous loop kickoff)
Stood up the autonomous improvement loop: a single objective function + guardrails so every
future change is measured before it ships. See `AUTONOMOUS_LOOP.md`.
- **`evaluate.py` (new)** — one scorecard for the whole system (model held-out calibration,
  live tracker, paper ROI, data completeness, derived-market sanity). Modes: fast / `--retrain`
  (fresh held-out metrics, gates model changes) / `--live` (fresh Polymarket diag, gates strategy
  changes). Writes `data/processed/scorecard.json` + appends `scorecard_history.jsonl`.
- **What it revealed:** data path is clean (95/95 markets map, 100%), so the giant slate edges
  are *real* model-vs-market disagreements, not bugs — the sim is over-concentrated on the Elo
  favourite in some groups (J: model 0.94 vs market 0.73) and under-confident in others (E:
  0.49 vs 0.75). Paper book down (ROI ≈ −15%); live tracker log-loss 1.155 (worse than the
  1.099 coin-flip) on 10 games — model accuracy on live games is now the #1 backlog item.
- **`src/edge/shrink.py` (new) + `_build_live_markets`** — humility shrinkage: blend each
  derived-market (champion/group-winner) probability `market_blend_weight` (config, 0.35) of the
  way toward the **de-vigged** Polymarket book before detecting edges. Per-contract and
  direction-aware (trims over-confident favourites, lifts under-confident long-shots), preserves
  the partition sum, leaves the match-level 1X2 model untouched. **Measured (live):
  extreme-disagreement edges 4 → 0, slate 27 → 23.** Framed as reversible risk control
  (`market_blend_weight: 0` restores old behaviour); ROI impact to be measured as markets settle.
- **Tests:** 67 → 74 green (7 new for the blend). Full engine `--once` smoke test passes.

## 2026-06-14 — Flawless audit: 19 verified bugs fixed (Claude + 33-agent workflow)
Ran an adversarial multi-agent audit (6 auditors × per-finding skeptic verification): 27 raw
findings, **19 confirmed real** (8 rejected as false positives). All 19 fixed; 67/67 tests green.
- **HIGH** — paper ledger re-opened already-resolved markets every cycle (NO-side bleed) → skip
  resolved mids in the deploy loop; USA/Paraguay double-counted in Elo (same fixture in results.csv
  06-12 + wc2026_results 06-13) → dedup by team-pair within ±5 days; deploy-refit deployed different
  models in different engines while metrics described a third → explicit `refit_full`, set identically
  everywhere; auto-deploy `git reset` wiped local commits + never retried a failed update → gate on a
  deployed-SHA marker.
- **MED** — atomic paper-ledger save; portfolio no longer goes stale (unconditional reset); knockout
  rematch no longer collapses with its group game ((date,pair) match); single-instance flock; pip
  non-fatal in update.sh; implementer commits only the edited file + defaults OFF.
- **LOW** — retrain keeps the equity curve / in-play snapshot; top-ups mark at mid + blend model_prob
  (honest equity); favourite KPI no longer double-escapes "Bosnia & Herzegovina"; OOS labelled on date
  not kickoff datetime; watchdog cooldown persisted across restarts.

## 2026-06-14 — Proposal "Enhance feature set": tried competitive-only form → REVERTED (Claude)
- **file:** `src/features/build_features.py`  ·  **area:** features  ·  **status:** reverted (no improvement)
- Added competitive-matches-only form (ppg / win-rate / goal-diff over last 5 & 10 competitive
  games, friendlies excluded). **Measured: held-out log-loss 0.8583 → 0.8595 (worse by 0.0012).**
  The GBT already extracts this signal from existing form + Elo-residual features, so the extra
  columns just added noise. Reverted — only changes that measurably help are kept. (The on-box
  auto-implementer would have wrongly kept this: it only checks "tests pass," not whether the
  metric improved.)

## 2026-06-14 — Deploy-refit the 1X2 model on full history (Claude, from proposal "Implement Calibration Method")
- **file:** `src/model/train.py`  ·  **area:** model  ·  **tests:** 67/67 green
- Investigated the "no calibration" proposal: the code already fits isotonic + temperature and
  correctly selects `none` because calibration didn't beat raw probs on the validation slice —
  so "add calibration" was a misread. The real waste was that the *shipped* model trained on only
  the 60% train split. Now: keep the chronological splits for an honest held-out estimate, then
  **refit the deployed model on 100% of the data** (when no calibrator is in play, so nothing
  mismatches). Deployed training rows **6,928 → 11,548** (+67%). Held-out report unchanged by
  design (it's the honest estimate); the gain accrues in live tracker accuracy.

