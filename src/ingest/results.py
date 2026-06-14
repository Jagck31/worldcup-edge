from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pandas as pd


RESULTS_URL = (
    "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
)


DEFAULT_ALIASES = {
    "USA": "United States",
    "U.S.A.": "United States",
    "USMNT": "United States",
    "South Korea": "Korea Republic",
    "North Korea": "Korea DPR",
    "Ivory Coast": "Cote d'Ivoire",
    "Czech Republic": "Czechia",
    "Iran": "IR Iran",
}


@dataclass(frozen=True)
class TeamNameNormalizer:
    aliases: Mapping[str, str]

    @classmethod
    def from_yaml(cls, path: Path) -> "TeamNameNormalizer":
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_dump_simple_yaml(DEFAULT_ALIASES), encoding="utf-8")
        loaded = _load_simple_yaml(path)
        return cls({str(k): str(v) for k, v in loaded.items()})

    def canonical(self, value: str) -> str:
        clean = str(value).strip()
        if clean in self.aliases:
            return self.aliases[clean]
        folded = clean.casefold()
        for alias, canonical in self.aliases.items():
            if str(alias).casefold() == folded:
                return canonical
        return clean

    def apply(self, frame: pd.DataFrame, columns: tuple[str, ...] = ("home_team", "away_team")) -> pd.DataFrame:
        out = frame.copy()
        for column in columns:
            if column in out.columns:
                out[column] = out[column].map(self.canonical)
        return out


def download_results(destination: Path, refresh: bool = False) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not refresh:
        return destination

    import requests

    response = requests.get(RESULTS_URL, timeout=30)
    response.raise_for_status()
    destination.write_bytes(response.content)
    return destination


def load_results(
    raw_path: Path,
    mapping_path: Path | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    csv_path = download_results(raw_path, refresh=refresh)
    results = pd.read_csv(csv_path, parse_dates=["date"])
    normalizer = (
        TeamNameNormalizer.from_yaml(mapping_path)
        if mapping_path is not None
        else TeamNameNormalizer(DEFAULT_ALIASES)
    )
    results = normalizer.apply(results)
    results["neutral"] = results["neutral"].astype(bool)
    return results.sort_values("date").reset_index(drop=True)


def _load_simple_yaml(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        return yaml.safe_load(text) or {}
    except ImportError:
        parsed: dict[str, str] = {}
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed[key.strip().strip("'\"")] = value.strip().strip("'\"")
        return parsed


def _dump_simple_yaml(values: Mapping[str, str]) -> str:
    try:
        import yaml

        return yaml.safe_dump(dict(values), sort_keys=True)
    except ImportError:
        return "\n".join(f"{key}: {value}" for key, value in sorted(values.items())) + "\n"
