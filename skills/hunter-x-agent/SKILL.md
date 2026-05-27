---
name: hunter-x-agent
description: Operate and replicate the Hunter X growth-engine stack, including engage daemon, Telegram approval bridge, analytics export feedback loop, content scouts, and Multica run-only tasks. Use when the user wants to debug Hunter automation, tune `accounts/GuoHunter95258`, or align Multica tasks with the live local stack.
---

# Hunter X agent

A multi-process growth engine for an X (Twitter) account. Runs as launchd daemons on macOS, drives a per-account headless Chrome via CDP, generates replies/posts with Claude, and routes every draft through a Telegram approval bridge before posting.

The reference account is `@GuoHunter95258`. The same stack can be cloned for any handle.

## When to use this skill

- "Why isn't engage replying?" / "queue is stuck" / "why didn't analytics suggest changes?"
- "Add a target / keyword / archetype" / "tune the daily cap"
- "Set up the stack for `@newhandle`"
- Any reference to engage_daemon, telegram_bridge, analytics_export_test, reply_scorer, quote_scout, ai_news_scout, buildlog_drafts, boost_hunter, login_watchdog, Multica tasks/autopilots

Do NOT use this skill for: writing single posts, generic X strategy, or anything that doesn't touch these daemons.

---

## System map

Primary Hunter source of truth: `x_agent/accounts/GuoHunter95258/engage_config.json`.
Per-account assets live under `x_agent/accounts/HANDLE/` (config.json, playbook.md, soul.md, targets.json, logs/) and `chrome-profiles/HANDLE/` if present on the deploy host.
Process state lives in `state/` (JSON files: reply_queue, reply_engagement, engage_seen, winning_replies, coach_state, login_watchdog, telegram offsets).
Daemons are managed by launchd via `com.solvea.*.plist` on the local macOS host, and some one-shot runs are now driven via Multica `run_only` tasks.

### Daemon → script map

| Daemon | Script | Purpose |
|---|---|---|
| `com.solvea.engage-daemon` | `scripts/engage_daemon.py` | Polls targets + keywords, drafts replies, appends to reply_queue |
| `com.solvea.telegram-bridge` | `scripts/telegram_bridge.py` | Sends approval cards, applies button actions to queue |
| `com.solvea.telegram-bridge-repost` | `scripts/telegram_bridge.py` (BRIDGE_VARIANT=repost) | Second bridge for ai_news_scout drafts |
| `com.solvea.coach-bot` | `scripts/coach_bot.py` | Reply-quality coach: digests, alerts, /score /why /top etc. |
| `com.solvea.reply-scorer` | `scripts/reply_scorer_daemon.py` | Post-hoc engagement scoring of posted replies |
| `com.solvea.boost-hunter` | `scripts/boost_hunter.py` | Booster accounts (mguo, shulex) like/RT/comment on Hunter's posts |
| `com.solvea.login-watchdog` | `scripts/login_watchdog.py` | Detects logged-out Chrome profiles, pings TG |
| `com.solvea.x-hunter` | `scripts/hunter.py` | Hunter's account driver (legacy entry; check before editing) |

`scripts/review_queue.py` is the consumer that actually posts approved drafts. Invoked from `engage_daemon` and as a periodic sweep.

### Analytics + feedback loop

- `scripts/analytics_export_test.py` — opens X analytics, exports CSV, joins rows against local posting metadata, and returns concise feedback
- Preferred reporting mode: short suggested changes in task output, not raw JSON dumps or local report files
- Feedback quality depends on row-to-post attribution quality; weak attribution means weaker suggestions

### Generation + content stack

- `scripts/generate.py` — prompt construction, archetype-aware few-shot, model call (Claude Haiku 4.5 by default).
- `scripts/harvest_replies.py` — mines winning replies under target accounts into `state/winning_replies.json`. Run after editing `target_accounts`.
- `scripts/quote_scout.py` — drafts quote-tweet takes on high-engagement keyword posts.
- `scripts/ai_news_scout.py` — drafts 3 framings (quote_take / original_reframe / counter_take) for viral AI commentary.
- `scripts/buildlog_drafts.py` — drafts build-in-public posts from recent git commits (multica, gtm-swarm).
- `scripts/topic_research.py` + `scripts/web_research.py` — research backing.
- `scripts/repost_agent.py` — handles the repost flow via the second TG bot.

### Multica alignment

- Favor `run_only` tasks and autopilots
- Report directly in Multica task output
- Do not create issues unless the user explicitly asks
- Prefer short summaries and suggested changes over long raw JSON payloads

