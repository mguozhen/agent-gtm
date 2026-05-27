---
name: voc-ai-x-agent
description: Operate and tune the VOC.ai X automation stack, including keyword engage, quote scout, mutual engage, Telegram approval flow, and Multica run-only autopilots. Use when the user wants to debug, retune, or replicate the VOC.ai account workflow.
---

# VOC.ai X agent

This skill is for the `@VOC_ai` automation stack.

The account is an ecommerce operator voice, not a generic brand account. The system watches target accounts and keyword searches, drafts replies and quote-tweets, routes them through Telegram approval, and can be run manually or via Multica autopilots.

## When to use this skill

- "Why didn't VOC.ai queue replies?"
- "Tune the VOC.ai keywords / targets / tone"
- "Check the quote scout"
- "Recreate the VOC.ai workspace or autopilots in Multica"
- Any mention of `accounts/VOC_ai`, `engage_config_mutual.json`, `telegram-bridge-voc-ai`, or the `voc-ai` workspace

Do not use this skill for generic ecommerce copywriting that does not touch the automation stack.

## Current operating model

- Local repo/runtime path: `/Users/siliconno3/x_agent`
- Account path: `accounts/VOC_ai/`
- Chrome debug port: `10002`
- Telegram bridge variant: `voc_ai`
- Multica workspace: `voc-ai`
- Agent name in Multica: `x_agent`
- Execution style: `run_only`
- Reporting style: direct task output, no issue creation by default

## Main configs and files

- `accounts/VOC_ai/config.json` — account port and limits
- `accounts/VOC_ai/playbook.md` — brand/account writing rules
- `accounts/VOC_ai/engage_config.json` — keyword engage + quote scout config
- `accounts/VOC_ai/engage_config_mutual.json` — mutual engage target sweep
- `accounts/VOC_ai/VOC_AUTOMATION_MAP.md` — local + Multica system map
- `state/reply_queue.json` — approval queue shared with the bridge

## Main scripts

- `scripts/engage_daemon.py` — target/keyword sweep and draft generation
- `scripts/quote_scout.py` — quote-tweet draft generation
- `scripts/telegram_bridge.py` — Telegram approval flow
- `scripts/generate.py` — prompt construction and model call

## Multica autopilots to expect

1. `VOC_ai Mutual Engage - btcmind + hunter`
- One-shot target sweep on `engage_config_mutual.json`

2. `VOC_ai X Quote Scout`
- One-shot quote scout run on `engage_config.json`

3. `VOC_ai X Keyword Engage`
- One-shot keyword engage sweep on `engage_config.json`

All are expected to run on `15,45 * * * *` in `America/Los_Angeles` and report the outcome directly in Multica.

## Operating procedure

### 1. Confirm workspace and runtime

- Verify the default Multica workspace if the task is workspace-related
- Verify Chrome on port `10002`
- Verify the relevant config file before changing behavior

### 2. If the user asks "what happened?"

Check:

- Multica task output first
- `state/reply_queue.json` counts before/after if queue movement matters
- Relevant daemon or one-shot stdout
- Whether the Telegram bridge or Chrome login is the actual blocker

### 3. If the user asks for tuning

Common changes:

- `target_accounts` and `archetypes`
- `keyword_engage.keywords`
- `quote_scout.keywords`
- daily caps and age/likes filters
- account `playbook.md` if the voice is drifting

Keep changes concrete and account-specific. Do not widen scope without evidence.

### 4. Reporting standard

- Keep the summary short
- Prioritize what changed, what queued, what failed, and what should be adjusted
- Avoid dumping raw JSON when a concise suggestion list is enough
- Do not create local report files unless explicitly requested

## Guardrails

- Do not create issues unless explicitly asked
- Do not rewrite Telegram or Chrome credentials
- Do not restart daemons unless needed for the task
- Do not touch Hunter workspace autopilots when operating on `voc-ai`
