#!/usr/bin/env python
"""Improver agent — uses OpenAI to propose concrete upgrades to the World Cup model/site.

Each cycle it summarises the current live metrics (model calibration, tracker accuracy,
edges, paper P&L, ops health) plus a short digest of the codebase, asks the model for a
ranked list of specific, high-leverage improvements, and appends them to a reviewable
backlog: data/processed/improvement_proposals.json + IMPROVEMENT_LOG.md.

It is a PROPOSER, not an auto-committer: it never edits code, never deploys, never trades.
Running an autonomous LLM that rewrites a live service next to a money bot is a foot-gun;
a human reviews the backlog and decides. (Want auto-apply later? That's a separate, gated
opt-in we can design with tests + a staging check.)

    python improver.py            # loop (interval from config; default 6h)
    python improver.py --once     # one proposal pass (smoke test)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from agents import llm  # noqa: E402
from pipeline.run_live import load_config  # noqa: E402

STATE = ROOT / "data" / "processed" / "dashboard_state.json"
OPS = ROOT / "data" / "processed" / "ops_report.json"
PROPOSALS = ROOT / "data" / "processed" / "improvement_proposals.json"
IMPL_LOG = ROOT / "data" / "processed" / "improvements_log.json"   # written by implementer.py
LOGMD = ROOT / "IMPROVEMENT_LOG.md"

# A compact digest of the system so the model proposes relevant, grounded changes.
SYSTEM_DIGEST = """\
World Cup 2026 edge model. Pipeline: international results -> football Elo (point-in-time) ->
features -> calibrated gradient-boosted 1X2 model -> Poisson/Dixon-Coles goal model ->
Monte Carlo on the official draw -> map sim probs to live Polymarket CLOB -> executable-price
edge detection + quarter-Kelly sizing + correlation-aware portfolio -> persistent paper account.
A live engine refreshes prices/results/sim continuously; a tracker scores frozen pre-match
predictions vs real results. Live results feed: TheSportsDB (season + per-day endpoints).
Priorities: calibration > honest executable-price edges > bankroll protection."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def metrics_snapshot() -> dict:
    s = _load(STATE)
    m = s.get("model", {}); cal = m.get("calibration", {})
    tr = s.get("tracker", {}).get("scorecard", {})
    mk = s.get("markets", {}); rs = mk.get("recommendation_summary", {})
    pa = s.get("paper_account", {}).get("summary", {})
    ops = _load(OPS)
    return {
        "model_kind": m.get("kind"),
        "calibrated_log_loss": cal.get("calibrated"),
        "uncalibrated_log_loss": cal.get("uncalibrated"),
        "calibration_method": cal.get("method"),
        "top_features": [f.get("feature") for f in (m.get("feature_importance") or [])[:8]],
        "tracker_completed": tr.get("completed", {}),
        "tracker_oos": tr.get("out_of_sample", {}),
        "edges_found": mk.get("edges_found"),
        "actionable": rs.get("actionable_count"),
        "markets_source": mk.get("source"),
        "paper": {k: pa.get(k) for k in ("equity", "total_pnl", "expected_value_usd", "n_open", "n_settled")},
        "ops_status": ops.get("status"),
        "ops_issues": ops.get("issues", []),
    }


def implementation_history() -> dict:
    """What the implementer has already done — so we don't repeat and can react to failures."""
    log = _load(IMPL_LOG)
    done, reverted = [], []
    for e in log.get("entries", []):
        if e.get("status") == "implemented":
            done.append(e.get("title"))
        elif e.get("status") in ("reverted", "failed", "skipped"):
            reverted.append({"title": e.get("title"), "why": e.get("reason")})
    return {"already_implemented": done[-20:], "tried_and_not_landed": reverted[-12:]}


