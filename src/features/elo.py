from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from math import exp, log
import re
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class EloConfig:
    base_rating: float = 1500.0
    home_advantage: float = 75.0
    host_advantage: float = 35.0  # partial home edge for 2026 hosts on a "neutral" home match
    friendly_k: float = 10.0
    qualifier_k: float = 25.0
    tournament_k: float = 35.0
    knockout_k: float = 50.0
    # 538/World-Football-Elo autocorrection: shrinks the margin-of-victory bonus when the
    # winner was already the stronger side, so favourites don't inflate by thrashing minnows.
    # 0.0 disables it. Tuned on a 2023+/2025+ time-forward holdout (see eval_elo.py).
    mov_autocorrection: float = 0.0018
    # xG-aware updates: when a match carries home_xg/away_xg, blend the actual result with an
    # xG-"deserved" result so a lucky win gains less and an unlucky loss costs less. xg_blend is
    # the weight on the actual scoreline (rest on xG). Matches without xG are unaffected.
    xg_blend: float = 0.6
    xg_scale: float = 0.9  # logistic scale (goals) mapping the xG gap to a 0-1 deserved result


@dataclass
class EloHistory:
    """Point-in-time Elo lookups indexed per team for O(log n) reads.

    For each team we keep its match dates with the pre-match and post-match rating.
    ``pre_match_rating`` returns:
      * the pre-match rating when the query date is exactly a stored match date
        (the rating going into that match, which already reflects every earlier result), or
      * the post-match rating of the team's most recent prior match for any other
        date (e.g. a future 2026 fixture) — so forward predictions are not stale by a match.
    """

    base_rating: float
    final_ratings: dict[str, float] = field(default_factory=dict)
    dates: dict[str, list] = field(default_factory=dict)
    pre_ratings: dict[str, list[float]] = field(default_factory=dict)
    post_ratings: dict[str, list[float]] = field(default_factory=dict)

    def record(self, team: str, date: pd.Timestamp, pre: float, post: float) -> None:
        self.dates.setdefault(team, []).append(date)
        self.pre_ratings.setdefault(team, []).append(pre)
        self.post_ratings.setdefault(team, []).append(post)

    def current_rating(self, team: str) -> float:
        return float(self.final_ratings.get(team, self.base_rating))

    def pre_match_rating(self, team: str, date: pd.Timestamp) -> float:
        date = pd.Timestamp(date)
        team_dates = self.dates.get(team)
        if not team_dates:
            return float(self.base_rating)
        index = bisect.bisect_left(team_dates, date)
        if index < len(team_dates) and team_dates[index] == date:
            return float(self.pre_ratings[team][index])
        if index == 0:
            return float(self.base_rating)
        return float(self.post_ratings[team][index - 1])


class EloEngine:
    def __init__(self, config: EloConfig | None = None) -> None:
        self.config = config or EloConfig()

    @staticmethod
    def expected_score(team_rating: float, opponent_rating: float) -> float:
        return 1.0 / (1.0 + 10 ** ((opponent_rating - team_rating) / 400.0))

    @staticmethod
    def margin_multiplier(
        goal_difference: int, rating_diff_winner: float = 0.0, autocorrection: float = 0.0
    ) -> float:
        """Log-dampened margin-of-victory bonus, optionally autocorrected.

        ``rating_diff_winner`` is the winner's pre-match (effective) rating minus the
        loser's. When ``autocorrection > 0`` the bonus is scaled by
        ``2.2 / (autocorrection * rating_diff_winner + 2.2)`` so a heavy favourite that
        wins big gains less, and an underdog that wins big gains a touch more.
        """
        margin = abs(float(goal_difference))  # float so an xG/blended margin works too
        base = 1.0 if margin <= 1.0 else 1.0 + log(margin)
        if autocorrection > 0.0 and margin >= 2.0:
            base *= 2.2 / (autocorrection * rating_diff_winner + 2.2)
        return base

    def k_factor(self, tournament: str, stage: str | None = None) -> float:
        tournament_label = str(tournament).lower()
        stage_label = str(stage or "").lower()
        knockout_patterns = (
            r"\bknockout\b",
            r"\bround of\b",
            r"\bquarter[-\s]?finals?\b",
            r"\bsemi[-\s]?finals?\b",
            r"^(third[-\s]?place|bronze|final)$",
        )
        if any(re.search(pattern, stage_label) for pattern in knockout_patterns):
            return self.config.knockout_k
        if any(
            word in tournament_label
            for word in ("world cup", "euro", "copa america", "afcon", "asian cup")
        ):
            return self.config.tournament_k
        if "qualif" in tournament_label or "qualif" in stage_label or "nations league" in tournament_label:
            return self.config.qualifier_k
        return self.config.friendly_k

    def process_matches(
        self,
        matches: pd.DataFrame | Iterable[dict],
        host_nations: Iterable[str] = (),
    ) -> EloHistory:
        frame = pd.DataFrame(matches).copy()
        if frame.empty:
            return EloHistory(base_rating=self.config.base_rating)
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values("date").reset_index(drop=True)

        hosts = {str(team) for team in host_nations}
        ratings: dict[str, float] = {}
        history = EloHistory(base_rating=self.config.base_rating)
        for _, row in frame.iterrows():
            home = str(row["home_team"])
            away = str(row["away_team"])
            home_rating = ratings.get(home, self.config.base_rating)
            away_rating = ratings.get(away, self.config.base_rating)

            neutral = bool(row.get("neutral", False))
            if not neutral:
                home_adjustment = self.config.home_advantage
            elif home in hosts:
                home_adjustment = self.config.host_advantage
            else:
                home_adjustment = 0.0
            expected_home = self.expected_score(home_rating + home_adjustment, away_rating)
            home_score = int(row["home_score"])
            away_score = int(row["away_score"])
            result_home = 1.0 if home_score > away_score else 0.5 if home_score == away_score else 0.0

            # xG-aware: blend the actual result + goal margin with an xG-"deserved" version, so a
            # win/loss that the underlying performance didn't earn moves the rating less.
            home_xg, away_xg = row.get("home_xg"), row.get("away_xg")
            if home_xg is not None and away_xg is not None and not (pd.isna(home_xg) or pd.isna(away_xg)):
                xg_soft = 1.0 / (1.0 + exp(-(float(home_xg) - float(away_xg)) / self.config.xg_scale))
                blend = self.config.xg_blend
                effective_result = blend * result_home + (1.0 - blend) * xg_soft
                perf_margin = blend * (home_score - away_score) + (1.0 - blend) * (float(home_xg) - float(away_xg))
            else:
                effective_result = result_home
                perf_margin = float(home_score - away_score)

            stage = row.get("stage") if "stage" in row else None
            base_k = self.k_factor(str(row.get("tournament", "")), str(stage) if stage is not None else None)
            # Winner's pre-match edge (incl. home advantage) for the MoV autocorrection.
            eff_home = home_rating + home_adjustment
            if perf_margin > 0:
                rating_diff_winner = eff_home - away_rating
            elif perf_margin < 0:
                rating_diff_winner = away_rating - eff_home
            else:
                rating_diff_winner = 0.0
            k = base_k * self.margin_multiplier(perf_margin, rating_diff_winner, self.config.mov_autocorrection)
            delta = k * (effective_result - expected_home)
            home_post = home_rating + delta
            away_post = away_rating - delta

            date = pd.Timestamp(row["date"])
            history.record(home, date, home_rating, home_post)
            history.record(away, date, away_rating, away_post)
            ratings[home] = home_post
            ratings[away] = away_post

        history.final_ratings = ratings
        return history
