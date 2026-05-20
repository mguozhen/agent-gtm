#!/usr/bin/env python3
"""
agent-gtm/scripts/render.py — JSON spec → HTML deck.

Usage:
    python3 render.py <spec.json>             # writes to ~/gtm-swarm/docs/agent-first-gtm-playbook-<slug>.html
    python3 render.py <spec.json> --out PATH  # writes to PATH

The JSON spec schema lives in SKILL.md. See examples/voc-amazon-reviews.json
for a complete worked example.

Pure stdlib — no jinja2 / pyyaml deps. Substitution is by `{{KEY}}` markers in
the template, with loop sections (cards, roadmap rows) pre-rendered into HTML
strings before substitution.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
TEMPLATE = SKILL_ROOT / "references" / "playbook-template.html"
DEFAULT_OUT_DIR = Path.home() / "gtm-swarm" / "docs"


REQUIRED_TOP = {"meta", "thesis", "cards", "roadmap"}
REQUIRED_META = {"product_name", "slug", "date", "author"}
REQUIRED_CARD_KEYS = {"num", "name", "cn_name", "subtitle", "shift_html",
                      "moves", "deliverable_html", "kpi"}
REQUIRED_ROADMAP_KEYS = {"week", "discovery", "traffic", "content", "distribution"}


def validate(spec: dict) -> None:
    missing = REQUIRED_TOP - spec.keys()
    if missing:
        sys.exit(f"✗ spec missing required top-level keys: {sorted(missing)}")

    missing = REQUIRED_META - spec["meta"].keys()
    if missing:
        sys.exit(f"✗ spec.meta missing: {sorted(missing)}")

    cards = spec["cards"]
    if len(cards) != 4:
        sys.exit(f"✗ spec.cards must have exactly 4 cards, got {len(cards)}")
    for i, c in enumerate(cards):
        missing = REQUIRED_CARD_KEYS - c.keys()
        if missing:
            sys.exit(f"✗ spec.cards[{i}] missing: {sorted(missing)}")
        if len(c["moves"]) != 5:
            sys.exit(
                f"✗ spec.cards[{i}] '{c.get('name','?')}' must have exactly 5 moves, "
                f"got {len(c['moves'])}. Per the rubric: 5 strong > 7 weak."
            )

    for i, row in enumerate(spec["roadmap"]):
        missing = REQUIRED_ROADMAP_KEYS - row.keys()
        if missing:
            sys.exit(f"✗ spec.roadmap[{i}] missing: {sorted(missing)}")


def render_meta_row(meta: dict) -> str:
    """Build the meta-row at top of header."""
    rows = meta.get("meta_row", [
        {"k": "受众", "v": "Agent &gt; Human"},
        {"k": "载体", "v": "MCP / OpenAPI / 结构化 JSON"},
        {"k": "反馈", "v": "tool call 调用次数 + 收入"},
        {"k": "目标", "v": "成为某类能力的<i>默认</i>"},
    ])
    return "\n      ".join(
        f'<span><b>{r["k"]}</b>：{r["v"]}</span>' for r in rows
    )


def render_shipped_section(shipped: list[dict] | None) -> str:
    """Optional 'current state' table."""
    if not shipped:
        return ""
    rows_html = []
    score_done = sum(1 for s in shipped if s["status"] == "done")
    score_partial = sum(1 for s in shipped if s["status"] == "partial")
    score_total = len(shipped)

    for s in shipped:
        status = s["status"]
        status_label = {"done": "✅ DONE", "partial": "🟡 PARTIAL", "todo": "❌ TODO"}.get(status, status.upper())
        rows_html.append(
            f'<div class="sc">{s["item"]}</div>'
            f'<div class="sc status-{status}">{status_label}</div>'
            f'<div class="sc">{s.get("note","")}</div>'
        )
    return f"""
  <section>
    <h2>当前状态盘点</h2>
    <div class="h2-sub">audit: {score_done} done · {score_partial} partial · {score_total - score_done - score_partial} todo · 总分 {score_done}/{score_total}</div>
    <div class="shipped">
      <div class="sh">Item</div><div class="sh">Status</div><div class="sh">Note</div>
      {"".join(rows_html)}
    </div>
  </section>
"""


def render_card(idx: int, card: dict) -> str:
    """Render one of the 4 cards."""
    c_class = f"c{idx + 1}"
    moves_html = "\n          ".join(f"<li>{m}</li>" for m in card["moves"])
    return f"""
      <article class="card {c_class}">
        <div class="card-num">Card {card['num']} / {card['name']}</div>
        <h3 class="card-name">{card['cn_name']} — <span class="cn">{card['subtitle']}</span></h3>
        <div class="card-shift">{card['shift_html']}</div>
        <div class="h-move">五个动作</div>
        <ol class="move-list">
          {moves_html}
        </ol>
        <div class="ship-block">
          <div class="h-ship">本季度交付</div>
          <div class="ship-content">{card['deliverable_html']}</div>
        </div>
        <div class="kpi">
          <span class="kpi-l">North Star KPI</span>
          <span class="kpi-v">{card['kpi']}</span>
        </div>
      </article>
