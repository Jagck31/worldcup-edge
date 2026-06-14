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

