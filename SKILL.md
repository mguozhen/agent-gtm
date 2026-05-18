---
name: agent-gtm
description: "Run the 4-card Agent-First GTM playbook on any product. Translates a product's GTM plan into Agent-era moves across Discovery / Traffic / Content / Distribution, with an 8-week roadmap. Renders a printable HTML deck and opens it. Triggers: agent-first gtm, agent gtm, 4 张卡, agent gtm playbook, agent-first playbook, agent first 分发, 给 <product> 做 gtm"
allowed-tools: Bash, Read, Write
---

# Agent-First GTM Skill

Translates any product into the 4-card Agent-First GTM playbook from `~/gtm-swarm/docs/agent-first-gtm-playbook.html`. Produces a per-product HTML deck + 8-week roadmap.

## When to invoke

User says any of:
- "agent-gtm <product>"
- "走一遍 agent-first gtm" / "4 张卡分析 <product>"
- "给 <product> 做一份 agent gtm"
- "agent-gtm playbook for X"
- "把 flatkey 那套 4 张卡套到 <product> 上"

## What it produces

1. **HTML deck** — `~/gtm-swarm/docs/agent-first-gtm-playbook-<slug>.html` (same visual style as the master playbook)
2. **JSON spec** — `~/gtm-swarm/docs/playbook-data/<slug>.json` (the raw analysis, reusable / diffable)
3. Auto `open` the HTML

## The 6 steps you (Claude) run

### Step 1 — Identify the product
Get from the user (or infer from `cwd`):
- `product_name` — string, the slug
- `repo_path` — local path or GitHub URL
- `tagline` — one-line what-it-does

### Step 2 — Classify horizontal vs vertical
Read `references/differentiation-vertical-vs-horizontal.md` and pick one. Wrong classification produces wrong moves.

| | Horizontal | Vertical |
|---|---|---|
| Examples | flatkey (LLM gateway), MCP runtime, code-editor plugin | voc-amazon-reviews, Solvea (CS AI), btcmind.ai |
| MCP catalog category | AI / Dev Tools | Domain-specific (Ecommerce / Customer Service / Finance) |
| Engineering: hub or spoke | Hub | Spoke |
| Traffic hook | Cheaper / drop-in compat | "X-replacement / open source / niche-specific" |
| Default-list target | continue.dev / aider / Cursor LLM providers | Domain-specific Agent presets (Cline Amazon preset, etc) |

### Step 3 — Audit current state
Run the checklist in `references/audit-checklist.md` against the product. Note which items are ✅ done / 🟡 partial / ❌ not started. This becomes the "shipped_status" section in the deck.

The audit covers ~10 items: MCP server, OpenAPI spec, GitHub topics, MCP catalog submissions, npm one-shot install, cookbook count, comparison page, reference architectures, telemetry, cross-link manifest.

### Step 4 — Fill the 4 cards
Use the rubric in `references/four-cards-rubric.md`. Each card needs:
- A **mindset shift** (`<strike>old way</strike> → <strong>new way</strong>`)
- **5 concrete moves** (numbered, with actual file paths / URLs / commands when possible — not abstract advice)
- **This-quarter deliverable** (one paragraph of shippable artifacts)
- **One North-Star KPI** (a number Hunter will actually look at in Q1)

Critical: each move must reference an **actual artifact path or URL** for this product. Don't paste flatkey's moves into a vertical product unchanged — re-derive per the differentiation rules.

### Step 5 — Write the 8-week roadmap
6 rows: W1, W2, W3, W4, W5-6, W7-8. Each row × 4 columns (Discovery / Traffic / Content / Distribution). Each cell = one shippable deliverable that week. Read `examples/voc-amazon-reviews.json` for the cadence.

### Step 6 — Render and open
Build the JSON spec → run `scripts/render.py <spec.json>` → it writes the HTML to `~/gtm-swarm/docs/agent-first-gtm-playbook-<slug>.html` → then `open` it.

```bash
mkdir -p ~/gtm-swarm/docs/playbook-data
# Write the spec
cat > ~/gtm-swarm/docs/playbook-data/<slug>.json <<'EOF'
{ ... }
EOF
# Render
python3 ~/.claude/skills/agent-gtm/scripts/render.py \
  ~/gtm-swarm/docs/playbook-data/<slug>.json
# Auto-open per ~/.claude/projects/-Users-hunter/memory/feedback_auto_open_artifacts.md
open ~/gtm-swarm/docs/agent-first-gtm-playbook-<slug>.html
```

## JSON spec schema

See `examples/voc-amazon-reviews.json` for a complete example. Required top-level keys:

```json
{
  "meta":     { "product_name", "slug", "tagline", "category", "date", "author" },
  "thesis":   { "headline_html", "lede_html" },
  "shipped":  [ { "item", "status", "note" }, ... ],
  "cards":    [ 4 cards × { num, name, cn_name, subtitle, shift_html, moves[5], deliverable_html, kpi } ],
  "roadmap":  [ 6 rows × { week, discovery, traffic, content, distribution } ],
  "principle":      { "tag", "body_html" },
  "differentiation_table": [ ... optional, comparing this product's playbook vs the master flatkey one ],
  "closing_paragraphs":  [ ... ]
}
```

Validation: `scripts/render.py` will exit nonzero with a clear message if required fields are missing.

## Style conventions for the content

- **Chinese-first** for shift / moves / deliverables. The deck is Hunter-facing.
- **No marketing fluff**. Every move must name a file / URL / command / framework.
- **Names are honest**. If you say "Helium 10 alternative", say it; don't write "the leading review intelligence platform".
- **Move counts**: exactly 5 per card. Less = lazy. More = unfocused.
- **KPI must be a number**. "Lots of installs" is not a KPI. "≥ 200 installs/week" is.

## Don't

- Don't render until you've completed the audit (Step 3). The deck loses 80% of its value if the "what's already done" column is missing.
- Don't pad cards. If a product genuinely has 3 good Discovery moves and 2 weak ones, write 5 strong + delete the weak; don't invent.
- Don't skip the differentiation step. Horizontal vs vertical changes Card 2 hooks and Card 4 hub/spoke role.
- Don't write to a path other than `~/gtm-swarm/docs/`. That's where the playbook ecosystem lives.

## After rendering

Tell Hunter:
- Path to the HTML (1 line)
- Top 3 weakest current-state items (so he knows where to start)
- Top 3 highest-leverage Q1 moves (so he picks one to ticket today)

Don't dump the full 4-card analysis again in chat — the HTML already shows it.