"""


def render_roadmap(roadmap: list[dict]) -> str:
    """Render 6 weekly roadmap rows."""
    parts = []
    for row in roadmap:
        parts.append(
            f'<div class="rc week">{row["week"]}</div>'
            f'<div class="rc">{row["discovery"]}</div>'
            f'<div class="rc">{row["traffic"]}</div>'
            f'<div class="rc">{row["content"]}</div>'
            f'<div class="rc">{row["distribution"]}</div>'
        )
    return "\n      ".join(parts)


def render_differentiation(diff: list[dict] | None) -> str:
    """Optional differentiation table — for vertical products comparing
    against the master flatkey playbook (or any reference)."""
    if not diff:
        return ""
    rows_html = []
    for row in diff:
        rows_html.append(
            f'<tr><td class="dim">{row["dim"]}</td>'
            f'<td>{row["reference"]}</td>'
            f'<td>{row["this_product"]}</td>'
            f'<td>{row.get("why","")}</td></tr>'
        )
    return f"""
  <section>
    <h2>差异化对照</h2>
    <div class="h2-sub">为什么这个产品的 playbook 跟 reference 不一样</div>
    <table class="diff">
      <thead>
        <tr><th>维度</th><th>Reference 玩法</th><th>本产品玩法</th><th>原因</th></tr>
      </thead>
      <tbody>
        {"".join(rows_html)}
      </tbody>
    </table>
  </section>
"""


def render_closing(paragraphs: list[str]) -> str:
    """Render the closing paragraph section."""
    return "\n    ".join(f"<p>{p}</p>" for p in paragraphs)


def render(spec: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")

    meta = spec["meta"]
    thesis = spec["thesis"]

    # Pre-render loops + optional sections.
    cards_html = "\n      ".join(render_card(i, c) for i, c in enumerate(spec["cards"]))
    roadmap_html = render_roadmap(spec["roadmap"])
    shipped_html = render_shipped_section(spec.get("shipped"))
    diff_html = render_differentiation(spec.get("differentiation"))
    closing_html = render_closing(spec.get("closing_paragraphs", []))
    meta_row_html = render_meta_row(meta)

    principle_intro = spec.get("principle_intro", {
        "tag": "PRINCIPLES.md · §06",
        "body_html": (
            'Agents Are the New Distribution Channel — External AI Agents '
            'are a new distribution channel on the order of the App Store / '
            'SEO / 公众号 / TikTok at their respective inflection points. '
            "Agents <em>don't call your UI, they call your capability</em>. "
            "A product with only a UI and no API gets bypassed. An API "
            "that is not agent-friendly loses to one that is."
        ),
    })
    principle_close = spec.get("principle_close", {
        "tag": "观察 · 三段式",
        "body_html": (
            '<b>1.</b> 用户行为正从 "open the app" 切到 "ask the agent." <br>'
            '<b>2.</b> Agent 不读你的落地页，它读你的 metadata。<br>'
            '<b>3.</b> <em>谁先成为 Agent 默认调用的能力源，谁就拿到这波分发红利。</em>'
        ),
    })

    footer_html = spec.get("footer_html", (
        f'Generated <b>{meta["date"]}</b> · {meta.get("author","Hunter")} · '
        f'agent-gtm skill <br>'
        f'Master playbook: <a href="agent-first-gtm-playbook.html">agent-first-gtm-playbook.html</a>'
    ))

    eyebrow_left = meta.get("eyebrow_left", f"{meta['product_name']} · Agent-First GTM")

    substitutions = {
        "{{TITLE}}": f"Agent-First GTM · {meta['product_name']}",
        "{{EYEBROW_LEFT}}": eyebrow_left,
        "{{EYEBROW_DATE}}": f"{meta['date']} · {meta['author']}",
        "{{H1_HTML}}": thesis.get("headline_html", "抽四张卡：<em>面向 Agent 的 GTM</em>"),
        "{{LEDE_HTML}}": thesis["lede_html"],
        "{{META_ROW_HTML}}": meta_row_html,
        "{{PRINCIPLE_INTRO_TAG}}": principle_intro["tag"],
        "{{PRINCIPLE_INTRO_BODY_HTML}}": principle_intro["body_html"],
        "{{SHIPPED_SECTION_HTML}}": shipped_html,
        "{{CARDS_HTML}}": cards_html,
        "{{ROADMAP_SUB}}": spec.get("roadmap_sub",
            "把四张卡变成时间表 · 每周一个 deliverable · 不动用全公司"),
        "{{ROADMAP_ROWS_HTML}}": roadmap_html,
        "{{DIFFERENTIATION_SECTION_HTML}}": diff_html,
        "{{PRINCIPLE_CLOSE_TAG}}": principle_close["tag"],
        "{{PRINCIPLE_CLOSE_BODY_HTML}}": principle_close["body_html"],
        "{{CLOSING_PARAGRAPHS_HTML}}": closing_html,
        "{{FOOTER_HTML}}": footer_html,
    }

    out = template
    for key, val in substitutions.items():
        out = out.replace(key, val)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("spec", help="Path to JSON spec file")
    ap.add_argument("--out", default=None, help="Output HTML path (default: ~/gtm-swarm/docs/agent-first-gtm-playbook-<slug>.html)")
    args = ap.parse_args()

    spec_path = Path(args.spec).expanduser().resolve()
    if not spec_path.exists():
        sys.exit(f"✗ spec file not found: {spec_path}")

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    validate(spec)
    html = render(spec)

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
    else:
        slug = spec["meta"]["slug"]
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DEFAULT_OUT_DIR / f"agent-first-gtm-playbook-{slug}.html"

    out_path.write_text(html, encoding="utf-8")
    print(f"✓ wrote {out_path}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
