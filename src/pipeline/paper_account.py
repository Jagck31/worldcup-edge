"""Persistent paper-trading account.

A real (fake-money) trading account that survives across pipeline runs. Each run it:
  1. marks open positions to market using live Polymarket mid prices,
  2. settles positions whose market has resolved (price → ~1 or ~0),
  3. opens new paper trades for actionable recommendations it isn't already holding,
then persists to data/processed/paper_account.json.

No real money, no wallet, no signing — this is a simulated ledger so the model's
edge-finding + sizing strategy can be tracked against live prices over the tournament.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


SETTLE_HIGH = 0.98  # market resolved YES
SETTLE_LOW = 0.02   # market resolved NO


def load_account(path: Path, starting_bankroll: float) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # Don't silently zero the ledger — preserve the corrupt file for inspection and warn loudly.
            print(f"WARNING: {path.name} is corrupt ({exc}); starting a fresh account. "
                  f"Saved corrupt copy to {path.name}.corrupt", file=sys.stderr, flush=True)
            try:
                os.replace(path, path.with_suffix(".json.corrupt"))
            except OSError:
                pass
    return {
        "starting_bankroll": round(float(starting_bankroll), 2),
        "cash": round(float(starting_bankroll), 2),
        "realized_pnl": 0.0,
        "n_trades": 0,
        "positions": [],
        "history": [],
        "created_at": None,
        "updated_at": None,
        "summary": {},
    }


def _current_price(side: str, mid: float) -> float:
    return mid if side == "YES" else 1.0 - mid


def update_account(
    account: dict,
    slate: list[dict],
    market_prices: dict[str, float],
    now_iso: str,
    size_mode: str = "fillable",
    max_total_exposure_pct: float = 0.80,
    min_stake_usd: float = 5.0,
) -> dict:
    """Settle/mark/deploy the paper book.

    ``size_mode`` controls how aggressively capital is deployed to actionable edges:
      * "fillable" — size at the live CLOB depth (tiny; realistic for real fills).
      * "kelly"    — size at the bankroll-capped fractional-Kelly stake (``capped_size_usd``),
                     deploying the bankroll up to the exposure cap. Right for a paper book
                     whose point is to show the strategy at full size; it assumes fills it
                     couldn't actually get on a thin book (a deliberate paper-only choice).
    Positions are topped up toward their target each cycle, best edge first, under the cap.
    """
    if account.get("created_at") is None:
        account["created_at"] = now_iso

    # 1 + 2: mark to market and settle resolved positions.
    still_open: list[dict] = []
    for position in account.get("positions", []):
        mid = market_prices.get(position["market_id"])
        if mid is None:
            still_open.append(position)  # no fresh price; carry last mark
            continue
        resolved_yes = mid >= SETTLE_HIGH
        resolved_no = mid <= SETTLE_LOW
        if resolved_yes or resolved_no:
            won = (position["side"] == "YES" and resolved_yes) or (position["side"] == "NO" and resolved_no)
            settle_value = round(position["shares"] * 1.0, 2) if won else 0.0
            pnl = round(settle_value - position["stake"], 2)
            account["cash"] = round(account["cash"] + settle_value, 2)
            account["realized_pnl"] = round(account["realized_pnl"] + pnl, 2)
            account["history"].append(
                {
                    **position,
                    "status": "settled",
                    "result": "WON" if won else "LOST",
                    "exit_price": 1.0 if won else 0.0,
                    "settle_value": settle_value,
                    "pnl": pnl,
                    "settled_at": now_iso,
                }
            )
        else:
            current = _current_price(position["side"], mid)
            position["current_price"] = round(current, 4)
            position["current_value"] = round(position["shares"] * current, 2)
            position["unrealized_pnl"] = round(position["current_value"] - position["stake"], 2)
            still_open.append(position)
    account["positions"] = still_open

    # 3: deploy capital to actionable edges — best edge first, sizing by `size_mode`,
    # topping up existing positions toward their target and opening new ones, under the cap.
    pos_by_key = {(p["market_id"], p["side"]): p for p in account["positions"]}
    exposure_cap = account["starting_bankroll"] * max_total_exposure_pct
    invested_now = round(sum(p["stake"] for p in account["positions"]), 2)
    for row in sorted(slate, key=lambda r: -(r.get("edge_pp") or 0.0)):
        if not row.get("actionable"):
            continue
        price = float(row.get("exec_price", 0.0))
        if price <= 0 or price >= 1:
            continue
        mid = market_prices.get(row.get("market_id"))
        if mid is not None and (mid >= SETTLE_HIGH or mid <= SETTLE_LOW):
            continue  # market already resolved — settlement handles it; never (re)deploy here
        if size_mode == "kelly":
            target = float(row.get("capped_size_usd") or row.get("kelly_size_usd") or 0.0)
        else:
            target = float(row.get("kelly_size_usd", 0.0))
        if target <= 0:
            continue
        key = (row.get("market_id"), str(row.get("side", "YES")))
        held = pos_by_key.get(key)
        held_stake = held["stake"] if held else 0.0
        # only ADD toward the target; never sell down on a marked-up entry
        delta = min(target - held_stake, max(0.0, exposure_cap - invested_now), account["cash"])
        if delta < min_stake_usd:
            continue
        add_shares = delta / price
        account["cash"] = round(account["cash"] - delta, 2)
        invested_now = round(invested_now + delta, 2)
        if held:
            new_shares = round(held["shares"] + add_shares, 1)
            # share-weighted blend of model_prob so old shares aren't retro-revalued
            old_mp = float(held.get("model_prob", 0.0) or 0.0)
            new_mp = float(row.get("model_prob", old_mp) or old_mp)
            held["model_prob"] = round((held["shares"] * old_mp + add_shares * new_mp) / new_shares, 4) if new_shares else round(new_mp, 4)
            held["shares"] = new_shares
            held["stake"] = round(held["stake"] + delta, 2)
            held["entry_price"] = round(held["stake"] / held["shares"], 4) if held["shares"] else round(price, 4)
            # mark to the MID (consistent with the settle/mark pass above), not the slate ask
            mark = _current_price(held["side"], mid) if mid is not None else price
            held["current_price"] = round(mark, 4)
            held["current_value"] = round(held["shares"] * mark, 2)
            held["unrealized_pnl"] = round(held["current_value"] - held["stake"], 2)
        else:
            account["n_trades"] += 1
            new_pos = {
                "trade_id": account["n_trades"],
                "opened_at": now_iso,
                "market": row.get("market", ""),
                "team": row.get("team", ""),
                "market_id": row.get("market_id", ""),
                "side": str(row.get("side", "YES")),
                "action": row.get("action", ""),
                "entry_price": round(price, 4),
                "model_prob": round(float(row.get("model_prob", 0.0)), 4),
                "shares": round(add_shares, 1),
                "stake": round(delta, 2),
                "current_price": round(price, 4),
                "current_value": round(delta, 2),
                "unrealized_pnl": 0.0,
                "edge_pp": row.get("edge_pp"),
                "risk_label": row.get("risk_label", ""),
                "settle_date": row.get("settle_date"),
                "status": "open",
            }
            account["positions"].append(new_pos)
            pos_by_key[key] = new_pos

    # Totals + profit metrics.
    positions = account["positions"]
    history = account["history"]
    invested = round(sum(p["stake"] for p in positions), 2)
    open_value = round(sum(p["current_value"] for p in positions), 2)
    equity = round(account["cash"] + open_value, 2)
    start = account["starting_bankroll"]

    # Expected value: each position pays shares × $1 with the model's win probability.
    expected_payout = sum(p["shares"] * p.get("model_prob", 0.0) for p in positions)
    expected_value = expected_payout - invested  # expected profit at settlement IF the model is right
    max_payout = sum(p["shares"] for p in positions)  # if every open position wins
    wins = sum(1 for h in history if h.get("result") == "WON")
    avg_edge = (sum(abs(p.get("edge_pp", 0) or 0) for p in positions) / len(positions)) if positions else 0.0

    account["updated_at"] = now_iso
    account["summary"] = {
        "starting_bankroll": start,
        "cash": round(account["cash"], 2),
        "invested": invested,
        "open_value": open_value,
        "equity": equity,
        "unrealized_pnl": round(open_value - invested, 2),
        "realized_pnl": round(account["realized_pnl"], 2),
        "total_pnl": round(equity - start, 2),
        "total_return_pct": round(100.0 * (equity - start) / start, 2) if start else 0.0,
        # Expected (model-implied) profit metrics:
        "expected_value_usd": round(expected_value, 2),
        "expected_roi_pct": round(100.0 * expected_value / invested, 1) if invested else 0.0,
        "expected_settle_equity": round(account["cash"] + expected_payout, 2),
        "max_payout_usd": round(max_payout, 2),
        "avg_edge_pp": round(avg_edge, 1),
        "win_rate_pct": round(100.0 * wins / len(history), 1) if history else None,
        "n_open": len(positions),
        "n_settled": len(history),
    }
    return account


def save_account(account: dict, path: Path) -> Path:
    """Atomic write (temp + os.replace) so a crash mid-write can't truncate the ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(account, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path
