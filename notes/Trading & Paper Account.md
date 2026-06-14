# Trading & Paper Account

Live Polymarket edges → recommendations → a persistent paper bot. Part of [[Architecture]].

## Edges (live, real money prices)
- `orchestrator._build_live_markets` fetches the **live Polymarket CLOB** order books for all champion + 12 group-winner markets, maps them to sim probabilities (Codex's `map_world_cup_markets`), and runs `detect_edges(include_no=True)` → Codex's `edge/recommend.py` (rank by Kelly impact, BUY YES / BUY NO, risk labels, exposure meters).
- The **Trades tab** shows the ranked slate + full model-vs-market comparison + the scanner. SAMPLE books are only a fallback if the feed is down.

## Paper account (`pipeline/paper_account.py`)
A persistent fake-money account (`data/processed/paper_account.json`). Each run it:
1. settles positions whose market resolved (price → ~1/0),
2. marks open positions to the live mid,
3. paper-executes new actionable recommendations it isn't already holding.
- **$200 bankroll.** Positions show **settle dates** (group winners 2026-06-27, champion 2026-07-20) as a timeline.
- **Profit metrics:** equity, cash, invested, realized + unrealized P&L, return %, and **expected value** (`shares × model_prob − stake`), expected ROI, expected settle equity, max payout, avg edge, win rate.
- Live execution is a **disabled, gated stub** — no wallet/signing. Manual-only ethos preserved.

## The honest caveat (see [[Findings & Decisions]])
Most actionable edges are the model overrating non-favourites' group-win chances (and the BUY-NO flip side) — likely sim miscalibration, not true edge. The credible ones are champion-market (Spain/Argentina). The [[Live Tracker]] + the paper account's realized P&L will judge this against reality over the tournament.
