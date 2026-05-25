"""repost_agent — single-turn agent loop for the @X_autorepost_bot.

Replaces the hardcoded routing (stash/consume/classify/dispatch) in
telegram_bridge.py with one Claude agent. Claude maintains conversation
context, decides when to ask for clarification, when to research the web,
when to scrape X, when to draft a post — entirely from the conversation.

Invoked by telegram_bridge whenever a new message arrives on the repost bot.
The bridge passes the latest message (text or photo) via CLI; this script
loads history, runs the agent loop until Claude returns end_turn, saves
history, and exits.

Anthropic SDK pattern:
    client.messages.create(model=..., tools=[server_tools..., local_tools...])
  - server tools (web_search) executed by Anthropic
  - local tools dispatched here via agent_tools.dispatch()
"""
import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;          env.load()
import anthropic
import agent_tools as _tools

STATE_DIR    = os.path.join(ROOT_DIR, "state")
HISTORY_PATH = os.path.join(STATE_DIR, "repost_chat_history.json")
LOG_DIR      = os.path.join(ROOT_DIR, "logs", "repost_agent")

MODEL          = "claude-opus-4-7"
MAX_TOKENS     = 4096
MAX_ITERATIONS = 12          # safety cap on tool-use rounds per message
HISTORY_LIMIT  = 30          # last N turns retained (user+assistant each count)

SYSTEM_PROMPT = """You are the X content assistant for the operator who runs @GuoHunter95258 (a founder/builder voice in the AI niche). You operate inside a Telegram chat. The operator sends you topics, URLs, screenshots, and instructions; you decide what to do.

YOUR JOB:
- Help the operator find, research, and craft content for X (Twitter).
- Maintain conversation context across messages — when the operator says "draft a post about that", "that" refers to your prior research/discussion in this thread.

TOOLS AVAILABLE:
- `web_search`: real-time web search (news, interviews, articles, etc.). USE THIS for anything happening outside X — interviews on YouTube, news articles, blog posts, recent announcements, what people are saying off-platform.
- `x_search_keyword(keyword, limit)`: search X (Top tab) for posts on a keyword. USE THIS to find recent viral X posts on a topic.
- `x_fetch_url(url)`: fetch one specific X tweet by URL. USE THIS when the operator shares a tweet link.
- `draft_post(text, target_url, angle)`: send a post draft to the operator's Telegram for approval (appears as a card with Approve/Regen/Reject buttons). USE THIS when the operator wants to publish something on X. `target_url` makes it a quote-tweet; leave empty for an original post. Hard limit: 280 chars.
- `reply_text(text)`: send a plain Markdown TG message to the operator. USE THIS for research summaries, clarifying questions, status updates, anything that isn't a draft post.

ROUTING RULES:
1. If the operator's message has clear instruction (e.g. "give me 3 takes on Hermes", "summarize CEO interviews about Slack's moat"), execute it directly — no clarifying question needed.
2. If the operator only sends an anchor (a URL, a keyword, a screenshot) with no instruction, AND there is no relevant prior context in this conversation, ask what they want via `reply_text`. Be concise — examples in the question are nice but optional.
3. If the operator sends what looks like a follow-up to your prior work in this thread ("draft a post about that", "make it shorter", "now make it sarcastic"), use prior context as the anchor. Don't ask again.
4. Default to ONE draft, unless the operator says otherwise (e.g. "give me 5 variants").

STYLE:
- Concise. Don't over-explain what you're about to do.
- When researching: lead with the headline insight, then 3-6 bullets with specifics/numbers/quotes, then a 1-line "why it matters". Cite URLs inline.
- When drafting posts: in the operator's voice. Specific over abstract. No hashtags, no emojis, no @-mentions unless asked. Lead with a claim or concrete fact, not a hedge. Use em-dashes for pivots.
- Always send a status `reply_text` if the work will take >10s, e.g. "🔎 searching the web…" — then proceed with the tool call.

ERROR HANDLING:
- If a tool returns an error, tell the operator briefly and propose an alternative (different keyword, different URL, manual input).
- If you're uncertain about intent, ask — don't guess at length.

END:
- Always end your turn with at least one `reply_text` or `draft_post` so the operator sees something."""


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


def _load_history() -> list:
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH) as f:
            d = json.load(f)
        return d.get("messages", []) if isinstance(d, dict) else []
    except Exception:
        return []


def _save_history(messages: list):
    # Keep tail to bound context size + cost
    trimmed = messages[-HISTORY_LIMIT:]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump({"updated_at": _ts(), "count": len(trimmed),
                   "messages": trimmed}, f, indent=2, ensure_ascii=False)


