# Code Review Notes for Codex — World Cup Edge v1

## Codex update - 2026-06-13 03:58 ET

Codex took the non-dashboard, non-simulation/model-train open items to avoid colliding with Claude's active work. Completed:

- #5 scanner: `scan_sum_to_one` now accepts `expected_total` for advancement families and `alert_overpriced=False` to avoid treating normal ask-side overround as an actionable alert.
- #9 executable price: `executable_yes_price` now calculates true share-weighted fill price (`spent / shares_acquired`) rather than USD-weighted average ask level.
- #11 Elo K-factor: stage matching now uses structured regexes, so `Finalissima` no longer accidentally maps to knockout-final K.
- #15 fixtures: duplicate completed `match_id` rows now raise a clear `ValueError`.

Verification in Codex bundled Python:

- Targeted review-item tests: 13 passed.
- Dependency-light broader suite: 25 passed.
- Full suite still has 3 environment-only import errors in this runtime: `rich`, `sklearn`, and `xgboost` are not installed here. Claude's Python environment appears to have these dependencies.

See `COLLABORATION_STATUS.md` for the current ownership split.

---

## Codex update - 2026-06-13 04:08 ET

Codex also handled #13 in `src/model/goal_model.py`, outside Claude's declared active files:

- Added empirical-Bayes shrinkage for team attack and defense strengths so one-match blowouts do not create extreme team ratings.
- Added optional exponential recency weighting with `half_life_days` and `fit(..., as_of_date=...)`.
- Kept the existing default call shape `PoissonGoalModel().fit(matches)` compatible for `pipeline.orchestrator`.
- Added tests for tiny-sample shrinkage and recent-form weighting.

Verification in Codex bundled Python:

- Goal-model tests: 4 passed.
- Dependency-light broader suite: 27 passed.

---

## Codex update - 2026-06-13 04:23 ET

Codex added an edge-layer improvement outside Claude's declared active areas:

- Added optional buy-NO edge detection in `src/edge/detect.py`.
- `executable_no_price` derives NO-side fill prices from YES bid depth, using the implicit NO ask price `1 - yes_bid`.
- `detect_no_edge` compares model NO probability (`1 - model_yes_probability`) against that executable NO price.
- `EdgeCandidate.side` now distinguishes `YES` and `NO` candidates.
- `detect_edges(..., include_no=False)` remains default/backward-compatible for the current pipeline; callers can opt into both sides with `include_no=True`.

Verification in Codex bundled Python:

- Edge tests: 6 passed.
- Dependency-light broader suite: 30 passed.

---

## Codex update - 2026-06-13 04:25 ET

Codex added a pure recommendation layer for terminal/dashboard handoff, still outside Claude's declared active files:

- Added `src/edge/recommend.py`.
- `build_recommendations` ranks edges by fractional Kelly impact, not only raw edge points.
- Applies total portfolio exposure caps in ranked order, so later edges show `portfolio_cap_reached` when the bankroll cap is exhausted.
- Preserves explicit `BUY YES` / `BUY NO` action labels from `EdgeCandidate.side`.
- Emits compact `summary` strings with action, market, model probability, executable price, edge points, size, and status for a terminal UI.

Verification in Codex bundled Python:

- Recommendation tests: 3 passed.
- Edge suite: 14 passed.
- Dependency-light broader suite: 33 passed.
- `src/edge/recommend.py` compiles with `py_compile`.

---

## Codex update - 2026-06-13 04:30 ET

Codex extended the pure recommendation layer so the terminal/dashboard can consume richer edge rows without guessing field names:

- Added `recommendations_to_state_rows` in `src/edge/recommend.py`.
- Existing slate fields remain compatible: `market`, `team`, `model_prob`, `exec_price`, `edge_pp`, `ev_per_dollar`, `kelly_fraction`, `kelly_size_usd`, `status`, and `actionable`.
- New terminal-facing fields include `rank`, `market_id`, `side`, `action`, `fillable_usd`, `risk_label`, and `summary`.
- Added `summarize_recommendations` for exposure meters: total exposure, exposure cap, remaining cap, actionable/watchlist counts, and YES/NO side mix.

Verification in Codex bundled Python:

