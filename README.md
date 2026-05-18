# agent-gtm

A [Claude Code](https://claude.com/claude-code) skill that runs the **4-card Agent-First GTM playbook** on any product.

The premise: **Agents are a new distribution channel** — on the order of the App Store, SEO, or TikTok at their inflection points. Agents don't call your UI, they call your *capability*. This skill translates a product's go-to-market plan into Agent-era moves across four cards, then renders a printable HTML deck.

## The four cards

| Card | Old funnel | New funnel |
|---|---|---|
| **01 · Discovery** | App Store / SEO | MCP catalog · capability manifest · training data |
| **02 · Traffic** | Landing page / ads | Agent router picks you on cost + description metadata |
| **03 · Content** | Blog / video | OpenAPI · cookbooks · semantic error codes |
| **04 · Distribution** | Channel BD | Successful tool calls self-propagate · hub-and-spoke cross-links |

Each card produces: a mindset shift, 5 concrete moves (every move names a real file / URL / command), a deliverable, and one numeric North-Star KPI.

## Install

Copy this directory into your Claude Code skills folder:

```bash
git clone https://github.com/mguozhen/agent-gtm.git ~/.claude/skills/agent-gtm
```

Then invoke it in Claude Code with: `agent-gtm <product>` — or "给 <product> 做一份 agent gtm".

## What it produces

1. **HTML deck** — a per-product playbook in the master visual style
2. **JSON spec** — the raw analysis, reusable and diffable
3. An 8-week (or compressed 1-week) roadmap across the four cards

## Layout

```
SKILL.md          — the skill definition Claude reads
references/        — horizontal-vs-vertical rules, audit checklist, the 4-card rubric, HTML template
examples/          — a complete filled-in playbook spec
scripts/render.py  — JSON spec → HTML deck renderer
```

## How it works

1. Identify the product (name, repo, tagline)
2. Classify horizontal (hub) vs vertical (spoke) — this changes the moves
3. Audit current state against the ~10-item Agent-GTM checklist
4. Fill the 4 cards with concrete, artifact-referencing moves
5. Write the roadmap
6. Render the HTML deck and open it

---

MIT — methodology skill, no warranty. Built for Claude Code.
