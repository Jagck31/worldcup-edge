#!/usr/bin/env python
"""Live World Cup prediction tracker.

Polls the live results feed, and each time a match finishes it re-scores the model's
FROZEN pre-match prediction against the actual result (hit/miss, log-loss, Brier), refreshes
predictions for the remaining fixtures with the updated Elo, and writes the result back into
dashboard_state.json so the dashboard updates live. Prints a scoreboard every cycle: games
that just finished (HIT/MISS), games in play, and the next kickoffs with the model's pick.

    python live_tracker.py                 # poll forever (interval from config, default 300s)
    python live_tracker.py --once          # single pass then exit
    python live_tracker.py --interval 120  # poll every 2 minutes
    python live_tracker.py --no-forward    # re-score finished games only, don't move upcoming picks

The model is trained once at startup; each cycle only rebuilds Elo + re-predicts, so polling
is cheap. Results land in data/manual/wc2026_results.csv (xG preserved for hand-entered rows).
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

from ingest.livescores import LiveScoreClient  # noqa: E402
from ingest.results import TeamNameNormalizer  # noqa: E402
from pipeline.live_tracker import (  # noqa: E402
    build_context,
    frozen_from_predictions,
    merge_finished_into_csv,
    recompute,
)
from pipeline.orchestrator import ALIASES_PATH, STATE_PATH, WC_RESULTS_PATH  # noqa: E402
from pipeline.run_live import load_config  # noqa: E402

PICK_LABEL = {"H": "Home", "D": "Draw", "A": "Away"}


def _pct(value) -> str:
    try:
        return f"{round(float(value) * 100)}%"
    except (TypeError, ValueError):
        return "—?"


def _triple(row: dict) -> str:
    return f"{round(row['p_home']*100)}/{round(row['p_draw']*100)}/{round(row['p_away']*100)}"


def _pick_text(row: dict) -> str:
    pick = row.get("pick", "?")
    if pick == "H":
        return f"{row['home']} win"
    if pick == "A":
        return f"{row['away']} win"
    return "Draw"


def _by_pair(predictions: list[dict]) -> dict[frozenset, dict]:
    return {frozenset((str(p["home"]), str(p["away"]))): p for p in predictions}


def _print_cycle(result: dict, events, newly: set[frozenset], source: str) -> None:
    preds = result["predictions"]
    by_pair = _by_pair(preds)
    completed = [p for p in preds if p.get("status") == "completed"]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*78}\n[{stamp}]  live feed: {source}  ·  {len(completed)} games scored\n{'='*78}")

    # Games that just finished since the previous poll -> the headline HIT/MISS.
    fresh = [p for p in completed if frozenset((p["home"], p["away"])) in newly]
    if fresh:
        print("\nJUST FINISHED")
        for p in sorted(fresh, key=lambda r: r.get("date", "")):
            _print_result_line(p, prefix="  ", tag=" << NEW")

    # Anything live right now (provisional score from the feed + the locked pre-match pick).
    live = [e for e in events if e.in_play]
    if live:
        print("\nLIVE NOW")
        for e in sorted(live, key=lambda e: e.kickoff):
            pred = by_pair.get(frozenset((e.home, e.away)))
            score = f"{e.home_score}-{e.away_score}" if e.home_score is not None else "0-0"
            pick = f"pre-match: {_pick_text(pred)} {_pct(pred['pick_prob'])}" if pred else ""
            minute = f" ({e.status_raw})" if e.status_raw else ""
            print(f"  ● {e.home} {score} {e.away}{minute}   {pick}")

    # Most recent finished results (scoreboard), newest first.
    recent = sorted(completed, key=lambda r: r.get("date", ""), reverse=True)[:6]
    if recent:
        print("\nRECENT RESULTS")
        for p in recent:
            _print_result_line(p, prefix="  ")

    # Next kickoffs with the model's current pick.
    upcoming = [p for p in preds if p.get("status") != "completed"]
    upcoming = sorted(upcoming, key=lambda r: (r.get("date", ""), r.get("time", "")))[:5]
    if upcoming:
        print("\nUP NEXT")
        for p in upcoming:
            print(f"  {p.get('kickoff', p['date']):<14} {p['home']} vs {p['away']:<22}"
                  f"  pick {_pick_text(p)} {_pct(p['pick_prob'])}  (H/D/A {_triple(p)})")

    _print_scorecard(result["scorecard"])


def _print_result_line(p: dict, prefix: str = "", tag: str = "") -> None:
    mark = "HIT " if p.get("correct") else "MISS"
    glyph = "✓" if p.get("correct") else "✗"
    predicted = f"predicted {_pick_text(p)} {_pct(p['pick_prob'])}"
    actual = PICK_LABEL.get(p.get("actual"), "?")
    print(f"{prefix}{glyph} {mark}  {p['home']} {p.get('score','?')} {p['away']:<22}"
          f"  {predicted}; actual {actual}  (ll {p.get('logloss','?')}){tag}")


def _print_scorecard(scorecard: dict) -> None:
    c = scorecard.get("completed", {})
    oos = scorecard.get("out_of_sample", {})
    base = scorecard.get("uniform_log_loss_baseline", "—")
    if not c.get("n"):
        print("\nScorecard: no completed games yet.")
        return
    line = (f"\nScorecard: {c['n']} scored · acc {c['accuracy']:.3f} · "
            f"log-loss {c['log_loss']:.3f} (uniform baseline {base}) · brier {c['brier']:.3f}")
    if oos.get("n"):
        line += f"\n           out-of-sample (post-cutoff): {oos['n']} · acc {oos['accuracy']:.3f} · log-loss {oos['log_loss']:.3f}"
    line += f"\n           {scorecard.get('n_scheduled', 0)} fixtures still upcoming"
    print(line)


def _write_state(result: dict, source: str, deltas: dict) -> None:
    if not STATE_PATH.exists():
        return
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    tracker = state.setdefault("tracker", {})
    tracker["predictions"] = result["predictions"]
    tracker["scorecard"] = result["scorecard"]
    tracker["n_fixtures"] = len(result["predictions"])
    tracker["live"] = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "results_fed": result.get("live_results_fed", 0),
        "newly_finished": [f"{e.home} {e.home_score}-{e.away_score} {e.away}" for e in deltas.get("new", [])],
    }
    if result.get("elo_leaderboard"):
        state.setdefault("elo", {})["leaderboard"] = result["elo_leaderboard"][:60]
        state["elo"]["live_results_fed"] = result.get("live_results_fed", 0)
    state["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def main() -> None:
    try:  # non-ASCII team names (Türkiye, Curaçao) on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Live World Cup prediction tracker.")
    ap.add_argument("--interval", type=int, default=None, help="Seconds between polls (default: config poll_interval_sec or 300)")
    ap.add_argument("--once", action="store_true", help="Single pass then exit")
    ap.add_argument("--no-forward", action="store_true", help="Re-score finished games only; don't move upcoming picks")
    ap.add_argument("--history-years", type=int, default=12, help="Training window for the model")
    args = ap.parse_args()

    config = load_config(ROOT / "config.yaml")
    interval = args.interval if args.interval is not None else int(config.get("poll_interval_sec", 300))
    normalizer = TeamNameNormalizer.from_yaml(ALIASES_PATH)
    client = LiveScoreClient.from_config(config, normalizer)

    print(f"Training model (one-time, ~10-40s) · feed {client.base_url} league {client.league_id} season {client.season} ...")
    context = build_context(config, history_years=args.history_years)
    print("Model ready. Tracking live. Ctrl+C to stop.")

    # Seed the frozen baseline + already-scored set from the last saved state so restarts
    # don't re-announce old results as new.
    frozen: dict = {}
    prev_completed: set[frozenset] = set()
    if STATE_PATH.exists():
        prior = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("tracker", {}).get("predictions", [])
        frozen = frozen_from_predictions(prior)
        prev_completed = {frozenset((p["home"], p["away"])) for p in prior if p.get("status") == "completed"}

    while True:
        try:
            events = client.fetch_events()
            deltas = merge_finished_into_csv(events, WC_RESULTS_PATH, normalizer)
            in_play_pairs = {e.pair for e in events if e.in_play}
            result = recompute(context, frozen, in_play_pairs, refresh_forward=not args.no_forward)

            completed_now = {frozenset((p["home"], p["away"])) for p in result["predictions"] if p.get("status") == "completed"}
            newly = completed_now - prev_completed
            source = f"{client.base_url.split('//')[-1].split('/')[0]} ({len(events)} events)"
            _print_cycle(result, events, newly, source)
            if deltas["new"] and not args.no_forward:
                print(f"           ↻ refreshed upcoming predictions with {result.get('live_results_fed', 0)} live result(s) in Elo")

            _write_state(result, source, deltas)
            frozen = frozen_from_predictions(result["predictions"])
            prev_completed = completed_now
        except KeyboardInterrupt:
            print("\nstopped.")
            break
        except Exception as exc:  # never let one bad cycle kill the watcher
            print(f"[{datetime.now().strftime('%H:%M:%S')}] cycle error: {type(exc).__name__}: {exc}")

        if args.once:
            break
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped.")
            break


if __name__ == "__main__":
    main()