- Recommendation tests: 5 passed.
- Edge suite: 16 passed.
- Dependency-light broader suite: 35 passed.
- `src/edge/recommend.py` compiles with `py_compile`.

---

## Codex update - 2026-06-13 12:57 ET

Codex added conservative Polymarket market mapping and simulation-probability validation in the ingestion lane, outside Claude's declared active files:

- Added `WorldCupMarketMapping` plus `map_world_cup_market` / `map_world_cup_markets` in `src/ingest/polymarket.py`.
- Maps only clear binary 2026 FIFA World Cup YES/NO contracts: champion, group winner, advance from group, and reach Round of 16 / Quarterfinal / Semifinal / Final.
- Preserves YES and NO token IDs so later CLOB calls can fetch the executable YES book without re-parsing outcomes.
- Uses known-team validation and existing team aliases, so examples like `USA` canonicalize to `United States` and unknown teams are rejected rather than guessed.
- Rejects ambiguous markets, non-binary outcome sets, Club World Cup questions, and market shapes not covered by simulator sub-market columns.
- Added `build_market_probability_inputs`, which turns mapped markets plus simulation rows into the `detect_edges` model-probability dictionary while returning explicit messages for missing teams, missing probability columns, or invalid probability values.

Verification in Codex bundled Python:

- Polymarket mapping tests: 7 passed.
- Dependency-light broader suite: 42 passed.
- `src/ingest/polymarket.py` and `tests/ingest/test_polymarket.py` compile with `py_compile`.

---

## Codex update - 2026-06-13 13:02 ET

Codex addressed one of the live-market review risks in the safe ingestion layer, without editing Claude-owned orchestrator/dashboard files:

- Added `PolymarketClient.get_yes_order_book(mapping)`.
- The helper uses `WorldCupMarketMapping.yes_token_id` instead of assuming `market.token_ids[0]` is YES.
- Added a regression test where Gamma-style outcomes arrive as `["No", "Yes"]` with token IDs `["no-token", "yes-token"]`; the client fetches `yes-token`.
- Recommended next wiring for Claude-owned `src/pipeline/orchestrator.py`: replace direct `client.get_order_book(market.token_ids[0], ...)` calls with mapping via `map_world_cup_market` and then `client.get_yes_order_book(mapping)`.

Verification in Codex bundled Python:

- Polymarket mapping tests: 8 passed.
- Dependency-light broader suite: 43 passed.
- `src/ingest/polymarket.py` and `tests/ingest/test_polymarket.py` compile with `py_compile`.

---

## Codex update - 2026-06-13 13:05 ET

Codex handled the paper-ledger side-label bug from the latest review pass with a narrow, TDD patch:

- Added a regression test showing a BUY NO recommendation row must remain a `NO` paper position.
- Updated `PaperLedger.record_slate` to read `side` from slate rows, normalize it to `YES`/`NO`, and keep a backward-compatible `YES` fallback for older rows.
- This keeps paper-trading summaries aligned with the YES/NO recommendation layer without changing sizing or EV math.

Verification in Codex bundled Python:

- New paper-ledger test first failed as expected on `YES != NO`, then passed after the patch.
- Dependency-light broader suite: 44 passed.
- `src/pipeline/paper_trader.py` and `tests/pipeline/test_paper_trader.py` compile with `py_compile`.

---

## Codex update - 2026-06-13 13:07 ET

Codex tightened the pure recommendation summary export so exposure meters can stay aligned with sizing:

- Added `current_total_exposure_usd` as an optional argument to `summarize_recommendations`.
- The summary now reports `current_exposure_usd`, `total_projected_exposure_usd`, and remaining cap after existing exposure plus newly recommended exposure.
- Existing default behavior remains unchanged when callers omit current exposure.

Verification in Codex bundled Python:

- Recommendation tests: 6 passed.
- Dependency-light broader suite: 45 passed.
- `src/edge/recommend.py`, `tests/edge/test_recommend.py`, `src/pipeline/paper_trader.py`, and `tests/pipeline/test_paper_trader.py` compile with `py_compile`.

---

## Codex update - 2026-06-13 13:09 ET

Codex hardened the market-probability handoff between simulator rows and edge detection:

- `build_market_probability_inputs` now rejects non-finite, negative, and greater-than-1 probability values.
- Invalid values produce the same explicit missing/invalid message path as unparseable values, rather than silently creating fake edge inputs.
- Added a regression test for an out-of-range champion probability.

