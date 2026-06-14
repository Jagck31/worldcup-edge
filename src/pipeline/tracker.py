"""Live 2026 World Cup prediction tracker.

Builds the group-stage fixtures from the official schedule (real dates + kickoff times,
host nations placed at home), overlays real results as they arrive, predicts every match
with the calibrated model + goal model, and scores predictions vs actual outcomes. Matches
after the data cutoff (2026-06-12) are genuine out-of-sample forward tests.
"""
from __future__ import annotations

from math import log
from pathlib import Path

import pandas as pd

from ingest.results import TeamNameNormalizer
from model.predict import CalibratedPredictor

DATA_CUTOFF = pd.Timestamp("2026-06-12")  # martj42 dataset ends here; later matches are out-of-sample
PLACEHOLDER_DATE = pd.Timestamp("2026-07-01")  # undated scheduled matches sort last, use current strength
ROUND_ROBIN = [(0, 1), (2, 3), (0, 2), (1, 3), (0, 3), (1, 2)]
# Host nations play their group games at home — they get home (not neutral) treatment.
HOST_NATIONS = {"Mexico", "United States", "Canada"}
BASE_COLS = ["date", "home_team", "away_team", "neutral", "tournament", "stage", "home_score", "away_score"]


def load_groups(path: Path) -> dict[str, list[str]]:
    frame = pd.read_csv(path)
    groups: dict[str, list[str]] = {}
    for group, sub in frame.groupby("group"):
        groups[str(group)] = list(sub.sort_values("seed")["team"].astype(str))
    return groups


def _empty_fixture(group: str, home: str, away: str) -> dict:
    return {
        "group": group,
        "home_team": home,
        "away_team": away,
        "tournament": "FIFA World Cup",
        "stage": "Group stage",
        "neutral": home not in HOST_NATIONS,
        "date": PLACEHOLDER_DATE,
        "time": "",
        "datetime": PLACEHOLDER_DATE,
        "status": "scheduled",
        "home_score": pd.NA,
        "away_score": pd.NA,
    }


def load_schedule(path: Path, groups: dict[str, list[str]], normalizer: TeamNameNormalizer) -> pd.DataFrame:
    """Real fixtures from the official schedule: normalized names, hosts placed at home,
    kickoff datetime for chronological ordering."""
    frame = pd.read_csv(path)
    team_group = {normalizer.canonical(t): g for g, teams in groups.items() for t in teams}
    rows = []
    for _, row in frame.iterrows():
        home = normalizer.canonical(str(row["home_team"]))
        away = normalizer.canonical(str(row["away_team"]))
        # Hosts play every group game at home — orient them as the home team.
        if away in HOST_NATIONS and home not in HOST_NATIONS:
            home, away = away, home
        group = team_group.get(home) or team_group.get(away) or "?"
        when = pd.to_datetime(f"{row['date']} {row['time']}", errors="coerce")
        record = _empty_fixture(group, home, away)
        record["date"] = pd.Timestamp(row["date"])
        record["time"] = str(row["time"])
        record["datetime"] = when if pd.notna(when) else pd.Timestamp(row["date"])
        rows.append(record)
    return pd.DataFrame(rows)


def build_group_fixtures(groups: dict[str, list[str]]) -> pd.DataFrame:
    """Fallback: synthesize round-robin fixtures when no official schedule is loaded."""
    rows = []
    for group, teams in groups.items():
        for a, b in ROUND_ROBIN:
            rows.append(_empty_fixture(group, teams[a], teams[b]))
    return pd.DataFrame(rows)


def overlay_results(
    fixtures: pd.DataFrame, results: pd.DataFrame | None, normalizer: TeamNameNormalizer
) -> pd.DataFrame:
    """Normalize names and fill completed scores by unordered team pair (preserving datetime)."""
    fixtures = fixtures.copy()
    fixtures["home_team"] = fixtures["home_team"].map(normalizer.canonical)
    fixtures["away_team"] = fixtures["away_team"].map(normalizer.canonical)

    if results is not None and not results.empty:
        results = results.copy()
        results["home_team"] = results["home_team"].map(normalizer.canonical)
        results["away_team"] = results["away_team"].map(normalizer.canonical)
        results["date"] = pd.to_datetime(results["date"])
        # Pair -> all results for that pairing. A group + knockout rematch shares a pair, so we
        # pick the result CLOSEST in date to the fixture rather than last-write-wins.
        pair_to_results: dict = {}
        for _, r in results.iterrows():
            pair_to_results.setdefault(frozenset((r["home_team"], r["away_team"])), []).append(r)
        for i, fx in fixtures.iterrows():
            cands = pair_to_results.get(frozenset((fx["home_team"], fx["away_team"])))
            if not cands:
                continue
            fx_date = pd.to_datetime(fx.get("date"), errors="coerce")
            if len(cands) > 1 and pd.notna(fx_date):
                match = min(cands, key=lambda r: abs((r["date"] - fx_date).days))
            else:
                match = cands[0]
            if match["home_team"] == fx["home_team"]:
                home_score, away_score = int(match["home_score"]), int(match["away_score"])
            else:
                home_score, away_score = int(match["away_score"]), int(match["home_score"])
            fixtures.at[i, "home_score"] = home_score
            fixtures.at[i, "away_score"] = away_score
            fixtures.at[i, "status"] = "completed"
    if "neutral" not in fixtures.columns:
        fixtures["neutral"] = ~fixtures["home_team"].isin(HOST_NATIONS)
    fixtures["date"] = fixtures["date"].fillna(PLACEHOLDER_DATE)
    if "datetime" not in fixtures.columns:
        fixtures["datetime"] = fixtures["date"]
    return fixtures


