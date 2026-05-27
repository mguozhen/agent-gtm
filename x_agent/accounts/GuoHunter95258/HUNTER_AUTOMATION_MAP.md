# Hunter X Automation Map

This is the canonical map for the Hunter X stack as it exists in the current local and Multica-driven setup.

## Account Identity

- Handle: `@GuoHunter95258`
- Local account root: `/Users/siliconno3/x_agent/accounts/GuoHunter95258`
- Chrome debug port: `10000`
- Multica style: `run_only` tasks that report directly in task output

## Core Local Runtime

1. `engage_daemon.py`
- Config: `/Users/siliconno3/x_agent/accounts/GuoHunter95258/engage_config.json`
- Purpose: target sweep + keyword sweep, queue reply drafts, push to Telegram for approval

2. `telegram_bridge.py`
- Variant: `hunter`
- Purpose: handle Approve / Regen / Reject actions for Hunter reply cards

3. `quote_scout.py`
- Config: `/Users/siliconno3/x_agent/accounts/GuoHunter95258/engage_config.json`
- Purpose: scout quote-tweet opportunities and draft them for approval

4. `analytics_export_test.py`
- Purpose: open X analytics, export the CSV, map rows back to local posting metadata, and return compact task summaries plus suggested changes

## Shared Runtime Files

- Queue: `/Users/siliconno3/x_agent/state/reply_queue.json`
- Reply scoring: `/Users/siliconno3/x_agent/state/reply_engagement.json`
- Seen state: `/Users/siliconno3/x_agent/state/engage_seen.json`
- Analytics feedback artifacts are returned in task output, not written as local reports

## Multica Alignment

- Prefer Multica tasks that run the local scripts and report results directly
- Do not create issues by default
- Keep summaries short and decision-oriented
- For analytics tasks, surface only suggested changes when available rather than raw long JSON dumps

## Cleanup Rule

Use only `x_agent` paths, not `x-agent` or `Desktop/x_agent`.
