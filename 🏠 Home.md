# 🏠 World Cup 2026 Edge — Vault Home

Map of content for this project. This is an Obsidian vault — notes link with `[[wikilinks]]`.

## Start here
- [[Architecture]] — how the pipeline fits together (data → Elo → model → sim → edges → paper account → dashboard)
- [[Deploy & Access]] — run it, watch it live, serve it to your phone / VPS
- [[Findings & Decisions]] — what we've learned and why (the durable insights)
- [[Ideas & Open Questions]] — the backlog

## Topic notes
- [[Model]] — calibrated 1X2 (gradient-boosted trees / MLP), features, the log-loss plateau
- [[Monte Carlo]] — tournament simulation, the Elo-spread / group-stage calibration fixes, the 40× speedup
- [[Trading & Paper Account]] — live Polymarket edges, recommender, the persistent paper bot, EV metrics
- [[Live Tracker]] — predictions vs real results, scored as the tournament plays

## Working docs (existing files)
- [[README]] — quickstart
- [[IMPROVEMENT_LOG]] — chronological change log with measured effects (the running record)
- [[COLLABORATION_STATUS]] — lane split with Codex (who owns what)
- [[CODEX_REVIEW_NOTES]] — the original code review + Codex's fixes

## How I use this vault (going forward)
Durable insight → a topic note here (linked). A change with a measurement → a row in [[IMPROVEMENT_LOG]].
A coordination boundary → [[COLLABORATION_STATUS]]. New idea → [[Ideas & Open Questions]].