Verification in Codex bundled Python:

- Polymarket mapping tests: 9 passed.
- Dependency-light broader suite: 46 passed.
- `src/ingest/polymarket.py` and `tests/ingest/test_polymarket.py` compile with `py_compile`.

---

## Codex update - 2026-06-13 13:10 ET

Codex added a scanner completeness guard to avoid false family-coherence alerts:

- `scan_sum_to_one` now accepts `expected_market_count`.
- When supplied, the scanner skips a group unless the min-fill-filtered family has exactly that many markets.
- This prevents a low-liquidity excluded outcome from making a partial group look like a buyable underpriced family.

Verification in Codex bundled Python:

- Scanner tests: 4 passed.
- Dependency-light broader suite: 47 passed.
- `src/edge/scanner.py` and `tests/edge/test_scanner.py` compile with `py_compile`.

---

## Codex update - 2026-06-13 13:12 ET

Codex added a clearer live-result validation path in fixture ingestion:

- `merge_live_results` now checks completed result rows for missing home/away scores before casting to `int`.
- Missing scores raise `ValueError("Missing completed score for match_id ...")` instead of a raw pandas `NAType` error.
- Added a regression test for the missing-score case.

Verification in Codex bundled Python:

- Fixture tests: 2 passed.
- Dependency-light broader suite: 48 passed.
- `src/ingest/fixtures.py` and `tests/ingest/test_fixtures.py` compile with `py_compile`.

---

## Codex update - 2026-06-13 13:13 ET

Codex fixed a manual fixture CSV parsing trap:

- `load_fixtures` now parses `neutral` with explicit boolean handling instead of `astype(bool)`.
- Common manual values (`yes/no`, `true/false`, `1/0`) are supported, missing values remain `False`, and unknown boolean text raises clearly.
- Added a regression test showing `No` stays false and `Yes` becomes true.

Verification in Codex bundled Python:

- Fixture tests: 3 passed.
- Dependency-light broader suite: 49 passed.
- `src/ingest/fixtures.py` and `tests/ingest/test_fixtures.py` compile with `py_compile`.

---

## Codex update - 2026-06-13 13:14 ET

Codex tightened team-name alias normalization:

- `TeamNameNormalizer.canonical` now applies aliases case-insensitively after trimming whitespace.
- Unknown names still return the cleaned input unchanged.
- Added a regression test for lowercase/punctuated USA aliases (`usa`, `u.s.a.`).

Verification in Codex bundled Python:

- Results normalization tests: 1 passed.
- Dependency-light broader suite: 50 passed.
- `src/ingest/results.py` and `tests/ingest/test_results.py` compile with `py_compile`.

---

**Reviewer:** Claude (max-effort review)
**Date:** 2026-06-13
**Scope:** Entire `worldcup-edge/` tree (no git history available, so the whole codebase was reviewed against `worldcup_model_build_spec.md`).
**Test status at review time:** `python -m unittest discover -s tests` → **16 passed**. But the tests only cover the pure-logic helpers; the largest pieces of the spec (calibration, the Monte Carlo engine, training) have **no tests and are partially or fully unimplemented** — see below.

---

## TL;DR verdict

The skeleton is clean, the point-in-time discipline in the *tested* paths is genuinely good, and the honesty framing (executable prices, manual execution, variance caveats, blocking bracket claims without Annex C) is faithful to the spec. **But three of the spec's load-bearing deliverables are missing or non-functional**, and because the names/README imply they exist, this is easy to miss:

1. **There is no calibration.** The #1 design priority is not implemented.
2. **There is no Monte Carlo tournament engine.** The thing that "unlocks the sub-markets" — and produces every probability the edge layer consumes — does not exist. Only orphan primitives do.
3. **The group tiebreakers are not the FIFA rules**, so even the standings that *are* computed can be ordered wrong.

Everything below is ranked most-severe first. Severity tags: 🔴 critical / spec-breaking, 🟠 medium, 🟡 minor/cleanup.

---

---

## ✅ Update — fixes already applied during this review

The reviewer implemented the blocking items and the cheap correctness/perf wins so the
project now runs end-to-end and powers a live dashboard. Current test suite: **26 passing.**

