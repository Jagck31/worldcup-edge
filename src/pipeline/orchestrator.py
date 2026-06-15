"""End-to-end pipeline that produces a single dashboard state artifact.

Runs the real chain: cached/downloaded results -> Elo -> features -> calibrated 1X2
model -> goal model -> Monte Carlo on a demo 2026 draw -> edge slate + Kelly + scanner,
then serialises everything (with explicit data-source labels) to JSON for the TUI.

Every stage is wrapped so a failure degrades to a labelled, still-renderable state
instead of taking down the whole run. Designed to be offline-safe: if the results
download fails but a cache exists, it uses the cache.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import time
import traceback

import numpy as np
import pandas as pd

from edge.detect import OrderBook, OrderLevel, detect_edges, detect_yes_edge, executable_yes_price
from edge.kelly import KellyConfig, size_bet
from edge.portfolio import BetSpec, build_portfolio
from edge.recommend import (
    build_recommendations,
    recommendations_to_state_rows,
    summarize_recommendations,
)
from edge.scanner import ConsistencyMarket, scan_sum_to_one
from edge.shrink import blend_toward_market
from features.build_features import HOST_NATIONS_2026, build_match_features
from features.elo import EloConfig, EloEngine, EloHistory
from ingest.polymarket import (
    PolymarketClient,
    PolymarketMarket,
    _decode_json_list,
    build_market_probability_inputs,
    map_world_cup_markets,
)
from ingest.results import TeamNameNormalizer, load_results
from model.goal_model import PoissonGoalModel
from model.predict import CalibratedPredictor
from model.train import train_1x2
from pipeline.backtest import tournament_backtest
from pipeline.paper_account import load_account, save_account, update_account
from pipeline.paper_trader import live_execution_status
from pipeline.run_live import load_config
from pipeline.tracker import (
    build_group_fixtures,
    build_sim_groups,
    load_groups,
    load_schedule,
    overlay_results,
    predict_fixtures,
    score_tracker,
)
from simulate.monte_carlo import GROUPS_2026, TournamentSimulator, load_third_place_assignment_table


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = PROJECT_ROOT / "data" / "processed" / "dashboard_state.json"
RAW_RESULTS_PATH = PROJECT_ROOT / "data" / "raw" / "results.csv"
ALIASES_PATH = PROJECT_ROOT / "data" / "manual" / "team_aliases.yaml"
GROUPS_PATH = PROJECT_ROOT / "data" / "manual" / "groups_2026.csv"
WC_RESULTS_PATH = PROJECT_ROOT / "data" / "manual" / "wc2026_results.csv"
THIRD_PLACE_PATH = PROJECT_ROOT / "data" / "manual" / "third_place_assignments.csv"
SQUADS_PATH = PROJECT_ROOT / "data" / "manual" / "squads_2026.csv"
SCHEDULE_PATH = PROJECT_ROOT / "data" / "manual" / "wc2026_schedule.csv"
PAPER_ACCOUNT_PATH = PROJECT_ROOT / "data" / "processed" / "paper_account.json"
SIM_OUTCOMES_PATH = PROJECT_ROOT / "data" / "processed" / "sim_outcomes.json"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
RANKINGS_URL = "https://raw.githubusercontent.com/Dato-Futbol/fifa-ranking/master/ranking_fifa_historical.csv"
RANKINGS_RAW = PROJECT_ROOT / "data" / "raw" / "fifa_ranking.csv"
CHAMPION_RE = re.compile(r"will\s+(.+?)\s+win the 2026", re.IGNORECASE)

# Columns that are identifiers, targets, or non-numeric metadata — never model inputs.
_NON_FEATURE_COLUMNS = {
    "date",
    "home_team",
    "away_team",
    "tournament",
    "stage",
    "target_1x2",
    "target_total_goals",
    "target_btts",
    "home_rank_date",
    "away_rank_date",
}


class PipelineHooks:
    """No-op streaming hooks. Subclass to push live updates to a UI.

    The CLI uses the base class (does nothing); the dashboard subclasses it so each
    callback marshals onto the Textual event loop and animates a panel.
    """

    def stage(self, name: str, status: str = "start", detail: str = "") -> None:  # noqa: D401
        pass

    def train_step(self, iteration: int, max_iter: int, val_log_loss: float) -> None:
        pass

    def sim_progress(self, done: int, total: int, counts: dict, bracket: dict | None = None) -> None:
        pass

    def elo_ready(self, leaderboard: list[dict]) -> None:
        pass


class _Log:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def step(self, name: str):
        return _Step(self, name)


class _Step:
    def __init__(self, log: _Log, name: str) -> None:
        self.log = log
        self.name = name

    def __enter__(self) -> "_Step":
        self.start = time.perf_counter()
        return self

    def ok(self, detail: str = "") -> None:
        self._record("ok", detail)

    def warn(self, detail: str) -> None:
        self._record("warn", detail)

    def _record(self, status: str, detail: str) -> None:
        self.log.entries.append(
            {
                "step": self.name,
                "status": status,
                "detail": detail,
                "seconds": round(time.perf_counter() - self.start, 2),
            }
        )

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.log.entries.append(
                {
                    "step": self.name,
                    "status": "error",
                    "detail": f"{exc_type.__name__}: {exc}",
                    "seconds": round(time.perf_counter() - self.start, 2),
                }
            )
            return True  # swallow; the run continues in degraded mode
        return False


class PoissonScorelineSampler:
    """Elo-aware scoreline sampler: expected goals from the goal model, then tilted by the
    Elo gap so the group stage reflects team strength (not just recent raw scoring), then
    drawn as independent Poissons. Without the Elo tilt the goal model barely separates
    teams and group outcomes come out far too random (minnows advancing too often)."""

    def __init__(self, goal_model: PoissonGoalModel, elo: EloHistory | None = None, seed: int = 0, divisor: float = 700.0) -> None:
        self.goal_model = goal_model
        self.elo = elo
        self.divisor = divisor
        self.rng = np.random.default_rng(seed)
        self._cache: dict[tuple[str, str], tuple[float, float]] = {}

    def __call__(self, home: str, away: str) -> tuple[int, int]:
        key = (home, away)
        xg = self._cache.get(key)
        if xg is None:
            home_xg, away_xg = self.goal_model.expected_goals(home, away)
            if self.elo is not None:
                gap = self.elo.current_rating(home) - self.elo.current_rating(away)
                adjust = 10 ** ((gap / self.divisor) * 0.5)
                home_xg, away_xg = home_xg * adjust, away_xg / adjust
            xg = (max(home_xg, 0.04), max(away_xg, 0.04))
            self._cache[key] = xg
        return int(self.rng.poisson(xg[0])), int(self.rng.poisson(xg[1]))


def _elo_win_probability(elo: EloHistory) -> "callable":
    def prob(team_a: str, team_b: str) -> float:
        rating_a = elo.current_rating(team_a)
        rating_b = elo.current_rating(team_b)
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))

    return prob


def _round_robin(teams: list[str]) -> pd.DataFrame:
    pairings = [(0, 1), (2, 3), (0, 2), (1, 3), (0, 3), (1, 2)]
    rows = [
        {"home_team": teams[a], "away_team": teams[b], "home_score": pd.NA, "away_score": pd.NA}
        for a, b in pairings
    ]
    return pd.DataFrame(rows)


def _demo_draw(leaderboard: list[dict]) -> dict[str, dict]:
    """Snake the top 48 teams by Elo into 12 balanced groups of 4 (DEMO draw)."""
    teams = [row["team"] for row in leaderboard[:48]]
    while len(teams) < 48:
        teams.append(f"Placeholder {len(teams) + 1}")
    pots = [teams[i * 12 : (i + 1) * 12] for i in range(4)]
    groups: dict[str, dict] = {}
    for index, letter in enumerate(GROUPS_2026):
        # Snake pots 2 and 4 so the strongest groups are not all front-loaded.
        members = [pots[0][index], pots[1][11 - index], pots[2][index], pots[3][11 - index]]
        groups[letter] = {"teams": members, "fixtures": _round_robin(members)}
    return groups


def _select_feature_columns(features: pd.DataFrame, has_rankings: bool = True) -> list[str]:
    """Every numeric/boolean column that isn't an identifier or target is a model input.

    Auto-detection means new features added in build_features.py flow straight into the
    model (and into the dashboard's importance panel) without editing a list here.
    """
    columns: list[str] = []
    for column in features.columns:
        if column in _NON_FEATURE_COLUMNS:
            continue
        if features[column].dtype == object:
            continue
        columns.append(column)
    return columns


def _to_model_matrix(features: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    matrix = features[columns].copy()
    for column in columns:
        if matrix[column].dtype == bool:
            matrix[column] = matrix[column].astype("int8")
    return matrix


def _sample_book(market_id: str, fair_price: float, vig: float, depth_usd: float) -> OrderBook:
    """Build a small synthetic order book around a fair price (clearly-labelled SAMPLE data)."""
    ask = min(0.99, max(0.01, fair_price + vig / 2))
    bid = min(0.99, max(0.01, fair_price - vig / 2))
    asks = [
        OrderLevel(price=round(ask, 3), size_usd=depth_usd),
        OrderLevel(price=round(min(0.99, ask + 0.02), 3), size_usd=depth_usd * 2),
    ]
    bids = [OrderLevel(price=round(bid, 3), size_usd=depth_usd)]
    return OrderBook(market_id=market_id, yes_asks=asks, yes_bids=bids)


def _slate_to_bets(slate: list[dict], team_to_group: dict[str, str], bankroll: float) -> list[BetSpec]:
    """Map recommendation-slate rows to portfolio BetSpecs (champion vs group-winner markets).

    Shared by the full pipeline and the live price-watcher so both build the book identically.
    Only positive-edge rows with a sane executable price that map to a known market are kept.
    """
    bets: list[BetSpec] = []
    for row in slate:
        team = row.get("team")
        price = float(row.get("exec_price") or 0.0)
        if not team or float(row.get("edge_pp", 0.0)) <= 0.0 or not (0.0 < price < 1.0):
            continue
        market_txt = str(row.get("market", ""))
        if CHAMPION_RE.search(market_txt) or "win the 2026" in market_txt.lower():
            market_key, bucket = "champion", "champion"
        else:
            grp = team_to_group.get(team)
            if grp is None:
                continue
            market_key, bucket = f"group:{grp}", grp
        naive = float(row.get("kelly_size_usd") or row.get("capped_size_usd") or 0.0) / bankroll
        bets.append(
            BetSpec(
                bet_id=str(row.get("market_id") or f"{team}-{market_key}-{row.get('side')}"),
                market_key=market_key, team=str(team), side=str(row.get("side", "YES")), price=price,
                label=f"{row.get('action', '')} {team} [{'champ' if bucket == 'champion' else 'grp ' + bucket}]".strip(),
                group=bucket, max_fraction=0.15, naive_fraction=naive,
                edge_pp=float(row.get("edge_pp", 0.0)), model_prob=float(row.get("model_prob", 0.0)),
            )
        )
    return bets


def _build_live_markets(
    submarkets: pd.DataFrame, normalizer: TeamNameNormalizer, config: dict
) -> dict | None:
    """Pull live Polymarket champion markets, price real executable edges vs the sim.

    Maps each "Will <Team> win the 2026 FIFA World Cup?" market to the simulated
    champion probability, reads the executable YES ask from the CLOB book, and runs the
    real edge/Kelly/scanner stack. Returns None if nothing usable is fetched (caller
    falls back to SAMPLE books).
    """
    kelly_config = KellyConfig(
        bankroll_usd=float(config.get("bankroll_usd", 75)),
        kelly_fraction=float(config.get("kelly_fraction", 0.25)),
        max_single_bet_pct=float(config.get("max_single_bet_pct", 0.20)),
        max_total_exposure_pct=float(config.get("max_total_exposure_pct", 0.80)),
        min_fillable_usd=float(config.get("min_fillable_usd", 5)),
    )
    target_usd = float(config.get("target_order_size_usd", 10))
    min_edge_pp = float(config.get("min_edge_pp", 5.0))
    fees_bps = float(config.get("fees_bps", 0))

    sim_rows = submarkets.to_dict("records")
    sim_by_team = {str(r["team"]): r for r in sim_rows}
    fee_mult = 1.0 + fees_bps / 10000.0
    client = PolymarketClient(cache_dir=CACHE_DIR)

    markets = _fetch_all_wc_markets(client)
    mappings = map_world_cup_markets(
        markets, known_teams=list(sim_by_team.keys()), aliases=dict(normalizer.aliases)
    )
    if not mappings:
        return None
    model_probs, _missing = build_market_probability_inputs(mappings, sim_rows)

    order_books: dict[str, OrderBook] = {}
    market_prices: dict[str, float] = {}
    market_mid_by_name: dict[str, float] = {}     # de-vig / blend target, keyed like model_probs
    group_partitions: dict[str, list[str]] = {}   # group -> its win-group contract names (sum to 1)
    champion_names: list[str] = []                # all champion contracts (sum to 1)
    comparison: list[dict] = []
    scanner_markets: list[ConsistencyMarket] = []
    snapshot: list[dict] = []
    unmatched: list[str] = []
    fetched = 0

    for mapping in mappings:
        prob = model_probs.get(mapping.market_name)
        try:
            book = client.get_yes_order_book(mapping)
        except Exception:
            continue
        if not book.yes_asks:
            continue
        fetched += 1
        order_books[mapping.market_name] = book
        best_ask = min((lvl.price for lvl in book.yes_asks), default=None)
        best_bid = max((lvl.price for lvl in book.yes_bids), default=None)
        mid: float | None = None
        if best_ask is not None and best_bid is not None:
            mid = (best_ask + best_bid) / 2
            market_prices[mapping.market_id] = round(mid, 4)
        elif best_ask is not None:
            mid = best_ask
            market_prices[mapping.market_id] = round(best_ask, 4)
        if mid is not None and 0.0 < mid < 1.0:
            market_mid_by_name[mapping.market_name] = mid
            if mapping.market_type == "win_group" and mapping.group:
                group_partitions.setdefault(mapping.group, []).append(mapping.market_name)
            elif mapping.market_type == "champion":
                champion_names.append(mapping.market_name)
        executable = executable_yes_price(book, target_usd)
        ask_after = round(executable.average_price * fee_mult, 6)
        label = mapping.market_name.rsplit(" - ", 1)[0]
        edge_pp = round((prob - ask_after) * 100, 2) if prob is not None else None
        snapshot.append(
            {"market": mapping.market_name, "team": mapping.team, "type": mapping.market_type,
             "executable_yes": executable.average_price, "fillable_usd": executable.fillable_usd, "model_prob": prob}
        )
        comparison.append(
            {"market": label, "team": mapping.team, "type": mapping.market_type,
             "model_prob": round(prob, 4) if prob is not None else None,
             "market_ask": ask_after, "edge_pp": edge_pp}
        )
        if mapping.market_type == "champion" and executable.fillable_usd > 0:
            scanner_markets.append(
                ConsistencyMarket(f"Champion - {mapping.team}", "Champion 2026", executable.average_price, executable.fillable_usd)
            )
        if prob is None:
            unmatched.append(mapping.team)

    if fetched == 0:
        return None

    # Humility shrinkage: pull the simulated derived-market probabilities a fraction of the way
    # toward the de-vigged market before detecting edges, so a single over/under-confident sim
    # contract can't mint an oversized bet. weight=0 keeps the raw model. See edge/shrink.py.
    blend_weight = float(config.get("market_blend_weight", 0.0))
    partitions = list(group_partitions.values()) + ([champion_names] if champion_names else [])
    blended_probs = blend_toward_market(model_probs, market_mid_by_name, partitions, blend_weight)
    if blend_weight > 0.0:
        for row in comparison:
            name = f"{row['market']} - {row['team']}"
            blended = blended_probs.get(name)
            if blended is None or row.get("model_prob") is None:
                continue
            row["model_prob_raw"] = row["model_prob"]
            row["model_prob"] = round(blended, 4)
            if row.get("market_ask") is not None:
                row["edge_pp"] = round((blended - row["market_ask"]) * 100, 2)

    # Codex's recommender: rank YES + NO edges by Kelly impact, size with exposure caps,
    # label risk, and export dashboard rows + exposure meters.
    candidates = detect_edges(blended_probs, order_books, target_usd, min_edge_pp, fees_bps, include_no=True)
    recommendations = build_recommendations(candidates, kelly_config)
    slate = recommendations_to_state_rows(recommendations)
    recommendation_summary = summarize_recommendations(recommendations, kelly_config)
    # Attach each market's resolution/settle date so the paper account can build a timeline.
    end_dates = {m.market_id: m.end_date for m in markets if getattr(m, "end_date", None)}
    for row in slate:
        row["settle_date"] = end_dates.get(row.get("market_id"))
    comparison.sort(key=lambda row: (row["model_prob"] or 0.0), reverse=True)
    # Coherence: champion YES asks should imply ~1 after vig; sub-1 = buyable arb.
    flags = scan_sum_to_one(
        scanner_markets, tolerance_pp=6.0, min_fillable_usd=1.0, expected_total=1.0, alert_overpriced=True
    )
    scanner_flags = [
        {
            "group": flag.group_key,
            "sum_implied": round(flag.sum_implied_probability, 3),
            "gap_pp": round(flag.gap_pp, 2),
            "direction": flag.direction,
            "markets": flag.market_count,
        }
        for flag in flags
    ]
    try:
        client.cache_snapshot("market_books", snapshot)
    except Exception:
        pass

    actionable_rows = [r for r in slate if r.get("actionable")]
    blend_note = (
        f" Derived-market probs shrunk {blend_weight:.0%} toward the de-vigged market (risk control)."
        if blend_weight > 0.0 else ""
    )
    return {
        "source": "LIVE",
        "note": f"Live Polymarket CLOB: {fetched} markets (champion + group winners), YES + NO edges ranked by "
        f"Kelly impact. {len(actionable_rows)} actionable recommendation(s) clear the {min_edge_pp:.0f}pp + $ min-fill bar."
        + blend_note,
        "market_blend_weight": blend_weight,
        "slate": slate,
        "scanner_flags": scanner_flags,
        "comparison": comparison,
        "recommendation_summary": recommendation_summary,
        "market_prices": market_prices,
        "total_recommended_exposure_usd": recommendation_summary.get("total_recommended_exposure_usd", 0.0),
        "n_markets": fetched,
        "edges_found": len(slate),
        "edges_above_threshold": len(actionable_rows),
        "unmatched_teams": sorted(set(unmatched)),
    }


def _fetch_all_wc_markets(client: PolymarketClient) -> list[PolymarketMarket]:
    """Champion markets (flat /markets list) + the 12 'Group X Winner' events as markets."""
    markets = list(client.list_world_cup_markets())
    for letter in "ABCDEFGHIJKL":
        slug = f"world-cup-group-{letter.lower()}-winner"
        try:
            payload = client._get_json(f"{client.gamma_base}/events", params={"slug": slug})
        except Exception:
            continue
        events = payload if isinstance(payload, list) else payload.get("events", [])
        for event in events:
            for market in event.get("markets", []):
                markets.append(
                    PolymarketMarket(
                        market_id=str(market.get("id") or market.get("slug")),
                        question=str(market.get("question", "")),
                        slug=str(market.get("slug", "")),
                        outcomes=_decode_json_list(market.get("outcomes")),
                        token_ids=_decode_json_list(market.get("clobTokenIds") or market.get("tokenIds")),
                        end_date=market.get("endDate"),
                    )
                )
    return markets


def _build_markets(submarkets: pd.DataFrame, groups: dict, config: dict, seed: int = 11) -> dict:
    """Demo edge slate + scanner over SAMPLE books priced around model probabilities.

    The price side is synthetic (clearly labelled). It exists so the real
    detect/Kelly/scanner code runs end-to-end on real model probabilities until a live
    CLOB feed is wired. A few teams are deliberately under/over-priced to create signal.
    """
    rng = random.Random(seed)
    kelly_config = KellyConfig(
        bankroll_usd=float(config.get("bankroll_usd", 75)),
        kelly_fraction=float(config.get("kelly_fraction", 0.25)),
        max_single_bet_pct=float(config.get("max_single_bet_pct", 0.20)),
        max_total_exposure_pct=float(config.get("max_total_exposure_pct", 0.80)),
        min_fillable_usd=float(config.get("min_fillable_usd", 5)),
    )
    target_usd = float(config.get("target_order_size_usd", 10))
    min_edge_pp = float(config.get("min_edge_pp", 5.0))
    fees_bps = float(config.get("fees_bps", 0))

    # Build SAMPLE books for two market families. A few contenders are deliberately
    # under-priced (value) and a few over-priced (traps) so the real detect/Kelly code
    # has signal to act on. Champion edges in pp are small, so Reach-Final is included too.
    slate = []
    exposure = 0.0

    def run_family(
        label: str, prob_column: str, value_ranks: set, trap_ranks: set, value_discount: float = 0.075
    ) -> None:
        nonlocal exposure
        model_probs: dict[str, float] = {}
        books: dict[str, OrderBook] = {}
        contenders = submarkets[submarkets[prob_column] > 0.02].head(14)
        for rank, (_, row) in enumerate(contenders.iterrows()):
            team = str(row["team"])
            fair = float(row[prob_column])
            if rank in value_ranks:
                book_price = max(0.02, fair - value_discount)  # under-priced -> positive edge
            elif rank in trap_ranks:
                book_price = min(0.95, fair + 0.06)  # over-priced trap
            else:
                book_price = min(0.95, fair + 0.015)  # roughly fair + half-spread
            model_probs[f"{label} - {team}"] = fair
            books[f"{label} - {team}"] = _sample_book(f"{label}::{team}", book_price, vig=0.02, depth_usd=40.0)

        for edge in detect_edges(model_probs, books, target_usd, min_edge_pp, fees_bps):
            sized = size_bet(
                model_probability=edge.model_probability,
                executable_price=edge.executable_price,
                fillable_usd=edge.fillable_usd,
                config=kelly_config,
                current_total_exposure_usd=exposure,
            )
            if sized.reason == "ok":
                exposure += sized.fillable_size_usd
            slate.append(
                {
                    "market": label,
                    "team": edge.market_name.split(" - ", 1)[-1],
                    "model_prob": round(edge.model_probability, 4),
                    "exec_price": round(edge.executable_price, 4),
                    "edge_pp": round(edge.edge_pp, 2),
                    "ev_per_dollar": round(edge.ev_per_dollar, 4),
                    "kelly_fraction": round(sized.fractional_kelly_fraction, 4),
                    "uncapped_size_usd": sized.uncapped_size_usd,
                    "capped_size_usd": sized.capped_size_usd,
                    "kelly_size_usd": sized.fillable_size_usd,
                    "status": sized.reason,
                    "actionable": sized.reason == "ok",
                }
            )

    # Advance markets carry higher probabilities, so a soft line there is actually betable;
    # champion/final edges are real but usually below the $5 min-fill on a $75 bankroll.
    run_family("Advance", "p_advanced", value_ranks={0, 1, 3}, trap_ranks={2}, value_discount=0.13)
    run_family("Champion", "p_champion", value_ranks={0, 2, 3}, trap_ranks={1, 4})
    run_family("Reach Final", "p_finalist", value_ranks={1, 4, 6}, trap_ranks={0, 3})
    slate.sort(key=lambda row: row["edge_pp"], reverse=True)

    # Scanner: each group's "win group" family should sum to ~1 after de-vig.
    scanner_markets: list[ConsistencyMarket] = []
    win_group_lookup = submarkets.set_index("team")["p_win_group"].to_dict()
    for letter, spec in groups.items():
        for team in spec["teams"]:
            fair = float(win_group_lookup.get(team, 0.25))
            price = min(0.95, max(0.01, fair + 0.03 / 4 + rng.uniform(-0.01, 0.02)))
            scanner_markets.append(
                ConsistencyMarket(
                    market_name=f"Win Group {letter} - {team}",
                    group_key=f"Group {letter}",
                    executable_yes_price=round(price, 3),
                    fillable_usd=30.0,
                )
            )
    flags = scan_sum_to_one(scanner_markets, tolerance_pp=8.0, min_fillable_usd=5.0)
    scanner_flags = [
        {
            "group": flag.group_key,
            "sum_implied": round(flag.sum_implied_probability, 3),
            "gap_pp": round(flag.gap_pp, 2),
            "direction": flag.direction,
            "markets": flag.market_count,
        }
        for flag in flags
    ]

    return {
        "source": "SAMPLE",
        "note": "Prices are synthetic, priced around model probabilities. Wire the live CLOB to replace.",
        "slate": slate,
        "scanner_flags": scanner_flags,
        "total_recommended_exposure_usd": round(exposure, 2),
    }


def _load_fifa_rankings(normalizer: TeamNameNormalizer, refresh: bool = False) -> pd.DataFrame | None:
    """Download/cache the historical FIFA ranking, derive numeric rank from points, and
    normalize team names so the point-in-time join lines up with the results dataset.

    Source goes to ~2024, so 2026 fixtures use the latest available ranking (slightly stale).
    """
    if refresh or not RANKINGS_RAW.exists():
        try:
            import requests

            RANKINGS_RAW.parent.mkdir(parents=True, exist_ok=True)
            response = requests.get(RANKINGS_URL, timeout=30)
            response.raise_for_status()
            RANKINGS_RAW.write_bytes(response.content)
        except Exception:
            if not RANKINGS_RAW.exists():
                return None
    try:
        frame = pd.read_csv(RANKINGS_RAW)
    except Exception:
        return None
    frame = frame.rename(columns={"total_points": "points", "date": "rank_date"})
    if not {"team", "points", "rank_date"}.issubset(frame.columns):
        return None
    frame["rank_date"] = pd.to_datetime(frame["rank_date"], errors="coerce")
    frame = frame.dropna(subset=["rank_date"])
    frame = frame[frame["rank_date"] >= pd.Timestamp("2014-01-01")]  # cover the window with margin
    frame["team"] = frame["team"].astype(str).map(normalizer.canonical)
    frame["points"] = pd.to_numeric(frame["points"], errors="coerce")
    frame = frame.dropna(subset=["points"])
    frame["rank"] = frame.groupby("rank_date")["points"].rank(ascending=False, method="min").astype(int)
    return frame[["rank_date", "team", "rank", "points"]]


def _build_squads(path: Path, normalizer: TeamNameNormalizer, elo_leaderboard: list[dict]) -> dict:
    """Per-team key players + squad market value, with a talent-vs-results read (value rank vs Elo rank)."""
    if not path.exists():
        return {"available": False, "teams": []}
    frame = pd.read_csv(path)
    elo_by_team = {normalizer.canonical(str(r["team"])): r["rating"] for r in elo_leaderboard}
    rows = []
    for _, r in frame.iterrows():
        team = normalizer.canonical(str(r["team"]))
        players = [p.strip() for p in str(r.get("key_players", "")).split(";") if p.strip()]
        rows.append({
            "team": team,
            "value_m": float(r.get("squad_value_m", 0) or 0),
            "key_players": players,
            "elo": round(float(elo_by_team.get(team, 1500.0)), 1),
        })
    # rank by squad value and by Elo (among listed teams); talent_gap > 0 means results beat talent
    by_value = sorted(rows, key=lambda x: -x["value_m"])
    by_elo = sorted(rows, key=lambda x: -x["elo"])
    value_rank = {x["team"]: i + 1 for i, x in enumerate(by_value)}
    elo_rank = {x["team"]: i + 1 for i, x in enumerate(by_elo)}
    for x in rows:
        x["value_rank"] = value_rank[x["team"]]
        x["elo_rank"] = elo_rank[x["team"]]
        x["talent_gap"] = value_rank[x["team"]] - elo_rank[x["team"]]  # + = overperforming talent
    rows.sort(key=lambda x: -x["value_m"])
    return {
        "available": True,
        "teams": rows,
        "note": "Squad market value (approx EUR m) + key players. talent_gap = value_rank - Elo_rank "
                "(positive = results outrun talent; negative = underachieving the roster). Manual data; "
                "Codex's FBref/Transfermarkt ingestion will expand + auto-update it.",
    }


def _live_wc_rows(path: Path, normalizer: TeamNameNormalizer, after_date, existing: pd.DataFrame | None = None) -> pd.DataFrame | None:
    """Live World Cup results that occurred after the dataset cutoff — to feed the Elo so
    ratings update as the tournament plays out (these are NOT added to model training).

    Dedups by match identity (unordered team pair within ±5 days) against ``existing`` (the
    training base), so a fixture present in BOTH results.csv and wc2026_results.csv on adjacent
    dates isn't rated twice by the Elo engine."""
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    if frame.empty:
        return None
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["date"] > pd.Timestamp(after_date)].dropna(subset=["home_score", "away_score"]).copy()
    if frame.empty:
        return None
    frame["home_team"] = frame["home_team"].map(normalizer.canonical)
    frame["away_team"] = frame["away_team"].map(normalizer.canonical)
    if existing is not None and not existing.empty:
        ex = existing[["date", "home_team", "away_team"]].copy()
        ex["date"] = pd.to_datetime(ex["date"])
        ex = ex[ex["date"] >= pd.Timestamp(after_date) - pd.Timedelta(days=30)]  # only recent can collide
        ex_pairs: dict = {}
        for _, r in ex.iterrows():
            key = frozenset((normalizer.canonical(str(r["home_team"])), normalizer.canonical(str(r["away_team"]))))
            ex_pairs.setdefault(key, []).append(r["date"])

        def _dup(row) -> bool:
            for d in ex_pairs.get(frozenset((row["home_team"], row["away_team"])), []):
                if abs((row["date"] - d).days) <= 5:
                    return True
            return False

        frame = frame[~frame.apply(_dup, axis=1)]
        if frame.empty:
            return None
    frame["home_score"] = frame["home_score"].astype(int)
    frame["away_score"] = frame["away_score"].astype(int)
    frame["tournament"] = "FIFA World Cup"
    frame["neutral"] = True
    cols = ["date", "home_team", "away_team", "home_score", "away_score", "tournament", "neutral"]
    # carry in-depth stats through to the xG-aware Elo update if the results file has them
    for stat in ("home_xg", "away_xg"):
        if stat in frame.columns:
            frame[stat] = pd.to_numeric(frame[stat], errors="coerce")
            cols.append(stat)
    return frame[cols]


