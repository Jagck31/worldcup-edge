from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import math
import re
from typing import Iterable, Sequence

import pandas as pd


@dataclass(frozen=True)
class SquadStrengthConfig:
    lookback_days: int = 730
    minutes_per_unit: float = 900.0
    performance_columns: tuple[str, ...] = ("xg_per90", "xa_per90")


_FBREF_ALIASES = {
    "player": ("player", "Player"),
    "team": ("team", "national_team", "Nation", "nation", "Country", "country"),
    "club": ("club", "squad", "Squad", "team_club"),
    "stat_date": ("stat_date", "date", "Date", "season_end", "Season_End", "Season End"),
    "minutes": ("minutes", "Min", "min", "Playing Time_Min", "Playing Time Min"),
    "xg_per90": ("xg_per90", "xG/90", "Expected_xG/90", "Expected xG/90"),
    "xa_per90": ("xa_per90", "xAG/90", "xA/90", "Expected_xAG/90", "Expected xAG/90"),
}

_MARKET_VALUE_ALIASES = {
    "player": ("player", "Player", "Name", "name"),
    "team": ("team", "National Team", "national_team", "Nation", "country"),
    "date": ("date", "Snapshot Date", "snapshot_date", "as_of_date"),
    "market_value_eur": ("market_value_eur", "Market Value", "market_value", "Value"),
}

_ABSENCE_ALIASES = {
    "player": ("player", "Player", "Name", "name"),
    "team": ("team", "National Team", "national_team", "Nation", "country"),
    "as_of_date": ("as_of_date", "Reported", "Report Date", "snapshot_date", "date"),
    "unavailable_from": ("unavailable_from", "From", "Start", "Injury From", "start_date"),
    "unavailable_until": ("unavailable_until", "Until", "Expected Return", "To", "end_date"),
    "reason": ("reason", "Reason", "Injury", "Status"),
}


def fetch_fbref_player_stats(
    seasons: Sequence[str | int],
    leagues: Sequence[str] | str | None = None,
    stat_type: str = "standard",
) -> pd.DataFrame:
    """Fetch FBref player season stats through ``soccerdata``.

    The import is lazy so the rest of the project can run without ``soccerdata``
    installed. Downstream code should pass the returned frame through
    ``normalize_fbref_player_stats`` before building squad features.
    """
    try:
        import soccerdata as sd  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "soccerdata is required to fetch FBref player stats; install it or "
            "provide cached normalized player stats."
        ) from exc

    reader = sd.FBref(leagues=leagues, seasons=list(seasons))
    return reader.read_player_season_stats(stat_type=stat_type)


def normalize_fbref_player_stats(frame: pd.DataFrame) -> pd.DataFrame:
    raw = _flatten_columns(frame)
    out = pd.DataFrame()
    for target, aliases in _FBREF_ALIASES.items():
        column = _first_present(raw, aliases)
        if column is not None:
            out[target] = raw[column]
    required = {"player", "team", "stat_date", "minutes"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"FBref player stats missing required fields: {sorted(missing)}")
    out["player"] = out["player"].astype(str).str.strip()
    out["team"] = out["team"].astype(str).str.replace(r"^[a-z]{2}\s+", "", regex=True).str.strip()
    if "club" in out:
        out["club"] = out["club"].astype(str).str.strip()
    else:
        out["club"] = ""
    out["stat_date"] = pd.to_datetime(out["stat_date"], errors="coerce")
    out["minutes"] = _numeric_series(out["minutes"]).fillna(0.0)
    for column in ("xg_per90", "xa_per90"):
        if column not in out:
            out[column] = 0.0
        out[column] = _numeric_series(out[column]).fillna(0.0)
    return out.dropna(subset=["stat_date"]).reset_index(drop=True)


def normalize_transfermarkt_market_values(frame: pd.DataFrame) -> pd.DataFrame:
    raw = _flatten_columns(frame)
    out = pd.DataFrame()
    for target, aliases in _MARKET_VALUE_ALIASES.items():
        column = _first_present(raw, aliases)
        if column is not None:
            out[target] = raw[column]
    required = {"player", "team", "date", "market_value_eur"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"Transfermarkt values missing required fields: {sorted(missing)}")
    out["player"] = out["player"].astype(str).str.strip()
    out["team"] = out["team"].astype(str).str.strip()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["market_value_eur"] = out["market_value_eur"].map(_parse_market_value_eur)
    return out.dropna(subset=["date"]).reset_index(drop=True)


def normalize_transfermarkt_absences(frame: pd.DataFrame) -> pd.DataFrame:
    raw = _flatten_columns(frame)
    out = pd.DataFrame()
    for target, aliases in _ABSENCE_ALIASES.items():
        column = _first_present(raw, aliases)
        if column is not None:
            out[target] = raw[column]
    required = {"player", "team", "unavailable_from"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"Transfermarkt absences missing required fields: {sorted(missing)}")
    out["player"] = out["player"].astype(str).str.strip()
    out["team"] = out["team"].astype(str).str.strip()
    for column in ("as_of_date", "unavailable_from", "unavailable_until"):
        if column not in out:
            out[column] = pd.NaT
        out[column] = pd.to_datetime(out[column], errors="coerce")
    if "reason" not in out:
        out["reason"] = ""
    out["reason"] = out["reason"].fillna("").astype(str).str.strip()
    return out.dropna(subset=["unavailable_from"]).reset_index(drop=True)