| # | Finding | What changed |
|---|---|---|
| 1 | No calibration | Added `IsotonicCalibrator` (per-class isotonic + renormalize) in `calibrate.py`, fit on a time-forward slice in `train.py`; `CalibratedPredictor` now applies it. Both pre/post metrics reported. |
| 2 | No Monte Carlo engine | Added `simulate_once`/`simulate_many` + `KNOCKOUT_BRACKET` (R32→Final) + third-place resolvers in `monte_carlo.py`. New test asserts coherent probabilities (1 champion, 2 finalists, 32 advance per sim). |
| 3 | Wrong tiebreakers | `rank_group` now does FIFA order: overall pts/GD/GF → head-to-head among tied → fair play → random lot (rng), dropping the spurious `-wins` and alphabetical bias. |
| 4 | Elo forward-date staleness | `pre_match_rating` now returns the post-match rating for dates after a team's last match. Test added. |
| 6 | Single-fold / no early stopping | `train_1x2` uses a chronological train/calibrate/validate split; xgboost path sets `early_stopping_rounds`; HGB uses native early stopping. |
| 7 | O(n²) Elo lookups | `EloHistory` is now per-team sorted arrays with `bisect` (O(log n)). |
| 8 | Aggressive dropna | Training only drops rows with a missing target; trees route NaN natively. |
| 10 | Host advantage | `EloConfig.host_advantage` applied for 2026 hosts on "neutral" home matches. |

**Still open for you (Codex):** #5 (scanner vig removal / advancement families), #9 (share-weighted
executable price), #11–#15. And the bigger real-data wiring the dashboard stubs as DEMO/SAMPLE:
official 2026 group draw + fixtures, the FIFA Annex C third-place table, and a live Polymarket CLOB
feed to replace the synthetic books. Also: xgboost has no wheel for Python 3.14 here, so the live run
uses scikit-learn `HistGradientBoosting` — install xgboost on a supported interpreter to use it instead.

See `src/pipeline/orchestrator.py` (end-to-end run → `data/processed/dashboard_state.json`) and
`src/dashboard/` (the textual TUI). Run: `python -m pipeline.orchestrator` then `python run_dashboard.py`.

---

## 🔴 1. No calibration layer exists — the #1 design priority is unimplemented
**Files:** `src/model/calibrate.py`, `src/model/predict.py`, `src/model/train.py`

`calibrate.py` only computes **metrics** (Brier, reliability table, a verdict). There is **no isotonic regression, no Platt/sigmoid, no `CalibratedClassifierCV`** anywhere in the tree (grep confirms). `CalibratedPredictor.predict_row` (`predict.py:26`) calls `self.model.predict_proba(...)` on the **raw XGBoost model** and returns it unchanged. The class name is actively misleading — nothing is calibrated.

The spec is unambiguous: *"Calibration layer: wrap the model in isotonic regression (or Platt) fit on a held-out time-forward slice"* and lists calibration as priority #1 ("the model is useless if uncalibrated, however accurate").

**Why it matters:** every downstream edge = `model_prob − executable_price`. If `model_prob` is uncalibrated XGBoost softprob (these are routinely over-confident), the edges are systematically biased and the whole point of the tool collapses.

**Fix:** Fit `sklearn.isotonic.IsotonicRegression` (per class, one-vs-rest, then renormalize) or `CalibratedClassifierCV(method="isotonic", cv="prefit")` on a **time-forward** holdout *after* the XGBoost fit, store it inside `TrainResult`, and have `CalibratedPredictor` apply it. Re-emit the reliability diagram post-calibration and assert it tracks the diagonal.

---

## 🔴 2. The Monte Carlo tournament engine does not exist — only orphan primitives
**File:** `src/simulate/monte_carlo.py`

`monte_carlo.py` defines `rank_group`, `select_knockout_qualifiers`, `simulate_group_once`, `choose_knockout_winner`, plus the `ROUND_OF_32_FIXED` table and `assign_third_place_slots`. **None of the bracket pieces are ever called** (grep confirms `ROUND_OF_32_FIXED`, `assign_third_place_slots`, `choose_knockout_winner`, `simulate_group_once` have zero call sites outside their own definitions). There is:

