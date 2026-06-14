# Findings & Decisions

Durable insights (the *why*). Chronological measured changes live in [[IMPROVEMENT_LOG]].

## Model
- **The 1X2 model is at a data-limited plateau (~0.858 log loss vs 1.099 uniform).** Hyperparameter search and sample-weighting both failed to beat it; international football is heavily Elo-determined. More features give diminishing returns *for log loss*. See [[Model]].
- **FIFA rankings genuinely helped** (0.876 → 0.868): `rank_points_diff`/`rank_diff` are the #2/#3 features — the one signal not derived from results.
- **`elo_expected_home`** (logistic of Elo + home advantage) is the single best input — better than raw `elo_diff`.
- **Calibration must be allowed to do nothing.** Forcing isotonic *worsened* log loss; the trainer now picks the best of none/isotonic/temperature.
- **Trees: more ≠ better.** The learning curve overfits past ~100 trees; the trainer keeps the best iteration.

## Elo
- **Elo needs long history; the model needs recency.** Restricting Elo to 12 years collapsed the rating *spread* (Spain→Iran gap 164 vs 264) and made the Monte Carlo too random (Iran 3.7% champion). Fix: **Elo on full history, model trained on last 12 years.** See [[Monte Carlo]].
- It's a standard chess-style Elo (zero-sum symmetric update) with football tuning (importance K, margin-of-victory, home advantage, draws=0.5).
- **Home advantage (75) and the K-factors are already near-optimal** — swept both on a 2023+/2025+ holdout (`eval_elo.py`) and neither moved log loss. Making Elo "more responsive" (higher K) helped the most-recent window but hurt the broader one — no robust gain. Don't chase responsiveness.
- **The one Elo win found: 538-style margin-of-victory autocorrection** (`mov_autocorrection=0.0018`). Scales the blowout bonus down when the winner was already favoured, so thrashing minnows doesn't inflate ratings. Holdout log loss 0.5469→0.5451, accuracy 0.786→0.792. This is the right fix for "modern rating accuracy" — it's about *correctness of the spread*, not recency. See [[IMPROVEMENT_LOG]] #18.

## Monte Carlo
- **The group stage must reflect Elo, not just the goal model.** The goal model barely separates teams → minnows advanced too often → fake group-winner edges. Fix: **Elo-aware scoreline sampler.** Iran 3.7% → 1.4%; champ probs now sane.
- **Don't touch pandas in the per-sim loop.** Rewriting the hot path in plain Python gave a **~40× speedup** (115 → ~5,000 sims/sec); 30k now runs in ~6s.

## Trading
- **Most "edges" are model error, not opportunity.** The model overrates non-favourites to win their group; the sharp Polymarket market is usually right. The credible edges are the champion-market ones (Spain/Argentina). The paper bot's BUY-NO shorts of favourites are the flip side of the same miscalibration — the [[Live Tracker]] + paper account test this against reality. See [[Trading & Paper Account]].
- Champion edges are real but usually below the $5 min-fill — that suppression is the point.
