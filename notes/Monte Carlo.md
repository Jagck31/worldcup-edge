# Monte Carlo

`src/simulate/monte_carlo.py`. Simulates the 2026 tournament N times → sub-market probabilities. Part of [[Architecture]].

## What one sim does (`simulate_once_fast`)
12 groups → rank by FIFA tiebreakers (pts, GD, GF, head-to-head, lots) → top 2 + best 8 thirds → R32 → R16 → QF → SF → Final. Group scorelines from the **Elo-aware** sampler; knockout from Elo win probability. Captures the full `bracket` (matchups per round) for the dashboard.

## Two calibration fixes (see [[Findings & Decisions]])
1. **Elo spread** — build Elo on full history (not the 12-year training window) so favourites are properly separated.
2. **Elo-aware scoreline sampler** — tilt the goal model's xG by the Elo gap so the group stage reflects team strength. Together: Iran champion 3.7% → 1.4%, distribution now sane (Spain ~26%, minnows ~0%, champ sums to 100%).

## Speed
~40× faster after removing pandas from the per-sim loop (`_prepare_groups` does the only `iterrows`, once). ~5,000 sims/sec; 30k in ~6s. Default is **30k sims**. The live convergence visuals (trajectories per team + movement meter + sample bracket) are in the Live Run tab.

## Watch out
- The `divisor=700` controls the Elo tilt strength — see [[Ideas & Open Questions]].
- Third-place → bracket-slot assignment uses a non-official resolver until FIFA Annex C is loaded.
