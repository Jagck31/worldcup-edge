# Ideas & Open Questions

Backlog. See [[Findings & Decisions]] for what's settled.

## High value
- [ ] **Fix the goal model's flat xG** (Brazil ~1.0 xG despite being favoured) — empirical-Bayes already in (Codex); consider opponent-adjusted xG / Dixon-Coles ρ. This is the root of the misleading scorelines and remaining suspect edges. Coordinate via [[COLLABORATION_STATUS]].
- [ ] **Proper responsive web app** (FastAPI + HTML/JS over `dashboard_state.json`) — more phone-native than `textual-serve`. See [[Deploy & Access]].
- [ ] **Attack/Defense Elo split** — would most help the goal model + scorelines.
- [ ] **Live odds movement in the paper account** — mark-to-market P&L as Polymarket prices shift between runs.

## Medium
- [ ] FIFA Annex C third-place table → real bracket assignment (currently a non-official resolver).
- [ ] Match-level Polymarket markets (don't exist yet; only champion + group-winner do) — would give bigger $ edges.
- [ ] Per-team home advantage (currently a global 75); travel/altitude from venue data.
- [ ] True connected bracket geometry (connector lines between rounds) vs the current per-round matchup columns.

## Questions
- Is the Elo-tilt strength in the sim (`divisor=700`) right? Iran 1.4% is defensible; could push favourites harder.
- Per-player form input — judged **too much work for free data, low marginal value over Elo+FIFA-rank** (squad/form data isn't a clean free CSV). Revisit only with a data source.
