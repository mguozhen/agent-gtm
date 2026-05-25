#!/usr/bin/env bash
set -euo pipefail

# VOC mode: launchd-primary
# Bring local VOC daemons back for continuous operation.

AGENTS=(
  com.solvea.engage-voc-ai
  com.solvea.quote-scout-voc-ai
  com.solvea.telegram-bridge-voc-ai
  com.solvea.voc-heartbeat
)

for a in "${AGENTS[@]}"; do
  plist="$HOME/Library/LaunchAgents/${a}.plist"
  if [[ -f "$plist" ]]; then
    launchctl load "$plist" >/dev/null 2>&1 || true
  fi
done

echo "Switched to LAUNCHD mode for VOC (local VOC daemons loaded)."
echo "Current VOC launchd status:"
launchctl list | grep -E 'com.solvea.(engage-voc-ai|quote-scout-voc-ai|telegram-bridge-voc-ai|voc-heartbeat)' || true

echo "Important: disable overlapping VOC Autopilots to avoid duplicate actions."
