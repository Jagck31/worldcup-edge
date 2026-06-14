# Live Tracker

Predictions vs real World Cup results, scored as the tournament plays out. `src/pipeline/tracker.py`. Part of [[Architecture]].

## What it does
1. Builds the group-stage fixtures from the **official schedule** (`wc2026_schedule.csv`) — real dates + kickoff times, host nations (Mexico / USA / Canada) oriented as **home** (not neutral).
2. Overlays **real results** as they arrive (`wc2026_results.csv`), matched by unordered team pair so home/away orientation can't mismatch a score.
3. Predicts every fixture with the calibrated [[Model]] (H/D/A probs) **and** the goal model (expected goals per team + likely scoreline distribution).
4. Scores completed matches: accuracy, **log loss**, **Brier**, avg favourite prob.

## The out-of-sample line
- `DATA_CUTOFF = 2026-06-12` — the martj42 dataset ends here. **Every match after that is a genuine forward test** (`out_of_sample: true`), scored separately from in-sample. This is the honest measure of whether the model has real edge.
- Baseline to beat: uniform log loss = `log(3)` ≈ **1.099**.

## In the dashboard (Live Tracker tab)
- Fixtures in **chronological kickoff order**, country names (not H/D/A), an **expected-goals** column next to the likely score, completed rows showing pick-vs-actual ✓/✗.
- Two scorecards: **all completed** and **out-of-sample only**.

## Why it matters for trading
The tracker is the reality check on the paper account. The suspect group-winner edges (model overrating non-favourites — see [[Trading & Paper Account]]) get judged here against what actually happens. If the out-of-sample log loss tracks the ~0.858 in-sample number, the edges are real; if it blows out, they were miscalibration. See [[Findings & Decisions]].
