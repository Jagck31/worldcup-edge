#!/usr/bin/env python
"""Live Polymarket price-watcher for the World Cup book.

Periodically re-fetches the live CLOB order books, re-detects edges at current prices, and
re-optimises the correlation-aware portfolio -- WITHOUT retraining the model or re-simulating
(model probabilities don't move intraday; only prices do). Writes the refreshed markets +
portfolio back into dashboard_state.json so the web dashboard shows live edges, and prints an
alert whenever a new actionable edge appears.

    python price_watcher.py --interval 180         # every 3 minutes
    python price_watcher.py --once                 # single pass (for testing)

Requires a prior full run (run_pipeline.py) to have produced dashboard_state.json and
data/processed/sim_outcomes.json (the cached simulated outcomes the portfolio re-uses).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from edge.portfolio import build_portfolio  # noqa: E402
from ingest.results import TeamNameNormalizer  # noqa: E402
from pipeline.orchestrator import (  # noqa: E402
    ALIASES_PATH,
    SIM_OUTCOMES_PATH,
    STATE_PATH,
    _build_live_markets,
    _slate_to_bets,
)
from pipeline.run_live import load_config  # noqa: E402


def _actionable_map(slate):
    return {(r.get("market_id"), r.get("side")): r for r in slate if r.get("actionable")}


def run_once(config, normalizer, samples, prev_slate):
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    sim = state.get("simulation", {})
    submarkets = pd.DataFrame(sim.get("submarkets", []))
    stamp = datetime.now().strftime("%H:%M:%S")
    if submarkets.empty:
        print(f"[{stamp}] no submarkets in state; run run_pipeline.py first")
        return prev_slate

    live = _build_live_markets(submarkets, normalizer, config)
    if not live:
        print(f"[{stamp}] live fetch returned nothing (feed down?); keeping previous state")
        return prev_slate
    state["markets"] = live

    bankroll = float(config.get("bankroll_usd", 200))
    team_to_group = {t: g for g, teams in sim.get("groups", {}).items() for t in teams}
    bets = _slate_to_bets(live.get("slate", []), team_to_group, bankroll)
    if bets and samples:
        state["portfolio"] = build_portfolio(bets, samples, bankroll_usd=bankroll)

    state["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")

    new_slate = live.get("slate", [])
    prev, now = _actionable_map(prev_slate), _actionable_map(new_slate)
    fresh = [r for k, r in now.items() if k not in prev]
    if fresh:
        for r in fresh:
            print(f"[{stamp}] NEW EDGE: {r.get('action')} {r.get('team')} @ {r.get('exec_price')} "
                  f"({r.get('edge_pp')}pp, {r.get('risk_label', '')})")
    else:
        print(f"[{stamp}] refreshed: {len(new_slate)} edges, {len(now)} actionable, no new edges")
    return new_slate


def main():
    ap = argparse.ArgumentParser(description="Live Polymarket price-watcher.")
    ap.add_argument("--interval", type=int, default=180, help="Seconds between refreshes")
    ap.add_argument("--once", action="store_true", help="Single pass then exit")
    args = ap.parse_args()

    config = load_config(ROOT / "config.yaml")
    normalizer = TeamNameNormalizer.from_yaml(ALIASES_PATH)
    samples = json.loads(SIM_OUTCOMES_PATH.read_text(encoding="utf-8")) if SIM_OUTCOMES_PATH.exists() else []
    if not samples:
        print("WARNING: no cached sim outcomes (data/processed/sim_outcomes.json). "
              "Portfolio won't re-optimise until you run run_pipeline.py once.")

    print(f"price-watcher: refreshing every {args.interval}s. Ctrl+C to stop.")
    prev_slate = []
    while True:
        try:
            prev_slate = run_once(config, normalizer, samples, prev_slate)
        except Exception as exc:  # never let one bad cycle kill the watcher
            print(f"watch cycle error: {type(exc).__name__}: {exc}")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
