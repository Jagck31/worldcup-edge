"""Live-tracker engine: re-score frozen predictions vs results and refresh forward picks.

Split from the full pipeline so it can run in a tight poll loop. `build_context` trains the
calibrated model + goal model once (the slow part). `recompute` then, per cycle, rebuilds
Elo from the latest results, re-scores every completed game against its FROZEN pre-match
prediction, and refreshes predictions only for still-scheduled fixtures with the updated Elo.

Freeze rule (why it matters): once a match kicks off, its prediction is locked. Re-predicting
a finished game with an Elo that already absorbed that result is look-ahead leakage and would
inflate accuracy. Completed and in-play games keep their pre-match probabilities; only
not-yet-started games move.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import log
from pathlib import Path

import pandas as pd

from features.build_features import HOST_NATIONS_2026, build_match_features
from features.elo import EloConfig, EloEngine
from ingest.livescores import LiveEvent, _to_int
from ingest.results import TeamNameNormalizer, load_results
from model.goal_model import PoissonGoalModel
from model.predict import CalibratedPredictor
from model.train import train_1x2
from pipeline.orchestrator import (
    ALIASES_PATH,
    GROUPS_PATH,
    RAW_RESULTS_PATH,
    SCHEDULE_PATH,
    WC_RESULTS_PATH,
    _live_wc_rows,
    _load_fifa_rankings,
    _select_feature_columns,
    _to_model_matrix,
)
from pipeline.tracker import (
    _row_brier,
    _row_logloss,
    build_group_fixtures,
    load_groups,
    load_schedule,
    overlay_results,
    predict_fixtures,
    score_tracker,
)

FEATURE_INPUT_COLS = ["date", "home_team", "away_team", "neutral", "tournament", "home_score", "away_score"]
RESULTS_COLUMNS = ["date", "home_team", "away_team", "home_score", "away_score", "home_xg", "away_xg"]
_FROZEN_FIELDS = ("p_home", "p_draw", "p_away", "pick", "pick_prob", "exp_home_goals", "exp_away_goals", "likely_score")


@dataclass
class TrackerContext:
    """Trained artifacts + reference frames reused across every poll cycle."""

    normalizer: TeamNameNormalizer
    predictor: CalibratedPredictor
    goal_model: PoissonGoalModel
    feature_columns: list[str]
    rankings: pd.DataFrame | None
    results_full: pd.DataFrame
    recent: pd.DataFrame
    base_fixtures: pd.DataFrame
    groups_raw: dict[str, list[str]]
    display_groups: dict[str, list[str]]


def build_context(config: dict, history_years: int = 12) -> TrackerContext:
    """Run the heavy stages once: results -> Elo -> features -> calibrated model + goal model."""
    normalizer = TeamNameNormalizer.from_yaml(ALIASES_PATH)
    results_full = load_results(RAW_RESULTS_PATH, mapping_path=ALIASES_PATH, refresh=False)
    results_full = results_full.dropna(subset=["home_score", "away_score"]).reset_index(drop=True)
    results_full["home_score"] = results_full["home_score"].astype(int)
    results_full["away_score"] = results_full["away_score"].astype(int)

    cutoff = results_full["date"].max() - pd.DateOffset(years=history_years)
    recent = results_full[results_full["date"] >= cutoff].reset_index(drop=True)

    rankings = _load_fifa_rankings(normalizer, refresh=False)
    elo_base = EloEngine(EloConfig()).process_matches(results_full, host_nations=HOST_NATIONS_2026)
    features = build_match_features(recent, elo_base, rankings=rankings, host_nations=HOST_NATIONS_2026)
    feature_columns = _select_feature_columns(features)
    labelled = features.dropna(subset=["target_1x2"]).copy()
    matrix = _to_model_matrix(labelled, feature_columns)
    matrix["date"] = labelled["date"].to_numpy()
    matrix["target_1x2"] = labelled["target_1x2"].to_numpy()
    train_result = train_1x2(matrix, feature_columns, model_type=str(config.get("model_type", "gbt")), refit_full=True)
    predictor = CalibratedPredictor.from_train_result(train_result)
    goal_model = PoissonGoalModel().fit(recent.tail(8000))

    groups_raw = load_groups(GROUPS_PATH)
    display_groups = {g: [normalizer.canonical(t) for t in teams] for g, teams in groups_raw.items()}
    if SCHEDULE_PATH.exists():
        base_fixtures = load_schedule(SCHEDULE_PATH, groups_raw, normalizer)
    else:
        base_fixtures = build_group_fixtures(groups_raw)

    return TrackerContext(
        normalizer=normalizer,
        predictor=predictor,
        goal_model=goal_model,
        feature_columns=feature_columns,
        rankings=rankings,
        results_full=results_full,
        recent=recent,
        base_fixtures=base_fixtures,
        groups_raw=groups_raw,
        display_groups=display_groups,
    )


def frozen_from_predictions(predictions: list[dict]) -> dict[frozenset, dict]:
    """Index stored predictions by unordered team pair so they can be locked next cycle."""
    frozen: dict[frozenset, dict] = {}
    for row in predictions or []:
        home, away = row.get("home"), row.get("away")
        if home and away:
            frozen[frozenset((str(home), str(away)))] = {f: row.get(f) for f in _FROZEN_FIELDS}
    return frozen


def _apply_freeze(
    predictions: list[dict], frozen: dict[frozenset, dict], in_play_pairs: set[frozenset]
) -> list[dict]:
    """Lock completed/in-play games to their pre-match probabilities; rescore from those."""
    for row in predictions:
        pair = frozenset((str(row["home"]), str(row["away"])))
        is_completed = row.get("status") == "completed"
        if not (is_completed or pair in in_play_pairs):
            continue  # still scheduled -> keep the freshly refreshed (forward) prediction
        locked = frozen.get(pair)
        if locked and locked.get("p_home") is not None:
            for field in _FROZEN_FIELDS:
                if locked.get(field) is not None:
                    row[field] = locked[field]
            row["frozen"] = True
        else:
            row["frozen"] = False  # no pre-match snapshot existed; fresh pred used (mild leakage)
        if is_completed and "actual" in row:
            probs = {"H": row["p_home"], "D": row["p_draw"], "A": row["p_away"]}
            row["pick"] = max(probs, key=probs.get)
            row["pick_prob"] = round(max(probs.values()), 3)
            row["correct"] = row["pick"] == row["actual"]
            row["logloss"] = round(_row_logloss(probs, row["actual"]), 3)
            row["brier"] = round(_row_brier(probs, row["actual"]), 3)
    return predictions


def recompute(
    context: TrackerContext,
    frozen: dict[frozenset, dict],
    in_play_pairs: set[frozenset] | None = None,
    refresh_forward: bool = True,
) -> dict:
    """Rebuild Elo from current results, score frozen predictions, refresh scheduled ones.

    refresh_forward=False locks every previously-seen fixture to its stored prediction
    (re-score only); just-discovered fixtures still get a fresh pick."""
    in_play_pairs = set(in_play_pairs or set())
    if not refresh_forward:
        in_play_pairs |= set(frozen.keys())
    wc_results = pd.read_csv(WC_RESULTS_PATH) if WC_RESULTS_PATH.exists() else None

    wc_live = _live_wc_rows(WC_RESULTS_PATH, context.normalizer, context.results_full["date"].max(), existing=context.results_full)
    live_n = 0 if wc_live is None else len(wc_live)
    if live_n:
        elo_input = pd.concat([context.results_full, wc_live], ignore_index=True, sort=False).sort_values("date")
    else:
        elo_input = context.results_full
    elo = EloEngine(EloConfig()).process_matches(elo_input, host_nations=HOST_NATIONS_2026)

    fixtures = overlay_results(context.base_fixtures, wc_results, context.normalizer)
    recent_min = context.recent[FEATURE_INPUT_COLS].copy()
    recent_min["stage"] = ""
    combined = pd.concat([recent_min, fixtures[FEATURE_INPUT_COLS + ["stage"]]], ignore_index=True, sort=False)
    fix_features = build_match_features(combined, elo, rankings=context.rankings, host_nations=HOST_NATIONS_2026)

    predictions = predict_fixtures(fixtures, fix_features, context.predictor, context.goal_model, context.feature_columns)
    predictions = _apply_freeze(predictions, frozen, in_play_pairs)
    scorecard = score_tracker(predictions)
    leaderboard = sorted(
        ({"team": team, "rating": round(rating, 1)} for team, rating in elo.final_ratings.items()),
        key=lambda r: r["rating"],
        reverse=True,
    )
    return {
        "predictions": predictions,
        "scorecard": scorecard,
        "elo_leaderboard": leaderboard,
        "live_results_fed": live_n,
        "elo": elo,  # the EloEngine result, so callers can re-simulate without rebuilding it
    }


def merge_finished_into_csv(
    events: list[LiveEvent], csv_path: Path, normalizer: TeamNameNormalizer, fill_only: bool = False
) -> dict:
    """Insert newly-finished feed results into the results CSV; preserve hand-set xG.

    Matching is by unordered team pair WITHIN ~5 days, so a group-stage game and a later
    knockout rematch of the same two teams stay distinct rows (a pair-only key would collapse
    them, silently losing one real result from the Elo feed). Existing rows keep their xG unless
    the feed reports a different score, in which case the score is updated and the stale xG
    dropped. Returns the new/changed deltas for alerting.

    ``fill_only=True`` is gap-fill mode for the manual seed: only games the CSV doesn't already
    have are appended; an existing (feed-sourced) result is never overwritten. That keeps the
    real feed authoritative and stops a stale hand-entered score from flip-flopping with the feed.
    """
    finished = [e for e in events if e.finished]
    if csv_path.exists():
        existing = pd.read_csv(csv_path)
    else:
        existing = pd.DataFrame(columns=RESULTS_COLUMNS)
    for col in RESULTS_COLUMNS:
        if col not in existing.columns:
            existing[col] = pd.NA

    index: dict[frozenset, list] = {}  # pair -> [(row_pos, date)]
    for i, row in existing.iterrows():
        key = frozenset((normalizer.canonical(str(row["home_team"])), normalizer.canonical(str(row["away_team"]))))
        index.setdefault(key, []).append((i, pd.to_datetime(row["date"], errors="coerce")))

    def _match(event) -> int | None:
        ev_date = pd.to_datetime(event.date, errors="coerce")
        for pos, d in index.get(event.pair, []):
            if pd.isna(d) or pd.isna(ev_date) or abs((d - ev_date).days) <= 5:
                return pos
        return None

    new: list[LiveEvent] = []
    changed: list[LiveEvent] = []
    for event in finished:
        pos = _match(event)
        if pos is None:
            existing.loc[len(existing)] = {
                "date": event.date,
                "home_team": event.home,
                "away_team": event.away,
                "home_score": event.home_score,
                "away_score": event.away_score,
                "home_xg": pd.NA,
                "away_xg": pd.NA,
            }
            new.append(event)
            index.setdefault(event.pair, []).append((len(existing) - 1, pd.to_datetime(event.date, errors="coerce")))
            continue
        if fill_only:
            continue  # game already present (feed wins) -> never overwrite with the manual seed
        row = existing.loc[pos]
        same_orient = normalizer.canonical(str(row["home_team"])) == event.home
        cur_home, cur_away = (row["home_score"], row["away_score"]) if same_orient else (row["away_score"], row["home_score"])
        if pd.isna(cur_home) or pd.isna(cur_away) or int(cur_home) != event.home_score or int(cur_away) != event.away_score:
            if same_orient:
                existing.at[pos, "home_score"], existing.at[pos, "away_score"] = event.home_score, event.away_score
            else:
                existing.at[pos, "home_score"], existing.at[pos, "away_score"] = event.away_score, event.home_score
            existing.at[pos, "home_xg"] = pd.NA  # score changed -> stale xG can't be trusted
            existing.at[pos, "away_xg"] = pd.NA
            changed.append(event)

    if new or changed:
        existing = existing.sort_values("date").reset_index(drop=True)
        existing.to_csv(csv_path, index=False)
    return {"new": new, "changed": changed, "total_finished": len(finished)}


def load_manual_seed_events(path: Path, normalizer: TeamNameNormalizer) -> list[LiveEvent]:
    """Read hand-entered results from a git-tracked CSV into finished LiveEvents.

    The free TheSportsDB feed is incomplete (it omits some 2026 games entirely -- e.g.
    Australia-Turkiye, Netherlands-Japan, Sweden-Tunisia were never published). This seed is
    the durable fallback: results committed here deploy to the box like any code and get merged
    (gap-fill only) so the tracker/Elo see games the feed never carried. Rows without a valid
    integer score for both teams are skipped, so a header-only or half-filled file is harmless.

    CSV columns: ``date,home_team,away_team,home_score,away_score`` (extra columns ignored).
    """
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return []
    required = {"date", "home_team", "away_team", "home_score", "away_score"}
    if not required.issubset(frame.columns):
        return []
    events: list[LiveEvent] = []
    for _, row in frame.iterrows():
        home_score, away_score = _to_int(row.get("home_score")), _to_int(row.get("away_score"))
        if home_score is None or away_score is None:
            continue  # incomplete row -> not a finished result, skip
        home = normalizer.canonical(str(row.get("home_team", "")).strip())
        away = normalizer.canonical(str(row.get("away_team", "")).strip())
        date = str(row.get("date", "")).strip()
        if not home or not away or not date:
            continue
        events.append(
            LiveEvent(
                event_id=f"manual:{date}:{home}:{away}",
                date=date,
                kickoff="",
                home=home,
                away=away,
                home_score=home_score,
                away_score=away_score,
                status_raw="FT (manual)",
                state="finished",
            )
        )
    return events


def uniform_baseline() -> float:
    return float(log(3))
