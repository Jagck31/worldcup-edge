"""Interactive textual dashboard for the World Cup edge model.

Run with:  python -m dashboard   (PYTHONPATH=src)   or   python dashboard.py
Press 'r' to re-run the pipeline, arrow keys / mouse to switch tabs, 'q' to quit.
"""
from __future__ import annotations

import time
from pathlib import Path

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from dashboard.render import (
    ACCENT,
    BAD,
    GOOD,
    MUTED,
    WARN,
    architecture_diagram,
    bar,
    bracket_panel,
    kv_panel,
    load_state,
    pct,
    reliability_panel,
    source_tag,
    sparkline,
    verdict_text,
)

LIVE_STAGES = [
    "Load results",
    "Build Elo",
    "Build features + train calibrated model",
    "Fit goal model",
    "Run Monte Carlo",
    "Detect edges + size bets + scan",
]
STAGE_SHORT = {
    "Load results": "Load data",
    "Build Elo": "Elo ratings",
    "Build features + train calibrated model": "Train model",
    "Fit goal model": "Goal model",
    "Run Monte Carlo": "Monte Carlo",
    "Detect edges + size bets + scan": "Edges & Kelly",
}


class _LiveHooks:
    """Duck-typed PipelineHooks that marshal every callback onto the UI thread."""

    def __init__(self, app: "DashboardApp") -> None:
        self.app = app

    def stage(self, name: str, status: str = "start", detail: str = "") -> None:
        self.app.call_from_thread(self.app.on_live_stage, name)

    def train_step(self, iteration: int, max_iter: int, val_log_loss: float) -> None:
        self.app.call_from_thread(self.app.on_live_train, iteration, max_iter, val_log_loss)

    def sim_progress(self, done: int, total: int, counts: dict, bracket: dict | None = None) -> None:
        rows = self.app.sim_rows(counts, done)
        self.app.call_from_thread(self.app.on_live_sim, done, total, rows, bracket)

    def elo_ready(self, leaderboard: list[dict]) -> None:
        self.app.call_from_thread(self.app.on_live_elo, leaderboard)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = PROJECT_ROOT / "data" / "processed" / "dashboard_state.json"


