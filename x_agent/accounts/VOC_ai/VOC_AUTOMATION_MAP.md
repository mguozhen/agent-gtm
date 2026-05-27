# VOC_ai Automation Map

This is the canonical map for VOC_ai automation connected to the current agent setup.

## Multica Workspace

- Workspace slug: `voc-ai`
- Workspace ID: `362a62fc-d34f-4ff1-a024-14e974fb9a4b`
- Agent name: `x_agent`
- Agent model: `gpt-5.5`
- Execution mode: `run_only`
- Reporting rule: results should be summarized directly in Multica task output, not written as local reports and not turned into issues by default

## Active Services (LaunchAgents)

1. `com.solvea.engage-voc-ai`
- Plist: `/Users/siliconno3/Library/LaunchAgents/com.solvea.engage-voc-ai.plist`
- Command: `/usr/bin/python3 -u /Users/siliconno3/x_agent/scripts/engage_daemon.py --config /Users/siliconno3/x_agent/accounts/VOC_ai/engage_config.json`
- Purpose: generate queue items (reply drafts) for VOC_ai

2. `com.solvea.telegram-bridge-voc-ai`
- Plist: `/Users/siliconno3/Library/LaunchAgents/com.solvea.telegram-bridge-voc-ai.plist`
- Command: `/usr/bin/python3 -u /Users/siliconno3/x_agent/scripts/telegram_bridge.py`
- Env override: `BRIDGE_VARIANT=voc_ai`
- Purpose: handle TG approve/regen/reject callbacks for VOC_ai queue cards

3. `com.solvea.quote-scout-voc-ai` (scheduled, not always running)
- Plist: `/Users/siliconno3/Library/LaunchAgents/com.solvea.quote-scout-voc-ai.plist`
- Command: `/usr/bin/python3 -u /Users/siliconno3/x_agent/scripts/quote_scout.py --config /Users/siliconno3/x_agent/accounts/VOC_ai/engage_config.json`
- Purpose: scout quote opportunities for VOC_ai

4. `com.solvea.engage-mutual-voc-ai` (scheduled, one-shot style)
- Plist: `/Users/siliconno3/Library/LaunchAgents/com.solvea.engage-mutual-voc-ai.plist`
- Command: `/usr/bin/python3 -u /Users/siliconno3/x_agent/scripts/engage_daemon.py --config /Users/siliconno3/x_agent/accounts/VOC_ai/engage_config_mutual.json --once`
- Purpose: mutual-target sweep for VOC_ai

## Multica Autopilots

1. `VOC_ai Mutual Engage - btcmind + hunter`
- Runs one target sweep using `engage_config_mutual.json`

2. `VOC_ai X Quote Scout`
- Runs one quote scout using `engage_config.json`

3. `VOC_ai X Keyword Engage`
- Runs one keyword-engage sweep using `engage_config.json`

Schedule for all three:
- Cron: `15,45 * * * *`
- Timezone: `America/Los_Angeles`

## VOC_ai Account Files

- Account root: `/Users/siliconno3/x_agent/accounts/VOC_ai`
- Core account config: `/Users/siliconno3/x_agent/accounts/VOC_ai/config.json`
- Active engage config: `/Users/siliconno3/x_agent/accounts/VOC_ai/engage_config.json`
- Runtime clone (optional backup): `/Users/siliconno3/x_agent/accounts/VOC_ai/engage_config.runtime.json`
- Mutual config: `/Users/siliconno3/x_agent/accounts/VOC_ai/engage_config_mutual.json`
- VOC account logs (historical): `/Users/siliconno3/x_agent/accounts/VOC_ai/logs/`

## Shared Runtime Files Used By VOC_ai

- Queue: `/Users/siliconno3/x_agent/state/reply_queue.json`
- Seen-state (VOC): `/Users/siliconno3/x_agent/state/engage_seen_voc_ai.json`
- TG offset (VOC): `/Users/siliconno3/x_agent/state/telegram_offset_voc_ai.json`
- Locks: `/Users/siliconno3/x_agent/state/locks/`

## Logs (VOC)

- Engage daemon out: `/Users/siliconno3/x_agent/logs/engage-vocai-daemon.out.log`
- Engage daemon err: `/Users/siliconno3/x_agent/logs/engage-vocai-daemon.err.log`
- TG bridge out: `/Users/siliconno3/x_agent/logs/telegram-bridge-voc-ai.out.log`
- TG bridge err: `/Users/siliconno3/x_agent/logs/telegram-bridge-voc-ai.err.log`
- Quote scout launchd out: `/Users/siliconno3/x_agent/logs/quote_scout_voc_ai_launchd.log`
- Quote scout launchd err: `/Users/siliconno3/x_agent/logs/quote_scout_voc_ai_launchd.err`

## Scripts Used For VOC_ai

- `/Users/siliconno3/x_agent/scripts/engage_daemon.py`
- `/Users/siliconno3/x_agent/scripts/telegram_bridge.py`
- `/Users/siliconno3/x_agent/scripts/quote_scout.py`
- `/Users/siliconno3/x_agent/scripts/engage.py`
- `/Users/siliconno3/x_agent/scripts/chrome.py`
- `/Users/siliconno3/x_agent/scripts/generate.py`

## Multica Connection Notes

- The `multica` repo reference appears in root buildlog config, not VOC runtime engage config:
  - `/Users/siliconno3/x_agent/engage_config.json` -> `buildlog.repos` includes `SolveaCX/multica-solveaagent`
- VOC active runtime currently uses:
  - `/Users/siliconno3/x_agent/accounts/VOC_ai/engage_config.json`
- If you want VOC to use multica-specific content generation, add/merge a `buildlog` section into `engage_config.runtime.json` and run a dedicated VOC buildlog launch agent.

## Cleanup Rule

Use only `x_agent` paths (underscore), not `x-agent` or `Desktop/x_agent`.