def _serialize_blocks(blocks) -> list:
    """Convert Anthropic content blocks to plain dicts that we can re-feed
    into the next messages.create call. Strips any non-serializable bits."""
    out = []
    for b in blocks:
        t = getattr(b, "type", None)
        if t == "text":
            out.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name,
                        "input": b.input})
        elif t == "server_tool_use":
            # Anthropic-side tool calls (e.g. web_search). Keep for replay.
            out.append({"type": "server_tool_use", "id": b.id,
                        "name": b.name, "input": b.input})
        elif t == "web_search_tool_result":
            out.append({"type": "web_search_tool_result",
                        "tool_use_id": b.tool_use_id,
                        "content": getattr(b, "content", [])})
        else:
            # Unknown block — try a generic conversion
            try:
                out.append(b.model_dump())
            except Exception:
                pass
    return out


def run(user_text: str = "", image_b64: str = "", image_media: str = ""):
    client = anthropic.Anthropic()
    history = _load_history()

    # Build the new user message
    if image_b64:
        content = [{"type": "image",
                    "source": {"type": "base64",
                               "media_type": image_media or "image/jpeg",
                               "data": image_b64}}]
        if user_text:
            content.append({"type": "text", "text": user_text})
        history.append({"role": "user", "content": content})
    elif user_text:
        history.append({"role": "user", "content": user_text})
    else:
        _log("no user input — bailing")
        return

    _log(f"agent run: history_len={len(history)}  user={(user_text or '<image>')[:120]!r}")

    tools = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
        *_tools.TOOL_DEFS,
    ]

    for iteration in range(MAX_ITERATIONS):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=history,
        )
        # Append assistant turn (serialized) to history
        history.append({"role": "assistant",
                        "content": _serialize_blocks(resp.content)})

        if resp.stop_reason == "end_turn":
            _log(f"end_turn after {iteration+1} iterations")
            # Safety net: if Claude returned text but no reply_text/draft_post
            # was used, surface the text to the operator anyway.
            tail_text = "\n".join(b.text for b in resp.content
                                  if getattr(b, "type", "") == "text").strip()
            if tail_text and not _any_user_facing_tool_used_recently(history):
                try:
                    _tools.reply_text(tail_text)
                except Exception:
                    pass
            break

        if resp.stop_reason == "max_tokens":
            _log("max_tokens hit — bailing")
            try:
                _tools.reply_text("_(ran out of room — try a more specific request)_")
            except Exception: pass
            break

        if resp.stop_reason != "tool_use":
            _log(f"unexpected stop_reason: {resp.stop_reason}")
            break

        # Execute local tool calls; web_search is server-side and was already
        # handled by Anthropic (results came back as web_search_tool_result).
        tool_results = []
        for block in resp.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            name = block.name
            inp  = block.input or {}
            _log(f"  tool_use: {name}  input={json.dumps(inp)[:200]}")
            result_str = _tools.dispatch(name, inp)
            _log(f"    result_len={len(result_str)}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        if not tool_results:
            # Claude said tool_use but no local tools were called (only server
            # tools, e.g. web_search). The next iteration will continue without
            # us injecting anything.
            continue

        history.append({"role": "user", "content": tool_results})
    else:
        _log(f"hit iteration cap ({MAX_ITERATIONS})")
        try:
            _tools.reply_text("_(I'm taking too many steps — let me know if you want to refine the request)_")
        except Exception: pass

    _save_history(history)


def _any_user_facing_tool_used_recently(history: list) -> bool:
    """Check the last assistant turn for reply_text/draft_post tool_use blocks
    so we don't double-send when the model already addressed the operator."""
    for turn in reversed(history):
        if turn.get("role") != "assistant":
            continue
        for b in turn.get("content", []):
            if isinstance(b, dict) and b.get("type") == "tool_use" \
               and b.get("name") in ("reply_text", "draft_post"):
                return True
        break
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--text",  default="", help="user message text")
    p.add_argument("--image-path", default="", help="path to a downloaded image file (for screenshots)")
    p.add_argument("--image-media", default="image/jpeg", help="image MIME type")
    p.add_argument("--reset", action="store_true", help="wipe chat history before running")
    args = p.parse_args()

    if args.reset:
        try: os.remove(HISTORY_PATH)
        except FileNotFoundError: pass
        _log("history reset")

    image_b64 = ""
    media = args.image_media
    if args.image_path:
        with open(args.image_path, "rb") as f:
            image_b64 = base64.standard_b64encode(f.read()).decode("ascii")
        # Best-effort media-type from extension
        ext = args.image_path.rsplit(".", 1)[-1].lower()
        media = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "png": "image/png", "webp": "image/webp",
                 "gif": "image/gif"}.get(ext, args.image_media)

    run(user_text=args.text, image_b64=image_b64, image_media=media)


if __name__ == "__main__":
    main()
