"""Rich renderable helpers shared by the dashboard screens."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


VERDICT_STYLE = {
    "usable_with_caution": "bold green",
    "research_only": "bold yellow",
    "do_not_bet": "bold red",
    "pipeline_scaffold": "dim",
}

ACCENT = "#7dd3fc"
GOOD = "#34d399"
WARN = "#fbbf24"
BAD = "#f87171"
MUTED = "#94a3b8"


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def bar(value: float, maximum: float, width: int = 18, color: str = ACCENT) -> Text:
    """Unicode horizontal bar scaled to ``maximum``."""
    if maximum <= 0:
        filled = 0
    else:
        filled = int(round((value / maximum) * width))
    filled = max(0, min(width, filled))
    text = Text()
    text.append("█" * filled, style=color)
    text.append("░" * (width - filled), style=MUTED)
    return text


SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 48, color: str = ACCENT) -> Text:
    """Render a numeric series as a Unicode sparkline (auto-scaled to its own range)."""
    if not values:
        return Text("—", style=MUTED)
    series = values[-width:]
    low = min(series)
    high = max(series)
    span = (high - low) or 1.0
    text = Text()
    for value in series:
        level = int((value - low) / span * (len(SPARK) - 1))
        text.append(SPARK[max(0, min(len(SPARK) - 1, level))], style=color)
    return text


def pct(value: float, digits: int = 1) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def verdict_text(verdict: str) -> Text:
    return Text(str(verdict).replace("_", " "), style=VERDICT_STYLE.get(verdict, "white"))


def source_tag(label: str) -> Text:
    label = str(label).upper()
    style = {
        "LIVE": "bold green",
        "DOWNLOADED": "bold green",
        "CACHED": "bold cyan",
        "HISTORICAL": "bold cyan",
        "DEMO": "bold yellow",
        "SAMPLE": "bold yellow",
    }.get(label, "white")
    return Text(f" {label} ", style=f"reverse {style}")


def kv_panel(title: str, rows: list[tuple[str, Any]], border: str = ACCENT) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", style=MUTED)
    table.add_column(justify="left", style="bold white")
    for key, value in rows:
        if isinstance(value, Text):
            table.add_row(key, value)
        else:
            table.add_row(key, str(value))
    return Panel(table, title=f"[bold]{title}", border_style=border, padding=(1, 2))


def architecture_diagram(model: dict) -> Panel:
    """A flow diagram of the (gradient-boosted-tree) pipeline, NN-style layer view."""
    kind = model.get("kind", "gradient boosted trees")
    inputs = model.get("architecture", {}).get("inputs", len(model.get("feature_columns", [])))
    importance = model.get("feature_importance", [])[:6]

    feature_lines = Text()
    max_imp = max((row["importance"] for row in importance), default=1.0) or 1.0
    for row in importance:
        feature_lines.append(f"{row['feature'][:20]:<20} ", style="white")
        feature_lines.append(bar(row["importance"], max_imp, width=12, color=ACCENT))
        feature_lines.append(f" {row['importance']:.3f}\n", style=MUTED)
    if not importance:
        feature_lines.append("(importance unavailable)\n", style=MUTED)

    diagram = Text()
    diagram.append("\n  INPUT LAYER", style=f"bold {ACCENT}")
    diagram.append(f"  ({inputs} point-in-time features)\n", style=MUTED)
    diagram.append("    Elo diff · FIFA rank · rolling form (5/10) · rest days · H2H · host/neutral\n\n", style="white")
    is_nn = kind == "neural_net"
    diagram.append("        │  feeds\n", style=MUTED)
    diagram.append("        ▼\n", style=MUTED)
    diagram.append("  HIDDEN: ", style=f"bold {WARN}")
    if is_nn:
        diagram.append("Neural Network (MLP)", style="bold white")
        diagram.append("  (impute → scale → 64→32 ReLU, early-stopped)\n", style=MUTED)
        diagram.append("    ◯◯◯◯◯ → ◯◯◯ fully-connected layers · backprop ◯◯◯◯◯\n\n", style="white")
    else:
        diagram.append("Gradient-Boosted Trees", style="bold white")
        diagram.append(f"  ({kind}, depth-3, shrinkage 0.05)\n", style=MUTED)
        diagram.append("    ░░░ ensemble of additive decision trees · native NaN routing ░░░\n\n", style="white")
    method = model.get("calibration", {}).get("method", "selected")
    method_label = {
        "none": "none — raw probabilities already best",
        "isotonic": "per-class isotonic + renormalize",
        "temperature": "temperature scaling",
    }.get(method, method)
    diagram.append("        │  raw softprob\n", style=MUTED)
    diagram.append("        ▼\n", style=MUTED)
    diagram.append("  CALIBRATION: ", style=f"bold {GOOD}")
    diagram.append(method_label, style="bold white")
    diagram.append("  (best of none/isotonic/temperature on a time-forward holdout)\n\n", style=MUTED)
    diagram.append("        │\n        ▼\n", style=MUTED)
    diagram.append("  OUTPUT LAYER: ", style=f"bold {ACCENT}")
    diagram.append("P(Home win)  ·  P(Draw)  ·  P(Away win)\n", style="bold white")

    body = Group(
        diagram,
        Panel(feature_lines, title="[bold]Top input weights (permutation importance)", border_style=MUTED, padding=(1, 2)),
    )
    return Panel(body, title="[bold]Model Architecture", border_style=ACCENT, padding=(1, 2))


def _match_cell(match: tuple[str, str, str], crown: bool = False) -> Text:
    """One knockout match: winner in green over the dimmed loser (✓ marks the winner)."""
    team_a, team_b, winner = match
    loser = team_b if winner == team_a else team_a
    cell = Text()
    if crown:
        cell.append("🏆 ", style=GOOD)
    cell.append(winner, style=f"bold {GOOD}")
    cell.append(" ✓\n", style=GOOD)
    cell.append(loser, style=f"dim {MUTED}")
    return cell


def bracket_panel(bracket: dict, title: str = "Sample tournament bracket", border: str = ACCENT) -> Panel:
    """Render one simulated knockout bracket showing the actual matchups each round
    (winner ✓ over the beaten team). ``bracket`` maps round -> [(team_a, team_b, winner), ...]."""
    rounds = [
        ("Round of 32", bracket.get("R32", [])),
        ("Round of 16", bracket.get("R16", [])),
        ("Quarterfinals", bracket.get("QF", [])),
        ("Semifinals", bracket.get("SF", [])),
        ("Final", bracket.get("F", [])),
    ]
    if not any(matches for _, matches in rounds):
        return Panel(Text("waiting for a simulated tournament…", style=MUTED), title=f"[bold]{title}", border_style=MUTED, padding=(1, 2))

    grid = Table(expand=True, show_lines=True, border_style=MUTED, header_style=f"bold {ACCENT}", padding=(0, 1))
    for name, _ in rounds:
        grid.add_column(name, overflow="fold")
    max_rows = max(len(matches) for _, matches in rounds)
    for i in range(max_rows):
        cells = []
        for name, matches in rounds:
            if i < len(matches):
                cells.append(_match_cell(matches[i], crown=(name == "Final")))
            else:
                cells.append("")
        grid.add_row(*cells)

    # Champion's path (who they beat each round).
    path = Text()
    final = bracket.get("F", [])
    if final:
        champ = final[0][2]
        beaten = []
        for key in ("R32", "R16", "QF", "SF", "F"):
            for team_a, team_b, winner in bracket.get(key, []):
                if winner == champ:
                    beaten.append(team_b if team_a == champ else team_a)
        path.append("\n  🏆 ", style=GOOD)
        path.append(f"{champ}", style=f"bold {GOOD}")
        path.append("  won it — beat: ", style=MUTED)
        path.append(" → ".join(beaten), style="white")
    return Panel(Group(grid, path), title=f"[bold]{title}", border_style=border, padding=(0, 1))


def reliability_panel(model: dict) -> Panel:
    reliability = model.get("reliability", [])
    table = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}")
    table.add_column("Class")
    table.add_column("Pred", justify="right")
    table.add_column("Observed", justify="right")
    table.add_column("Reliability (pred vs obs)")
    table.add_column("N", justify="right")
    for row in reliability:
        if row.get("count", 0) < 1:
            continue
        predicted = float(row.get("mean_predicted", 0.0))
        observed = float(row.get("observed_rate", 0.0))
        line = Text()
        line.append(bar(predicted, 1.0, width=12, color=ACCENT))
        line.append(" / ")
        line.append(bar(observed, 1.0, width=12, color=GOOD))
        gap = abs(predicted - observed)
        cls_style = GOOD if gap < 0.08 else WARN if gap < 0.18 else BAD
        table.add_row(
            Text(str(row.get("class", "")), style="bold white"),
            f"{predicted:.2f}",
            Text(f"{observed:.2f}", style=cls_style),
            line,
            str(row.get("count", 0)),
        )
    return Panel(table, title="[bold]Reliability — predicted vs observed", border_style=ACCENT, padding=(1, 1))