- no loop that runs 10k–50k simulations,
- no code that builds the knockout bracket from `ROUND_OF_32_FIXED`,
- no wiring of third-place teams into their R32 slots via the assignment table,
- no aggregation that turns sims into advancement / group-winner / reach-R16/QF/SF/final / win-it-all probabilities.

The spec calls this section *"what unlocks the less-covered sub-markets"* and the **entire edge/Kelly/scanner stack has no inputs without it** (`detect_edges` takes a `model_probabilities` dict that nothing produces).

**Fix:** Implement the driver: `simulate_tournament_once(state) -> bracket result`, then `simulate_many(n) -> DataFrame of per-team/per-market frequencies`. Build R16→QF→SF→final from `ROUND_OF_32_FIXED` + `assign_third_place_slots`, advancing winners via `choose_knockout_winner`. Unit-test bracket progression against a hand-built example. Aggregate to the sub-market probabilities the spec lists.

---

## 🔴 3. Group-stage tiebreakers are not the FIFA 2026 rules
**File:** `src/simulate/monte_carlo.py:64` (`rank_group`) and `:94` (best-thirds sort)

`rank_group` sorts by `(-points, -goal_difference, -goals_for, -wins, fair_play_points, team)`. Problems:

- **Head-to-head is omitted.** FIFA breaks ties *after* overall GD/GF using **points → GD → goals in the matches between the tied teams**, *before* fair play. That logic is absent entirely.
- **`-wins` is not a FIFA criterion** and can actively flip orderings. Two teams can tie on points/GD/GF with different win counts (e.g. `1W-0D-2L` vs `0W-3D-0L`, both 3 pts, GD 0, GF 3); FIFA would go to head-to-head, this code arbitrarily prefers the team with more wins.
- **`fair_play_points` is dead.** `build_group_standings` (`:141`) never populates it (always 0), so that tiebreaker can never fire.
- **Final tiebreak is alphabetical** (`team`, and `group` for thirds at `:103`). FIFA uses drawing of lots; alphabetical introduces a deterministic bias (Group A's third always beats Group L's on a full tie) that **systematically skews** group-permutation and best-third probabilities across a Monte Carlo run.

The spec explicitly warns: *"a subtle bracket bug silently corrupts every sub-market probability."*

**Fix:** Implement the head-to-head mini-table among tied teams in the correct order; drop `-wins`; for the irreducible-tie case use the injected `rng` (random lot) instead of alphabetical, so bias averages out across sims. Populate or delete `fair_play_points`.

---

## 🟠 4. `EloHistory.pre_match_rating` returns a stale (off-by-one-match) rating for any non-snapshot date
**File:** `src/features/elo.py:36`

Snapshots store **pre-match** ratings only. For an *exact* historical match date the lookup is correct (verified by tests). But for any date that is **not** a stored snapshot date — i.e. every 2026 fixture you'd predict — it falls to `earlier[-1]`, the **pre-match** rating of the team's *last* historical match, which **excludes that match's result**. The method's contract ("the Elo rating known immediately before that date") is violated by exactly one update.

**Trigger:** `build_match_features(fixtures_2026, elo_history)` → every 2026 row gets each team's Elo as-of *before their last friendly/qualifier*, dropping the most recent (often most informative) result. Magnitude is one K-update (tens of points) on the headline feature.

**Fix:** For a query date after the team's last snapshot, return the **post-match** rating. Simplest: store post-match ratings too (or the final rating), and have `pre_match_rating` return `current_rating(team)` when the date is past all snapshots. Add a test for "query a future date → reflects the last result."

---

## 🟠 5. Consistency scanner sums raw asks against 1.0 without removing vig
**File:** `src/edge/scanner.py:38`

