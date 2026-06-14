from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        return yaml.safe_load(text) or {}
    except ImportError:
        return _parse_simple_yaml(text)


def write_markdown_report(
    output_dir: Path,
    calibration: dict[str, Any],
    slate: list[Any],
    scanner_flags: list[Any],
    bankroll_exposure: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"{timestamp}_worldcup_edge_report.md"
    lines = [
        "# World Cup Edge Report",
        "",
        "Manual execution only. This report does not place orders or sign transactions.",
        "",
        "With a $75 bankroll over one tournament, variance dominates short-run P&L. Judge the process: calibration, executable prices, liquidity, and sizing discipline.",
        "",
        "## Calibration Health",
        "",
        f"- Log loss: {calibration.get('log_loss', 'not_run')}",
        f"- Brier score: {calibration.get('brier', 'not_run')}",
        f"- Verdict: {calibration.get('verdict', 'not_run')}",
        "",
        "## Bankroll Exposure",
        "",
        f"- Bankroll: ${bankroll_exposure.get('bankroll_usd', 'unknown')}",
        f"- Open exposure: ${bankroll_exposure.get('open_exposure_usd', 'unknown')}",
        "",
        "## Ranked Betting Slate",
        "",
    ]
    if not slate:
        lines.append("No executable edges survived the configured threshold and liquidity checks.")
    else:
        lines.extend(_table(slate))
    lines.extend(["", "## Consistency Scanner Flags", ""])
    if not scanner_flags:
        lines.append("No cross-market incoherence flags survived the configured buffer.")
    else:
        lines.extend(_table(scanner_flags))
    lines.extend(
        [
            "",
            "## Honesty Notes",
            "",
            "- Edges use executable top-of-book depth after configured fees, never midpoints.",
            "- A recommendation with insufficient fillable size is suppressed.",
            "- Bracket-dependent simulations require the official third-place assignment table.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _table(rows: list[Any]) -> list[str]:
    normalized = [_to_dict(row) for row in rows]
    columns = list(normalized[0].keys())
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in normalized:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return lines


def _to_dict(row: Any) -> dict[str, Any]:
    if is_dataclass(row):
        return asdict(row)
    if isinstance(row, dict):
        return row
    return dict(row)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = _parse_scalar(value.strip())
    return parsed


def _parse_scalar(value: str) -> Any:
    if value == "":
        return ""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value.strip("'\"")


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    config = load_config(project_root / "config.yaml")
    report = write_markdown_report(
        output_dir=project_root / "reports",
        calibration={"log_loss": "not_run", "brier": "not_run", "verdict": "pipeline_scaffold"},
        slate=[],
        scanner_flags=[],
        bankroll_exposure={"bankroll_usd": config.get("bankroll_usd", 75), "open_exposure_usd": 0},
    )
    print(report)


if __name__ == "__main__":
    main()
