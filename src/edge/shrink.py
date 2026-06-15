"""Shrink derived-market model probabilities toward the de-vigged market.

The tournament-derived probabilities we trade -- champion, win-group, reach-stage, advance --
come from a Monte-Carlo over Elo, not from a directly fitted market model. On *partition*
markets especially (exactly one team wins a group; exactly one team is champion) the
simulation often disagrees sharply with the real-money Polymarket book: in some groups it is
far more concentrated on the Elo favourite than the market, in others far less. Those sharp
disagreements are precisely where a naive (model - price) edge is most likely to be model
error rather than a true mispricing -- and the paper book bled on exactly those bets
(2026-06-14: a slate full of big NO edges on group-winner long-shots, equity drifting down).

This applies a deliberately *humble* shrinkage: blend each derived YES probability a fraction
``weight`` toward the market's de-vigged implied probability.

    weight = 0.0  -> raw model (original behaviour; nothing changes)
    weight = 1.0  -> defer entirely to the de-vigged market

It is a variance-reduction prior, NOT a claim the market is always right. Both the model and
the market carry information; a partial blend reduces the damage when either is badly wrong on
a single contract. The weight is meant to be *tuned against realised calibration* as group and
championship markets resolve (see AUTONOMOUS_LOOP.md) -- start conservative, let the data move
it. The match-level 1X2 model is untouched; only the simulated tournament markets are shrunk.
"""
from __future__ import annotations

from typing import Iterable, Mapping

_EPS = 1e-6


def _devig(
    market_mid: Mapping[str, float], partitions: Iterable[Iterable[str]]
) -> dict[str, float]:
    """Market-implied probabilities, normalised within each partition that sums to one.

    A *partition* is a set of mutually-exclusive contracts that must sum to 1 (the win-group
    markets within one group; all champion markets). Normalising by the partition's mid sum
    removes the book's vig so the blend target is a real probability distribution. Contracts
    not in any multi-member partition (independent binaries like reach-stage / advance) keep
    their own mid -- a single binary's mid is already a fair-ish probability.
    """
    devig: dict[str, float] = {}
    covered: set[str] = set()
    for part in partitions:
        mids = {n: market_mid[n] for n in part if n in market_mid and 0.0 < market_mid[n] < 1.0}
        total = sum(mids.values())
        if len(mids) >= 2 and total > 0.0:
            for name, mid in mids.items():
                devig[name] = mid / total
                covered.add(name)
    for name, mid in market_mid.items():
        if name not in covered and 0.0 < mid < 1.0:
            devig[name] = mid
    return devig


def blend_toward_market(
    model_probs: Mapping[str, float],
    market_mid: Mapping[str, float],
    partitions: Iterable[Iterable[str]] | None = None,
    weight: float = 0.0,
) -> dict[str, float]:
    """Return model probabilities blended ``weight`` of the way toward the de-vigged market.

    Only contracts with a usable market mid are blended; everything else is returned
    unchanged, so a missing/locked book can never silently zero out an edge. The result is
    clamped to ``(0, 1)``. ``weight <= 0`` is a no-op copy (old behaviour).
    """
    out = dict(model_probs)
    weight = float(weight)
    if weight <= 0.0:
        return out
    weight = min(1.0, weight)
    devig = _devig(market_mid, partitions or [])
    for name, prob in model_probs.items():
        target = devig.get(name)
        if target is None:
            continue
        blended = (1.0 - weight) * float(prob) + weight * float(target)
        out[name] = min(1.0 - _EPS, max(_EPS, blended))
    return out