class DashboardApp(App):
    CSS = """
    Screen { background: #0b1220; }
    Header { background: #111c33; color: #7dd3fc; text-style: bold; }
    Footer { background: #111c33; }
    TabbedContent { padding: 0 1; }
    Tabs { background: #0b1220; }
    VerticalScroll { padding: 1 1; scrollbar-size: 1 1; }
    .body { padding: 0 1; }
    """

    TITLE = "World Cup 2026 — Edge Model Dashboard"
    SUB_TITLE = "calibrated probabilities · Monte Carlo · executable edges"

    BINDINGS = [
        ("r", "refresh", "Re-run pipeline"),
        ("g", "goto_markets", "Trades"),
        ("d", "toggle_theme", "Theme"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, state_path: Path = STATE_PATH, n_sims: int = 30000) -> None:
        super().__init__()
        self.state_path = state_path
        self.n_sims = n_sims
        self.state = load_state(state_path) or {}
        self._busy = False
        self._reset_live_state()

    def _reset_live_state(self) -> None:
        self._live_stage = "idle"
        self._live_detail = ""
        self._live_curve: list[float] = []
        self._live_train: tuple | None = None
        self._live_sim: tuple | None = None
        self._live_elo: list[dict] = []
        self._run_t0 = 0.0
        self._sim_t0 = 0.0
        self._prev_leader = None
        self._sim_traj: dict[str, list[float]] = {}
        self._sim_traj_adv: dict[str, list[float]] = {}
        self._sim_traj_final: dict[str, list[float]] = {}
        self._sim_prev: dict[str, float] = {}
        self._sim_movement: list[float] = []
        self._live_bracket: dict = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="overview"):
            with TabPane("Overview", id="overview"):
                yield VerticalScroll(Static(id="overview-body", classes="body"))
            with TabPane("● Live Run", id="live"):
                with VerticalScroll(classes="body"):
                    yield Static(id="live-status")
                    yield Static(id="live-train")
                    yield Static(id="live-sim")
                    yield Static(id="live-bracket")
            with TabPane("Data & Pipeline", id="data"):
                yield VerticalScroll(Static(id="data-body", classes="body"))
            with TabPane("Elo Ratings", id="elo"):
                yield VerticalScroll(Static(id="elo-body", classes="body"))
            with TabPane("◆ Squads & Players", id="squads"):
                yield VerticalScroll(Static(id="squads-body", classes="body"))
            with TabPane("Model & Calibration", id="model"):
                yield VerticalScroll(Static(id="model-body", classes="body"))
            with TabPane("Simulation", id="sim"):
                yield VerticalScroll(Static(id="sim-body", classes="body"))
            with TabPane("◆ Live Tracker", id="tracker"):
                yield VerticalScroll(Static(id="tracker-body", classes="body"))
            with TabPane("Trades & Markets", id="markets"):
                yield VerticalScroll(Static(id="markets-body", classes="body"))
            with TabPane("$ Paper Account", id="paper"):
                yield VerticalScroll(Static(id="paper-body", classes="body"))
        yield Footer()

    def on_mount(self) -> None:
        self.populate()
        self._render_live()

    # ------------------------------------------------------------------ actions
    def action_refresh(self) -> None:
        if self._busy:
            self.notify("Pipeline already running…", severity="warning")
            return
        self._busy = True
        self._reset_live_state()
        self._run_t0 = time.perf_counter()
        self._live_stage = "starting"
        self.query_one(TabbedContent).active = "live"
        self._render_live()
        self.sub_title = "LIVE — watch it train and simulate"
        self.notify("Live run started — watch the Live Run tab.", timeout=5)
        self._run_pipeline_worker()

    def action_goto_markets(self) -> None:
        self.query_one(TabbedContent).active = "markets"

    def action_toggle_theme(self) -> None:
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"

    @work(thread=True, exclusive=True)
    def _run_pipeline_worker(self) -> None:
        from pipeline.orchestrator import run_pipeline, write_state

        try:
            state = run_pipeline(refresh=False, n_sims=self.n_sims, hooks=_LiveHooks(self))
            write_state(state, self.state_path)
        except Exception as exc:  # pragma: no cover - surfaced to UI
            self.call_from_thread(self.notify, f"Pipeline failed: {exc}", severity="error")
            self.call_from_thread(self._finish_refresh)
            return
        self.state = state
        self.call_from_thread(self._on_run_complete)

    def _on_run_complete(self) -> None:
        self._live_stage = "complete"
        self.populate()
        self._render_live()
        self.notify("Pipeline complete — every tab updated.")
        self._finish_refresh()

    def _finish_refresh(self) -> None:
        self._busy = False
        self.sub_title = self.SUB_TITLE

    # ------------------------------------------------------------------ live callbacks
    def sim_rows(self, counts: dict, done: int, n: int = 12) -> list[dict]:
        if done <= 0:
            return []
        rows = [
            {
                "team": team,
                "champ": tally["champion"] / done,
                "advance": tally["advanced"] / done,
                "final": tally["finalist"] / done,
            }
            for team, tally in counts.items()
        ]
        rows.sort(key=lambda r: r["champ"], reverse=True)
        return rows[:n]

    def on_live_stage(self, name: str) -> None:
        self._live_stage = name
        if name == "Run Monte Carlo":
            self._sim_t0 = time.perf_counter()
        self._render_live()

    def on_live_train(self, iteration: int, max_iter: int, val_log_loss: float) -> None:
        if val_log_loss == val_log_loss:  # not NaN
            self._live_curve.append(val_log_loss)
        self._live_train = (iteration, max_iter, val_log_loss)
        self._render_live()

    def on_live_sim(self, done: int, total: int, rows: list[dict], bracket: dict | None = None) -> None:
        # Accumulate each team's champion / advance / reach-final trajectories and measure
        # how much the top probabilities are still moving (→ 0 means the sim has converged).
        movement = []
        for row in rows:
            team = row["team"]
            self._sim_traj.setdefault(team, []).append(row["champ"])
            self._sim_traj_adv.setdefault(team, []).append(row["advance"])
            self._sim_traj_final.setdefault(team, []).append(row["final"])
            if team in self._sim_prev:
                movement.append(abs(row["champ"] - self._sim_prev[team]))
            self._sim_prev[team] = row["champ"]
        if movement:
            self._sim_movement.append(sum(movement) / len(movement))
        if bracket:
            self._live_bracket = bracket
        self._live_sim = (done, total, rows)
        self._render_live()

    def on_live_elo(self, leaderboard: list[dict]) -> None:
        self._live_elo = leaderboard
        self._render_live()

    def _render_live(self) -> None:
        try:
            self.query_one("#live-status", Static).update(self._live_status_panel())
            self.query_one("#live-train", Static).update(self._live_train_panel())
            self.query_one("#live-sim", Static).update(self._live_sim_panel())
            self.query_one("#live-bracket", Static).update(self._live_bracket_panel())
        except Exception:
            pass  # widgets not mounted yet

    def _live_bracket_panel(self):
        if not self._live_bracket:
            return Panel(Text("a sample tournament will play out here as the sims run…", style=MUTED), title="[bold]Sample tournament", border_style=MUTED, padding=(1, 2))
        return bracket_panel(self._live_bracket, title="Sample tournament playing out (a fresh one each batch)", border=WARN)

    # ------------------------------------------------------------------ populate
    def populate(self) -> None:
        if not self.state:
            empty = Panel(
                Text(
                    "No pipeline state found.\n\nPress  r  to run the pipeline:\n"
                    "  download results → Elo → calibrated model → Monte Carlo → edges.\n"
                    "It takes ~45 seconds and writes data/processed/dashboard_state.json.",
                    justify="center",
                ),
                title="World Cup Edge Dashboard",
                border_style=WARN,
                padding=(2, 4),
            )
            for view in ("overview", "data", "elo", "squads", "model", "sim", "tracker", "markets", "paper"):
                self.query_one(f"#{view}-body", Static).update(empty)
            return
        self.query_one("#overview-body", Static).update(self._overview())
        self.query_one("#data-body", Static).update(self._data())
        self.query_one("#elo-body", Static).update(self._elo())
        self.query_one("#squads-body", Static).update(self._squads())
        self.query_one("#model-body", Static).update(self._model())
        self.query_one("#sim-body", Static).update(self._sim())
        self.query_one("#tracker-body", Static).update(self._tracker())
        self.query_one("#markets-body", Static).update(self._markets())
        self.query_one("#paper-body", Static).update(self._paper_account())

    # ------------------------------------------------------------------ views
    def _squads(self) -> Group:
        squads = self.state.get("squads", {})
        if not squads.get("available"):
            return Group(Text("\n  No squad data. Add data/manual/squads_2026.csv.", style=MUTED))
        teams = squads.get("teams", [])
        header = Text()
        header.append("  Squads & Key Players  ", style=f"bold {ACCENT}")
        header.append(f"{len(teams)} teams · by squad market value", style=MUTED)

        table = Table(expand=True, border_style="grey37", title_style=f"bold {ACCENT}")
        table.add_column("Team", style="bold white", no_wrap=True)
        table.add_column("Squad €m", justify="right", style="cyan")
        table.add_column("Elo", justify="right", style=ACCENT)
        table.add_column("Talent vs Results", justify="center")
        table.add_column("Key players", style="grey78")
        for t in teams:
            gap = t.get("talent_gap", 0)
            if gap > 1:
                tvr = Text(f"▲ over +{gap}", style=GOOD)        # results beat the roster
            elif gap < -1:
                tvr = Text(f"▼ under {gap}", style=BAD)          # underachieving the roster
            else:
                tvr = Text("≈ on par", style=MUTED)
            players = "  ·  ".join(p.split(" (")[0] for p in t.get("key_players", [])[:4])
            table.add_row(t["team"], f"{t['value_m']:,.0f}", f"{t['elo']:.0f}", tvr, players)

        # spotlight: the most over/underachieving rosters
        ranked = sorted(teams, key=lambda x: x.get("talent_gap", 0))
        spotlight = Text()
        if ranked:
            under = ranked[0]; over = ranked[-1]
            spotlight.append("\n  Underachieving roster: ", style=MUTED)
            spotlight.append(f"{under['team']} ", style=f"bold {BAD}")
            spotlight.append(f"(value #{under['value_rank']}, Elo #{under['elo_rank']})", style=MUTED)
            spotlight.append("     Punching above talent: ", style=MUTED)
            spotlight.append(f"{over['team']} ", style=f"bold {GOOD}")
            spotlight.append(f"(value #{over['value_rank']}, Elo #{over['elo_rank']})", style=MUTED)

        note = Text(f"\n  {squads.get('note', '')}", style=MUTED)
        return Group(header, Text(""), table, spotlight, note)

    def _overview(self) -> Group:
        data = self.state.get("data", {})
        model = self.state.get("model", {})
        sim = self.state.get("simulation", {})
        markets = self.state.get("markets", {})
        config = self.state.get("config", {})
        submarkets = sim.get("submarkets", [])
        favourite = submarkets[0] if submarkets else {}

        cal = model.get("calibration", {}).get("calibrated", {})
        slate = markets.get("slate", [])
        actionable = [r for r in slate if r.get("actionable")]

        header = Text()
        header.append("  World Cup 2026 Edge Model  ", style=f"bold {ACCENT}")
        header.append("generated ", style=MUTED)
        header.append(str(self.state.get("generated_at", "—")), style="white")
        header.append("    data ", style=MUTED)
        header += source_tag(data.get("source", "—"))
        header.append("  draw ", style=MUTED)
        header += source_tag("DEMO")
        header.append("  prices ", style=MUTED)
        header += source_tag(markets.get("source", "SAMPLE"))

        left = kv_panel(
            "Bankroll & Rules",
            [
                ("Bankroll", f"${config.get('bankroll_usd', 75)}"),
                ("Kelly fraction", config.get("kelly_fraction", 0.25)),
                ("Max single bet", pct(config.get("max_single_bet_pct", 0.2), 0)),
                ("Min edge", f"{config.get('min_edge_pp', 5)} pp"),
                ("Recommended exposure", f"${markets.get('total_recommended_exposure_usd', 0)}"),
            ],
            border=GOOD,
        )
        mid = kv_panel(
            "Model",
            [
                ("Type", model.get("kind", "—")),
                ("Inputs", model.get("architecture", {}).get("inputs", "—")),
                ("Log loss", cal.get("log_loss", "—")),
                ("Brier", cal.get("brier", "—")),
                ("Verdict", verdict_text(cal.get("verdict", "—"))),
            ],
            border=ACCENT,
        )
        right = kv_panel(
            "Tournament",
            [
                ("Favourite", favourite.get("team", "—")),
                ("Champion P", pct(favourite.get("p_champion", 0))),
                ("Simulations", f"{sim.get('n_sims', 0):,}"),
                ("Edges found", len(slate)),
                ("Actionable bets", Text(str(len(actionable)), style=GOOD if actionable else MUTED)),
            ],
            border=WARN,
        )

        columns = Table.grid(expand=True)
        columns.add_column(ratio=1)
        columns.add_column(ratio=1)
        columns.add_column(ratio=1)
        columns.add_row(left, mid, right)

        hint = Text("\n  Tabs: ←/→ or click · ", style=MUTED)
        hint.append("r", style=f"bold {ACCENT}")
        hint.append(" re-run pipeline · ", style=MUTED)
        hint.append("g", style=f"bold {ACCENT}")
        hint.append(" jump to trades · ", style=MUTED)
        hint.append("q", style=f"bold {ACCENT}")
        hint.append(" quit", style=MUTED)

        return Group(Panel(header, border_style=ACCENT, padding=(1, 2)), columns, self._mini_submarket_table(), hint)

    def _mini_submarket_table(self) -> Panel:
        submarkets = self.state.get("simulation", {}).get("submarkets", [])[:8]
        table = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}", title="[bold]Title contenders")
        table.add_column("Team", style="bold white")
        table.add_column("Champion", justify="right")
        table.add_column("")
        table.add_column("Final", justify="right")
        table.add_column("Advance", justify="right")
        max_champ = max((r.get("p_champion", 0) for r in submarkets), default=1.0) or 1.0
        for row in submarkets:
            table.add_row(
                row.get("team", "—"),
                pct(row.get("p_champion", 0)),
                bar(row.get("p_champion", 0), max_champ, width=20, color=ACCENT),
                pct(row.get("p_finalist", 0)),
                pct(row.get("p_advanced", 0)),
            )
        return Panel(table, border_style=ACCENT, padding=(0, 1))

    def _data(self) -> Group:
        data = self.state.get("data", {})
        info = kv_panel(
            "Dataset (martj42 international results)",
            [
                ("Source", source_tag(data.get("source", "—"))),
                ("Matches", f"{data.get('n_matches', 0):,}"),
                ("Teams", data.get("n_teams", "—")),
                ("Date range", f"{data.get('date_min', '—')} → {data.get('date_max', '—')}"),
                ("Cache path", data.get("path", "—")),
            ],
            border=ACCENT,
        )

        log_table = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}", title="[bold]Pipeline run log")
        log_table.add_column("Step", style="white")
        log_table.add_column("Status")
        log_table.add_column("Time", justify="right")
        log_table.add_column("Detail", style=MUTED)
        for entry in self.state.get("pipeline_log", []):
            status = entry.get("status", "")
            style = {"ok": GOOD, "warn": WARN, "error": BAD}.get(status, MUTED)
            log_table.add_row(
                entry.get("step", ""),
                Text(status, style=style),
                f"{entry.get('seconds', 0)}s",
                entry.get("detail", ""),
            )

        notes = Text()
        for note in self.state.get("notes", []):
            notes.append("• ", style=WARN)
            notes.append(note + "\n", style="white")

        return Group(info, Panel(log_table, border_style=ACCENT, padding=(0, 1)), Panel(notes, title="[bold]Honesty notes", border_style=WARN, padding=(1, 2)))

    def _elo(self) -> Panel:
        leaderboard = self.state.get("elo", {}).get("leaderboard", [])
        table = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}")
        table.add_column("#", justify="right", style=MUTED)
        table.add_column("Team", style="bold white")
        table.add_column("Elo", justify="right")
        table.add_column("Strength")
        if leaderboard:
            top = leaderboard[0]["rating"]
            floor = leaderboard[-1]["rating"]
            span = max(1.0, top - floor)
            for index, row in enumerate(leaderboard, start=1):
                rating = row["rating"]
                colour = GOOD if index <= 8 else ACCENT if index <= 24 else MUTED
                table.add_row(
                    str(index),
                    row["team"],
                    Text(f"{rating:.0f}", style="bold white"),
                    bar(rating - floor, span, width=34, color=colour),
                )
        return Panel(
            table,
            title="[bold]Elo Ratings — football-tuned, point-in-time",
            subtitle=f"{self.state.get('elo', {}).get('n_teams_rated', 0)} teams rated",
            border_style=ACCENT,
            padding=(0, 1),
        )

    def _model(self) -> Group:
        model = self.state.get("model", {})
        cal = model.get("calibration", {})
        before = cal.get("uncalibrated", {})
        after = cal.get("calibrated", {})

        compare = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}", title="[bold]Calibration effect (validation slice)")
        compare.add_column("Metric", style="white")
        compare.add_column("Raw model", justify="right")
        compare.add_column("Calibrated", justify="right")
        for label, key, lower_better in [("Log loss", "log_loss", True), ("Brier", "brier", True), ("Accuracy", "accuracy", False)]:
            b = before.get(key)
            a = after.get(key)
            style = MUTED
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                improved = (a < b) if lower_better else (a > b)
                style = GOOD if improved else WARN
            compare.add_row(label, str(b), Text(str(a), style=style))
        compare.add_row(
            "Verdict",
            verdict_text(before.get("verdict", "—")),
            verdict_text(after.get("verdict", "—")),
        )

        split = kv_panel(
            "Training split (chronological, no leakage)",
            [
                ("Train rows", f"{model.get('n_train', 0):,}"),
                ("Calibration rows", f"{model.get('n_calibration', 0):,}"),
                ("Validation rows", f"{model.get('n_validation', 0):,}"),
                ("Feature inputs", len(model.get("feature_columns", []))),
            ],
            border=GOOD,
        )

        method = cal.get("method", "none")
        raw_ll = before.get("log_loss", "—")
        diag = Text()
        diag.append("Calibration applied: ", style=MUTED)
        diag.append(str(method), style="bold white")
        diag.append("   (none / isotonic / temperature all fit; best on a time-forward holdout kept)\n", style=MUTED)
        diag.append("Skill: ", style=MUTED)
        diag.append(f"log loss {raw_ll}", style=f"bold {GOOD}")
        diag.append(" vs 1.099 uniform baseline", style="white")
        diag.append("  — real signal, but near the data-limited ceiling for international 1X2.\n", style=MUTED)
        diag.append("Verified plateau: hyperparameter search and sample-weighting both failed to beat it. ", style=MUTED)
        diag.append("Next gains live in real-draw realism + the scoreline model, not more tuning.", style=MUTED)

        bt = self.state.get("backtest", {})
        group_items = [
            architecture_diagram(model),
            split,
            Panel(compare, border_style=ACCENT, padding=(0, 1)),
        ]
        if bt.get("available"):
            bt_table = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}")
            bt_table.add_column("Out-of-sample slice", style="bold white")
            bt_table.add_column("Matches", justify="right")
            bt_table.add_column("Log loss", justify="right")
            bt_table.add_column("Brier", justify="right")
            bt_table.add_column("Accuracy", justify="right")
            for label, key in [("All internationals", "overall"), ("Competitive only", "competitive_only"), ("Major tournaments", "major_tournaments")]:
                row = bt.get(key, {})
                if not row.get("n"):
                    continue
                ll = row.get("log_loss", 0)
                bt_table.add_row(
                    label,
                    f"{row.get('n', 0):,}",
                    Text(str(ll), style=GOOD if ll < bt.get("uniform_log_loss", 1.099) else BAD),
                    str(row.get("brier", "—")),
                    pct(row.get("accuracy", 0), 1),
                )
            group_items.append(
                Panel(
                    bt_table,
                    title=f"[bold]Backtest — out-of-sample calibration ({bt.get('window', '')})",
                    subtitle=f"uniform baseline {bt.get('uniform_log_loss', 1.099)} log loss",
                    border_style=GOOD,
                    padding=(0, 1),
                )
            )
        group_items.append(Panel(diag, title="[bold]Diagnostics", border_style=GOOD, padding=(1, 2)))
        group_items.append(reliability_panel(model))
        return Group(*group_items)

    def _sim(self) -> Group:
        sim = self.state.get("simulation", {})
        groups = sim.get("groups", {})
        submarkets = sim.get("submarkets", [])

        draw = Table(expand=True, border_style=MUTED, header_style=f"bold {WARN}")
        draw.add_column("Grp", style=f"bold {WARN}")
        for i in range(4):
            draw.add_column(f"Seed {i + 1}", style="white")
        for letter, teams in groups.items():
            draw.add_row(letter, *[str(t) for t in teams])

        prob_table = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}")
        prob_table.add_column("Team", style="bold white")
        prob_table.add_column("Win grp", justify="right")
        prob_table.add_column("Advance", justify="right")
        prob_table.add_column("R16", justify="right")
        prob_table.add_column("QF", justify="right")
        prob_table.add_column("SF", justify="right")
        prob_table.add_column("Final", justify="right")
        prob_table.add_column("Champion", justify="right")
        prob_table.add_column("")
        max_champ = max((r.get("p_champion", 0) for r in submarkets), default=1.0) or 1.0
        for row in submarkets[:24]:
            prob_table.add_row(
                row.get("team", "—"),
                pct(row.get("p_win_group", 0), 0),
                pct(row.get("p_advanced", 0), 0),
                pct(row.get("p_last_16", 0), 0),
                pct(row.get("p_last_8", 0), 0),
                pct(row.get("p_last_4", 0), 0),
                pct(row.get("p_finalist", 0), 0),
                Text(pct(row.get("p_champion", 0)), style="bold white"),
                bar(row.get("p_champion", 0), max_champ, width=16, color=ACCENT),
            )

        caption = Text()
        caption.append(f"  {sim.get('n_sims', 0):,} Monte Carlo tournaments  ", style="white")
        caption.append("· draw is ", style=MUTED)
        caption += source_tag(sim.get("draw_label", "DEMO"))

        return Group(
            Panel(draw, title="[bold]2026 Group Draw", border_style=WARN, padding=(0, 1)),
            Panel(prob_table, title="[bold]Sub-market probabilities", subtitle="top 24 by champion P", border_style=ACCENT, padding=(0, 1)),
            bracket_panel(sim.get("sample_bracket", {}), title="One simulated tournament (a sample run)"),
            caption,
        )

    def _tracker(self) -> Group:
        tracker = self.state.get("tracker", {})
        preds = tracker.get("predictions", [])
        sc = tracker.get("scorecard", {})
        comp = sc.get("completed", {})
        oos = sc.get("out_of_sample", {})
        if not preds:
            return Group(Panel(Text("No tracker data yet — press r to run.", justify="center"), border_style=WARN, padding=(2, 4)))

        score = kv_panel(
            "Live model scorecard (all completed)",
            [
                ("Matches scored", comp.get("n", 0)),
                ("Pick accuracy", pct(comp.get("accuracy", 0), 0) if comp.get("n") else "—"),
                ("Log loss", Text(str(comp.get("log_loss", "—")), style=GOOD)),
                ("Brier", comp.get("brier", "—")),
                ("Uniform baseline", f"{sc.get('uniform_log_loss_baseline', '—')} log loss"),
            ],
            border=GOOD,
        )
        oos_panel = kv_panel(
            "Out-of-sample (kickoff after data cutoff)",
            [
                ("Matches", oos.get("n", 0)),
                ("Pick accuracy", pct(oos.get("accuracy", 0), 0) if oos.get("n") else "—"),
                ("Log loss", oos.get("log_loss", "—")),
                ("Scheduled ahead", sc.get("n_scheduled", 0)),
                ("Data cutoff", tracker.get("data_cutoff", "—")),
            ],
            border=WARN,
        )
        cols = Table.grid(expand=True)
        cols.add_column(ratio=1)
        cols.add_column(ratio=1)
        cols.add_row(score, oos_panel)

        def outcome_team(p: dict, code: str) -> str:
            return p["home"] if code == "H" else p["away"] if code == "A" else "Draw"

        def xg_text(p: dict) -> str:
            return f"{p.get('exp_home_goals', 0):.1f} – {p.get('exp_away_goals', 0):.1f}"

        # predictions arrive in chronological order from the tracker
        completed = [p for p in preds if p.get("status") == "completed"]
        res_table = Table(expand=True, show_lines=True, border_style=MUTED, header_style=f"bold {ACCENT}")
        res_table.add_column("Kickoff", style=MUTED)
        res_table.add_column("Grp", style=MUTED, justify="center")
        res_table.add_column("Match", style="bold white")
        res_table.add_column("Model P (H/D/A)")
        res_table.add_column("Pick")
        res_table.add_column("Actual")
        res_table.add_column("Score", justify="center")
        res_table.add_column("", justify="center")
        res_table.add_column("OOS", style=MUTED, justify="center")
        for p in completed:
            hit = p.get("correct")
            probs = f"{p['p_home']:.2f} / {p['p_draw']:.2f} / {p['p_away']:.2f}"
            res_table.add_row(
                p.get("kickoff", p["date"]),
                p["group"],
                f"{p['home']} v {p['away']}",
                probs,
                Text(outcome_team(p, p["pick"]), style="bold white"),
                Text(outcome_team(p, p.get("actual", "")), style=GOOD if hit else BAD),
                p.get("score", "—"),
                Text("✓" if hit else "✗", style=f"bold {GOOD}" if hit else f"bold {BAD}"),
                "•" if p.get("out_of_sample") else "",
            )
        if not completed:
            res_table.add_row("—", "", "no completed matches yet", "", "", "", "", "", "")

        upcoming = [p for p in preds if p.get("status") != "completed"]
        up_table = Table(expand=True, show_lines=True, border_style=MUTED, header_style=f"bold {ACCENT}")
        up_table.add_column("Kickoff", style=MUTED)
        up_table.add_column("Grp", style=MUTED, justify="center")
        up_table.add_column("Match", style="bold white")
        up_table.add_column("P (H/D/A)")
        up_table.add_column("Pick", style="bold white")
        up_table.add_column("Conf", justify="right")
        up_table.add_column("Exp goals", justify="center")
        up_table.add_column("Likely", justify="right")
        for p in upcoming[:32]:
            up_table.add_row(
                p.get("kickoff", ""),
                p["group"],
                f"{p['home']} v {p['away']}",
                f"{p['p_home']:.2f} / {p['p_draw']:.2f} / {p['p_away']:.2f}",
                Text(outcome_team(p, p["pick"]), style="bold white"),
                pct(p["pick_prob"], 0),
                Text(xg_text(p), style=ACCENT),
                p["likely_score"],
            )

        note = Text("\n  Out-of-sample (•) = kickoff after the 2026-06-12 data cutoff — a genuine forward test. ", style=MUTED)
        note.append("Host advantage isn't modelled yet (cf. USA 4-1 Paraguay).\n", style=MUTED)
        unmapped = tracker.get("unmapped_teams", [])
        if unmapped:
            note.append(f"  ⚠ Unmapped teams on base Elo: {', '.join(unmapped)}", style=BAD)
        else:
            note.append("  ✓ All 48 teams mapped to real Elo histories.", style=GOOD)

        return Group(
            cols,
            Panel(res_table, title="[bold]Results so far — prediction vs reality", border_style=GOOD, padding=(0, 1)),
            Panel(up_table, title="[bold]Upcoming predictions (next group matches)", border_style=ACCENT, padding=(0, 1)),
            note,
        )

    def _paper_account(self) -> Group:
        acct = self.state.get("paper_account", {})
        s = acct.get("summary", {})
        execution = self.state.get("execution", {})
        if not s:
            return Group(Panel(Text("No paper account yet — run the pipeline to start trading.", justify="center"), border_style=WARN, padding=(2, 4)))

        pnl = s.get("total_pnl", 0)
        pnl_style = GOOD if pnl >= 0 else BAD
        upnl = s.get("unrealized_pnl", 0)
        rpnl = s.get("realized_pnl", 0)
        left = kv_panel(
            "Account",
            [
                ("Starting bankroll", f"${s.get('starting_bankroll', 0)}"),
                ("Cash", f"${s.get('cash', 0)}"),
                ("Invested", f"${s.get('invested', 0)}"),
                ("Equity", Text(f"${s.get('equity', 0)}", style=f"bold {pnl_style}")),
            ],
            border=ACCENT,
        )
        mid = kv_panel(
            "P&L (paper, no real money)",
            [
                ("Unrealized", Text(f"${upnl}", style=GOOD if upnl >= 0 else BAD)),
                ("Realized", Text(f"${rpnl}", style=GOOD if rpnl >= 0 else BAD)),
                ("Total P&L", Text(f"${pnl}", style=f"bold {pnl_style}")),
                ("Return", Text(f"{s.get('total_return_pct', 0):+}%", style=f"bold {pnl_style}")),
            ],
            border=GOOD if pnl >= 0 else BAD,
        )
        ev = s.get("expected_value_usd", 0)
        expected = kv_panel(
            "Expected value (if model right)",
            [
                ("Expected profit", Text(f"${ev}", style=f"bold {GOOD if ev >= 0 else BAD}")),
                ("Expected ROI", Text(f"{s.get('expected_roi_pct', 0):+}%", style=f"bold {GOOD if ev >= 0 else BAD}")),
                ("Exp. settle equity", f"${s.get('expected_settle_equity', 0)}"),
                ("Max payout (all win)", f"${s.get('max_payout_usd', 0)}"),
                ("Avg edge", f"{s.get('avg_edge_pp', 0)}pp"),
            ],
            border=GOOD,
        )
        right = kv_panel(
            "Activity",
            [
                ("Open positions", s.get("n_open", 0)),
                ("Settled", s.get("n_settled", 0)),
                ("Win rate", f"{s.get('win_rate_pct')}%" if s.get("win_rate_pct") is not None else "—"),
                ("Total trades placed", acct.get("n_trades", 0)),
                ("Last run", str(acct.get("updated_at", ""))[:16].replace("T", " ")),
            ],
            border=WARN,
        )
        cols = Table.grid(expand=True)
        for _ in range(4):
            cols.add_column(ratio=1)
        cols.add_row(left, mid, expected, right)

        pos_table = Table(expand=True, show_lines=True, border_style=MUTED, header_style=f"bold {ACCENT}")
        pos_table.add_column("Settles", style=ACCENT)
        pos_table.add_column("Opened", style=MUTED)
        pos_table.add_column("Action", style="bold white")
        pos_table.add_column("Market", style=MUTED)
        pos_table.add_column("Team", style="bold white")
        pos_table.add_column("Entry", justify="right")
        pos_table.add_column("Now", justify="right")
        pos_table.add_column("Stake", justify="right")
        pos_table.add_column("EV", justify="right")
        pos_table.add_column("Unreal P&L", justify="right")

        def settle_str(p: dict) -> str:
            sd = p.get("settle_date")
            return str(sd)[:10] if sd else "tourn. end"

        # Timeline order: earliest settle date first.
        for p in sorted(acct.get("positions", []), key=lambda r: str(r.get("settle_date") or "9999")):
            up = p.get("unrealized_pnl", 0)
            pev = p.get("shares", 0) * p.get("model_prob", 0) - p.get("stake", 0)
            pos_table.add_row(
                settle_str(p),
                str(p.get("opened_at", ""))[5:16].replace("T", " "),
                Text(p.get("action", ""), style=GOOD if p.get("side") == "YES" else WARN),
                p.get("market", ""),
                p.get("team", ""),
                f"{p.get('entry_price', 0):.3f}",
                f"{p.get('current_price', 0):.3f}",
                f"${p.get('stake', 0)}",
                Text(f"${pev:+.2f}", style=GOOD if pev >= 0 else BAD),
                Text(f"${up:+.2f}", style=GOOD if up >= 0 else BAD),
            )
        if not acct.get("positions"):
            pos_table.add_row("—", "", "", "", "no open positions yet", "", "", "", "", "")

        history = acct.get("history", [])[-12:]
        hist_table = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}")
        hist_table.add_column("Settled", style=MUTED)
        hist_table.add_column("Action", style="white")
        hist_table.add_column("Team", style="bold white")
        hist_table.add_column("Entry", justify="right")
        hist_table.add_column("Result")
        hist_table.add_column("P&L", justify="right")
        for h in reversed(history):
            won = h.get("result") == "WON"
            hist_table.add_row(
                str(h.get("settled_at", ""))[5:16].replace("T", " "),
                h.get("action", ""),
                h.get("team", ""),
                f"{h.get('entry_price', 0):.3f}",
                Text(h.get("result", ""), style=GOOD if won else BAD),
                Text(f"${h.get('pnl', 0):+.2f}", style=GOOD if h.get("pnl", 0) >= 0 else BAD),
            )
        if not history:
            hist_table.add_row("—", "", "no settled trades yet", "", "", "")

        note = Text()
        note.append("  The bot finds edges each run, sizes them quarter-Kelly under bankroll caps, and ", style=MUTED)
        note.append("paper-executes", style=f"bold {GOOD}")
        note.append(" the actionable ones — holding positions and marking them to the live Polymarket mid each run.\n", style=MUTED)
        note.append("  No real money / wallet / signing. Live execution: ", style=MUTED)
        note.append(execution.get("mode", "DISABLED — manual only"), style=f"bold {WARN}")
        note.append(".  Champion/group markets settle at the tournament; until then P&L is mark-to-market.", style=MUTED)

        return Group(
            cols,
            Panel(pos_table, title="[bold]Open paper positions (marked to live market)", border_style=GOOD, padding=(0, 1)),
            Panel(hist_table, title="[bold]Settled trades", border_style=MUTED, padding=(0, 1)),
            Panel(note, border_style=MUTED, padding=(1, 2)),
        )

    def _markets(self) -> Group:
        markets = self.state.get("markets", {})
        slate = markets.get("slate", [])

        slate_table = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}")
        slate_table.add_column("#", style=MUTED, justify="right")
        slate_table.add_column("Action", style="bold white")
        slate_table.add_column("Market", style=MUTED)
        slate_table.add_column("Team", style="bold white")
        slate_table.add_column("Model", justify="right")
        slate_table.add_column("Price", justify="right")
        slate_table.add_column("Edge", justify="right")
        slate_table.add_column("Bet", justify="right")
        slate_table.add_column("Risk")
        slate_table.add_column("Status")
        for row in slate[:24]:
            actionable = row.get("actionable")
            bet = row.get("kelly_size_usd", 0)
            side = row.get("side", "YES")
            action_style = GOOD if side == "YES" else WARN
            risk = row.get("risk_label", "")
            risk_style = GOOD if risk == "high_conviction" else ACCENT if risk == "standard" else MUTED
            slate_table.add_row(
                str(row.get("rank", "")),
                Text(row.get("action", "BUY " + side), style=f"bold {action_style}"),
                row.get("market", ""),
                row.get("team", ""),
                pct(row.get("model_prob", 0)),
                f"{row.get('exec_price', 0):.3f}",
                Text(f"{row.get('edge_pp', 0):+.1f}pp", style=GOOD if actionable else MUTED),
                Text(f"${bet}", style=f"bold {GOOD}" if actionable else MUTED),
                Text(risk, style=risk_style),
                Text(row.get("status", ""), style=GOOD if actionable else MUTED),
            )
        if not slate:
            slate_table.add_row("—", "", "", "no edges ≥ threshold", "", "", "", "", "", "")

        flags = markets.get("scanner_flags", [])
        flag_table = Table(expand=True, border_style=MUTED, header_style=f"bold {WARN}")
        flag_table.add_column("Group", style="bold white")
        flag_table.add_column("Σ implied", justify="right")
        flag_table.add_column("Gap", justify="right")
        flag_table.add_column("Direction")
        flag_table.add_column("Markets", justify="right")
        for flag in flags:
            flag_table.add_row(
                flag.get("group", ""),
                f"{flag.get('sum_implied', 0):.3f}",
                Text(f"{flag.get('gap_pp', 0):+.1f}pp", style=WARN),
                flag.get("direction", ""),
                str(flag.get("markets", 0)),
            )
        if not flags:
            flag_table.add_row("—", "coherent within buffer", "", "", "")

        comparison = markets.get("comparison", [])
        panels = [
            Panel(slate_table, title="[bold]Ranked Betting Slate (manual execution only)", border_style=GOOD, padding=(0, 1)),
        ]
        if comparison:
            scale = min(0.7, max([c.get("model_prob") or 0 for c in comparison] + [c.get("market_ask") or 0 for c in comparison] + [0.3]))
            ranked = sorted(comparison, key=lambda c: abs(c.get("edge_pp") or 0), reverse=True)
            cmp_table = Table(expand=True, show_lines=True, border_style=MUTED, header_style=f"bold {ACCENT}")
            cmp_table.add_column("Market", style=MUTED)
            cmp_table.add_column("Team", style="bold white")
            cmp_table.add_column("Model P", justify="right")
            cmp_table.add_column("Mkt ask", justify="right")
            cmp_table.add_column("Edge", justify="right")
            cmp_table.add_column("model vs market")
            for row in ranked[:20]:
                mp = row.get("model_prob")
                ask = row.get("market_ask")
                edge = row.get("edge_pp")
                edge_style = GOOD if (edge or 0) >= 5 else BAD if (edge or 0) <= -5 else MUTED
                line = Text()
                line.append(bar(mp or 0, scale, width=14, color=ACCENT))
                line.append(" m ")
                line.append(bar(ask or 0, scale, width=14, color=WARN))
                line.append(" mkt")
                cmp_table.add_row(
                    row.get("market", ""),
                    row.get("team", ""),
                    pct(mp) if mp is not None else "—",
                    f"{ask:.3f}" if ask is not None else "—",
                    Text(f"{edge:+.1f}pp" if edge is not None else "—", style=edge_style),
                    line,
                )
            panels.append(
                Panel(cmp_table, title="[bold]Model vs live Polymarket — biggest disagreements (champion + group winners)", border_style=ACCENT, padding=(0, 1))
            )

        panels.append(
            Panel(flag_table, title="[bold]Consistency Scanner", border_style=WARN, padding=(0, 1))
        )

        rec = markets.get("recommendation_summary", {})
        execution = self.state.get("execution", {})
        if rec:
            meter = Text()
            meter.append(f"  Recommendations: {rec.get('count', 0)}   ", style="bold white")
            meter.append(f"actionable {rec.get('actionable_count', 0)} · watchlist {rec.get('watchlist_count', 0)}   ", style=MUTED)
            sides = rec.get("side_counts", {})
            meter.append(f"(BUY YES {sides.get('YES', 0)} / BUY NO {sides.get('NO', 0)})\n\n", style=MUTED)
            cap = rec.get("exposure_cap_usd", 1) or 1
            meter.append("  Exposure  ", style=MUTED)
            meter.append(bar(rec.get("total_projected_exposure_usd", 0), cap, width=26, color=ACCENT))
            meter.append(f"  ${rec.get('total_projected_exposure_usd', 0)} / ${rec.get('exposure_cap_usd', 0)} cap  ", style="white")
            meter.append(f"(${rec.get('exposure_cap_remaining_usd', 0)} left)\n", style=MUTED)
            meter.append("  Live execution: ", style=MUTED)
            meter.append(execution.get("mode", "DISABLED — manual only"), style=f"bold {WARN}")
            panels.append(
                Panel(meter, title="[bold]Recommendation exposure + execution gate (paper account in its own tab)", border_style=GOOD, padding=(1, 2))
            )

        note = Text()
        note.append("Prices are ", style=MUTED)
        note += source_tag(markets.get("source", "SAMPLE"))
        note.append("  " + markets.get("note", "") + "\n", style=MUTED)
        note.append("⚠ Read the edges critically: ", style=f"bold {WARN}")
        note.append(
            "the model systematically gives non-favourites large 'edges' to WIN THEIR GROUP vs the sharp market "
            "(e.g. Australia 28% vs 5%). That pattern is sim miscalibration — the group stage is simulated from the "
            "goal model (weaker at separating teams) and doesn't yet apply host advantage — not true edge.\n",
            style=MUTED,
        )
        note.append("Champion-market edges (top teams vs the market) are the credible ones. ", style="white")
        note.append("Bet = fractional-Kelly after caps + $5 min-fill; most edges are below it on a $75 bankroll.", style=MUTED)
        unmatched = markets.get("unmatched_teams", [])
        if unmatched:
            note.append(f"\nUnmatched market teams: {', '.join(sorted(set(unmatched)))}", style=MUTED)
        panels.append(Panel(note, border_style=WARN, padding=(1, 2)))

        return Group(*panels)

    # ------------------------------------------------------------------ live panels
    def _live_status_panel(self) -> Panel:
        stage = self._live_stage
        elapsed = (time.perf_counter() - self._run_t0) if self._run_t0 else 0.0
        if stage == "complete":
            current = len(LIVE_STAGES)
        elif stage in LIVE_STAGES:
            current = LIVE_STAGES.index(stage)
        else:
            current = -1

        header = Text()
        if stage == "idle":
            header.append("Idle — press ", style=MUTED)
            header.append("r", style=f"bold {ACCENT}")
            header.append(" to run the whole pipeline live and watch it here.", style=MUTED)
        elif stage == "complete":
            header.append("✓ run complete   ", style=f"bold {GOOD}")
            header.append(f"{elapsed:.1f}s total", style="white")
        else:
            header.append("● LIVE   ", style=f"bold {ACCENT}")
            header.append(f"{elapsed:.1f}s elapsed", style="white")

        checklist = Text()
        for index, name in enumerate(LIVE_STAGES):
            short = STAGE_SHORT[name]
            if stage == "complete" or index < current:
                checklist.append("  ✓ ", style=GOOD)
                checklist.append(short + "\n", style=MUTED)
            elif index == current:
                checklist.append("  ▶ ", style=f"bold {ACCENT}")
                checklist.append(short + "   …running\n", style="bold white")
            else:
                checklist.append("  ○ ", style=MUTED)
                checklist.append(short + "\n", style=MUTED)

        elo_line = Text()
        if self._live_elo:
            elo_line.append("\n  Elo locked — top: ", style=MUTED)
            elo_line.append(
                " · ".join(f"{row['team']} {row['rating']:.0f}" for row in self._live_elo[:5]),
                style="white",
            )
        return Panel(Group(header, Text(""), checklist, elo_line), title="[bold]Pipeline", border_style=ACCENT, padding=(1, 2))

    def _live_train_panel(self) -> Panel:
        if not self._live_curve and self._live_train is None:
            return Panel(
                Text("waiting for the model to start training…", style=MUTED),
                title="[bold]Model training",
                border_style=MUTED,
                padding=(1, 2),
            )
        iteration, max_iter, _ = self._live_train if self._live_train else (0, 1, 0.0)
        last = self._live_curve[-1] if self._live_curve else float("nan")
        first = self._live_curve[0] if self._live_curve else last

        body = Text()
        body.append("  trees ", style=MUTED)
        body.append(f"{iteration}/{max_iter}", style="bold white")
        body.append("    validation log loss ", style=MUTED)
        body.append(f"{last:.4f}", style=f"bold {GOOD}")
        if len(self._live_curve) > 1:
            body.append(f"   (↓ {first - last:+.4f} since start)", style=MUTED)
        body.append("\n\n  ")
        body.append(sparkline(self._live_curve, width=62, color=GOOD))
        body.append("\n\n  ")
        body.append(bar(iteration, max_iter or 1, width=46, color=ACCENT))
        body.append(f"  {int(100 * iteration / max_iter) if max_iter else 0}%", style=MUTED)
        return Panel(
            body,
            title="[bold]Model training — watch validation log loss fall (lower = better)",
            border_style=GOOD,
            padding=(1, 2),
        )

    def _live_sim_panel(self) -> Panel:
        if self._live_sim is None:
            return Panel(
                Text("waiting for the Monte Carlo to start…", style=MUTED),
                title="[bold]Monte Carlo — live convergence",
                border_style=MUTED,
                padding=(1, 2),
            )
        done, total, rows = self._live_sim
        elapsed = (time.perf_counter() - self._sim_t0) if self._sim_t0 else 0.0
        rate = done / elapsed if elapsed > 0 else 0.0
        eta = (total - done) / rate if rate > 0 else 0.0
        movement_pp = (self._sim_movement[-1] * 100) if self._sim_movement else 1.0
        converged = movement_pp < 0.04 and done > total * 0.3

        header = Text()
        header.append("  ")
        header.append(bar(done, total, width=46, color=ACCENT))
        header.append(f"  {done:,}/{total:,}\n", style="bold white")
        header.append(f"  {rate:,.0f} sims/s", style=GOOD)
        header.append(f"   ETA {eta:.0f}s", style=MUTED)
        header.append("   movement ", style=MUTED)
        header.append(f"±{movement_pp:.3f}pp ", style=GOOD if converged else WARN)
        header.append(sparkline(self._sim_movement, width=22, color=WARN))
        header.append("  ✓ converged" if converged else "  (→0 = settled)", style=GOOD if converged else MUTED)

        def cell(prob: float, traj: list[float]) -> Text:
            text = Text(f"{pct(prob, 1):>6} ", style="bold white")
            text.append(sparkline(traj, width=22, color=ACCENT))
            return text

        table = Table(expand=True, border_style=MUTED, header_style=f"bold {ACCENT}")
        table.add_column("Team", style="bold white")
        table.add_column("Champion (shaping over time)", ratio=1)
        table.add_column("Reach final", ratio=1)
        table.add_column("Advance group", ratio=1)
        for row in rows[:10]:
            team = row["team"]
            table.add_row(
                team,
                cell(row["champ"], self._sim_traj.get(team, [])),
                cell(row["final"], self._sim_traj_final.get(team, [])),
                cell(row["advance"], self._sim_traj_adv.get(team, [])),
            )

        caption = Text(
            f"\n  {total:,} tournaments — champion, reach-final and advancement probabilities all traced live; "
            "the movement meter shrinks to zero as the estimates settle.",
            style=MUTED,
        )
        return Panel(
            Group(header, Text(""), table, caption),
            title="[bold]Monte Carlo — live convergence (champion · final · advance)",
            border_style=ACCENT,
            padding=(1, 2),
        )


def render_snapshot(state_path: Path = STATE_PATH, width: int = 120) -> None:
    """Print every dashboard panel once and exit — no alternate screen, no interactivity.

    Works through any pipe/non-TTY (unlike the live TUI), so it can be shown anywhere.
    """
    from rich.console import Console

    app = DashboardApp(state_path=state_path)
    console = Console(width=width)
    if not app.state:
        console.print("No pipeline state yet — run:  python -m pipeline.orchestrator")
        return
    sections = [
        ("OVERVIEW", app._overview),
        ("DATA & PIPELINE", app._data),
        ("ELO RATINGS", app._elo),
        ("MODEL & CALIBRATION", app._model),
        ("SIMULATION", app._sim),
        ("LIVE TRACKER", app._tracker),
        ("TRADES & MARKETS", app._markets),
        ("PAPER ACCOUNT", app._paper_account),
    ]
    for title, builder in sections:
        console.rule(f"[bold cyan]{title}")
        console.print(builder())
        console.print()


def main() -> None:
    import sys

    if "--snapshot" in sys.argv:
        render_snapshot()
        return
    DashboardApp().run()


if __name__ == "__main__":
    main()
