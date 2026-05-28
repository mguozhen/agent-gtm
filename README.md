# agent-gtm

Repository for agent-era GTM methodology and X automation operations.

## What is in this repo

| Path | Purpose |
|---|---|
| [`skills/agent-gtm`](skills/agent-gtm/) | Claude Code skill for the 4-card Agent-First GTM playbook (Discovery / Traffic / Content / Distribution). |
| [`skills/hunter-x-agent`](skills/hunter-x-agent/) | Skill for operating Hunter's X automation stack, analytics feedback loop, and Multica run-only tasks. |
| [`skills/multica-hunter-x-analytics`](skills/multica-hunter-x-analytics/) | Skill for collecting Hunter X analytics telemetry and reporting `swarm.telemetry.v1` batches to GTM Swarm. |
| [`skills/voc-ai-x-agent`](skills/voc-ai-x-agent/) | Skill for operating the VOC.ai X automation stack and its restored `voc-ai` Multica workspace. |
| [`x_agent/`](x_agent/) | X automation code and account configs (scripts + per-account config/playbook/targets). |
| [`projects/voc-amazon-reviews/`](projects/voc-amazon-reviews/) | Project snapshot included in this repo (MCP server, docs, scripts, tests). |

## Skills

### `agent-gtm`

Converts a product into an agent-first GTM plan with:

- 4 cards (Discovery / Traffic / Content / Distribution)
- shipped-status audit
- 8-week execution roadmap
- rendered HTML playbook deck

Typical prompts:

- `agent-gtm <product>`
- `给 <product> 做一份 agent gtm`

### `hunter-x-agent`

Operator workflow for the Hunter X stack:

- daemon status/debug
- Telegram approval bridge checks
- queue and posting flow troubleshooting
- analytics export feedback loop
- new-handle replication runbook

### `multica-hunter-x-analytics`

Focused telemetry workflow for Hunter's Multica X analytics agent:

- `collect_daily_telemetry` job handling
- X analytics observation batching
- GTM Swarm `swarm.telemetry.v1` validation
- short analytics judgement and system-change recommendations

### `voc-ai-x-agent`

Operator workflow for the `@VOC_ai` stack:

- keyword engage tuning
- quote scout and mutual engage checks
- Telegram approval bridge debugging
- `voc-ai` Multica workspace and autopilot alignment

## Install (Claude Code skills)

```bash
git clone https://github.com/mguozhen/agent-gtm.git ~/code/agent-gtm
ln -s ~/code/agent-gtm/skills/agent-gtm ~/.claude/skills/agent-gtm
ln -s ~/code/agent-gtm/skills/hunter-x-agent ~/.claude/skills/hunter-x-agent
ln -s ~/code/agent-gtm/skills/multica-hunter-x-analytics ~/.claude/skills/multica-hunter-x-analytics
ln -s ~/code/agent-gtm/skills/voc-ai-x-agent ~/.claude/skills/voc-ai-x-agent
```

For project-scoped use, place a skill under `.claude/skills/` in that project.

## License

MIT
