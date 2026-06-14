#!/usr/bin/env python
"""Run the always-on World Cup live engine with a live terminal dashboard.

One process, one writer. It trains the model once, then continuously refreshes every
section -- live edges + paper account (~60s), live results + tracker + Elo (~90s),
championship probabilities re-simulated on every new result, and a full model retrain
every ~6h. It writes data/processed/dashboard_state.json atomically, which the web app
(web_app.py) serves to browsers in real time over SSE.

    python live_engine.py                 # live terminal UI (rich), runs forever
    python live_engine.py --once          # single pass of every job, then exit (smoke test)
    python live_engine.py --no-ui         # plain line logging (use this under systemd)
    python live_engine.py --sims 100000   # Monte Carlo iterations per re-sim

Cadences come from config.yaml (engine_*_interval_sec) and can be overridden with flags.
Ctrl+C to stop.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from pipeline.live_engine import LiveEngine  # noqa: E402
from pipeline.run_live import load_config  # noqa: E402

ACCENT = "#3ddc84"
BLUE = "#5aa9ff"
DIM = "grey58"


def _money(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    return ("-$" if x < 0 else "$") + f"{abs(x):,.2f}"


def _pct(x) -> str:
    try:
        return f"{float(x) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _sign(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "white"
    return ACCENT if x > 0 else ("red" if x < 0 else "white")


def _fmt_secs(s: int) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


# --------------------------------------------------------------------------------------
# rich UI
# --------------------------------------------------------------------------------------

def build_renderer():
    from rich.align import Align
    from rich.console import Group
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    def header(engine) -> Panel:
        eng = engine.state.get("engine", {})
        up = _fmt_secs(eng.get("uptime_sec", 0))
        gen = engine.state.get("generated_at", "—").replace("T", " ").replace("Z", " UTC")
        sim_n = eng.get("sim_n", engine.sim_n)
        t = Text()
        t.append("●", style=f"bold {ACCENT}")
        t.append("  WORLD CUP 2026 — LIVE EDGE ENGINE", style="bold white")
        t.append(f"    uptime {up}", style=DIM)
        t.append(f"   ·   {sim_n:,} sims/refresh", style=DIM)
        t.append(f"   ·   updated {gen}", style=DIM)
        return Panel(Align.center(t), style=ACCENT, padding=(0, 1))

    def account_panel(engine) -> Panel:
        a = engine.state.get("paper_account", {})
        sm = a.get("summary", {})
        g = Table.grid(expand=True, padding=(0, 1))
        g.add_column(justify="left")
        g.add_column(justify="right")

        def kv(k, v, style="white"):
            g.add_row(Text(k, style=DIM), Text(v, style=style))

        kv("Equity", _money(sm.get("equity")), "bold white")
        kv("Cash", _money(sm.get("cash")))
        kv("Invested", _money(sm.get("invested")))
        kv("Total P&L", _money(sm.get("total_pnl")), f"bold {_sign(sm.get('total_pnl'))}")
        kv("Expected value", _money(sm.get("expected_value_usd")), _sign(sm.get("expected_value_usd")))
        roi = sm.get("expected_roi_pct")
        kv("Expected ROI", f"{roi:+.1f}%" if isinstance(roi, (int, float)) else "—", _sign(roi))
        kv("Open / settled", f"{sm.get('n_open', 0)} / {sm.get('n_settled', 0)}")
        return Panel(g, title="[bold]Paper account[/]", border_style=ACCENT, padding=(1, 1))

    def trades_panel(engine) -> Panel:
        m = engine.state.get("markets", {})
        slate = sorted(m.get("slate", []), key=lambda r: (not r.get("actionable"), -(r.get("edge_pp") or 0)))
        tbl = Table.grid(expand=True, padding=(0, 1))
        tbl.add_column("t", ratio=4)
        tbl.add_column("e", justify="right", ratio=2)
        tbl.add_column("p", justify="right", ratio=2)
        tbl.add_column("s", justify="right", ratio=2)
        if not slate:
            return Panel(Text("no edges yet", style=DIM), title="[bold]Live edges[/]", border_style=BLUE)
        for r in slate[:9]:
            act = "●" if r.get("actionable") else "○"
            side = str(r.get("side", "YES"))
            col = ACCENT if side == "YES" else "#ffb454"
            name = Text()
            name.append(f"{act} ", style=ACCENT if r.get("actionable") else DIM)
            name.append(str(r.get("team", ""))[:18], style="white")
            name.append(f" {side}", style=col)
            tbl.add_row(
                name,
                Text(f"{(r.get('edge_pp') or 0):.1f}pp", style="bold white"),
                Text(f"{(r.get('exec_price') or 0):.3f}", style=DIM),
                Text(_money(r.get("kelly_size_usd") or r.get("capped_size_usd")), style=DIM),
            )
        src = m.get("source", "?")
        return Panel(tbl, title=f"[bold]Live edges[/] [{DIM}]· {src}[/]", border_style=BLUE, padding=(1, 1))

    def probs_panel(engine) -> Panel:
        s = engine.state.get("simulation", {})
        subs = sorted(s.get("submarkets", []), key=lambda r: -(r.get("p_champion") or 0))[:10]
        tbl = Table.grid(expand=True, padding=(0, 1))
        tbl.add_column("team", ratio=5)
        tbl.add_column("champ", justify="right", ratio=2)
        tbl.add_column("final", justify="right", ratio=2)
        tbl.add_column("adv", justify="right", ratio=2)
        if not subs:
            return Panel(Text("simulating…", style=DIM), title="[bold]Championship odds[/]", border_style=ACCENT)
        mx = subs[0].get("p_champion") or 1
        for r in subs:
            frac = (r.get("p_champion") or 0) / (mx or 1)
            blocks = "█" * max(1, round(frac * 8))
            name = Text()
            name.append(blocks + " ", style=ACCENT)
            name.append(str(r.get("team", ""))[:16], style="white")
            tbl.add_row(
                name,
                Text(_pct(r.get("p_champion")), style="bold white"),
                Text(_pct(r.get("p_finalist")), style=DIM),
                Text(_pct(r.get("p_advanced")), style=DIM),
            )
        return Panel(tbl, title="[bold]Championship odds[/]", border_style=ACCENT, padding=(1, 1))

    def portfolio_panel(engine) -> Panel:
        p = engine.state.get("portfolio", {})
        if not p.get("available"):
            return Panel(Text(p.get("reason", "not available"), style=DIM),
                         title="[bold]Optimised book[/]", border_style=BLUE)
        st = (p.get("recommended", {}) or {}).get("stats", {})
        g = Table.grid(expand=True, padding=(0, 1))
        g.add_column(justify="left")
        g.add_column(justify="right")
        g.add_row(Text("Log-growth", style=DIM), Text(f"{st.get('exp_log_growth_pct', 0):.2f}%", style=_sign(st.get("exp_log_growth_pct"))))
        g.add_row(Text("Exp return", style=DIM), Text(f"{st.get('exp_return_pct', 0):.1f}%", style=_sign(st.get("exp_return_pct"))))
        g.add_row(Text("Prob. loss", style=DIM), Text(f"{(st.get('prob_loss', 0) * 100):.0f}%", style="white"))
        g.add_row(Text("Eff. bets", style=DIM), Text(f"{st.get('effective_bets', 0):.1f}", style="white"))
        return Panel(g, title="[bold]Optimised book[/]", border_style=BLUE, padding=(1, 1))

    def tracker_panel(engine) -> Panel:
        t = engine.state.get("tracker", {})
        live = t.get("live", {})
        preds = t.get("predictions", [])
        sc = t.get("scorecard", {}).get("completed", {})
        items = []
        head = Text()
        head.append(f"{sc.get('n', 0)} scored", style="white")
        if sc.get("accuracy") is not None:
            head.append(f"  ·  acc {_pct(sc.get('accuracy'))}", style=DIM)
        if sc.get("log_loss") is not None:
            head.append(f"  ·  ll {sc.get('log_loss')}", style=DIM)
        items.append(head)

        in_play = live.get("in_play", [])
        if in_play:
            for line in in_play[:3]:
                items.append(Text(f"  ● LIVE  {line}", style="bold #ffb454"))

        completed = [p for p in preds if p.get("status") == "completed"]
        completed = sorted(completed, key=lambda r: r.get("date", ""), reverse=True)[:5]
        for p in completed:
            ok = p.get("correct")
            glyph = "✓" if ok else "✗"
            line = Text()
            line.append(f"  {glyph} ", style=ACCENT if ok else "red")
            line.append(f"{p.get('home','')} {p.get('score','')} {p.get('away','')}"[:34], style="white")
            items.append(line)

        upcoming = [p for p in preds if p.get("status") != "completed"]
        upcoming = sorted(upcoming, key=lambda r: (r.get("date", ""), r.get("time", "")))[:3]
        if upcoming:
            items.append(Text("  up next", style=DIM))
            for p in upcoming:
                line = Text()
                line.append(f"  {p.get('home','')} v {p.get('away','')}"[:26], style="white")
                line.append(f"  {_pct(p.get('pick_prob'))}", style=DIM)
                items.append(line)
        return Panel(Group(*items), title="[bold]Live tracker[/]", border_style=ACCENT, padding=(1, 1))

    def elo_panel(engine) -> Panel:
        lb = engine.state.get("elo", {}).get("leaderboard", [])[:10]
        tbl = Table.grid(expand=True, padding=(0, 1))
        tbl.add_column("r", justify="right", ratio=1)
        tbl.add_column("team", ratio=5)
        tbl.add_column("rating", justify="right", ratio=2)
        for i, row in enumerate(lb, 1):
            tbl.add_row(Text(str(i), style=DIM), Text(str(row.get("team", ""))[:16], style="white"),
                        Text(str(round(row.get("rating", 0))), style="bold white"))
        if not lb:
            return Panel(Text("—", style=DIM), title="[bold]Elo[/]", border_style=BLUE)
        return Panel(tbl, title="[bold]Elo top 10[/]", border_style=BLUE, padding=(1, 1))

    def jobs_panel(engine) -> Panel:
        tbl = Table(expand=True, box=None, padding=(0, 1))
        tbl.add_column("Job", style="white", ratio=2)
        tbl.add_column("Status", ratio=2)
        tbl.add_column("Last", ratio=6)
        tbl.add_column("Next", justify="right", ratio=2)
        tbl.add_column("Runs", justify="right", ratio=1)
        for name, job in engine.jobs.items():
            color = {"ok": ACCENT, "running": BLUE, "error": "red", "warn": "#ffb454"}.get(job.last_status, DIM)
            nxt = "now" if job.triggered else (_fmt_secs(max(0, int(job.next_due - time.monotonic()))) if job.last_run else "—")
            tbl.add_row(
                name,
                Text(job.last_status, style=color),
                Text(f"{job.last_detail}"[:48], style=DIM),
                Text(nxt, style=DIM),
                Text(f"{job.runs}" + (f"/{job.errors}✗" if job.errors else ""), style="red" if job.errors else DIM),
            )
        errs = engine.errors[-3:]
        if errs:
            body = Group(tbl, Text(""), *[Text(e[:110], style="red") for e in errs])
        else:
            body = tbl
        return Panel(body, title="[bold]Engine jobs[/]", border_style=DIM, padding=(0, 1))

    def render(engine):
        layout = Layout()
        layout.split_column(
            Layout(header(engine), size=3, name="head"),
            Layout(name="body"),
            Layout(jobs_panel(engine), size=8, name="foot"),
        )
        layout["body"].split_row(Layout(name="left"), Layout(name="mid"), Layout(name="right"))
        layout["left"].split_column(Layout(account_panel(engine)), Layout(trades_panel(engine)))
        layout["mid"].split_column(Layout(probs_panel(engine)), Layout(portfolio_panel(engine)))
        layout["right"].split_column(Layout(tracker_panel(engine)), Layout(elo_panel(engine)))
        return layout

    return render


def run_with_ui(engine: LiveEngine) -> None:
    from rich.console import Console
    from rich.live import Live

    console = Console()
    render = build_renderer()
    with Live(console=console, refresh_per_second=4, screen=True) as live:
        def listener(eng, event):
            live.update(render(eng))
        engine.listener = listener
        live.update(render(engine))
        engine.run()


def run_plain(engine: LiveEngine) -> None:
    def listener(eng, event):
        if not event.endswith(":done"):
            return
        name = event.split(":")[1]
        job = eng.jobs.get(name)
        if job is None:
            return
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{stamp}] {name:<9} {job.last_status:<7} {job.last_seconds:>5.1f}s  {job.last_detail}", flush=True)

    engine.listener = listener
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] live engine starting (plain log mode)…", flush=True)
    engine.run()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="World Cup 2026 always-on live engine.")
    ap.add_argument("--once", action="store_true", help="Single pass of every job, then exit")
    ap.add_argument("--no-ui", action="store_true", help="Plain line logging (use under systemd)")
    ap.add_argument("--sims", type=int, default=None, help="Monte Carlo iterations per re-sim")
    ap.add_argument("--history-years", type=int, default=12, help="Training window for the model")
    args = ap.parse_args()

    config = load_config(ROOT / "config.yaml")
    if args.sims:
        config["engine_sim_n"] = args.sims

    engine = LiveEngine(config, history_years=args.history_years)

    if args.once:
        print("Running one pass of every job (smoke test)…", flush=True)
        engine.run(once=True)
        eng = engine.state.get("engine", {})
        for name, job in eng.get("jobs", {}).items():
            print(f"  {name:<9} {job.get('status'):<7} {job.get('seconds'):>5}s  {job.get('detail')}")
        print(f"\nWrote {engine.state and 'dashboard_state.json'} · errors: {engine.errors or 'none'}")
        return

    use_ui = not args.no_ui and sys.stdout.isatty()
    if use_ui:
        try:
            run_with_ui(engine)
        except Exception:
            import traceback
            traceback.print_exc()
    else:
        run_plain(engine)


if __name__ == "__main__":
    main()
