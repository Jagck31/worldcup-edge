# Deploy onto the Stock Claude Hetzner box (Tailscale-private)

Reuse the existing Hetzner box (`ubuntu-2gb-ash-1`, 2 GB RAM) that runs Stock Claude.
World Cup runs **alongside** it — separate app dir, separate systemd services, its own
Tailscale-serve port. Stock Claude (Docker `stock-claude-web` on `:8765`, its root crontab,
`/opt/stock-claude`) is **never touched**.

| | Stock Claude | World Cup (new) |
|---|---|---|
| Code | `/opt/stock-claude` (Docker) | `/opt/worldcup-edge` (systemd) |
| Local port | `127.0.0.1:8765` | `127.0.0.1:8000` |
| Tailnet URL | `https://stock-claude.tail6a0165.ts.net` | `https://stock-claude.tail6a0165.ts.net:8443` |

Access to the box is **over Tailscale** (public SSH/22 is closed). Use the tailnet IP
`100.127.11.17` (or `stock-claude` if MagicDNS resolves for you). Your laptop must be on
the same tailnet (Tailscale running).

---

## 1. Ship the code over Tailscale
Build the tarball with **tar**, not PowerShell `Compress-Archive` — a .NET zip bug bit a
past Stock Claude deploy (empty entries that Linux `unzip` CRC-fails). From **Git Bash** on
your Windows machine:

```bash
cd "/c/Users/jackg/Desktop/World Cup ML"
tar czf /tmp/worldcup-edge.tgz \
  --exclude='worldcup-edge/.venv' \
  --exclude='worldcup-edge/.uv-cache' \
  --exclude='worldcup-edge/data/cache' \
  --exclude='**/__pycache__' \
  worldcup-edge
scp /tmp/worldcup-edge.tgz root@100.127.11.17:/tmp/
```

## 2. Unpack on the box
```bash
ssh root@100.127.11.17
mkdir -p /opt/worldcup-edge
tar xzf /tmp/worldcup-edge.tgz -C /opt           # creates /opt/worldcup-edge
cd /opt/worldcup-edge
```

## 3. Add swap (the box is only 2 GB and already runs a trading stack)
Training the model + Monte Carlo briefly needs a few hundred MB; a swapfile keeps a
concurrent peak from OOM-killing either app. Skip if `free -h` already shows swap.
```bash
free -h
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
```
Also keep sims modest on this box — set `engine_sim_n: 20000` in `config.yaml` (lighter than
the 50k default; still smooth). Edit before the next step if you like.

## 4. Deploy (installs venv + 2 systemd services + Tailscale serve :8443)
```bash
cd /opt/worldcup-edge
bash deploy/deploy-tailscale.sh          # run with `bash` (immune to the +x-strip gotcha)
```
This builds a venv from `requirements-runtime.txt`, installs `wc-engine` + `wc-web`
(both enabled + started), and runs `tailscale serve --bg --https 8443 http://127.0.0.1:8000`.

## 5. Verify
```bash
systemctl status wc-engine wc-web --no-pager
journalctl -u wc-engine -f          # watch it train (~1 min) then go live; Ctrl+C to stop
curl -s localhost:8000/healthz      # -> ok
tailscale serve status              # MUST still list :8765 (stock) AND now :8443 (worldcup)
```
Then open **https://stock-claude.tail6a0165.ts.net:8443** on your phone/laptop (Tailscale on).
The page shows a "waiting for the engine" splash until the first snapshot lands (~1 min), then
every section streams live over SSE (which proxies fine through Tailscale — it's plain HTTP
streaming, unlike the WebSocket TUI that didn't).

## Operating
- **Logs:** `journalctl -u wc-engine -f` / `-u wc-web -f`.
- **Redeploy after edits:** re-scp the tarball + unpack, then `sudo bash deploy/update.sh`.
- **Change cadence/bankroll/sims:** edit `config.yaml`, then `systemctl restart wc-engine`.
- **Private results key (optional):** `Environment=LIVE_API_KEY=...` in
  `/etc/systemd/system/wc-engine.service` → `systemctl daemon-reload && systemctl restart wc-engine`.

## Safety / coexistence
- Different app dir, port (8000 vs 8765), systemd units, and a separate tailscale-serve port —
  nothing overlaps Stock Claude. No public ports are opened (Tailscale-only, like the rest of
  the box).
- World Cup is **paper only** — no wallet, no keys, no order placement.
- **Resource note:** the 6-hourly model retrain is the heaviest moment. With the swapfile and
  `engine_sim_n: 20000` it's comfortable next to Stock Claude on 2 GB; watch `free -h` /
  `journalctl -u wc-engine` after the first retrain if you want to confirm.

## Tear down (clean removal)
```bash
systemctl disable --now wc-engine wc-web
rm -f /etc/systemd/system/wc-engine.service /etc/systemd/system/wc-web.service
systemctl daemon-reload
tailscale serve --https=8443 off
rm -rf /opt/worldcup-edge        # optional
```
