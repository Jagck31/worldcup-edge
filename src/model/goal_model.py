from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import exp, factorial, log

import pandas as pd


@dataclass
class PoissonGoalModel:
    max_goals: int = 10
    dixon_coles_rho: float = 0.0
    shrinkage_matches: float = 6.0
    half_life_days: float | None = None
    global_home_goals: float = 1.35
    global_away_goals: float = 1.10
    # Opponent-adjusted attack/defense (iterative Poisson MLE) instead of raw goals ÷ global
    # average. International schedules are unbalanced (minnows pad goals vs minnows; top teams
    # play each other), so the raw method compresses the ratings ("flat xG"). The iterative
    # version credits goals by the strength of the defence faced — it actually separates teams.
    opponent_adjusted: bool = True
    fit_iterations: int = 12
    attack_strength: dict[str, float] = field(default_factory=dict)
    defense_weakness: dict[str, float] = field(default_factory=dict)

    def fit(self, matches: pd.DataFrame, as_of_date: pd.Timestamp | None = None) -> "PoissonGoalModel":
        frame = matches.dropna(subset=["home_score", "away_score"]).copy()
        if frame.empty:
            raise ValueError("Cannot fit goal model with no completed matches")
        frame["_weight"] = _recency_weights(frame, self.half_life_days, as_of_date)
        self.global_home_goals = max(_weighted_mean(frame["home_score"], frame["_weight"]), 0.05)
        self.global_away_goals = max(_weighted_mean(frame["away_score"], frame["_weight"]), 0.05)
        total_goals = float((frame["home_score"] * frame["_weight"]).sum() + (frame["away_score"] * frame["_weight"]).sum())
        total_weight = float(frame["_weight"].sum() * 2.0)
        global_for = max(total_goals / total_weight, 0.05)

        rows = [
            (str(r["home_team"]), str(r["away_team"]), float(r["home_score"]), float(r["away_score"]), float(r["_weight"]))
            for _, r in frame.iterrows()
        ]
        if self.opponent_adjusted:
            self.attack_strength, self.defense_weakness = self._fit_opponent_adjusted(rows)
        else:
            self.attack_strength, self.defense_weakness = self._fit_raw(rows, global_for)
        return self

    def _fit_raw(self, rows, global_for):
        """Original method: a team's strength is its raw goals-for/against ÷ global average."""
        records: dict[str, dict[str, float]] = {}
        for home, away, hs, as_, w in rows:
            records.setdefault(home, {"for": 0.0, "against": 0.0, "weight": 0.0})
            records.setdefault(away, {"for": 0.0, "against": 0.0, "weight": 0.0})
            records[home]["for"] += hs * w
            records[home]["against"] += as_ * w
            records[home]["weight"] += w
            records[away]["for"] += as_ * w
            records[away]["against"] += hs * w
            records[away]["weight"] += w
        attack = {t: max(_shrink_ratio(v["for"], v["weight"], global_for, self.shrinkage_matches), 0.1) for t, v in records.items()}
        defense = {t: max(_shrink_ratio(v["against"], v["weight"], global_for, self.shrinkage_matches), 0.1) for t, v in records.items()}
        return attack, defense

    def _fit_opponent_adjusted(self, rows):
        """Iterative Poisson MLE: alternately solve attack and defence so that expected goals
        for team i vs j = baseline · attack_i · defence_j, anchoring the scale by shrinking
        sparse teams toward 1.0. A few sweeps converge; this is what de-flattens the xG."""
        teams = {t for home, away, *_ in rows for t in (home, away)}
        team_weight: dict[str, float] = defaultdict(float)
        for home, away, _hs, _as, w in rows:
            team_weight[home] += w
            team_weight[away] += w
        attack = {t: 1.0 for t in teams}
        defense = {t: 1.0 for t in teams}
        s = max(self.shrinkage_matches, 0.0)
        for _ in range(self.fit_iterations):
            num_a, den_a = defaultdict(float), defaultdict(float)
            num_d, den_d = defaultdict(float), defaultdict(float)
            for home, away, hs, as_, w in rows:
                # attack: goals scored vs the baseline implied by the opponent's defence
                num_a[home] += hs * w
                den_a[home] += self.global_home_goals * defense[away] * w
                num_a[away] += as_ * w
                den_a[away] += self.global_away_goals * defense[home] * w
                # defence (weakness): goals conceded vs the baseline implied by the opponent's attack
                num_d[home] += as_ * w
                den_d[home] += self.global_away_goals * attack[away] * w
                num_d[away] += hs * w
                den_d[away] += self.global_home_goals * attack[home] * w
            new_attack, new_defense = {}, {}
            for t in teams:
                eff = team_weight[t]
                ra = num_a[t] / den_a[t] if den_a[t] > 0 else 1.0
                rd = num_d[t] / den_d[t] if den_d[t] > 0 else 1.0
                new_attack[t] = max((ra * eff + s) / (eff + s), 0.1)   # shrink toward 1.0
                new_defense[t] = max((rd * eff + s) / (eff + s), 0.1)
            attack, defense = new_attack, new_defense
        return attack, defense

    def expected_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        home_attack = self.attack_strength.get(home_team, 1.0)
        away_attack = self.attack_strength.get(away_team, 1.0)
        home_defense = self.defense_weakness.get(home_team, 1.0)
        away_defense = self.defense_weakness.get(away_team, 1.0)
        home_xg = max(self.global_home_goals * home_attack * away_defense, 0.05)
        away_xg = max(self.global_away_goals * away_attack * home_defense, 0.05)
        return home_xg, away_xg

    def scoreline_distribution(self, home_team: str, away_team: str) -> dict[tuple[int, int], float]:
        home_xg, away_xg = self.expected_goals(home_team, away_team)
        distribution: dict[tuple[int, int], float] = {}
        for home_goals in range(self.max_goals + 1):
            for away_goals in range(self.max_goals + 1):
                probability = _poisson_pmf(home_goals, home_xg) * _poisson_pmf(away_goals, away_xg)
                probability *= self._dixon_coles_adjustment(home_goals, away_goals, home_xg, away_xg)
                distribution[(home_goals, away_goals)] = max(probability, 0.0)
        total = sum(distribution.values())
        if total <= 0:
            raise ValueError("Scoreline distribution has zero probability mass")
        return {scoreline: value / total for scoreline, value in distribution.items()}

    def _dixon_coles_adjustment(
        self,
        home_goals: int,
        away_goals: int,
        home_xg: float,
        away_xg: float,
    ) -> float:
        rho = self.dixon_coles_rho
        if rho == 0:
            return 1.0
        if home_goals == 0 and away_goals == 0:
            return 1.0 - home_xg * away_xg * rho
        if home_goals == 0 and away_goals == 1:
            return 1.0 + home_xg * rho
        if home_goals == 1 and away_goals == 0:
            return 1.0 + away_xg * rho
        if home_goals == 1 and away_goals == 1:
            return 1.0 - rho
        return 1.0