### Required .env keys

```
ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=               # main approval bot
TELEGRAM_CHAT_ID=
TELEGRAM_BOT_TOKEN_HUNTER=        # per-account login_watchdog bot
TELEGRAM_BOT_TOKEN_MGUO=
TELEGRAM_BOT_TOKEN_SHULEX=
TELEGRAM_CHAT_ID_HUNTER=          # usually same chat for all
TELEGRAM_CHAT_ID_MGUO=
TELEGRAM_CHAT_ID_SHULEX=
COACH_BOT_TOKEN=                  # coach pushes
COACH_CHAT_ID=
TELEGRAM_BOT_TOKEN_REPOST=        # ai_news_scout / second bridge variant
TELEGRAM_CHAT_ID_REPOST=
```

`scripts/env.py` loads `.env` from repo root with `os.environ.setdefault` — already-set vars win.

---

## Mode A — Operate

### 1. Check what's running

```bash
launchctl list | grep com.solvea
```

Look at the second column: `0` = last run exited cleanly, non-zero = crashed. PID column shows running daemons.

### 2. Tail the right log

Per-daemon logs are in `logs/`. Most useful:

- `logs/engage-daemon.out.log` — every cycle's target/keyword sweep, drafts, skips
- `logs/telegram-bridge.out.log` — incoming callbacks, queue mutations
- `logs/coach-bot.out.log` — scheduled push attempts, command handling
- `logs/login-watchdog.out.log` — login state per profile
- `logs/YYYY-MM-DD.log` — daily aggregate (older legacy format)

If you don't know where a daemon writes, grep the matching `.plist` for `StandardOutPath`.

### 3. Inspect the queue

```bash
# Count by status
python3 -c "import json; q=json.load(open('state/reply_queue.json')); from collections import Counter; print(Counter(r.get('status') for r in q))"
```

Statuses: `pending` (awaiting TG approval), `approved` (awaiting posting), `posted`, `skipped`, `failed`. Stuck on `pending` → telegram_bridge issue. Stuck on `approved` → review_queue / Chrome login issue.

### 4. Tune `accounts/GuoHunter95258/engage_config.json` safely

Common edits and what to expect:

| Change | Effect | Restart needed |
|---|---|---|
| Add/remove `target_accounts` | Next sweep includes new targets | None — daemon re-reads on cycle |
| Edit `archetypes` mapping | Generator falls back differently when target has few examples | None |
| Raise `daily_caps.total_replies` | More replies/day | None |
| Tighten `filters.max_post_age_minutes` | Fewer stale posts replied to | None |
| Add `keyword_engage.keywords` | New keyword in rotation next sweep | None |
| Change `generation.model` | Next generation uses new model | None |
| Edit `quote_scout` / `ai_news_scout` / `buildlog` | Picked up by their launchd-scheduled runs | None |

After editing `target_accounts`: re-run `scripts/harvest_replies.py` so the few-shot library covers new targets, otherwise the generator falls back to archetype-mates only.

### 5. Restart a daemon

```bash
launchctl unload ~/Library/LaunchAgents/com.solvea.engage-daemon.plist
launchctl load   ~/Library/LaunchAgents/com.solvea.engage-daemon.plist
```

The plists in repo root are the source; symlink or copy them into `~/Library/LaunchAgents/`. Note: production plists reference `/Users/solvea/x-agent` or `/Users/siliconno3/x-agent` — edit `WorkingDirectory` and `ProgramArguments` paths to match the deploy host before loading.

### 6. Common failure modes

- **Login lost** → `login-watchdog` pings the per-account TG bot. Fix by running `scripts/auto_login_hunter.py` manually, watch for 2FA prompts.
- **Chrome tab frozen** → engage_daemon's `_recover_frozen_tab` swaps to a fresh tab automatically. If stuck, kill the Chrome process for that profile; daemon will reconnect.
- **Daemons fighting over Chrome** → `scripts/lock.py` provides `chrome_lock` / `file_lock`. If a daemon hangs holding the lock, check `state/locks/`.
- **Telegram offset stuck** → delete `state/telegram_offset.json` (or `..._repost.json`); bridge restarts from latest.
- **engage_seen.json bloat** → it's append-only; safe to prune old entries by date.
- **Analytics run completed but no suggestions** → usually either too little attributable data, weak metadata matching, or the result genuinely found no changes to recommend. Check the row attribution count before concluding the feedback loop is broken.

### 7. Coach commands (reference for the user)

