# 4-Cards Rubric

Each of the 4 cards has the same internal structure. Fill in the same 5 slots for each.

## Slot 1 — Card name + Chinese subtitle

| Card | Eng | 中文 | Subtitle hint |
|---|---|---|---|
| 01 | Discovery | 发现 | "让 Agent 知道我们存在" |
| 02 | Traffic | 引流 | "让 Agent 把任务路由给我们" |
| 03 | Content | 内容 | "为 Agent 写，不为人写" |
| 04 | Distribution | 分发 | "每次 tool call 都是一次种子" |

## Slot 2 — Mindset shift

One sentence. Strike out the old way, bold the new way, end with the *operational* implication (not the philosophical one).

**Template**: `<strike>旧动作</strike> → <strong>新动作</strong>。具体的 router/catalog/protocol-level 后果。`

**Good** (Card 1):
> ~~"用户发现 App"~~ → **"Agent 在它的工具索引里看到我们"**。Agent 的索引来自 3 处：MCP 目录、LLM 训练数据、Agent 框架的默认配置。我们必须出现在这三处。

**Bad** (too philosophical):
> ~~"用户找产品"~~ → **"Agent 是新用户"**。这是范式革命。

## Slot 3 — 5 concrete moves

Numbered list. Each move:
- Names a **specific artifact** (file path / URL / npm package name / repo name)
- Or names a **specific channel** (catalog name, framework name, contact target)
- Or names a **specific command** (`claude mcp add ...`, `npx ...`)

**Good**:
> 1. 提交 flatkey 到 **5 大 MCP catalog**：`mcp.so` / `glama.ai` / `smithery.ai` / GitHub `modelcontextprotocol/servers` / PulseMCP。所有 catalog 一周内全收录。

**Bad** (no artifact):
> 1. 提升我们在 MCP 生态的曝光度。

Use `<code>` for paths and commands, `<b>` for the key term of each move.

## Slot 4 — This-quarter deliverable

One paragraph. The actual shippable artifacts you'd be embarrassed not to have by quarter-end. Should map to specific moves above.

**Good**:
> 5 大 MCP catalog 收录 + 2 个 Agent 框架默认列表（continue.dev / aider）。所有 4 个产品都暴露 `/api/agent-capabilities`。

**Bad**:
> Significant traction in Agent ecosystem.

## Slot 5 — North-star KPI

ONE number. Weekly cadence preferred. Must be measurable from a dashboard, not a survey.

**Good**:
> 每周 unique Agent 看到我们 metadata 数 ≥ 500
> MCP install 数 ≥ 200/周 · 单次 install 后 30 天留存调用率 ≥ 40%
> 第 4 周末：MCP 通道 tool call 数 ≥ 10K · 进入 ≥ 1 个 framework 的默认配置

**Bad**:
> Healthy ecosystem traction across all dimensions.

## When data isn't there yet

If audit shows the product hasn't built X yet (e.g., no MCP catalog submissions), the card still gets 5 moves — they're just **shipping moves** instead of optimization moves. The deck doesn't pretend things are done. Use the `shipped_status` section to track what's true vs aspirational.

## Roadmap row format

6 rows: W1, W2, W3, W4, W5-6, W7-8. Each row has 4 cells (one per card). Each cell:
- One **bolded primary deliverable**
- Optionally a one-line "how" (file path, command, repo)

Roadmap weeks should be **front-loaded**: W1-W4 are heavy execution, W5-W8 are integration / measurement / iteration. Don't put creative work in W7-W8 — that's where things drift.

## The principle quote box

One sentence per first-principles claim, 3 sentences max. The format is:
- §1: behavioral shift (what users are doing differently)
- §2: structural implication (what that means for product surface)
- §3: outcome (who wins)

Always italic the **outcome line** with `<em>` and accent color.

## Closing paragraphs

Two paragraphs max:
1. The "addition not replacement" reminder — old GTM doesn't stop, agent-GTM gets layered on top.
2. The "8 weeks in we'll have 3 numbers" — the specific KPIs that define agent-era PMF for this product.
