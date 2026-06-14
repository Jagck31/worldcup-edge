#!/usr/bin/env bash
# Pull-based auto-deploy: redeploy ONLY when origin/main actually advanced since the last
# SUCCESSFUL deploy. Gating on a deployed-SHA marker (not LOCAL!=REMOTE) means local commits
# aren't silently wiped, and a failed update.sh isn't marked done so it retries next tick.
# Pure outbound (git fetch) — works on the locked-down box with no inbound/Tailscale.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR" || exit 0
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
git fetch --quiet origin main 2>/dev/null || exit 0
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo none)
[ "$REMOTE" = none ] && exit 0
MARKER="$APP_DIR/.last_deployed_sha"
DEPLOYED=$(cat "$MARKER" 2>/dev/null || echo none)
[ "$REMOTE" = "$DEPLOYED" ] && exit 0          # remote unchanged since last successful deploy

echo "$(date -u +%FT%TZ) autodeploy: ${DEPLOYED:0:8} -> ${REMOTE:0:8}"
git reset --hard "$REMOTE"                      # GitHub is the source of truth for deployed code
if bash deploy/update.sh; then
  echo "$REMOTE" > "$MARKER"                     # mark success ONLY after update.sh fully succeeds
  echo "$(date -u +%FT%TZ) autodeploy: deployed ${REMOTE:0:8}"
else
  echo "$(date -u +%FT%TZ) autodeploy: update.sh FAILED for ${REMOTE:0:8} — will retry next tick" >&2
  exit 1
fi