def propose(snapshot: dict, history: dict, model: str | None, n: int) -> list[dict] | None:
    if not llm.have_key():
        return None
    prompt = (
        f"{SYSTEM_DIGEST}\n\nCURRENT LIVE METRICS:\n{json.dumps(snapshot, indent=2)}\n\n"
        f"IMPLEMENTATION HISTORY (an implementer agent applies your proposals automatically):\n"
        f"{json.dumps(history, indent=2)}\n\n"
        f"Propose the {n} highest-leverage, concrete improvements (model calibration, features, "
        "goal model, edge detection/sizing, simulation, data feeds, or ops). Do NOT repeat anything "
        "in 'already_implemented'. If something is in 'tried_and_not_landed', either propose a "
        "different, simpler angle or move on. Each: a short title, why it matters given the metrics, "
        "and a specific first implementation step scoped to ONE source file where possible. "
        "Prefer ideas that improve calibration or executable-edge honesty over flashy additions. "
        'Return STRICT JSON: {"proposals":[{"title":"","area":"","rationale":"","first_step":"","impact":"high|med|low"}]}'
    )
    out = llm.chat(
        [{"role": "system", "content": "You are a sharp quantitative ML engineer. Return only valid JSON."},
         {"role": "user", "content": prompt}],
        model=model, max_tokens=900, temperature=0.5,
    )
    if not out:
        return None
    try:
        if out.startswith("```"):
            out = out.strip("`")
            out = out[out.find("{"):out.rfind("}") + 1]
        return (json.loads(out) or {}).get("proposals") or None
    except (json.JSONDecodeError, ValueError):
        return [{"title": "Model returned unparseable JSON", "area": "ops",
                 "rationale": out[:300], "first_step": "review raw output", "impact": "low"}]


def record(proposals: list[dict], snapshot: dict) -> None:
    history = _load(PROPOSALS) if PROPOSALS.exists() else {}
    entries = history.get("entries", []) if isinstance(history, dict) else []
    entries.append({"at": _now(), "metrics": snapshot, "proposals": proposals})
    entries = entries[-40:]
    try:
        PROPOSALS.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROPOSALS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"updated_at": _now(), "entries": entries}, indent=2), encoding="utf-8")
        tmp.replace(PROPOSALS)
    except OSError:
        pass
    lines = [f"\n## Improver proposals — {_now()}\n"]
    for p in proposals:
        lines.append(f"- **{p.get('title','?')}** _({p.get('area','')}, impact {p.get('impact','?')})_ — "
                     f"{p.get('rationale','')} → _first step:_ {p.get('first_step','')}")
    try:
        with LOGMD.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def run_once(model: str | None, n: int) -> None:
    snap = metrics_snapshot()
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if not llm.have_key():
        print(f"[{stamp}] improver: no OPENAI_API_KEY found (.env or env) — skipping LLM pass.", flush=True)
        return
    proposals = propose(snap, implementation_history(), model, n)
    if not proposals:
        print(f"[{stamp}] improver: no proposals returned (LLM error?).", flush=True)
        return
    record(proposals, snap)
    print(f"[{stamp}] improver: recorded {len(proposals)} proposal(s):", flush=True)
    for p in proposals:
        print(f"    • [{p.get('impact','?'):>4}] {p.get('title','?')} ({p.get('area','')})", flush=True)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="World Cup improver agent.")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=None)
    ap.add_argument("--n", type=int, default=5, help="proposals per pass")
    args = ap.parse_args()

    config = load_config(ROOT / "config.yaml")
    llm.load_env(ROOT / ".env")
    interval = args.interval or int(config.get("improver_interval_sec", 21600))
    model = config.get("improver_model") or None

    print(f"improver: every {interval}s, llm={'on' if llm.have_key() else 'OFF (set OPENAI_API_KEY)'}. "
          "Proposes only — never edits/deploys. Ctrl+C to stop.", flush=True)
    while True:
        try:
            run_once(model, args.n)
        except KeyboardInterrupt:
            break
        except Exception as exc:  # noqa: BLE001
            print(f"improver cycle error: {type(exc).__name__}: {exc}", flush=True)
        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
