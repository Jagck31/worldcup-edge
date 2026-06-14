#!/usr/bin/env bash
# Pull-based auto-deploy: if the GitHub repo has new commits, fast-forward and redeploy.
# Pure outbound (git fetch) — works on the locked-down box with no inbound/Tailscale needed.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR" || exit 0
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
git fetch --quiet origin main 2>/dev/null || exit 0
LOCAL=$(git rev-parse HEAD 2>/dev/null || echo none)
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo none)
[ "$LOCAL" = "$REMOTE" ] && exit 0          # already current — do nothing
echo "$(date -u +%FT%TZ) autodeploy: ${LOCAL:0:8} -> ${REMOTE:0:8}"
git reset --hard origin/main                # runtime data is gitignored, so it's untouched
bash deploy/update.sh
