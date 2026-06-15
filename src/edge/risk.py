"""Concentration + capital-lock risk of the open paper book.

The paper book sizes by bankroll fractional-Kelly, best-edge-first, under a single-bet cap and
a total-exposure cap. On a slate dominated by a few big derived-market edges that concentrates
hard: on 2026-06-15 the book held five bets of ~$1.3-2.0k (top-3 = ~50% of a $10k bankroll) plus
27 token $10 bets, all settling within one fortnight -- which is most of the -15% mark-to-market
drawdown. The Kelly/exposure caps bound *each* bet and the *total*, but nothing bounds how few
names the risk piles into, or how long capital is locked. This quantifies both so the loop can
see concentration move as the single-bet cap / sizing rules change. Pure function, no I/O.
"""
from __future__ import annotations

from typing import Mapping, Sequence


def position_risk(positions: Sequence[Mapping], bankroll: float) -> dict:
    stakes = sorted((float(p.get("stake") or 0.0) for p in positions), reverse=True)
    total = sum(stakes)
    pct = (lambda x: round(x / bankroll * 100, 2)) if bankroll else (lambda x: None)

    buckets: dict[str, float] = {}
    for p in positions:
        settle = str(p.get("settle_date") or "")[:10]
        buckets[settle] = buckets.get(settle, 0.0) + float(p.get("stake") or 0.0)

    return {
        "n_open": len(positions),
        "invested_usd": round(total, 2),
        "invested_pct": pct(total),
        "max_position_pct": pct(stakes[0]) if stakes else 0.0,
        "top3_pct": pct(sum(stakes[:3])),
        "top5_pct": pct(sum(stakes[:5])),
        "settle_buckets": {k: round(v, 2) for k, v in sorted(buckets.items())},
        "n_settle_buckets": len([k for k in buckets if k]),
    }