def parse_transfermarkt_market_values_html(
    html: str,
    team: str,
    snapshot_date: str | pd.Timestamp,
) -> pd.DataFrame:
    parser = _SimpleTableParser()
    parser.feed(html)
    rows = parser.rows
    if not rows:
        return pd.DataFrame(columns=["player", "team", "date", "market_value_eur"])
    header = [cell.strip().casefold() for cell in rows[0]]
    player_idx = _first_header_index(header, ("player", "name"))
    value_idx = _first_header_index(header, ("market value", "value"))
    if player_idx is None or value_idx is None:
        raise ValueError("Transfermarkt HTML table must include player/name and market value columns")
    records = []
    for row in rows[1:]:
        if len(row) <= max(player_idx, value_idx):
            continue
        player = row[player_idx].strip()
        if not player:
            continue
        records.append(
            {
                "player": player,
                "team": team,
                "date": pd.Timestamp(snapshot_date),
                "market_value_eur": _parse_market_value_eur(row[value_idx]),
            }
        )
    return pd.DataFrame(records)


def build_squad_strength_table(
    team_dates: pd.DataFrame,
    player_stats: pd.DataFrame,
    squad_members: pd.DataFrame | None = None,
    market_values: pd.DataFrame | None = None,
    absences: pd.DataFrame | None = None,
    config: SquadStrengthConfig | None = None,
) -> pd.DataFrame:
    cfg = config or SquadStrengthConfig()
    dates = team_dates.copy()
    dates["date"] = pd.to_datetime(dates["date"])
    stats = _prepare_player_stats(player_stats)
    squads = _prepare_squad_members(squad_members)
    values = _prepare_market_values(market_values)
    absences_frame = _prepare_absences(absences)

    rows: list[dict] = []
    for _, row in dates.iterrows():
        team = str(row["team"]).strip()
        as_of = pd.Timestamp(row["date"])
        players = _eligible_players(team, as_of, stats, squads)
        window_start = as_of - pd.Timedelta(days=cfg.lookback_days)
        stat_rows = stats[
            stats["player"].isin(players)
            & stats["team"].eq(team)
            & stats["stat_date"].le(as_of)
            & stats["stat_date"].ge(window_start)
        ].copy()
        minutes = float(stat_rows["minutes"].sum()) if not stat_rows.empty else 0.0
        strength = _squad_strength(stat_rows, cfg)
        latest_values = _latest_values(values, players, team, as_of)
        unavailable = _active_absences(absences_frame, players, team, as_of)
        total_value = float(sum(latest_values.values()))
        unavailable_value = float(sum(latest_values.get(player, 0.0) for player in unavailable))
        if total_value > 0:
            availability = max(0.0, 1.0 - unavailable_value / total_value)
        elif players:
            availability = max(0.0, 1.0 - len(unavailable) / len(players))
        else:
            availability = math.nan
        rows.append(
            {
                "team": team,
                "date": as_of,
                "squad_strength": round(strength, 6),
                "squad_availability": availability if math.isfinite(availability) else math.nan,
                "squad_minutes": round(minutes, 2),
                "squad_player_count": len(players),
                "squad_contributors": int(stat_rows["player"].nunique()) if not stat_rows.empty else 0,
                "unavailable_players": len(unavailable),
                "squad_market_value_eur": round(total_value, 2),
                "key_players_out_value_eur": round(unavailable_value, 2),
            }
        )
    return pd.DataFrame(rows)


def _flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [
            "_".join(str(part).strip() for part in column if str(part).strip())
            for column in out.columns
        ]
    else:
        out.columns = [str(column).strip() for column in out.columns]
    return out


def _first_present(frame: pd.DataFrame, names: Iterable[str]) -> str | None:
    columns = {str(column).casefold(): str(column) for column in frame.columns}
    for name in names:
        exact = str(name)
        if exact in frame.columns:
            return exact
        folded = columns.get(exact.casefold())
        if folded is not None:
            return folded
    return None


def _first_header_index(header: list[str], names: Iterable[str]) -> int | None:
    for name in names:
        target = name.casefold()
        for index, value in enumerate(header):
            if value == target or target in value:
                return index
    return None


def _numeric_series(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values.astype(str).str.replace(",", "", regex=False), errors="coerce")


def _parse_market_value_eur(value: object) -> float:
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").casefold()
    text = text.replace("\u20ac", "").replace("eur", "").strip()
    multiplier = 1.0
    if text.endswith("bn") or text.endswith("b"):
        multiplier = 1_000_000_000.0
        text = re.sub(r"b(?:n)?$", "", text).strip()
    elif text.endswith("m"):
        multiplier = 1_000_000.0
        text = text[:-1].strip()
    elif text.endswith("k"):
        multiplier = 1_000.0
        text = text[:-1].strip()
    try:
        return float(text) * multiplier
    except ValueError:
        return 0.0


