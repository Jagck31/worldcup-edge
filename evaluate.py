#!/usr/bin/env python
"""evaluate.py -- one scorecard for the whole World Cup edge system.

This is the OBJECTIVE FUNCTION for the autonomous improvement loop (see AUTONOMOUS_LOOP.md):
every change is judged by whether it moves these numbers the right way. It scores five
things and writes them to data/processed/scorecard.json (+ appends a row to
scorecard_history.jsonl so the loop can see trends):

  1. model     -- held-out 1X2 calibration (log-loss / Brier / accuracy), overall vs the
                  major-tournament + competitive subsets we actually bet on. Lower log-loss
                  is better; uniform 3-way baseline is ln(3)=1.0986.
  2. tracker   -- live World Cup accuracy/log-loss on games played so far (frozen-at-kickoff).
  3. trading   -- paper-book equity, ROI, realised/unrealised P&L, exposure, # actionable.
  4. data      -- coverage/completeness: fixtures, teams, groups, results rows, live feed
                  events, and how many live Polymarket markets actually map to a model prob.
  5. strategy  -- derived-market sanity: per-group model-vs-market concentration and the
                  count of "extreme-disagreement" edges (model ~certain while the market is
                  not) -- the failure mode where naive model-minus-price bets are riskiest.

Modes:
  python evaluate.py                # FAST: read on-disk dashboard_state.json + paper_account
  python evaluate.py --retrain      # also retrain the model now for FRESH held-out metrics
                                    #   (use this to gate model/feature changes)
  python evaluate.py --live         # also fetch live Polymarket markets for fresh strategy diag
  python evaluate.py --json out.json  # write the scorecard somewhere else

Output is ASCII only (Windows consoles are cp1252).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

SCORECARD_PATH = ROOT / "data" / "processed" / "scorecard.json"
HISTORY_PATH = ROOT / "data" / "processed" / "scorecard_history.jsonl"
UNIFORM_LOGLOSS = 1.0986  # ln(3): a 3-way coin flip


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_state() -> dict:
    from pipeline.orchestrator import STATE_PATH

    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


# --------------------------------------------------------------------------- model

def model_scorecard(state: dict, retrain: bool, config: dict) -> dict:
    """Held-out 1X2 calibration. From on-disk backtest by default; freshly trained with --retrain."""
    if not retrain:
        bt = state.get("backtest") or {}
        if not bt.get("available"):
            return {"available": False, "source": "state", "reason": "no backtest in state"}
        return {
            "available": True,
            "source": "state",
            "overall": bt.get("overall", {}),
            "major_tournaments": bt.get("major_tournaments", {}),
            "competitive_only": bt.get("competitive_only", {}),
            "uniform_log_loss": bt.get("uniform_log_loss", UNIFORM_LOGLOSS),
            "window": bt.get("window"),
        }

    # Fresh: rebuild features + train the current code's model, score the held-out slice.
    import pandas as pd
    from features.build_features import HOST_NATIONS_2026, build_match_features
    from features.elo import EloConfig, EloEngine
    from ingest.results import TeamNameNormalizer, load_results
    from model.predict import CalibratedPredictor
    from model.train import train_1x2
    from pipeline.backtest import tournament_backtest
    from pipeline.orchestrator import (
        ALIASES_PATH,
        RAW_RESULTS_PATH,
        _load_fifa_rankings,
        _select_feature_columns,
        _to_model_matrix,
    )

    history_years = int(config.get("history_years", 12))
    normalizer = TeamNameNormalizer.from_yaml(ALIASES_PATH)
    results_full = load_results(RAW_RESULTS_PATH, mapping_path=ALIASES_PATH, refresh=False)
    results_full = results_full.dropna(subset=["home_score", "away_score"]).reset_index(drop=True)
    results_full["home_score"] = results_full["home_score"].astype(int)
    results_full["away_score"] = results_full["away_score"].astype(int)
    cutoff = results_full["date"].max() - pd.DateOffset(years=history_years)
    recent = results_full[results_full["date"] >= cutoff].reset_index(drop=True)

    rankings = _load_fifa_rankings(normalizer, refresh=False)
    elo_base = EloEngine(EloConfig()).process_matches(results_full, host_nations=HOST_NATIONS_2026)
    features = build_match_features(recent, elo_base, rankings=rankings, host_nations=HOST_NATIONS_2026)
    feature_columns = _select_feature_columns(features)
    labelled = features.dropna(subset=["target_1x2"]).copy()
    matrix = _to_model_matrix(labelled, feature_columns)
    matrix["date"] = labelled["date"].to_numpy()
    matrix["target_1x2"] = labelled["target_1x2"].to_numpy()
    if "tournament" in labelled.columns:
        matrix["tournament"] = labelled["tournament"].to_numpy()

    train_result = train_1x2(
        matrix, feature_columns, model_type=str(config.get("model_type", "gbt")), refit_full=False
    )
    predictor = CalibratedPredictor.from_train_result(train_result)
    bt = tournament_backtest(matrix, predictor)
    bt["source"] = "retrain"
    bt["calibration_method"] = train_result.calibration_method
    bt["n_validation"] = train_result.n_validation
    return bt


# --------------------------------------------------------------------------- tracker

def tracker_scorecard(state: dict) -> dict:
    tr = (state.get("tracker") or {}).get("scorecard") or {}
    if not tr:
        return {"available": False}
    out = {
        "available": True,
        "completed": tr.get("completed", {}),
        "out_of_sample": tr.get("out_of_sample", {}),
        "n_scheduled": tr.get("n_scheduled"),
        "uniform_log_loss_baseline": tr.get("uniform_log_loss_baseline", UNIFORM_LOGLOSS),
    }
    live = (state.get("tracker") or {}).get("live") or {}
    out["in_play"] = len(live.get("games", []) or [])
    out["live_source"] = live.get("source")
    return out


# --------------------------------------------------------------------------- trading

def trading_scorecard(state: dict, config: dict) -> dict:
    from edge.risk import position_risk
    from pipeline.orchestrator import PAPER_ACCOUNT_PATH

    bankroll = float(config.get("bankroll_usd", 10000))
    summ, positions = {}, []
    try:
        acct = json.loads(PAPER_ACCOUNT_PATH.read_text(encoding="utf-8"))
        summ = acct.get("summary", {})
        positions = acct.get("positions", []) or []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pa = state.get("paper_account") or {}
        summ = pa.get("summary", {})
        positions = pa.get("positions", []) or []

    equity = summ.get("equity")
    roi_pct = round((equity - bankroll) / bankroll * 100, 3) if equity is not None and bankroll else None
    markets = state.get("markets", {})
    slate = markets.get("slate", []) or []
    actionable = [r for r in slate if r.get("actionable")]
    return {
        "available": equity is not None,
        "source": markets.get("source"),
        "start_bankroll_usd": bankroll,
        "equity_usd": equity,
        "roi_pct": roi_pct,
        "expected_roi_pct": summ.get("expected_roi_pct"),  # the model's view if its probs are right
        "realized_pnl_usd": summ.get("realized_pnl"),
        "unrealized_pnl_usd": summ.get("unrealized_pnl"),
        "invested_usd": summ.get("invested") or summ.get("deployed"),
        "n_open_positions": summ.get("n_open") or summ.get("open_positions"),
        "n_edges": len(slate),
        "n_actionable": len(actionable),
        "risk": position_risk(positions, bankroll),
    }


# --------------------------------------------------------------------------- data

def data_scorecard(state: dict, live_markets: dict | None) -> dict:
    data = state.get("data", {})
    sim = state.get("simulation", {})
    groups = sim.get("groups") or {}
    n_teams = sum(len(v) for v in groups.values()) if groups else None
    tracker = state.get("tracker", {})
    live = tracker.get("live", {}) or {}
    markets = live_markets or state.get("markets", {})
    comparison = markets.get("comparison", []) or []
    mapped = [c for c in comparison if c.get("model_prob") is not None]
    out = {
        "results_rows": data.get("n_results") or data.get("rows") or data.get("n_matches"),
        "n_groups": len(groups) if groups else None,
        "n_teams": n_teams,
        "n_fixtures_tracked": tracker.get("n_fixtures"),
        "n_scheduled": (tracker.get("scorecard") or {}).get("n_scheduled"),
        "live_feed_source": live.get("source"),
        "live_results_fed": (state.get("elo") or {}).get("live_results_fed"),
        "market_mappings_total": len(comparison),
        "market_mappings_resolved": len(mapped),
        "market_mapping_rate": round(len(mapped) / len(comparison), 3) if comparison else None,
        "unmatched_teams": markets.get("unmatched_teams", []),
    }
    return out


# --------------------------------------------------------------------------- strategy

def strategy_scorecard(state: dict, live_markets: dict | None, config: dict) -> dict:
    """Derived-market sanity: per-group concentration vs market + extreme-disagreement count.

    The dangerous failure mode (observed 2026-06-14): the slate fills with NO bets on
    group-winner/champion markets where the model is ~certain (model_prob ~0.99 or ~0.01)
    while the market is far less sure. Those big 'edges' are usually Monte-Carlo / Elo
    over-concentration, not real mispricings. This quantifies how much of that we are doing.
    """
    markets = live_markets or state.get("markets", {})
    comparison = markets.get("comparison", []) or []
    slate = markets.get("slate", []) or []

    extreme_hi = float(config.get("diag_extreme_hi", 0.95))
    extreme_lo = float(config.get("diag_extreme_lo", 0.05))
    disagree = float(config.get("diag_disagree", 0.15))

    extreme = []
    for c in comparison:
        mp, ask = c.get("model_prob"), c.get("market_ask")
        if mp is None or ask is None:
            continue
        if (mp >= extreme_hi or mp <= extreme_lo) and abs(mp - ask) >= disagree:
            extreme.append({"market": c.get("market"), "team": c.get("team"),
                            "model_prob": mp, "market_ask": ask, "edge_pp": c.get("edge_pp")})

    # Per-group concentration: model favourite share vs market favourite share.
    groups: dict[str, list] = {}
    for c in comparison:
        if c.get("type") != "win_group":
            continue
        groups.setdefault(str(c.get("market")), []).append(c)
    group_diag = []
    for label, rows in sorted(groups.items()):
        mps = [r["model_prob"] for r in rows if r.get("model_prob") is not None]
        asks = [r["market_ask"] for r in rows if r.get("market_ask") is not None]
        if not mps:
            continue
        group_diag.append({
            "group": label,
            "n_teams": len(rows),
            "model_sum": round(sum(mps), 3),
            "market_sum": round(sum(asks), 3) if asks else None,
            "model_top_share": round(max(mps), 3),
            "market_top_share": round(max(asks), 3) if asks else None,
        })

    sides = {}
    for r in slate:
        sides[r.get("side")] = sides.get(r.get("side"), 0) + 1
    actionable = [r for r in slate if r.get("actionable")]
    return {
        "n_edges": len(slate),
        "n_actionable": len(actionable),
        "sides": sides,
        "n_extreme_disagreement": len(extreme),
        "extreme_examples": sorted(extreme, key=lambda e: -abs(e["model_prob"] - e["market_ask"]))[:8],
        "groups": group_diag,
        "thresholds": {"extreme_hi": extreme_hi, "extreme_lo": extreme_lo, "disagree": disagree},
    }


# --------------------------------------------------------------------------- live fetch

def fetch_live_markets(config: dict) -> dict | None:
    """Fresh Polymarket markets priced against the latest on-disk simulation."""
    import pandas as pd
    from ingest.results import TeamNameNormalizer
    from pipeline.orchestrator import ALIASES_PATH, _build_live_markets

    state = _load_state()
    submarkets = pd.DataFrame((state.get("simulation") or {}).get("submarkets", []))
    if submarkets.empty:
        return None
    normalizer = TeamNameNormalizer.from_yaml(ALIASES_PATH)
    try:
        return _build_live_markets(submarkets, normalizer, config)
    except Exception as exc:  # network/parse failures shouldn't crash the scorecard
        return {"error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- report

def _fmt(v, nd=4):
    return "n/a" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def print_report(card: dict) -> None:
    p = print
    p("=" * 70)
    p(f"WORLD CUP EDGE SCORECARD   {card['generated_at']}   ({card['mode']})")
    p("=" * 70)

    m = card["model"]
    p("\n[MODEL] held-out 1X2 calibration   source=%s" % m.get("source"))
    if m.get("available"):
        for k in ("overall", "major_tournaments", "competitive_only"):
            d = m.get(k, {})
            if d:
                p("  %-18s n=%-5s logloss=%s brier=%s acc=%s"
                  % (k, d.get("n"), _fmt(d.get("log_loss")), _fmt(d.get("brier")), _fmt(d.get("accuracy"))))
        p("  uniform baseline logloss = %s   (lower than this = better than a coin flip)"
          % _fmt(m.get("uniform_log_loss")))
    else:
        p("  unavailable: %s" % m.get("reason"))

    t = card["tracker"]
    p("\n[TRACKER] live World Cup games (frozen-at-kickoff)")
    if t.get("available"):
        c = t.get("completed", {})
        o = t.get("out_of_sample", {})
        p("  completed     n=%-3s acc=%s logloss=%s brier=%s"
          % (c.get("n"), _fmt(c.get("accuracy")), _fmt(c.get("log_loss")), _fmt(c.get("brier"))))
        p("  out-of-sample n=%-3s acc=%s logloss=%s brier=%s"
          % (o.get("n"), _fmt(o.get("accuracy")), _fmt(o.get("log_loss")), _fmt(o.get("brier"))))
        p("  scheduled remaining=%s  in-play now=%s" % (t.get("n_scheduled"), t.get("in_play")))
    else:
        p("  unavailable")

    tr = card["trading"]
    p("\n[TRADING] paper book   source=%s" % tr.get("source"))
    if tr.get("available"):
        p("  equity=$%s  ROI=%s%%  (model expects %s%%)  unrealized=$%s"
          % (_fmt(tr.get("equity_usd"), 2), _fmt(tr.get("roi_pct"), 3),
             _fmt(tr.get("expected_roi_pct"), 1), _fmt(tr.get("unrealized_pnl_usd"), 2)))
        p("  edges=%s  actionable=%s  open_positions=%s"
          % (tr.get("n_edges"), tr.get("n_actionable"), tr.get("n_open_positions")))
        rk = tr.get("risk", {})
        p("  RISK: invested=%s%%  max_single=%s%%  top3=%s%%  top5=%s%%  settle_buckets=%s   <-- concentration"
          % (_fmt(rk.get("invested_pct"), 1), _fmt(rk.get("max_position_pct"), 1),
             _fmt(rk.get("top3_pct"), 1), _fmt(rk.get("top5_pct"), 1), rk.get("n_settle_buckets")))
    else:
        p("  unavailable")

    d = card["data"]
    p("\n[DATA] coverage / completeness")
    p("  results_rows=%s  groups=%s  teams=%s  fixtures=%s  scheduled=%s"
      % (d.get("results_rows"), d.get("n_groups"), d.get("n_teams"),
         d.get("n_fixtures_tracked"), d.get("n_scheduled")))
    p("  live_feed=%s  results_fed=%s" % (d.get("live_feed_source"), d.get("live_results_fed")))
    p("  market_mappings=%s/%s resolved (rate=%s)  unmatched=%s"
      % (d.get("market_mappings_resolved"), d.get("market_mappings_total"),
         _fmt(d.get("market_mapping_rate"), 3), d.get("unmatched_teams")))

    s = card["strategy"]
    p("\n[STRATEGY] derived-market sanity")
    p("  edges=%s actionable=%s sides=%s" % (s.get("n_edges"), s.get("n_actionable"), s.get("sides")))
    p("  EXTREME-DISAGREEMENT edges (model ~certain, market not): %s   <-- watch this"
      % s.get("n_extreme_disagreement"))
    for e in s.get("extreme_examples", [])[:6]:
        p("    %-26s %-22s model=%s market=%s edge=%s"
          % (str(e.get("market"))[:26], str(e.get("team"))[:22],
             _fmt(e.get("model_prob"), 3), _fmt(e.get("market_ask"), 3), _fmt(e.get("edge_pp"), 1)))
    for g in s.get("groups", []):
        p("    %-16s model_top=%s market_top=%s  model_sum=%s market_sum=%s"
          % (str(g.get("group"))[:16], _fmt(g.get("model_top_share"), 3),
             _fmt(g.get("market_top_share"), 3), _fmt(g.get("model_sum"), 3), _fmt(g.get("market_sum"), 3)))
    p("\n" + "=" * 70)


def build_scorecard(retrain: bool, live: bool) -> dict:
    from pipeline.run_live import load_config

    config = load_config(ROOT / "config.yaml")
    state = _load_state()
    live_markets = fetch_live_markets(config) if live else None
    card = {
        "generated_at": _now(),
        "mode": ("retrain" if retrain else "fast") + ("+live" if live else ""),
        "model": model_scorecard(state, retrain, config),
        "tracker": tracker_scorecard(state),
        "trading": trading_scorecard(state, config),
        "data": data_scorecard(state, live_markets if isinstance(live_markets, dict) and "error" not in live_markets else None),
        "strategy": strategy_scorecard(state, live_markets if isinstance(live_markets, dict) and "error" not in live_markets else None, config),
    }
    if isinstance(live_markets, dict) and "error" in live_markets:
        card["live_fetch_error"] = live_markets["error"]
    return card


def _headline(card: dict) -> dict:
    """The few numbers the loop compares run-to-run."""
    m = card["model"].get("major_tournaments", {}) if card["model"].get("available") else {}
    t = card["tracker"].get("completed", {}) if card["tracker"].get("available") else {}
    return {
        "at": card["generated_at"],
        "mode": card["mode"],
        "model_major_logloss": m.get("log_loss"),
        "model_major_brier": m.get("brier"),
        "tracker_logloss": t.get("log_loss"),
        "tracker_acc": t.get("accuracy"),
        "roi_pct": card["trading"].get("roi_pct"),
        "max_position_pct": (card["trading"].get("risk") or {}).get("max_position_pct"),
        "top3_pct": (card["trading"].get("risk") or {}).get("top3_pct"),
        "n_extreme_disagreement": card["strategy"].get("n_extreme_disagreement"),
        "market_mapping_rate": card["data"].get("market_mapping_rate"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="World Cup edge system scorecard")
    ap.add_argument("--retrain", action="store_true", help="retrain the model now for fresh held-out metrics")
    ap.add_argument("--live", action="store_true", help="fetch live Polymarket markets for fresh strategy diag")
    ap.add_argument("--json", type=str, default=None, help="write scorecard JSON here (default data/processed/scorecard.json)")
    ap.add_argument("--quiet", action="store_true", help="don't print the human report")
    args = ap.parse_args()

    card = build_scorecard(retrain=args.retrain, live=args.live)

    out_path = Path(args.json) if args.json else SCORECARD_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
    try:
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_headline(card)) + "\n")
    except OSError:
        pass

    if not args.quiet:
        print_report(card)
    print("\nwrote %s" % out_path)
    print("headline: %s" % json.dumps(_headline(card)))


if __name__ == "__main__":
    main()