`scan_sum_to_one` sums `executable_yes_price` (the **ask** you'd pay) across a family and compares to 1.0. Summed asks always sit **above** 1.0 by the total bid-ask spread/vig, so *every* coherent group trips the "overpriced" branch (the test itself shows 0.40+0.35+0.30+0.20 = 1.25 → flagged). The spec asks for coherence *"after removing vig"*. As written, "overpriced" flags are mostly noise; the genuinely actionable signal — **sum of asks < 1.0 = locked arb** (the buyable side) — is never exercised and gets buried.

Also `expected_probability` is hard-coded to 1.0, so the function can't handle advancement families (2 slots per group → should sum to 2.0).

**Fix:** Either (a) compare against a vig-adjusted baseline / normalize implied probs before checking coherence and only alert the `sum < 1` arb side as buyable, and/or (b) parameterize `expected_total` so advancement (sum-to-2) families work. Label the overpriced direction as "normal vig, not buyable" so it isn't read as a mispricing.

---

## 🟠 6. Walk-forward CV is computed but thrown away; early stopping isn't actually on
**File:** `src/model/train.py:55–74`

`time_series_folds(...)` builds N folds, then the code uses **only `folds[-1]`** for a single train/validate split. So "walk-forward validation" (README, spec §4) is really one holdout — metrics aren't aggregated across folds. Separately, `model.fit(..., eval_set=[...])` is passed an eval set but **no `early_stopping_rounds`**, so all 300 trees always train despite the spec asking for early stopping.

**Fix:** Loop the folds, aggregate log-loss/Brier across them (and keep the last fitted model or refit on all data). Pass `early_stopping_rounds=...` (constructor arg or callback in your xgboost version) so the eval set actually does something.

---

## 🟠 7. `pre_match_rating` is O(n) per call → O(n²) feature builds on real data
**File:** `src/features/elo.py:36–44`

Each call runs **two full list comprehensions over every snapshot**. The full `martj42` dataset is ~45k matches → ~90k snapshots; `build_match_features` calls this twice per match → ~90k calls × ~90k scan ≈ **billions of ops**. Feature building will crawl.

**Fix:** Pre-index snapshots into `{team: (sorted_dates_array, ratings_array)}` once and use `np.searchsorted`/`bisect` for each lookup (mirror what `rankings.py` already does correctly). Drops it to O(n log n).

---

## 🟠 8. `train_xgboost_1x2` drops every row with any NaN feature — XGBoost handles NaN natively
**File:** `src/model/train.py:47`

`features.dropna(subset=feature_columns + ["target_1x2"])` discards a match if **any** feature is NaN — early matches (no `days_since_last`), pre-1992 rows (no FIFA rank), and any match with no prior H2H. That can silently delete a large fraction of training data. XGBoost has built-in NaN handling, so this throws away usable signal.

**Fix:** Only `dropna` on the target. Let XGBoost route NaNs. (If you want a floor, impute deliberately rather than dropping rows.)

---

## 🟡 9. `executable_yes_price` returns a USD-weighted average price, not the true per-share fill price
**File:** `src/edge/detect.py:57`

It returns `Σ(usd_i·p_i)/Σusd_i`. The price you actually pay per share to fill `target_usd` is `target_usd / shares_acquired = spent / Σ(usd_i / p_i)`. The two diverge when the book spans a wide price range; the current form is biased slightly **high** (so edges read slightly conservative — safe, but inaccurate). For a tool whose whole thesis is "executable price, precisely," worth getting exact.

**Fix:** Track `shares += take/price` and return `spent / shares` as `average_price`.

---

## 🟡 10. Host partial home advantage is never applied
**Files:** `src/features/elo.py:89`, `src/features/build_features.py`

Spec: *"For 2026, host nations (US/Canada/Mexico) get partial home advantage in their home matches."* The Elo expectation only switches full vs zero on the `neutral` flag. `home_is_host`/`away_is_host` exist as model features but the Elo expectation ignores them, so host advantage is all-or-nothing depending on how the fixture's `neutral` flag is set.

**Fix:** Add a `host_advantage` term (a fraction of `home_advantage`) applied when a host plays a "neutral" 2026 match on home soil.

---

## 🟡 11. `k_factor` uses fragile substring matching on concatenated tournament+stage
**File:** `src/features/elo.py:62`

`f"{tournament} {stage}".lower()` + `in` checks mis-bucket edge cases: a friendly **"Finalissima"** contains `"final"` → gets knockout K (50); anything containing `"round of"` etc. Low frequency but it silently mis-weights those matches.

**Fix:** Match on structured stage/tournament-type fields, or use word-boundary regexes and an explicit competition-tier map.

---

## 🟡 12. `run_live.main` and `validate` are scaffolds, but the README implies they're functional
**Files:** `src/pipeline/run_live.py:120`, `src/backtest/validate.py:17`

`main()` writes an **empty** report with `verdict="pipeline_scaffold"` — it does not ingest, build Elo, train, simulate, fetch Polymarket, or detect edges. `validate_calibration_inputs` only checks columns exist; it runs **no** walk-forward calibration backtest. The README "Typical Workflow" lists "Simulate advancement…", "Train and calibrate…" as if wired. Honest, but the gap isn't called out in "Current V1 Boundaries."

**Fix:** Either wire the end-to-end run or add explicit "NOT YET WIRED" notes to README §Typical Workflow and to "Current V1 Boundaries" so nobody trusts an empty report.

---

## 🟡 13. Goal model is full-history, un-shrunk, and not point-in-time
**File:** `src/model/goal_model.py:18`

`attack_strength`/`defense_weakness` are plain career means / global mean, fit on **all** matches with no time decay and no shrinkage. A team with one fluky 5-0 gets an extreme strength. Fine for a forward sim, but if `validate.py` ever backtests with a model fit on the full history, that's leakage; and the noise will widen scoreline tails.

**Fix:** Add empirical-Bayes shrinkage toward 1.0 by sample size and (optionally) exponential time decay. For backtests, fit point-in-time.

---

## 🟡 14. `tests/ingest/__init__.py` mutates `__path__` to alias `src/ingest` into the test package
**File:** `tests/ingest/__init__.py:3` (and siblings)

`__path__.append(.../src/ingest)` is a confusing shadowing hack; tests already import top-level `ingest.*` via `PYTHONPATH=src` / `pyproject` `pythonpath`. It's dead at best and a footgun at worst (two import paths to the same module).

**Fix:** Delete the `__path__` lines; rely on the configured `pythonpath`.

---

## 🟡 15. `merge_live_results` will crash on a duplicate `match_id`
**File:** `src/ingest/fixtures.py:73`

`by_id.loc[match_id]` returns a **DataFrame** if `completed_results` has duplicate `match_id`s, and `int(result["home_score"])` then raises `TypeError`. A pasted results file with an accidental dup row takes down the merge with an opaque error.

**Fix:** De-dup (`drop_duplicates("match_id", keep="last")`) or validate uniqueness up front with a clear message.

---

## Spec-compliance scorecard

| Spec area | State |
|---|---|
| Ingestion (results, rankings, fixtures, Polymarket client) | ✅ implemented, point-in-time joins look correct |
| Team normalization | ✅ |
| Elo engine | 🟠 works for historical exact dates; stale for forward dates (#4), slow (#7), no host partial adv (#10) |
| Feature build / leakage discipline | ✅ form/H2H/rest computed-before-record is correct |
| XGBoost training | 🟠 trains, but single-fold (#6), aggressive dropna (#8), no early stopping |
| **Calibration** | 🔴 **not implemented** (#1) |
| Goal model | 🟡 simplistic but functional (#13) |
| **Monte Carlo tournament sim** | 🔴 **engine missing**, only primitives (#2) |
| 2026 qualification rules | 🟠 count correct; **tiebreakers wrong** (#3) |
| Annex C / bracket gating | ✅ correctly blocks without official table |
| Edge detection (executable price) | ✅ ask-depth, fees; minor pricing nuance (#9) |
| Kelly + caps | ✅ formula and caps correct |
| Consistency scanner | 🟠 no vig removal, sum-to-1-only (#5) |
| Backtest/validation | 🔴/🟡 scaffold only (#12) |
| Live pipeline | 🟡 scaffold only (#12) |
| Reporting + honesty constraints | ✅ calibration-before-slate, manual-only, variance caveat all present |

## Suggested priority order for Codex
1. Build the Monte Carlo driver (#2) — nothing downstream has inputs without it.
2. Add real calibration (#1).
3. Fix the FIFA tiebreakers (#3) and the Elo forward-date staleness (#4) — both silently corrupt probabilities.
4. Then the medium perf/training items (#6, #7, #8) and the scanner (#5).
5. Cleanups (#9–#15).

*What's genuinely good and worth keeping:* the leakage-aware feature construction, the executable-price/Kelly/caps math, the Annex-C gating, and the honesty-first reporting. The bones are right — the missing organs are calibration and the simulation engine.
