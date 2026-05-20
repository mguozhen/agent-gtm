# agent-gtm

A collection of [Claude Code](https://claude.com/claude-code) skills for **agent-era GTM** — building, operating, and distributing products in a world where AI agents are the new distribution channel.

## Skills in this repo

| Skill | What it does |
|---|---|
| [`agent-gtm`](skills/agent-gtm/) | The 4-card Agent-First GTM playbook — translates any product's go-to-market plan into Agent-era moves across Discovery / Traffic / Content / Distribution, with an 8-week roadmap and a printable HTML deck. |
| [`hunter-x-agent`](skills/hunter-x-agent/) | Operate and replicate the Hunter X growth-engine stack — engage daemon, Telegram approval bridge, reply-quality coach, content scouts, booster accounts. Project-specific skill for the multi-account X agent system. |

## Install

Clone the repo, then symlink each skill folder into your Claude Code skills directory:

```bash
git clone https://github.com/mguozhen/agent-gtm.git ~/code/agent-gtm
ln -s ~/code/agent-gtm/skills/agent-gtm       ~/.claude/skills/agent-gtm
ln -s ~/code/agent-gtm/skills/hunter-x-agent  ~/.claude/skills/hunter-x-agent
```

Or copy individual skills directly:

```bash
cp -R skills/agent-gtm ~/.claude/skills/agent-gtm
```

For project-scoped use, drop a skill into the target repo's `.claude/skills/` instead of `~/.claude/skills/`.

## Skill summaries

### `agent-gtm` — the 4-card playbook

The premise: **agents are a new distribution channel** — on the order of the App Store, SEO, or TikTok at their inflection points. Agents don't call your UI, they call your *capability*.

| Card | Old funnel | New funnel |
|---|---|---|
| **01 · Discovery** | App Store / SEO | MCP catalog · capability manifest · training data |
| **02 · Traffic** | Landing page / ads | Agent router picks you on cost + description metadata |
| **03 · Content** | Blog / video | OpenAPI · cookbooks · semantic error codes |
| **04 · Distribution** | Channel BD | Successful tool calls self-propagate · hub-and-spoke cross-links |

Invoke with `agent-gtm <product>` or "给 <product> 做一份 agent gtm". Produces an HTML deck, a JSON spec, and a roadmap.

### `hunter-x-agent` — operate and replicate the X stack

A multi-process growth engine for an X (Twitter) account — runs as launchd daemons on macOS, drives a per-account headless Chrome via CDP, generates replies/posts with Claude, and routes every draft through a Telegram approval bridge.

Two modes:

- **Operate** — check daemon status, tune `engage_config.json`, debug the reply queue, interpret coach reports
- **Replicate** — stand up the full stack for a new X handle (chrome profile, .env, config, plists, login bootstrap, smoke test)

This skill expects to be installed inside the x-agent repo as a project skill (it references `engage_config.json`, `scripts/engage_daemon.py`, etc.).

---

MIT — methodology + operations skills, no warranty. Built for Claude Code.