def build_sim_groups(fixtures: pd.DataFrame) -> dict[str, dict]:
    """Shape normalized fixtures into the {group: {teams, fixtures}} form the simulator wants,
    with completed group matches locked in."""
    groups: dict[str, dict] = {}
    for group, sub in fixtures.groupby("group"):
        if str(group) == "?":
            continue
        teams = sorted(set(sub["home_team"]) | set(sub["away_team"]))
        groups[str(group)] = {"teams": teams, "fixtures": sub[["home_team", "away_team", "home_score", "away_score"]].copy()}
    return groups


def _row_logloss(probs: dict[str, float], actual: str) -> float:
    return float(-log(max(probs.get(actual, 1e-12), 1e-12)))


def _row_brier(probs: dict[str, float], actual: str) -> float:
    return float(sum((probs.get(k, 0.0) - (1.0 if k == actual else 0.0)) ** 2 for k in ("H", "D", "A")))


def predict_fixtures(
    fixtures: pd.DataFrame,
    feature_rows: pd.DataFrame,
    predictor: CalibratedPredictor,
    goal_model: object,
    feature_columns: list[str],
) -> list[dict]:
    index = {
        (pd.Timestamp(row["date"]), str(row["home_team"]), str(row["away_team"])): row
        for _, row in feature_rows.iterrows()
    }
    preds: list[dict] = []
    ordered = fixtures.sort_values("datetime") if "datetime" in fixtures.columns else fixtures
    for _, fx in ordered.iterrows():
        key = (pd.Timestamp(fx["date"]), str(fx["home_team"]), str(fx["away_team"]))
        frow = index.get(key)
        if frow is None:
            continue
        probs = predictor.predict_row(frow[feature_columns])
        pdict = probs.as_dict()
        home_xg, away_xg = goal_model.expected_goals(str(fx["home_team"]), str(fx["away_team"]))
        score_dist: list[dict] = []
        try:
            dist = goal_model.scoreline_distribution(str(fx["home_team"]), str(fx["away_team"]))
            top = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:8]
            score_dist = [{"score": f"{h}-{a}", "p": round(p, 3)} for (h, a), p in top]
            likely_score = score_dist[0]["score"] if score_dist else "—"
        except Exception:
            likely_score = "—"
        when = pd.Timestamp(fx["datetime"]) if "datetime" in fx and pd.notna(fx["datetime"]) else pd.Timestamp(fx["date"])
        rec = {
            "group": fx["group"],
            "date": str(pd.Timestamp(fx["date"]).date()),
            "time": str(fx.get("time", "")),
            "kickoff": when.strftime("%b %d %H:%M") if str(fx.get("time", "")) else when.strftime("%b %d"),
            "home": str(fx["home_team"]),
            "away": str(fx["away_team"]),
            "p_home": round(pdict["H"], 3),
            "p_draw": round(pdict["D"], 3),
            "p_away": round(pdict["A"], 3),
            "pick": max(pdict, key=pdict.get),
            "pick_prob": round(max(pdict.values()), 3),
            "exp_home_goals": round(home_xg, 2),
            "exp_away_goals": round(away_xg, 2),
            "likely_score": likely_score,
            "status": str(fx["status"]),
            # Compare on DATE (not the kickoff datetime), else any non-midnight kickoff on the
            # cutoff day is wrongly flagged out-of-sample even though it was in the training base.
            "out_of_sample": pd.Timestamp(fx["date"]).normalize() > DATA_CUTOFF,
        }
        if str(fx["status"]) == "completed":
            home_score, away_score = int(fx["home_score"]), int(fx["away_score"])
            actual = "H" if home_score > away_score else "D" if home_score == away_score else "A"
            rec.update(
                {
                    "actual": actual,
                    "score": f"{home_score}-{away_score}",
                    "correct": rec["pick"] == actual,
                    "logloss": round(_row_logloss(pdict, actual), 3),
                    "brier": round(_row_brier(pdict, actual), 3),
                }
            )
        preds.append(rec)
    return preds


def score_tracker(predictions: list[dict]) -> dict:
    completed = [p for p in predictions if p.get("status") == "completed"]
    oos = [p for p in completed if p.get("out_of_sample")]

    def summarize(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0}
        return {
            "n": len(rows),
            "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 3),
            "log_loss": round(sum(r["logloss"] for r in rows) / len(rows), 3),
            "brier": round(sum(r["brier"] for r in rows) / len(rows), 3),
            "avg_fav_prob": round(sum(r["pick_prob"] for r in rows) / len(rows), 3),
        }

    return {
        "completed": summarize(completed),
        "out_of_sample": summarize(oos),
        "n_scheduled": sum(1 for p in predictions if p.get("status") != "completed"),
        "uniform_log_loss_baseline": round(float(log(3)), 3),
    }