Sent to the COACH_BOT in Telegram:
`/score` (today's rate) · `/why <reply_id>` · `/top` · `/flops` · `/target <handle>` · `/gate strict|normal|loose` · `/pause <handle>` · `/resume <handle>` · `/help`.

---

## Mode B — Replicate for a new handle

Goal: stand up the full stack for `@newhandle` alongside (or instead of) Hunter. Steps assume you're at repo root and the handle is `NEWHANDLE`.

### 1. Decide port + paths

Each account needs a unique Chrome remote-debugging port. Hunter uses `10000`. Pick the next free one (e.g., `10001`).

### 2. Create the per-account skeleton

```bash
mkdir -p accounts/NEWHANDLE/logs chrome-profiles/NEWHANDLE
cp accounts/GuoHunter95258/{config.json,playbook.md,soul.md,targets.json} accounts/NEWHANDLE/
```

Edit `accounts/NEWHANDLE/config.json` — at minimum set the handle, port, and any account-specific knobs. Update `playbook.md` and `soul.md` to reflect the new account's voice and positioning (these feed `generate.py`'s prompts).

### 3. Add credentials to .env

For each new account, add:
```
TELEGRAM_BOT_TOKEN_NEWHANDLE=...   # create a new bot via @BotFather
TELEGRAM_CHAT_ID_NEWHANDLE=...
```
For a separate engage stack (its own approval bridge, coach, etc.), also create new bots for the main bridge / coach / repost variants.

### 4. Decide: shared config or per-account config?

The current Hunter config is hardcoded to `hunter_handle: GuoHunter95258` and `hunter_port: 10000`. Two paths:

- **Per-account config file**: copy `accounts/GuoHunter95258/engage_config.json` to a per-account variant, edit handle + port, and pass `--config` to `engage_daemon.py`.
- **Shared config refactor**: parameterize `hunter_handle` / `hunter_port` and run multiple daemon instances with `--account NEWHANDLE`. Bigger change; only do this if the user wants to run >2 accounts long-term.

Default to per-account config — minimal blast radius.

### 5. Curate targets for the new account

Edit `target_accounts` and `archetypes` in the new config. Apply the rubric in `target-accounts.md` (5K–30K followers, 20–80 replies/post, OP engages with replies, ≥1/day). Then:

```bash
python3 scripts/harvest_replies.py --config engage_config.NEWHANDLE.json
```

This populates `state/winning_replies.json` with archetype-grouped few-shot examples for the new targets.

### 6. Clone the launchd plists

For each daemon Hunter uses, copy the plist, rename `Label`, update `ProgramArguments` to pass the new config or `--account NEWHANDLE`, update `WorkingDirectory`, and update `StandardOutPath` / `StandardErrorPath` to a new log filename. Recommended naming: `com.solvea.engage-daemon.NEWHANDLE.plist`.

Then:
```bash
cp com.solvea.engage-daemon.NEWHANDLE.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.solvea.engage-daemon.NEWHANDLE.plist
```

### 7. Bootstrap the Chrome login

```bash
# Adapt auto_login_hunter.py for NEWHANDLE, or run interactively first time:
python3 scripts/auto_login_hunter.py --handle NEWHANDLE --port 10001
```

Solve any captcha / 2FA prompts manually. `login_watchdog.py` will keep it alive afterward.

### 8. Smoke test

```bash
python3 scripts/engage_daemon.py --config engage_config.NEWHANDLE.json --once --dry-run
```

Confirm targets are reached, posts are parsed, no Chrome lock errors. Then run once for real (drop `--dry-run`) and watch the TG approval card show up.

### 9. Wire boosters (optional)

If the new account also has booster accounts, add them to `boost.boosters` in the config and clone `com.solvea.boost-hunter.plist`.

---

## Files Claude should read before making non-trivial changes

- `x_agent/accounts/GuoHunter95258/engage_config.json` — every Hunter knob lives here. Read before tuning anything.
- `project_x_growth_strategy.md` — strategy doc: borrow-distribution thesis, quote-tweet-ability target, mid-tier reply-first workflow. Honor this when suggesting content direction.
- `target-accounts.md` — scoring rubric for adding/removing targets.
- The daemon's docstring (top of file) — every script's purpose is documented in its header.

## What this skill will NOT do without the user asking

- Restart daemons or kill processes
- Edit `.env` (contains live API keys + bot tokens)
- Run `auto_login_hunter.py` (may need 2FA interaction)
- Push approved drafts manually by bypassing the queue
- Delete or rotate state files

Confirm with the user before any of these.
