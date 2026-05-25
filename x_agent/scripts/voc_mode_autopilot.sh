#!/usr/bin/env bash
set -euo pipefail

# VOC mode: Autopilot-primary
# Keep heartbeat; stop local VOC execution daemons to avoid duplicate actions.

AGENTS=(
  com.solvea.engage-voc-ai
  com.solvea.quote-scout-voc-ai
  com.solvea.telegram-bridge-voc-ai
)

for a in "${AGENTS[@]}"; do
  plist="$HOME/Library/LaunchAgents/${a}.plist"
  if [[ -f "$plist" ]]; then
    launchctl unload "$plist" >/dev/null 2>&1 || true
  fi
done

echo "Switched to AUTOPILOT mode for VOC (local VOC daemons unloaded)."
echo "Current VOC launchd status:"
launchctl list | grep -E 'com.solvea.(engage-voc-ai|quote-scout-voc-ai|telegram-bridge-voc-ai|voc-heartbeat)' || true

echo "Tip: verify Autopilot jobs are active in Multica UI before leaving this mode."
