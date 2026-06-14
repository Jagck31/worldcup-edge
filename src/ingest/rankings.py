from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


RANKING_COLUMNS = {
    "date": "rank_date",
    "rank_date": "rank_date",
    "country_full": "team",
    "country_abrv": "team_code",
    "team": "team",
    "rank": "rank",
    "total_points": "points",
    "points": "points",
}


def load_rankings_csv(path: Path) -> pd.DataFrame:
    rankings = pd.read_csv(path)
    rename = {column: RANKING_COLUMNS[column] for column in rankings.columns if column in RANKING_COLUMNS}
    rankings = rankings.rename(columns=rename)
    required = {"rank_date", "team", "rank", "points"}
    missing = required - set(rankings.columns)
    if missing:
        raise ValueError(f"Ranking file missing required columns: {sorted(missing)}")
    rankings["rank_date"] = pd.to_datetime(rankings["rank_date"])
    return rankings[["rank_date", "team", "rank", "points"]].sort_values(["team", "rank_date"])


def _lookup_prior_ranking(
    by_team: dict[str, pd.DataFrame],
    team: str,
    match_date: pd.Timestamp,
) -> dict[str, Any]:
    table = by_team.get(team)
    if table is None or table.empty:
        return {"rank": np.nan, "points": np.nan, "rank_date": pd.NaT}

    dates = table["rank_date"].to_numpy(dtype="datetime64[ns]")
    position = np.searchsorted(dates, np.datetime64(match_date), side="right") - 1
    if position < 0:
        return {"rank": np.nan, "points": np.nan, "rank_date": pd.NaT}
    row = table.iloc[int(position)]
    return {"rank": row["rank"], "points": row["points"], "rank_date": row["rank_date"]}


def add_point_in_time_rankings(matches: pd.DataFrame, rankings: pd.DataFrame) -> pd.DataFrame:
    """Attach FIFA ranking snapshots known on or before each match date."""
    out = matches.copy().reset_index(drop=True)
    out["date"] = pd.to_datetime(out["date"])
    rankings = rankings.copy()
    rankings["rank_date"] = pd.to_datetime(rankings["rank_date"])
    rankings = rankings.sort_values(["team", "rank_date"])
    by_team = {team: group.reset_index(drop=True) for team, group in rankings.groupby("team")}

    for side in ("home", "away"):
        ranks: list[float] = []
        points: list[float] = []
        dates: list[pd.Timestamp] = []
        for _, row in out.iterrows():
            lookup = _lookup_prior_ranking(by_team, str(row[f"{side}_team"]), pd.Timestamp(row["date"]))
            ranks.append(lookup["rank"])
            points.append(lookup["points"])
            dates.append(lookup["rank_date"])
        out[f"{side}_rank"] = ranks
        out[f"{side}_rank_points"] = points
        out[f"{side}_rank_date"] = dates

    out["rank_diff"] = out["home_rank"] - out["away_rank"]
    out["rank_points_diff"] = out["home_rank_points"] - out["away_rank_points"]
    return out
