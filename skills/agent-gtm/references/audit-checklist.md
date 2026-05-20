# Agent-First GTM Audit Checklist

Run this against any product before filling the 4 cards. Each item gets ✅ / 🟡 / ❌ + a one-line note.

## Discovery layer (Card 1)

- [ ] **MCP server** exists and is published / installable
  - Check: is there an `mcp_server/` dir? `package.json` with mcp deps? A `server.py`?
- [ ] **5 MCP catalogs** submitted: mcp.so / glama.ai / smithery.ai / GitHub `modelcontextprotocol/servers` / PulseMCP
  - Check: search each catalog for product name; check repo README badges
- [ ] **`.well-known/agent-capabilities.json`** or `.well-known/ai-plugin.json` exposed
  - Check: `curl https://<domain>/.well-known/agent-capabilities.json`
- [ ] **OpenAPI spec for the MCP tool layer** (not the underlying API) at a public URL
  - Check: GitHub repo for `openapi.json` / `openapi.yaml`
- [ ] **GitHub topics** include all of: `mcp`, `mcp-server`, `model-context-protocol`, domain-specific topics (`amazon-fba`, `customer-service`, etc)
  - Check: `gh repo view <owner>/<name> --json topics`

## Traffic layer (Card 2)

- [ ] **Tool descriptions** in MCP schema are router-optimized (lead with concrete value, mention alternative being replaced)
  - Check: inspect `mcp_server/server.py` `@mcp.tool` docstrings
- [ ] **Cost / capability metadata** in tool schema (`_meta.credits_per_call` or equivalent)
  - Check: tool definitions for cost hints
- [ ] **Error responses** include `suggested_action` field for agent self-repair
  - Check: error handling code paths in server
- [ ] **One-shot install** under 5 seconds: `npx -y @org/mcp` or single bash one-liner
  - Check: README install section — count commands required
- [ ] **Cross-link manifest** with sister products (if part of a group)
  - Check: README + capabilities.json for links to sibling products

## Content layer (Card 3)

- [ ] **5+ cookbooks** in 30-second / 3-line format (not blog posts)
  - Check: `docs/cookbooks/` or similar; count `.md` files with 3-line code samples
- [ ] **Comparison page** vs top competitor (structured table, not prose)
  - Check: `docs/compare/` or similar; or substring "vs " in README
- [ ] **Error code semantics** documented (table of error.type → suggested_action)
  - Check: `docs/errors.md` / similar; or README error table
- [ ] **README "Quick install for Agents"** section with Cursor / Claude Code / Continue.dev configs
  - Check: README for explicit MCP client config snippets

## Distribution layer (Card 4)

- [ ] **MCP call telemetry** (privacy-conscious, opt-out flag)
  - Check: server source for any analytics / telemetry calls
- [ ] **3+ OSS reference architectures** as separate repos (or `examples/` with full-runnable templates)
  - Check: `examples/` subdir; sister repos under same owner
- [ ] **Bundled config** in at least one Agent framework's default preset
  - Check: web-search for "<product> default preset" / framework preset repos
- [ ] **Status / fallback page** for failure-as-distribution
  - Check: any `/status` route or status page domain
- [ ] **gtm-swarm content engine** has this product as a registered "channel" with auto MCP CTA
  - Check: `~/gtm-swarm/projects/` for product entry; engine voice files for MCP CTA rule

## How to score

After scanning, count ✅. Out of 20:
- **0-5**: Greenfield. The deck will mostly be "this is the work, here's the order".
- **6-12**: Mid-build. Highlight which 3-5 highest-leverage gaps to close in Q1.
- **13-18**: Mature. Focus the deck on the remaining moat-builders (telemetry, framework defaults, comparison content).
- **19-20**: This isn't a GTM problem, it's an execution / scale problem — different playbook.

Always include the score in the deck's "shipped_status" section (e.g., `audit: 5/20`).
