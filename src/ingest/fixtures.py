from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


FIXTURE_COLUMNS = [
    "match_id",
    "date",
    "group",
    "stage",
    "home_team",
    "away_team",
    "venue",
    "country",
    "neutral",
    "home_score",
    "away_score",
    "status",
]


@dataclass(frozen=True)
class TournamentState:
    fixtures: pd.DataFrame

    @property
    def completed(self) -> pd.DataFrame:
        return self.fixtures[self.fixtures["status"].eq("completed")].copy()

    @property
    def remaining(self) -> pd.DataFrame:
        return self.fixtures[~self.fixtures["status"].eq("completed")].copy()


def ensure_manual_fixture_template(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        pd.DataFrame(columns=FIXTURE_COLUMNS).to_csv(path, index=False)
    return path


def load_fixtures(path: Path) -> TournamentState:
    ensure_manual_fixture_template(path)
    fixtures = pd.read_csv(path)
    for column in FIXTURE_COLUMNS:
        if column not in fixtures.columns:
            fixtures[column] = pd.NA
    fixtures = fixtures[FIXTURE_COLUMNS]
    if not fixtures.empty:
        fixtures["date"] = pd.to_datetime(fixtures["date"])
        fixtures["neutral"] = fixtures["neutral"].map(_parse_bool_flag)
        fixtures["status"] = fixtures["status"].fillna("scheduled")
    return TournamentState(fixtures)


def merge_live_results(fixtures: pd.DataFrame, completed_results: pd.DataFrame) -> pd.DataFrame:
    """Overlay completed scores by match_id without changing scheduled rows."""
    out = fixtures.copy()
    if completed_results.empty:
        return out
    required = {"match_id", "home_score", "away_score"}
    missing = required - set(completed_results.columns)
    if missing:
        raise ValueError(f"Completed results missing columns: {sorted(missing)}")
    duplicates = completed_results["match_id"][completed_results["match_id"].duplicated()].dropna().unique()
    if len(duplicates):
        raise ValueError(f"Duplicate completed result match_id values: {sorted(str(item) for item in duplicates)}")

    by_id = completed_results.set_index("match_id")
    for index, row in out.iterrows():
        match_id = row["match_id"]
        if match_id in by_id.index:
            result = by_id.loc[match_id]
            if pd.isna(result["home_score"]) or pd.isna(result["away_score"]):
                raise ValueError(f"Missing completed score for match_id {match_id}")
            out.at[index, "home_score"] = int(result["home_score"])
            out.at[index, "away_score"] = int(result["away_score"])
            out.at[index, "status"] = "completed"
    return out


def _parse_bool_flag(value: object) -> bool:
    if pd.isna(value):
        return False
    normalized = str(value).strip().casefold()
    if normalized in {"true", "t", "yes", "y", "1"}:
        return True
    if normalized in {"false", "f", "no", "n", "0", ""}:
        return False
    raise ValueError(f"Invalid boolean fixture flag: {value!r}")