def _prepare_player_stats(player_stats: pd.DataFrame) -> pd.DataFrame:
    stats = player_stats.copy()
    stats["team"] = stats["team"].astype(str).str.strip()
    stats["player"] = stats["player"].astype(str).str.strip()
    stats["stat_date"] = pd.to_datetime(stats["stat_date"], errors="coerce")
    stats["minutes"] = pd.to_numeric(stats["minutes"], errors="coerce").fillna(0.0)
    for column in ("xg_per90", "xa_per90"):
        if column not in stats:
            stats[column] = 0.0
        stats[column] = pd.to_numeric(stats[column], errors="coerce").fillna(0.0)
    return stats.dropna(subset=["stat_date"])


def _prepare_squad_members(squad_members: pd.DataFrame | None) -> pd.DataFrame | None:
    if squad_members is None:
        return None
    squads = squad_members.copy()
    squads["team"] = squads["team"].astype(str).str.strip()
    squads["player"] = squads["player"].astype(str).str.strip()
    if "as_of_date" in squads:
        squads["as_of_date"] = pd.to_datetime(squads["as_of_date"], errors="coerce")
    else:
        squads["as_of_date"] = pd.Timestamp.min
    if "valid_until" in squads:
        squads["valid_until"] = pd.to_datetime(squads["valid_until"], errors="coerce")
    else:
        squads["valid_until"] = pd.NaT
    return squads


def _prepare_market_values(market_values: pd.DataFrame | None) -> pd.DataFrame:
    if market_values is None:
        return pd.DataFrame(columns=["team", "player", "date", "market_value_eur"])
    values = market_values.copy()
    values["team"] = values["team"].astype(str).str.strip()
    values["player"] = values["player"].astype(str).str.strip()
    values["date"] = pd.to_datetime(values["date"], errors="coerce")
    values["market_value_eur"] = pd.to_numeric(values["market_value_eur"], errors="coerce").fillna(0.0)
    return values.dropna(subset=["date"])


def _prepare_absences(absences: pd.DataFrame | None) -> pd.DataFrame:
    if absences is None:
        return pd.DataFrame(columns=["team", "player", "as_of_date", "unavailable_from", "unavailable_until"])
    out = absences.copy()
    out["team"] = out["team"].astype(str).str.strip()
    out["player"] = out["player"].astype(str).str.strip()
    for column in ("as_of_date", "unavailable_from", "unavailable_until"):
        if column not in out:
            out[column] = pd.NaT
        out[column] = pd.to_datetime(out[column], errors="coerce")
    return out


def _eligible_players(
    team: str,
    as_of: pd.Timestamp,
    stats: pd.DataFrame,
    squads: pd.DataFrame | None,
) -> set[str]:
    if squads is None:
        return set(stats.loc[stats["team"].eq(team), "player"].dropna().astype(str))
    rows = squads[
        squads["team"].eq(team)
        & squads["as_of_date"].le(as_of)
        & (squads["valid_until"].isna() | squads["valid_until"].ge(as_of))
    ]
    return set(rows["player"].dropna().astype(str))


def _squad_strength(stat_rows: pd.DataFrame, config: SquadStrengthConfig) -> float:
    if stat_rows.empty:
        return 0.0
    performance = pd.Series(0.0, index=stat_rows.index)
    for column in config.performance_columns:
        if column in stat_rows:
            performance = performance + pd.to_numeric(stat_rows[column], errors="coerce").fillna(0.0)
    weighted = (stat_rows["minutes"] * performance).sum()
    return float(weighted / config.minutes_per_unit)


def _latest_values(
    market_values: pd.DataFrame,
    players: set[str],
    team: str,
    as_of: pd.Timestamp,
) -> dict[str, float]:
    if market_values.empty or not players:
        return {}
    rows = market_values[
        market_values["team"].eq(team)
        & market_values["player"].isin(players)
        & market_values["date"].le(as_of)
    ].sort_values(["player", "date"])
    latest = rows.groupby("player").tail(1)
    return dict(zip(latest["player"], latest["market_value_eur"]))


def _active_absences(
    absences: pd.DataFrame,
    players: set[str],
    team: str,
    as_of: pd.Timestamp,
) -> set[str]:
    if absences.empty or not players:
        return set()
    rows = absences[
        absences["team"].eq(team)
        & absences["player"].isin(players)
        & (absences["as_of_date"].isna() | absences["as_of_date"].le(as_of))
        & (absences["unavailable_from"].isna() | absences["unavailable_from"].le(as_of))
        & (absences["unavailable_until"].isna() | absences["unavailable_until"].ge(as_of))
    ]
    return set(rows["player"].dropna().astype(str))


class _SimpleTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(" ".join(part.strip() for part in self._current_cell).strip())
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = None
