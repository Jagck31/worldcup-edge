from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Iterable

import numpy as np
import pandas as pd

from features.elo import EloHistory
from ingest.rankings import add_point_in_time_rankings


HOST_NATIONS_2026 = {"United States", "USA", "Canada", "Mexico"}


def _elo_expectation(rating_for: float, rating_against: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_against - rating_for) / 400.0))


def build_match_features(
    matches: pd.DataFrame,
    elo_history: EloHistory,
    rankings: pd.DataFrame | None = None,
    form_windows: tuple[int, ...] = (5, 10),
    host_nations: Iterable[str] = HOST_NATIONS_2026,
    home_advantage: float = 75.0,
    strength_window: int = 10,
) -> pd.DataFrame:
    frame = matches.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").reset_index(drop=True)
    host_set = set(host_nations)
    rows: list[dict] = []
    team_history: dict[str, list[dict]] = defaultdict(list)
    h2h_history: dict[tuple[str, str], list[int]] = defaultdict(list)

    # Running, point-in-time global goal rates for opponent-adjusted strengths.
    home_goal_sum = 0.0
    away_goal_sum = 0.0
    match_count = 0

    for _, match in frame.iterrows():
        date = pd.Timestamp(match["date"])
        home = str(match["home_team"])
        away = str(match["away_team"])
        neutral = bool(match.get("neutral", False))
        home_elo = elo_history.pre_match_rating(home, date)
        away_elo = elo_history.pre_match_rating(away, date)
        home_adj = 0.0 if neutral else home_advantage
        expected_home = _elo_expectation(home_elo + home_adj, away_elo)

        global_home = home_goal_sum / match_count if match_count else 1.35
        global_away = away_goal_sum / match_count if match_count else 1.10
        global_for = (home_goal_sum + away_goal_sum) / (2 * match_count) if match_count else 1.20
        home_attack, home_defense = _strength(team_history[home], strength_window, global_for)
        away_attack, away_defense = _strength(team_history[away], strength_window, global_for)
        expected_home_goals = global_home * home_attack * away_defense
        expected_away_goals = global_away * away_attack * home_defense

        row = {
            "date": date,
            "home_team": home,
            "away_team": away,
            "home_elo": home_elo,
            "away_elo": away_elo,
            "elo_diff": home_elo - away_elo,
            "elo_expected_home": expected_home,
            "abs_elo_diff": abs(home_elo - away_elo),
            "neutral": neutral,
            "home_is_host": home in host_set,
            "away_is_host": away in host_set,
            "tournament": match.get("tournament", ""),
            "stage": match.get("stage", ""),
            "competitive": _is_competitive(str(match.get("tournament", ""))),
            "home_days_since_last": _days_since_last(team_history[home], date),
            "away_days_since_last": _days_since_last(team_history[away], date),
            "matches_last_30d_home": _matches_in_window(team_history[home], date, 30),
            "matches_last_30d_away": _matches_in_window(team_history[away], date, 30),
            # Rating reliability: a team with few matches in the last year has a stale/uncertain rating.
            "matches_last_365d_home": _matches_in_window(team_history[home], date, 365),
            "matches_last_365d_away": _matches_in_window(team_history[away], date, 365),
            "recent_h2h_goal_diff": _recent_h2h_diff(h2h_history, home, away),
            "home_attack": home_attack,
            "away_attack": away_attack,
            "home_defense": home_defense,
            "away_defense": away_defense,
            "expected_home_goals": expected_home_goals,
            "expected_away_goals": expected_away_goals,
            "expected_total_goals": expected_home_goals + expected_away_goals,
            "expected_goal_diff": expected_home_goals - expected_away_goals,
        }
        for window in form_windows:
            row.update(_form_features(team_history[home], "home", window))
            row.update(_form_features(team_history[away], "away", window))
            row.update(_momentum_features(team_history[home], "home", window))
            row.update(_momentum_features(team_history[away], "away", window))

        if not pd.isna(match.get("home_score")) and not pd.isna(match.get("away_score")):
            home_score = int(match["home_score"])
            away_score = int(match["away_score"])
            row["target_1x2"] = "H" if home_score > away_score else "D" if home_score == away_score else "A"
            row["target_total_goals"] = home_score + away_score
            row["target_btts"] = int(home_score > 0 and away_score > 0)
            result_home = 1.0 if home_score > away_score else 0.5 if home_score == away_score else 0.0
            resid_home = result_home - expected_home
            _record_match(team_history, date, home, away, home_score, away_score, away_elo, home_elo, resid_home)
            _record_h2h(h2h_history, home, away, home_score - away_score)
            home_goal_sum += home_score
            away_goal_sum += away_score
            match_count += 1
        rows.append(row)

    features = pd.DataFrame(rows)
    if rankings is not None:
        features = add_point_in_time_rankings(features, rankings)
    return features


