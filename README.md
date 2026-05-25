# agent-gtm

Repository for agent-era GTM methodology and X automation operations.

## What is in this repo

| Path | Purpose |
|---|---|
| [`skills/agent-gtm`](skills/agent-gtm/) | Claude Code skill for the 4-card Agent-First GTM playbook (Discovery / Traffic / Content / Distribution). |
| [`skills/hunter-x-agent`](skills/hunter-x-agent/) | Claude Code skill for operating and replicating the Hunter X automation stack. |
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
- new-handle replication runbook

## Install (Claude Code skills)

```bash
git clone https://github.com/mguozhen/agent-gtm.git ~/code/agent-gtm
ln -s ~/code/agent-gtm/skills/agent-gtm ~/.claude/skills/agent-gtm
ln -s ~/code/agent-gtm/skills/hunter-x-agent ~/.claude/skills/hunter-x-agent
```

For project-scoped use, place a skill under `.claude/skills/` in that project.

## License

MIT
