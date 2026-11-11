#!/usr/bin/env bash
# Sync the working tree to GitHub after an iteration.
#
#   ./scripts/sync.sh            # commit + push if anything changed
#   ./scripts/sync.sh "message"  # custom commit message
#
# Run this after changing the skill, the Python CLI, or the plugin entry so the
# public repo (and anything installing from it) picks the change up.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "$(git status --porcelain)" ]]; then
  echo "qq-digest: nothing to sync"
  exit 0
fi

msg="${1:-chore: sync $(date '+%Y-%m-%d %H:%M')}"
git add -A
git commit -m "$msg"
git push origin HEAD
echo "qq-digest: pushed $(git rev-parse --short HEAD)"