def run_pipeline(
    refresh: bool = False,
    n_sims: int = 3000,
    history_years: int = 12,
    hooks: PipelineHooks | None = None,
) -> dict:
    log = _Log()
    hooks = hooks or PipelineHooks()
    config = load_config(PROJECT_ROOT / "config.yaml")
    state: dict = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": config,
        "data": {},
        "elo": {},
        "model": {},
        "goal_model": {},
        "simulation": {},
        "markets": {},
        "notes": [
            "Model is gradient-boosted trees (XGBoost or HistGradientBoosting), not a neural net.",
            "Group draw is the OFFICIAL 2026 draw; the knockout third-place assignment uses a non-official resolver until FIFA Annex C is loaded.",
            "Live tracker scores predictions vs real results; matches after 2026-06-12 are genuine out-of-sample.",
            "Live WC results feed an xG-AWARE Elo: a match's rating update blends the scoreline with the "
            "xG-deserved result (when home_xg/away_xg are in wc2026_results.csv), so lucky wins move ratings "
            "less (e.g. USA's 4-1 on 1.35 xG vs Paraguay gained 11 fewer Elo than the scoreline alone).",
            "Trades use the LIVE Polymarket CLOB order books (champion + group-winner markets), executable asks after fees; "
            "SAMPLE synthetic books are a fallback only if the feed is unreachable (the Trades tab tags which is in use).",
        ],
    }

    # 1. Results
    results = None
    hooks.stage("Load results")
    with log.step("Load results") as step:
        results_full = load_results(RAW_RESULTS_PATH, mapping_path=ALIASES_PATH, refresh=refresh)
        # Keep only played matches with both scores; the source carries a few blank rows.
        results_full = results_full.dropna(subset=["home_score", "away_score"]).reset_index(drop=True)
        results_full["home_score"] = results_full["home_score"].astype(int)
        results_full["away_score"] = results_full["away_score"].astype(int)
        dataset_max = results_full["date"].max()
        # Elo uses ALL history (a cumulative rating needs long history to spread teams apart);
        # the model/goal-model train only on the last `history_years` (recency).
        cutoff = dataset_max - pd.DateOffset(years=history_years)
        results = results_full[results_full["date"] >= cutoff].reset_index(drop=True)
        source = "downloaded" if refresh else ("cached" if RAW_RESULTS_PATH.exists() else "downloaded")
        state["data"] = {
            "source": source,
            "window_years": history_years,
            "n_matches": int(len(results)),
            "elo_matches": int(len(results_full)),
            "n_teams": int(pd.concat([results["home_team"], results["away_team"]]).nunique()),
            "date_min": str(results["date"].min().date()),
            "date_max": str(results["date"].max().date()),
            "path": str(RAW_RESULTS_PATH),
        }
        step.ok(f"{len(results):,} train (last {history_years}y) / {len(results_full):,} for Elo, {state['data']['n_teams']} teams")

    if results is None:
        state["pipeline_log"] = log.entries
        return state

    # 2. Elo — built on the window + live WC results so ratings update each matchday
    elo = None
    normalizer = TeamNameNormalizer.from_yaml(ALIASES_PATH)
    hooks.stage("Build Elo")
    with log.step("Build Elo") as step:
        wc_live = _live_wc_rows(WC_RESULTS_PATH, normalizer, results_full["date"].max(), existing=results_full)
        live_n = 0 if wc_live is None else len(wc_live)
        if live_n:
            elo_input = pd.concat([results_full, wc_live], ignore_index=True, sort=False).sort_values("date")
        else:
            elo_input = results_full
        elo = EloEngine(EloConfig()).process_matches(elo_input, host_nations=HOST_NATIONS_2026)
        leaderboard = sorted(
            ({"team": team, "rating": round(rating, 1)} for team, rating in elo.final_ratings.items()),
            key=lambda row: row["rating"],
            reverse=True,
        )
        state["elo"] = {
            "config": asdict(EloConfig()),
            "n_teams_rated": len(leaderboard),
            "live_results_fed": live_n,
            "leaderboard": leaderboard[:60],
        }
        hooks.elo_ready(leaderboard[:20])
        state["squads"] = _build_squads(SQUADS_PATH, normalizer, leaderboard)
        step.ok(f"top: {leaderboard[0]['team']} {leaderboard[0]['rating']} (+{live_n} live WC results)")

    # 3. Features + calibrated model
    train_result = None
    recent = None
    rankings = None
    feature_columns: list[str] = []
    hooks.stage("Build features + train calibrated model")
    with log.step("Build features + train calibrated model") as step:
        rankings = _load_fifa_rankings(normalizer, refresh=refresh)
        recent = results.copy()  # already the last-N-years window from stage 1
        features = build_match_features(recent, elo, rankings=rankings, host_nations=HOST_NATIONS_2026)
        feature_columns = _select_feature_columns(features)
        labelled = features.dropna(subset=["target_1x2"]).copy()
        matrix = _to_model_matrix(labelled, feature_columns)
        matrix["date"] = labelled["date"].to_numpy()
        matrix["target_1x2"] = labelled["target_1x2"].to_numpy()
        train_result = train_1x2(
            matrix, feature_columns, progress=hooks.train_step,
            model_type=str(config.get("model_type", "gbt")), refit_full=True,
        )
        importance = [
            {"feature": name, "importance": round(value, 4)}
            for name, value in (train_result.feature_importance or [])
        ]
        state["model"] = {
            "kind": train_result.model_kind,
            "n_train": train_result.n_train,
            "n_calibration": train_result.n_calibration,
            "n_validation": train_result.n_validation,
            "feature_columns": feature_columns,
            "feature_importance": importance,
            "calibration": {
                "method": train_result.calibration_method,
                "uncalibrated": _report_dict(train_result.uncalibrated_report),
                "calibrated": _report_dict(train_result.report),
            },
            "reliability": _reliability_records(train_result.report),
            "architecture": _architecture(train_result, feature_columns),
        }
        try:
            backtest_predictor = CalibratedPredictor.from_train_result(train_result)
            state["backtest"] = tournament_backtest(labelled, backtest_predictor)
        except Exception as exc:  # never let the backtest break training
            state["backtest"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        step.ok(
            f"{train_result.model_kind}: logloss {train_result.report.log_loss:.3f} "
            f"-> verdict {train_result.report.verdict}"
        )

    # 4. Goal model
    goal_model = None
    hooks.stage("Fit goal model")
    with log.step("Fit goal model") as step:
        goal_window = results.tail(8000)
        goal_model = PoissonGoalModel().fit(goal_window)
        state["goal_model"] = {
            "global_home_goals": round(goal_model.global_home_goals, 3),
            "global_away_goals": round(goal_model.global_away_goals, 3),
            "n_teams": len(goal_model.attack_strength),
        }
        step.ok(f"home xg base {goal_model.global_home_goals:.2f}")

    # 4b. Live fixtures + predictions vs real results (official 2026 draw)
    sim_groups = None
    display_groups: dict[str, list[str]] = {}
    hooks.stage("Predict fixtures vs results")
    with log.step("Predict fixtures vs results") as step:
        if train_result is None or goal_model is None or recent is None:
            raise RuntimeError("model/goal-model/features unavailable for tracker")
        normalizer = TeamNameNormalizer.from_yaml(ALIASES_PATH)
        groups_raw = load_groups(GROUPS_PATH)
        display_groups = {g: [normalizer.canonical(t) for t in teams] for g, teams in groups_raw.items()}
        wc_results = pd.read_csv(WC_RESULTS_PATH) if WC_RESULTS_PATH.exists() else None
        if SCHEDULE_PATH.exists():
            base_fixtures = load_schedule(SCHEDULE_PATH, groups_raw, normalizer)
        else:
            base_fixtures = build_group_fixtures(groups_raw)
        fixtures = overlay_results(base_fixtures, wc_results, normalizer)
        sim_groups = build_sim_groups(fixtures)

        all_teams = sorted(set(fixtures["home_team"]) | set(fixtures["away_team"]))
        unmapped = [t for t in all_teams if t not in elo.final_ratings]

        feature_input_cols = ["date", "home_team", "away_team", "neutral", "tournament", "home_score", "away_score"]
        recent_min = recent[feature_input_cols].copy()
        recent_min["stage"] = ""
        fixture_cols = feature_input_cols + ["stage"]
        combined = pd.concat([recent_min, fixtures[fixture_cols]], ignore_index=True, sort=False)
        fix_features = build_match_features(combined, elo, rankings=rankings, host_nations=HOST_NATIONS_2026)

        predictor = CalibratedPredictor.from_train_result(train_result)
        predictions = predict_fixtures(fixtures, fix_features, predictor, goal_model, feature_columns)
        scorecard = score_tracker(predictions)
        state["tracker"] = {
            "predictions": predictions,
            "scorecard": scorecard,
            "unmapped_teams": unmapped,
            "data_cutoff": "2026-06-12",
            "n_fixtures": len(predictions),
        }
        comp = scorecard.get("completed", {})
        step.ok(
            f"{comp.get('n', 0)} scored, acc {comp.get('accuracy', '-')}, "
            f"ll {comp.get('log_loss', '-')}; unmapped {len(unmapped)}"
        )

    # 5. Monte Carlo on the official 2026 draw
    submarkets = None
    hooks.stage("Run Monte Carlo")
    with log.step("Run Monte Carlo") as step:
        if elo is None or goal_model is None:
            raise RuntimeError("Elo / goal model unavailable")
        if sim_groups is not None:
            groups = sim_groups
            draw_label = "OFFICIAL 2026 DRAW"
            shown_groups = display_groups
        else:
            groups = _demo_draw(state["elo"]["leaderboard"])
            draw_label = "DEMO (Elo-seeded)"
            shown_groups = {letter: spec["teams"] for letter, spec in groups.items()}
        sampler = PoissonScorelineSampler(goal_model, elo=elo, seed=1)
        # Official FIFA Annex C third-place table (Codex) → exact R32 bracket assignment.
        third_place_table = None
        if THIRD_PLACE_PATH.exists():
            try:
                third_place_table = load_third_place_assignment_table(THIRD_PLACE_PATH)
            except Exception as exc:
                log.entries.append({"step": "Third-place table", "status": "warn",
                                    "detail": f"{type(exc).__name__}: {exc}", "seconds": 0.0})
        simulator = TournamentSimulator(
            scoreline_sampler=sampler,
            knockout_win_probability=_elo_win_probability(elo),
            third_place_assignment_table=third_place_table,
            rng=random.Random(2026),
        )
        submarkets = simulator.simulate_many(
            groups, n_sims=n_sims, on_progress=hooks.sim_progress, progress_every=max(25, n_sims // 200)
        )
        state["simulation"] = {
            "n_sims": n_sims,
            "draw_label": draw_label,
            "groups": shown_groups,
            "submarkets": [
                {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}
                for row in submarkets.to_dict("records")
            ],
        }
        state["simulation"]["sample_bracket"] = simulator.last_bracket
        champ = submarkets.iloc[0]
        step.ok(f"favourite: {champ['team']} ({champ['p_champion'] * 100:.1f}% champion)")

    # 6. Markets / edges / scanner — live Polymarket CLOB, SAMPLE fallback
    hooks.stage("Detect edges + size bets + scan")
    with log.step("Detect edges + size bets + scan") as step:
        if submarkets is None:
            raise RuntimeError("No simulation output for market comparison")
        market_normalizer = TeamNameNormalizer.from_yaml(ALIASES_PATH)
        live = None
        if config.get("use_live_markets", True):
            try:
                live = _build_live_markets(submarkets, market_normalizer, config)
            except Exception as exc:  # network/rate-limit — degrade to SAMPLE
                log.entries.append(
                    {"step": "Live Polymarket", "status": "warn", "detail": f"{type(exc).__name__}: {exc}", "seconds": 0.0}
                )
                live = None
        if live is not None:
            state["markets"] = live
        else:
            scanner_groups = sim_groups if sim_groups is not None else _demo_draw(state["elo"]["leaderboard"])
            state["markets"] = _build_markets(submarkets, scanner_groups, config)
        step.ok(
            f"{state['markets'].get('source')}: {len(state['markets']['slate'])} edges, "
            f"{len(state['markets']['scanner_flags'])} flags"
        )

    # 6b. Correlation-aware portfolio: optimise the whole book against the simulated joint
    # distribution (growth-optimal / fractional-Kelly under concentration caps) instead of
    # sizing each bet independently. Surfaces near-duplicate (correlated) exposures.
    state["portfolio"] = {"available": False, "reason": "not computed"}
    hooks.stage("Optimize portfolio")
    with log.step("Optimize portfolio") as step:
        slate = state.get("markets", {}).get("slate", [])
        bankroll = float(config.get("bankroll_usd", 200))
        team_to_group = {t: g for g, teams in display_groups.items() for t in teams}
        bets = _slate_to_bets(slate, team_to_group, bankroll)
        if bets and sim_groups is not None and simulator is not None:
            samples = simulator.sample_market_outcomes(sim_groups, n_sims=min(int(n_sims), 10000))
            # Cache the simulated outcomes so the live price-watcher can re-optimise the book on
            # fresh prices without re-simulating (model probabilities don't change intraday).
            try:
                SIM_OUTCOMES_PATH.write_text(json.dumps(samples), encoding="utf-8")
            except OSError:
                pass
            state["portfolio"] = build_portfolio(bets, samples, bankroll_usd=bankroll)
            rec = state["portfolio"].get("recommended", {})
            step.ok(
                f"{len(bets)} candidates -> {len(rec.get('allocation', []))} positions, "
                f"Sharpe {rec.get('stats', {}).get('sharpe', 0):.2f}, "
                f"{len(state['portfolio'].get('correlations', []))} corr-pairs flagged"
            )
        else:
            state["portfolio"] = {"available": False, "reason": "no positive-edge candidates mapped to markets"}
            step.warn("no positive-edge candidates mapped to markets")

    # 7. Persistent paper-trading account: settle resolved, mark-to-market, execute new trades
    hooks.stage("Paper account")
    with log.step("Paper account") as step:
        markets_state = state.get("markets", {})
        account = load_account(PAPER_ACCOUNT_PATH, float(config.get("bankroll_usd", 75)))
        now_iso = datetime.now(timezone.utc).isoformat()
        account = update_account(
            account, markets_state.get("slate", []), markets_state.get("market_prices", {}), now_iso,
            size_mode=str(config.get("paper_size_mode", "kelly")),
            max_total_exposure_pct=float(config.get("max_total_exposure_pct", 0.80)),
            min_stake_usd=float(config.get("min_fillable_usd", 5)),
        )
        save_account(account, PAPER_ACCOUNT_PATH)
        state["paper_account"] = account
        state["execution"] = live_execution_status(
            enabled=bool(config.get("use_live_execution", False)), has_credentials=False
        )
        summary = account["summary"]
        step.ok(
            f"equity ${summary['equity']} ({summary['total_return_pct']:+}%) · "
            f"{summary['n_open']} open / {summary['n_settled']} settled · ${summary['cash']} cash"
        )

    state["pipeline_log"] = log.entries
    return state


def _report_dict(report) -> dict:
    if report is None:
        return {}
    return {
        "log_loss": round(report.log_loss, 4),
        "brier": round(report.brier, 4),
        "accuracy": round(report.accuracy, 4),
        "verdict": report.verdict,
    }


def _reliability_records(report) -> list[dict]:
    if report is None or report.reliability is None or report.reliability.empty:
        return []
    records = []
    for row in report.reliability.to_dict("records"):
        records.append(
            {
                "class": row.get("class"),
                "bin": row.get("bin"),
                "mean_predicted": round(float(row.get("mean_predicted", 0.0)), 3),
                "observed_rate": round(float(row.get("observed_rate", 0.0)), 3),
                "count": int(row.get("count", 0)),
            }
        )
    return records


def _architecture(train_result, feature_columns: list[str]) -> dict:
    return {
        "inputs": len(feature_columns),
        "stages": [
            {"name": "Point-in-time features", "detail": f"{len(feature_columns)} inputs (Elo, form, rest, context)"},
            {"name": "Gradient-boosted trees", "detail": f"{train_result.model_kind}, depth-3 boosting"},
            {"name": "Isotonic calibration", "detail": "per-class isotonic + renormalize on a time-forward slice"},
            {"name": "1X2 output", "detail": "calibrated P(home win / draw / away win)"},
        ],
    }


def write_state(state: dict, path: Path = STATE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    return path


class _CliHooks(PipelineHooks):
    """Prints live progress to stdout so the CLI run doesn't look frozen."""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._sim_marker = 0

    def stage(self, name: str, status: str = "start", detail: str = "") -> None:
        print(f"  [{time.perf_counter() - self._t0:6.1f}s] {name} ...", flush=True)

    def sim_progress(self, done: int, total: int, counts: dict, bracket: dict | None = None) -> None:
        if total and (done - self._sim_marker >= total / 10 or done == total):
            self._sim_marker = done
            print(f"            Monte Carlo {done:,}/{total:,}  ({100 * done // total}%)", flush=True)


def main() -> None:
    import argparse
    import sys

    try:  # so non-ASCII team names in the log can't crash a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Run the World Cup edge pipeline and write dashboard state.")
    parser.add_argument("--refresh", action="store_true", help="Force re-download of results.csv")
    parser.add_argument("--sims", type=int, default=1000000, help="Monte Carlo iterations (~5k/s; 1M ≈ 3-4 min)")
    parser.add_argument("--years", type=int, default=12, help="Use only the last N years of matches")
    args = parser.parse_args()

    print(
        f"Running pipeline: {args.sims:,} sims, last {args.years}y of data, live Polymarket fetch.\n"
        f"Progress prints below as each step finishes (full run ~2-5 min). It's DONE when you see 'Wrote ...'.",
        flush=True,
    )
    try:
        state = run_pipeline(refresh=args.refresh, n_sims=args.sims, history_years=args.years, hooks=_CliHooks())
    except Exception:  # pragma: no cover - top-level guard
        traceback.print_exc()
        raise
    path = write_state(state)
    print(f"\n=== DONE - wrote {path} ===\n")
    for entry in state.get("pipeline_log", []):
        print(f"  [{entry['status']:>5}] {entry['step']} ({entry['seconds']}s) {entry['detail']}")


if __name__ == "__main__":
    main()
