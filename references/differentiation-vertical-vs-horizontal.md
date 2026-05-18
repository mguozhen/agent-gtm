# Vertical vs Horizontal — Why the master playbook can't be copy-pasted

The master `agent-first-gtm-playbook.html` was written for **flatkey** — a horizontal product (LLM gateway, drop-in compat with Anthropic/OpenAI SDKs). Many moves don't transfer cleanly to vertical products. Adjust per this table.

## Quick test: is the product horizontal or vertical?

**Horizontal** if it answers yes to any:
- It replaces or wraps a primitive (LLM API, embedding API, MCP runtime, vector store, code editor)
- It's evaluated on price / latency / compatibility
- Its competitors are other infrastructure (Anthropic, OpenAI, Bedrock, Vercel)
- Cross-industry buyers (an Amazon seller AND a SaaS dev AND a researcher all use it the same way)

**Vertical** if it answers yes to any:
- It serves one industry / role / workflow (Amazon sellers, customer service teams, crypto traders, lawyers)
- It's evaluated on domain accuracy / depth, not infrastructure metrics
- Its competitors are SaaS tools (Helium 10, Zendesk, Glassnode, Harvey)
- Buyer needs a specific skill / vocabulary to use it

## How each card differs

### Card 1 — Discovery

| | Horizontal | Vertical |
|---|---|---|
| MCP catalog category | "AI Provider" / "Dev Tools" / "Inference" | Domain ("Ecommerce", "Customer Service", "Finance", "Legal") |
| GitHub topics | `mcp`, `mcp-server`, `llm-gateway`, `inference` | `mcp`, `mcp-server`, plus domain (`amazon-fba`, `helium10-alternative`, `voc`, `customer-service-ai`) |
| Target framework defaults | continue.dev / aider / Cursor as **LLM provider** | Cline / Open Hands **system prompt presets for the domain** ("Amazon FBA assistant preset") |
| `.well-known` hosting | On the product's own domain | Often GitHub Pages (no dedicated domain) |

### Card 2 — Traffic

| | Horizontal | Vertical |
|---|---|---|
| Tool description hook | "Cheaper than X · drop-in compat" | "X-replacement · open source · niche-specific feature Y" |
| Cost metadata | `input_per_1m` / `output_per_1m` (LLM token pricing) | `credits_per_call` / `per_request` (API call pricing) or "free tier ~N requests/month" |
| Error message as ad | "X is down? Set our endpoint via env" | "Don't have an API key for X? Free starter tier here" |
| Cross-link direction | **Hub** — list verticals in our manifest | **Spoke** — appear in horizontal hub's manifest |
| One-shot install | npm `npx -y @org/mcp` | Same target, but emphasize **domain context** ("for Amazon sellers: npx ...") |

### Card 3 — Content

| | Horizontal | Vertical |
|---|---|---|
| Cookbook style | "Switch from OpenAI in 3 lines" | "Solve <domain pain point> in 3 lines" |
| Comparison page | vs other infra (Anthropic, OpenRouter) | vs domain SaaS (Helium 10, Data Dive, JungleScout) |
| OpenAPI spec | The tool surface for any caller | Same, but with **domain examples** in the operation descriptions |
| LLM-training-data targets | apis.guru / openapi-directory | Same + domain-specific aggregators (Amazon Seller tools lists, etc) |

### Card 4 — Distribution

| | Horizontal | Vertical |
|---|---|---|
| Hub-or-spoke role | **Hub** (other verticals route through us) | **Spoke** (we plug into a horizontal hub or a domain stack) |
| OSS reference archs | `<product>-langchain-router`, `<product>-cursor-bridge`, default LLM in popular IDEs | `<product>-langchain-agent` (for the domain workflow), `<product>-cron-monitor`, `<product>-cursor-<domain>-preset` |
| Co-distribution targets | Generic Agent frameworks (Cursor, Continue.dev, Aider) | Domain workflow templates (Amazon Seller Cursor pack, CS team Slack bot template) |
| KPI volume | High — every tool user is potential traffic | Lower — niche audience. Set realistic KPIs (half of horizontal target). |
| Failure-as-distribution | Status page → SEO + LLM training | Same, but error string also seeds domain-specific Google queries |

## Cross-link patterns inside a group

If the product is part of a multi-product group (e.g., Hunter has flatkey + voc-amazon-reviews + solvea.cx + btcmind.ai), the cross-link logic is:

```
flatkey (hub, horizontal)
  ├─ recommend: voc-amazon-reviews (for Amazon workflow callers)
  ├─ recommend: solvea.cx (for CS workflow callers)
  └─ recommend: btcmind.ai (for crypto workflow callers)

voc-amazon-reviews (spoke, vertical)
  └─ uses flatkey for the underlying LLM in extract_listing_improvements

solvea.cx (spoke, vertical)
  └─ uses flatkey for underlying LLM

btcmind.ai (spoke, vertical)
  └─ uses flatkey for underlying LLM
```

Internal cross-links are the cheapest distribution channel. The vertical spoke gets free traffic from the hub; the horizontal hub gets free credibility from having domain-specific verticals on top.

## Anti-patterns

1. **Vertical pretending to be horizontal**: a vertical product trying to compete in `continue.dev` default provider list — you'll lose to flatkey/Anthropic/OpenAI. Stay in your lane.
2. **Horizontal trying to be deep in one vertical**: a horizontal LLM gateway writing 50 Amazon-specific cookbooks — diluted, worse than a thin vertical that does it natively.
3. **Spoke claiming hub role**: a vertical writing "use us as your primary capability hub" — Agent routers don't believe it.
4. **Hub without spokes**: a horizontal product with no vertical case studies — looks generic, no defensibility.
