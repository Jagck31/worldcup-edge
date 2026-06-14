#!/usr/bin/env bash
# Provision the World Cup live dashboard on a box that's ALREADY on Tailscale
# (e.g. the Stock Claude Hetzner box). No Caddy, no public ports — it installs the
# two systemd services and exposes the web app privately via `tailscale serve` on a
# second HTTPS port. Coexists with whatever else runs on the box (it only touches
# its own app dir, its own systemd units, and one new tailscale-serve port).
#
# Run from the repo root on the box, as root:
#     bash deploy/deploy-tailscale.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
[ -f "${SCRIPT_DIR}/.env" ] && { set -a; . "${SCRIPT_DIR}/.env"; set +a; }

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo bash deploy/deploy-tailscale.sh" >&2; exit 1; }

SERVICE_USER="${SERVICE_USER:-root}"
TS_PORT="${WC_TS_PORT:-8443}"
APP_PORT="${WC_APP_PORT:-8000}"

echo "==> App dir:      ${APP_DIR}"
echo "==> Service user: ${SERVICE_USER}"
echo "==> Local port:   127.0.0.1:${APP_PORT}   ->   tailscale https :${TS_PORT}"
echo

command -v tailscale >/dev/null 2>&1 || { echo "tailscale not found on this box — is this the right machine?" >&2; exit 1; }

echo "==> Installing python venv tooling…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null
# git repo so the implementer agent's changes are revertible
if [ ! -d "${APP_DIR}/.git" ]; then
  git -C "${APP_DIR}" init -q && git -C "${APP_DIR}" config user.email implementer@worldcup.local \
    && git -C "${APP_DIR}" config user.name wc-implementer \
    && git -C "${APP_DIR}" add -A && git -C "${APP_DIR}" commit -q -m "baseline" || true
fi

echo "==> Building virtualenv + runtime deps…"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install -q --upgrade pip wheel
"${APP_DIR}/.venv/bin/pip" install -q -r "${APP_DIR}/requirements-runtime.txt"
[ "${SERVICE_USER}" != "root" ] && chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

# Ensure an OPENAI_API_KEY lives in the app .env for the AI agents (ops + improver).
if [ -n "${OPENAI_API_KEY:-}" ] && ! grep -q '^OPENAI_API_KEY=' "${APP_DIR}/.env" 2>/dev/null; then
  echo "OPENAI_API_KEY=${OPENAI_API_KEY}" >> "${APP_DIR}/.env"
  chmod 600 "${APP_DIR}/.env"
  echo "==> wrote OPENAI_API_KEY to ${APP_DIR}/.env"
fi
[ -f "${APP_DIR}/.env" ] && grep -q '^OPENAI_API_KEY=' "${APP_DIR}/.env" \
  && echo "==> OPENAI_API_KEY present (agents will use the LLM)" \
  || echo "==> NOTE: no OPENAI_API_KEY in ${APP_DIR}/.env — agents run in mechanical mode until you add one"

echo "==> Installing systemd services…"
for unit in wc-engine wc-web wc-ops wc-improver wc-implementer; do
  sed -e "s|__APP_DIR__|${APP_DIR}|g" -e "s|__USER__|${SERVICE_USER}|g" \
    "${SCRIPT_DIR}/${unit}.service" > "/etc/systemd/system/${unit}.service"
done
# web binds 127.0.0.1:${APP_PORT}
sed -i "s|--host 127.0.0.1 --port 8000|--host 127.0.0.1 --port ${APP_PORT}|" /etc/systemd/system/wc-web.service
if [ -n "${LIVE_API_KEY:-}" ]; then
  sed -i "s|# Environment=LIVE_API_KEY=your_key_here|Environment=LIVE_API_KEY=${LIVE_API_KEY}|" /etc/systemd/system/wc-engine.service
fi
systemctl daemon-reload
systemctl enable --now wc-engine.service
systemctl enable --now wc-web.service
systemctl enable --now wc-ops.service
systemctl enable --now wc-improver.service
systemctl enable --now wc-implementer.service

echo "==> Waiting for the web app to answer locally…"
for i in $(seq 1 20); do
  curl -fsS "http://127.0.0.1:${APP_PORT}/healthz" >/dev/null 2>&1 && break || sleep 2
done
curl -fsS "http://127.0.0.1:${APP_PORT}/healthz" >/dev/null 2>&1 && echo "   web up." || echo "   (web not answering yet — check: journalctl -u wc-web -e)"

echo "==> Exposing privately via Tailscale on https :${TS_PORT} …"
tailscale serve --bg --https "${TS_PORT}" "http://127.0.0.1:${APP_PORT}"

TS_HOST="$(tailscale status --json 2>/dev/null | grep -oE '"DNSName":"[^"]+"' | head -1 | sed 's/"DNSName":"//;s/\.$//;s/"//')"
cat <<EOF

============================================================
 World Cup dashboard deployed (private, Tailscale-only).

   URL:    https://${TS_HOST:-<your-node>.<tailnet>.ts.net}:${TS_PORT}
   Engine: journalctl -u wc-engine -f     (training ~1 min on first boot)
   Web:    journalctl -u wc-web -f
   Serve:  tailscale serve status         (should list BOTH this and 8765)

 It does NOT touch Stock Claude (separate app dir, port ${APP_PORT}≠8765,
 own systemd units, a second tailscale-serve port).

 Redeploy after a code change:  sudo bash deploy/update.sh
 Tear down:  systemctl disable --now wc-engine wc-web; tailscale serve --https=${TS_PORT} off
============================================================
EOF
