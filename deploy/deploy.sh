#!/usr/bin/env bash
# One-shot provisioner for the World Cup 2026 live dashboard on a fresh Ubuntu/Debian
# Hetzner VPS. Installs Python + Caddy, creates a venv, installs two systemd services
# (the live engine + the web app), and configures Caddy with automatic HTTPS + basic auth.
#
# Usage (run from the repo root on the VPS, as root):
#     sudo bash deploy/deploy.sh
# It reads deploy/.env if present, otherwise prompts for the domain + basic-auth creds.
set -euo pipefail

# --- locate the repo + load config -------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
[ -f "${SCRIPT_DIR}/.env" ] && { set -a; . "${SCRIPT_DIR}/.env"; set +a; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root:  sudo bash deploy/deploy.sh" >&2
  exit 1
fi

SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-wc}}"

prompt() {  # prompt VAR "question" [silent]
  local var="$1" q="$2" silent="${3:-}" val=""
  eval "val=\${$var:-}"
  if [ -z "$val" ]; then
    if [ "$silent" = "silent" ]; then read -rsp "$q: " val; echo; else read -rp "$q: " val; fi
    eval "$var=\$val"
  fi
}
prompt WC_DOMAIN "Domain pointed at this server (e.g. wc.example.com)"
prompt WC_BASIC_USER "Basic-auth username"
prompt WC_BASIC_PASSWORD "Basic-auth password" silent

echo
echo "==> App dir:        ${APP_DIR}"
echo "==> Service user:   ${SERVICE_USER}"
echo "==> Domain:         ${WC_DOMAIN}"
echo

# --- packages ----------------------------------------------------------------------
echo "==> Installing system packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl debian-keyring debian-archive-keyring apt-transport-https gnupg

# --- Caddy (official repo) ---------------------------------------------------------
if ! command -v caddy >/dev/null 2>&1; then
  echo "==> Installing Caddy…"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

# --- service user + ownership ------------------------------------------------------
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "==> Creating system user ${SERVICE_USER}…"
  useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

# --- python venv -------------------------------------------------------------------
echo "==> Creating virtualenv + installing runtime deps…"
sudo -u "${SERVICE_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${SERVICE_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip wheel
sudo -u "${SERVICE_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements-runtime.txt"

# --- systemd units -----------------------------------------------------------------
echo "==> Installing systemd services…"
for unit in wc-engine wc-web; do
  sed -e "s|__APP_DIR__|${APP_DIR}|g" -e "s|__USER__|${SERVICE_USER}|g" \
    "${SCRIPT_DIR}/${unit}.service" > "/etc/systemd/system/${unit}.service"
done
# Inject a private live-feed key into the engine unit if one was provided.
if [ -n "${LIVE_API_KEY:-}" ]; then
  sed -i "s|# Environment=LIVE_API_KEY=your_key_here|Environment=LIVE_API_KEY=${LIVE_API_KEY}|" /etc/systemd/system/wc-engine.service
fi
systemctl daemon-reload
systemctl enable --now wc-engine.service
systemctl enable --now wc-web.service

# --- Caddy config ------------------------------------------------------------------
echo "==> Configuring Caddy (domain ${WC_DOMAIN}, automatic HTTPS + basic auth)…"
PW_HASH="$(caddy hash-password --plaintext "${WC_BASIC_PASSWORD}")"
mkdir -p /var/log/caddy
sed -e "s|__DOMAIN__|${WC_DOMAIN}|g" -e "s|__USER__|${WC_BASIC_USER}|g" \
  -e "s|__PASSWORD_HASH__|${PW_HASH}|g" "${SCRIPT_DIR}/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

# --- summary -----------------------------------------------------------------------
cat <<EOF

============================================================
 Deployed. The engine is training the model now (~1 min on
 first boot), then every section goes live.

   Site:    https://${WC_DOMAIN}     (login: ${WC_BASIC_USER})
   Engine:  systemctl status wc-engine   ·  journalctl -u wc-engine -f
   Web:     systemctl status wc-web      ·  journalctl -u wc-web -f
   Caddy:   systemctl status caddy       ·  journalctl -u caddy -f

 DNS: make sure an A record for ${WC_DOMAIN} points at this
 server's public IP, or Caddy can't issue the TLS cert.

 Redeploy after a code change:  sudo bash deploy/update.sh
============================================================
EOF
