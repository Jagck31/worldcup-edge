# Deploying the live dashboard

> **Reusing the Stock Claude Hetzner box?** Follow **[HETZNER_STOCKBOX.md](HETZNER_STOCKBOX.md)**
> instead — it deploys World Cup privately over Tailscale (`:8443`) alongside Stock Claude,
> with `deploy/deploy-tailscale.sh` (no Caddy, no public ports). The guide below is the
> alternative for a **fresh, public box with its own domain** (Caddy + Let's Encrypt + basic auth).

---

# Deploying to a fresh public Hetzner VPS (Caddy + domain)

Two always-on services behind Caddy (automatic HTTPS + basic auth):

```
                         ┌────────────────────────────────────────────┐
  browser ── HTTPS ──►   │  Caddy :443  (Let's Encrypt + basic auth)   │
   (SSE)                 │     └─ reverse_proxy ─► web_app :8000        │
                         │                          ▲  reads (SSE)     │
                         │   live_engine ─ writes ─►│ dashboard_state.json
                         │   (single writer: model, odds, edges,       │
                         │    portfolio, paper account, tracker, Elo)  │
                         └────────────────────────────────────────────┘
```

- **`live_engine.py`** is the single writer. It trains the model once, then loops:
  prices+paper-account (~60s), live results+tracker+Elo (~90s), re-simulate
  probabilities on every new result (else ~15 min), full retrain (~6h). Cadences are in
  `config.yaml` (`engine_*_interval_sec`).
- **`web_app.py`** is a read-only reader that pushes every change to the browser over
  Server-Sent Events — no polling lag. It binds to `127.0.0.1`; Caddy is the only thing
  exposed publicly.

## 1. Create the server
- Hetzner Cloud → a CX/CPX instance, **Ubuntu 24.04**. 2 vCPU / 4 GB is plenty.
- Note its public IPv4.

## 2. Point your domain at it
- Add a DNS **A record**: `wc.example.com` → the server IP. Wait for it to resolve
  (`dig +short wc.example.com` should print the IP). Caddy needs this to issue the cert.

## 3. Get the code onto the box
```bash
ssh root@SERVER_IP
# option A — git:
git clone <your-repo-url> /opt/worldcup-edge && cd /opt/worldcup-edge
# option B — rsync from your laptop instead:
#   rsync -avz --exclude .venv --exclude data/cache "C:/Users/jackg/Desktop/World Cup ML/worldcup-edge/" root@SERVER_IP:/opt/worldcup-edge/
```

## 4. Configure + deploy
```bash
cd /opt/worldcup-edge
cp deploy/.env.example deploy/.env
nano deploy/.env          # set WC_DOMAIN, WC_BASIC_USER, WC_BASIC_PASSWORD
sudo bash deploy/deploy.sh
```
`deploy.sh` installs Python + Caddy, builds the venv (`requirements-runtime.txt`),
installs and starts both systemd services, and writes `/etc/caddy/Caddyfile` with your
domain, a hashed basic-auth password, and SSE-friendly proxying.

## 5. Verify
```bash
systemctl status wc-engine wc-web caddy
journalctl -u wc-engine -f      # watch it train, then go live
curl -s localhost:8000/healthz  # -> ok
```
Open `https://wc.example.com`, log in, and watch the **live** chips tick. The engine
takes ~1 minute on first boot to train the model; the page shows a "waiting for the
engine" splash until the first snapshot lands.

## Operating it
- **Logs:** `journalctl -u wc-engine -f` (engine), `-u wc-web` (web), `-u caddy` (TLS/proxy).
- **Restart:** `systemctl restart wc-engine wc-web`.
- **Redeploy after edits:** `sudo bash deploy/update.sh`.
- **Change cadences / bankroll:** edit `config.yaml`, then `systemctl restart wc-engine`.
- **Private results key:** put `LIVE_API_KEY=...` in `deploy/.env` before deploying, or add
  `Environment=LIVE_API_KEY=...` to `/etc/systemd/system/wc-engine.service` and
  `systemctl daemon-reload && systemctl restart wc-engine`.
- **"Refresh now" button** on the site writes `data/processed/engine_control.json`, which the
  engine picks up within a second and forces an immediate re-sim + price refresh.

## Notes
- Caddy < 2.8 uses the directive `basicauth` (one word) instead of `basic_auth`; the
  bundled `Caddyfile` uses the new name. Ubuntu 24.04's Caddy is new enough.
- Only ports 80/443 need to be open publicly. The app port 8000 stays on localhost.
- This is paper trading only — no wallet, no keys, no order placement. The basic-auth gate
  is just so your book isn't world-readable.
