#!/bin/bash
# Nightly recipe-pipeline batch: extract the next 50 un-done recipes, then escalate the
# failed tail to cloud vision. Scheduled via launchd (net.meklab.recipe-pipeline) at 01:00.
# Pair with a scheduled wake so it runs even if the Mac is asleep:
#   sudo pmset repeat wake MTWRFSU 00:55:00
#
# Resume is automatic (already-extracted recipes are skipped), so this safely chips through the
# whole corpus a batch at a time. caffeinate keeps the Mac awake for the duration.
set -uo pipefail

REPO="/Users/mekstromjr/Dev/meklab/recipe-pipeline"
UV="/Users/mekstromjr/.langflow/uv/uv"
# Absolute paths — launchd runs with a minimal PATH that excludes Homebrew (/opt/homebrew/bin).
KUBECTL="/opt/homebrew/bin/kubectl"
BATCH=50

cd "$REPO" || { echo "repo not found: $REPO"; exit 1; }
echo "===================================================================="
echo "nightly batch START $(date '+%Y-%m-%d %H:%M:%S')"

# Ollama must be running — orientation A/B, structure, enrich and review all use the local model.
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "ERROR: Ollama not reachable on :11434 — skipping tonight's batch (will retry next run)."
  exit 1
fi

# caffeinate -i prevents idle sleep while each stage runs.
caffeinate -i "$UV" run recipe-pipeline extract --limit "$BATCH"
caffeinate -i "$UV" run recipe-pipeline escalate --limit "$BATCH"

echo "nightly batch DONE  $(date '+%Y-%m-%d %H:%M:%S')"
