#!/usr/bin/env bash
# Redeploy after a code change: refresh deps, reinstall units, restart the wc-* services.
# Robust to root-owned / odd-owned app dirs (a Windows-made tarball extracted as root keeps
# its Windows UID, which has no name on Linux — we normalise ownership to the service user).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_USER="${SERVICE_USER:-root}"   # the wc-* services run as this user (root on the stock box)

if [ "$(id -u)" -ne 0 ]; then echo "Run as root: sudo bash deploy/update.sh" >&2; exit 1; fi

# Run a command as the service user only when that's a real, non-root user; else run directly.
run_as(){ if [ "$SERVICE_USER" = "root" ] || ! id "$SERVICE_USER" >/dev/null 2>&1; then "$@"; else sudo -u "$SERVICE_USER" "$@"; fi; }

echo "==> normalising ownership to ${SERVICE_USER}…"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}" 2>/dev/null || chown -R "${SERVICE_USER}" "${APP_DIR}" 2>/dev/null || true

if [ -d "${APP_DIR}/.git" ]; then
  echo "==> git pull…"; run_as git -C "${APP_DIR}" pull --ff-only || true
fi

echo "==> ensuring git (for the implementer's revertible history)…"
command -v git >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; apt-get install -y -qq git >/dev/null 2>&1 || true; }
git config --global --add safe.directory "${APP_DIR}" 2>/dev/null || true
if command -v git >/dev/null 2>&1 && [ ! -d "${APP_DIR}/.git" ]; then
  git -C "${APP_DIR}" init -q && git -C "${APP_DIR}" config user.email implementer@worldcup.local \
    && git -C "${APP_DIR}" config user.name wc-implementer \
    && git -C "${APP_DIR}" add -A && git -C "${APP_DIR}" commit -q -m "baseline" || true
fi

echo "==> refreshing deps…"
# Non-fatal: a transient pip/network blip must not skip the service restart below.
run_as "${APP_DIR}/.venv/bin/pip" install -q -r "${APP_DIR}/requirements-runtime.txt" || echo "==> WARN: dep refresh failed (continuing with existing deps)"

echo "==> (re)installing systemd units…"
WEB_PORT="$(grep -E '^web_port:' "${APP_DIR}/config.yaml" | grep -oE '[0-9]+' | head -1 || true)"
WEB_PORT="${WEB_PORT:-8000}"
for unit in wc-engine wc-web wc-ops wc-improver wc-implementer; do
  [ -f "${APP_DIR}/deploy/${unit}.service" ] || continue
  sed -e "s|__APP_DIR__|${APP_DIR}|g" -e "s|__USER__|${SERVICE_USER}|g" \
    "${APP_DIR}/deploy/${unit}.service" > "/etc/systemd/system/${unit}.service"
done
sed -i "s|--host 127.0.0.1 --port 8000|--host 127.0.0.1 --port ${WEB_PORT}|" /etc/systemd/system/wc-web.service 2>/dev/null || true
systemctl daemon-reload

echo "==> restarting services…"
for unit in wc-engine wc-web wc-ops wc-improver wc-implementer; do
  systemctl enable "${unit}.service" >/dev/null 2>&1 || true
  systemctl restart "${unit}.service" || echo "   !! ${unit} failed to start — journalctl -u ${unit} -e"
done
echo "Done. Tail logs:  journalctl -u wc-engine -f   (also wc-web / wc-ops / wc-improver)"
