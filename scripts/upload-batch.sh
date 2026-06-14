#!/bin/bash
# Upload high-confidence (ok) recipes to Mealie when the cluster is reachable.
# Decoupled from the 1am batch on purpose: the LAN route to the k3s API is unreliable after the
# overnight sleep/wake (dual-homed wired+wireless + Tailscale subnet route), so this runs a few
# times during the day (launchd: net.meklab.recipe-pipeline-upload) and is a clean no-op when the
# cluster can't be reached. The uploader is idempotent (.uploaded markers), so backlog self-heals.
set -uo pipefail

REPO="/Users/mekstromjr/Dev/meklab/recipe-pipeline"
UV="/Users/mekstromjr/.langflow/uv/uv"
KUBECTL="/opt/homebrew/bin/kubectl"   # launchd's minimal PATH excludes Homebrew

cd "$REPO" || exit 1
ts="$(date '+%Y-%m-%d %H:%M:%S')"

grep -qE '^RECIPE_MEALIE_API_TOKEN=.+' .env 2>/dev/null || { echo "$ts upload: no Mealie token, skip"; exit 0; }

# Reachability guard: only proceed if the k3s API answers right now.
if ! "$KUBECTL" -n prod get svc mealie >/dev/null 2>&1; then
  echo "$ts upload: cluster unreachable, skipping (will retry next slot)"
  exit 0
fi

echo "$ts upload: cluster reachable -> uploading ok recipes"
"$KUBECTL" -n prod port-forward svc/mealie 9000:9000 >/tmp/mealie-pf-upload.log 2>&1 &
PF=$!
sleep 6
"$UV" run recipe-pipeline upload --ok-only || echo "$ts upload: command failed"
kill "$PF" 2>/dev/null || true
echo "$ts upload: done"
