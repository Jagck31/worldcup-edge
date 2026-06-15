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

## 2026-06-15 — Live match-minute + dashboard scroll-lag fix (Claude, user-requested)
- **files:** `src/ingest/livescores.py` (`_match_minute`, `minute` on `LiveEvent`, merge tie-break),
  `src/ingest/espn.py` (minute from `displayClock`), `src/pipeline/live_engine.py` (minute on live
  games), `web_app.py` (show minute; drop per-tile backdrop blur). · **tests:** 87→91 green.
- **Minute:** in-play games showed a clock/phase, not the match minute. Now `LiveEvent.minute`
  is parsed per source (TheSportsDB `strProgress` → "67'", ESPN `displayClock`), the merge prefers
  the copy that has a minute, and the banner/ticker render it. Blank for non-live so no stray clock.
- **Lag:** every bento tile had `backdrop-filter:blur(13px)`, re-blurring the backdrop each frame
  while 3 blurred blobs drift behind them — the main scroll-jank source. The blobs are already
  `blur(60px)`, so per-tile backdrop blur was redundant; replaced with a near-opaque glass
  (`--glass .46→.74`). Added a `prefers-reduced-motion` block that freezes blobs/ticker/pulses.

## 2026-06-15 — ESPN second results source: stop missing a game a day (Claude, user-requested)
- **files:** `src/ingest/espn.py` (new), `src/ingest/livescores.py` (`merge_event_lists`),
  `src/pipeline/live_engine.py` (merge ESPN in `job_results`), `config.yaml` (`use_espn`). · **tests:** 80→87 green.
- **Why:** confirmed the free TheSportsDB tier silently omits whole fixtures (it returned 3 of
  06-14's 5 games), so the tracker missed Australia-Turkiye, Netherlands-Japan, Sweden-Tunisia.
- **What:** ESPN's free, key-less soccer scoreboard (`site.api.espn.com/.../soccer/fifa.world`)
  as a second source, parsed to the same `LiveEvent` shape and merged by team-pair+day keeping the
  most-advanced state. ESPN carries the full slate (24 events vs TheSportsDB's 18 in the smoke
  test), names canonicalised (Türkiye→Turkey, Ivory Coast→Cote d'Ivoire, USA→United States). The
  manual seed stays as a last-resort backstop; ESPN should make daily misses a non-issue. Engine
  `--once` clean: "24 events, 13 scored". Degrades gracefully (ESPN failure can't sink the cycle).

## 2026-06-15 — Strategy risk: concentration diagnostic + halve single-bet cap (Claude, loop iter)
- **files:** `src/edge/risk.py` (new `position_risk`), `evaluate.py` (RISK section + headline),
  `config.yaml` (`max_single_bet_pct` 0.20 -> 0.10). · **tests:** 77 -> 80 green.
- **Measured the -15% book:** it's 80% deployed but brutally concentrated — top-3 = 49.9% of
  bankroll, top-5 = 77.5%, one $2,000 (20%) Austria-NO bet, the rest $10 tokens, all settling
  06-27/07-20 (n_settled=0). Bankroll-Kelly best-edge-first piles into a handful of derived bets.
- **Change:** scorecard now reports invested/max_single/top3/top5/settle-buckets (+ model
  expected ROI vs marked) so concentration is tracked every iteration; and the single-bet cap is
  halved to 10% so no future paper bet can be 20% of the book. Reversible via config; forward-
  looking (existing positions unchanged, so the headline still shows the legacy 20% until those
  settle). ROI itself isn't measurable until markets resolve — logged as conservative risk control.

## 2026-06-15 — Manual-results seed: git-tracked fallback for games the feed omits (Claude)
- **files:** `src/pipeline/live_tracker.py` (`load_manual_seed_events`, `merge_finished_into_csv(fill_only=)`),
  `src/pipeline/live_engine.py` (merge in `job_results`), `src/pipeline/orchestrator.py`
  (`MANUAL_RESULTS_SEED_PATH`), `data/manual/manual_results_seed.csv` (new). · **tests:** 74→77 green.
- **Why:** confirmed via direct API query that the free TheSportsDB feed simply does not carry some
  2026 games (06-14 returns only Haiti-Scotland / Germany-Curaçao / Ivory Coast-Ecuador; 06-15 only
  Belgium-Egypt / Saudi-Uruguay / Spain-Cape Verde). So Australia-Turkiye, Netherlands-Japan and
  Sweden-Tunisia stay "scheduled" forever — the tracker never sees them. The box is unreachable from
  the dev sandbox, so a hand-entry on the box won't do; the fix has to deploy through git.
- **What:** a git-tracked CSV (`manual_results_seed.csv`) the engine merges every results cycle as
  **gap-fill only** — it adds games the feed lacks but never overwrites a real feed result, and the
  feed's own `wc2026_results.csv` stays gitignored so auto-deploy can't clobber it. Blank/half-filled
  rows are skipped, so the file is safe to ship empty. Verified end-to-end with dummy scores: the 3
  games appear as completed + frozen + scored, then reverted. **Awaiting the real final scores to
  populate the seed** (won't commit fabricated results).

## 2026-06-15 — Investigated live tracker log-loss spike → small-sample noise, NO change (Claude)
- **area:** model · **status:** measured, no code change (correctly *not* "fixed")
- Live tracker log-loss hit 1.155 (worse than the 1.099 coin-flip) on n=10 games; 4 were draws
  and the model never picks draw, so the draw-under-calibration hypothesis was obvious.
- **Tested it on the large held-out sample before touching anything:** mean predicted P(draw)
  = 0.220 vs actual draw freq 0.233 (majors: 0.209 vs 0.222) — diff −0.013. Home/Away equally
  tight. **The model's class calibration is correct in aggregate; the live spike is variance**
  (e.g. Cape Verde drawing Spain is a real upset, not a model bug). "Fixing" draws would have
  chased 10 games of noise and worsened the honest 0.809 metric. Logged as a guard against the
  loop overfitting to small live samples. Backlog re-ranked: live model accuracy is not
  optimizable at n=10 — wait for games to accumulate; strategy/data/website are next.

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

