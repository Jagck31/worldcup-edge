# Deploy & Access

How to run it, watch it live, and reach it from your phone / VPS. See [[Architecture]].

## Run locally
```
cd "C:\Users\jackg\Desktop\World Cup ML\worldcup-edge"
python run_pipeline.py        # refresh model + live Polymarket + paper account (~1 min; Monte Carlo is ~6s now)
python run_dashboard.py       # interactive TUI; press r to watch it run live
python -m dashboard --snapshot   # print the whole dashboard once (works anywhere, non-interactive)
```
Done = you see `=== DONE - wrote ...dashboard_state.json ===`.

## Phone / browser / VPS  ← the "see it from my phone" answer  (USE THIS)
`web_app.py` is a **responsive HTML web page** over `dashboard_state.json` — stdlib only, no deps. This is the phone path that actually works.
```
python web_app.py --host 0.0.0.0 --port 8000   # serves http://<host>:8000
```
Then expose it. **Tailscale Serve is the cleanest** (no firewall change, real HTTPS cert):
```
tailscale serve --bg 8000      # -> https://<machine>.<tailnet>.ts.net   (tailnet-only)
```
Open that HTTPS URL on the phone (Tailscale app on). Tabs: Champions · Account · Trades · Tracker · Model · Elo · Notes. The **Run** button POSTs `/run` → launches `run_pipeline.py` in the background, streams the log, auto-refreshes. Page also auto-refreshes every 30s.

### Why NOT `serve_dashboard.py` (textual-serve) on a phone
`serve_dashboard.py` streams the TUI to a browser over a **WebSocket**. Tailscale Serve's HTTPS proxy does **not** forward that WS upgrade, so the phone loaded the title page but no terminal (confirmed 2026-06-13 — `GET /` 200s in the log, zero WS connections). A wide TUI on a phone is cramped anyway. `serve_dashboard.py` is fine for a **desktop** browser on the same machine/LAN; for phone use `web_app.py`.

### LAN instead of Tailscale
`http://<lan-ip>:8000` needs a Windows Firewall inbound allow for TCP 8000 (creating it needs an **elevated** shell — this session isn't). The home Wi-Fi here is a *Public* network, so the rule would land on the Public profile. Tailscale sidesteps all of this — prefer it.

## Always-on live mode (2026-06-14) — everything live, no manual runs
`live_engine.py` is the single writer that keeps **every** section live (no Run button, no cron).
```
python live_engine.py            # rich live terminal — all sections update continuously
python live_engine.py --no-ui    # plain log mode (systemd)
python live_engine.py --once     # one pass of every job (smoke test)
```
Run `web_app.py` alongside it — the page now pushes updates over **SSE** (`/events`), so the browser updates the instant the engine writes (no 30s poll). KPIs flash on change, there's a live equity sparkline, per-section "live · Xs ago" chips, and an engine-status bar showing each loop's next run. The ↻ button asks the engine to re-sim now (writes `engine_control.json`). Tabs: Overview · Account · Trades · Book · Odds · Tracker · Model · Elo · Notes.

### VPS deploy (Hetzner) — turnkey
`deploy/` has everything: two systemd units (`wc-engine`, `wc-web`), a `Caddyfile` (automatic HTTPS + basic auth, SSE-friendly), `deploy.sh` (installs Python+Caddy, venv from `requirements-runtime.txt`, services, TLS) and `update.sh`. Full runbook in `deploy/README.md`.
```
# on a fresh Ubuntu 24.04 Hetzner box, repo at /opt/worldcup-edge:
cp deploy/.env.example deploy/.env   # set WC_DOMAIN + basic-auth creds
sudo bash deploy/deploy.sh           # -> https://<domain>  (login-gated)
```
Point an A record at the box first (Caddy needs it for the cert). web_app binds `127.0.0.1`; only Caddy (80/443) is public. Paper-trading only — the basic-auth gate is just so the book isn't world-readable. Redeploy after edits: `sudo bash deploy/update.sh`.