def _poisson_pmf(k: int, lambda_: float) -> float:
    return exp(-lambda_) * lambda_**k / factorial(k)


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    total_weight = float(weights.sum())
    if total_weight <= 0:
        return float(values.mean())
    return float((values.astype(float) * weights.astype(float)).sum() / total_weight)


def _shrink_ratio(
    weighted_goals: float,
    effective_matches: float,
    global_goals_per_match: float,
    shrinkage_matches: float,
) -> float:
    if effective_matches <= 0:
        return 1.0
    raw_average = weighted_goals / effective_matches
    shrinkage = max(float(shrinkage_matches), 0.0)
    shrunk_average = (
        weighted_goals + shrinkage * global_goals_per_match
    ) / (effective_matches + shrinkage)
    return shrunk_average / global_goals_per_match


def _recency_weights(
    frame: pd.DataFrame,
    half_life_days: float | None,
    as_of_date: pd.Timestamp | None,
) -> pd.Series:
    if half_life_days is None or "date" not in frame.columns:
        return pd.Series(1.0, index=frame.index)
    half_life = float(half_life_days)
    if half_life <= 0:
        raise ValueError("half_life_days must be positive")
    dates = pd.to_datetime(frame["date"])
    reference = pd.Timestamp(as_of_date) if as_of_date is not None else dates.max()
    ages = (reference - dates).dt.days.clip(lower=0)
    return (-log(2.0) * ages / half_life).map(exp)
