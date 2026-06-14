#!/usr/bin/env bash
# ONE-TIME on the box: point /opt/worldcup-edge at the GitHub repo and install the
# auto-deploy timer. After this, a push to the repo redeploys the box within ~2 minutes —
# no more scp/ssh. Safe: it stops tracking runtime data + .env so a reset never wipes the
# live paper book, the state, or your secrets.
#
#   sudo bash deploy/setup-autodeploy.sh https://github.com/<you>/worldcup-edge.git
set -euo pipefail
REPO="${1:?usage: sudo bash deploy/setup-autodeploy.sh <https-github-repo-url>}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

command -v git >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; apt-get update -y -qq; apt-get install -y -qq git; }
cd "$APP_DIR"
git config --global --add safe.directory "$APP_DIR" || true
[ -d .git ] || git init -q

echo "==> untracking runtime data + secrets (so resets never clobber them)…"
git rm -r --cached --ignore-unmatch \
  data/processed data/cache data/raw data/manual/wc2026_results.csv .env .venv >/dev/null 2>&1 || true

echo "==> pointing at $REPO …"
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO"
git fetch origin main
git reset --hard origin/main     # gitignored runtime data/.env are untouched

echo "==> installing the 2-minute auto-deploy timer…"
sed "s|__APP_DIR__|$APP_DIR|g" deploy/wc-autodeploy.service > /etc/systemd/system/wc-autodeploy.service
cp deploy/wc-autodeploy.timer /etc/systemd/system/wc-autodeploy.timer
chmod +x deploy/wc-autodeploy.sh
systemctl daemon-reload
systemctl enable --now wc-autodeploy.timer

echo "==> first deploy now…"
bash deploy/update.sh
cat <<EOF

============================================================
 Auto-deploy is live. Pushes to:
   $REPO
 redeploy this box within ~2 minutes. No more scp/ssh.

 Watch:  systemctl list-timers wc-autodeploy
         journalctl -u wc-autodeploy -f
============================================================
EOF