def _is_competitive(tournament: str) -> bool:
    return "friendly" not in tournament.lower()


def _days_since_last(history: list[dict], date: pd.Timestamp) -> float:
    if not history:
        return np.nan
    return float((date - pd.Timestamp(history[-1]["date"])).days)


def _matches_in_window(history: list[dict], date: pd.Timestamp, days: int) -> int:
    cutoff = date - pd.Timedelta(days=days)
    return sum(1 for item in history if pd.Timestamp(item["date"]) >= cutoff)


def _strength(history: list[dict], window: int, global_for: float) -> tuple[float, float]:
    """Point-in-time attack strength and defensive weakness relative to the global rate.

    >1 attack = scores more than average; >1 defense = concedes more than average.
    Falls back to league-average (1.0) before a team has any history.
    """
    recent = history[-window:]
    if not recent or global_for <= 0:
        return 1.0, 1.0
    attack = mean(item["goals_for"] for item in recent) / global_for
    defense = mean(item["goals_against"] for item in recent) / global_for
    return max(attack, 0.05), max(defense, 0.05)


def _form_features(history: list[dict], prefix: str, window: int) -> dict[str, float]:
    recent = history[-window:]
    if not recent:
        return {
            f"{prefix}_goals_for_last{window}": 0.0,
            f"{prefix}_goals_against_last{window}": 0.0,
            f"{prefix}_points_per_game_last{window}": 0.0,
            f"{prefix}_win_rate_last{window}": 0.0,
            f"{prefix}_draw_rate_last{window}": 0.0,
            f"{prefix}_loss_rate_last{window}": 0.0,
        }
    return {
        f"{prefix}_goals_for_last{window}": float(mean(item["goals_for"] for item in recent)),
        f"{prefix}_goals_against_last{window}": float(mean(item["goals_against"] for item in recent)),
        f"{prefix}_points_per_game_last{window}": float(mean(item["points"] for item in recent)),
        f"{prefix}_win_rate_last{window}": float(mean(item["result"] == "W" for item in recent)),
        f"{prefix}_draw_rate_last{window}": float(mean(item["result"] == "D" for item in recent)),
        f"{prefix}_loss_rate_last{window}": float(mean(item["result"] == "L" for item in recent)),
    }


def _momentum_features(history: list[dict], prefix: str, window: int) -> dict[str, float]:
    """Opponent-adjusted form: how the team has done relative to its Elo expectation,
    plus the strength of schedule it faced. NaN with no history so trees can branch on it."""
    recent = history[-window:]
    if not recent:
        return {
            f"{prefix}_elo_resid_last{window}": np.nan,
            f"{prefix}_avg_opp_elo_last{window}": np.nan,
        }
    return {
        f"{prefix}_elo_resid_last{window}": float(mean(item["result_resid"] for item in recent)),
        f"{prefix}_avg_opp_elo_last{window}": float(mean(item["opp_elo"] for item in recent)),
    }


def _record_match(
    team_history: dict[str, list[dict]],
    date: pd.Timestamp,
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    home_opp_elo: float,
    away_opp_elo: float,
    resid_home: float,
) -> None:
    home_points = 3 if home_score > away_score else 1 if home_score == away_score else 0
    away_points = 3 if away_score > home_score else 1 if home_score == away_score else 0
    team_history[home].append(
        {
            "date": date,
            "opponent": away,
            "goals_for": home_score,
            "goals_against": away_score,
            "points": home_points,
            "result": "W" if home_points == 3 else "D" if home_points == 1 else "L",
            "opp_elo": home_opp_elo,
            "result_resid": resid_home,
        }
    )
    team_history[away].append(
        {
            "date": date,
            "opponent": home,
            "goals_for": away_score,
            "goals_against": home_score,
            "points": away_points,
            "result": "W" if away_points == 3 else "D" if away_points == 1 else "L",
            "opp_elo": away_opp_elo,
            "result_resid": -resid_home,
        }
    )


def _record_h2h(history: dict[tuple[str, str], list[int]], home: str, away: str, diff: int) -> None:
    key = tuple(sorted((home, away)))
    history[key].append(diff if key[0] == home else -diff)


def _recent_h2h_diff(history: dict[tuple[str, str], list[int]], home: str, away: str) -> float:
    key = tuple(sorted((home, away)))
    recent = history.get(key, [])[-5:]
    if not recent:
        return 0.0
    diff = float(mean(recent))
    return diff if key[0] == home else -diff
