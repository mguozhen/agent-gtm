"""Tool implementations for the repost agent loop.

Each function corresponds to an Anthropic tool. Inputs come from Claude as a
dict (matching the input_schema declared in TOOL_DEFS). Outputs are returned
as strings (Anthropic expects tool_result.content to be a string for the
common case — multi-block content also supported but unnecessary here).
"""
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;     env.load()
import chrome  as _chrome
import telegram as _tg
from lock import chrome_lock, file_lock

STATE_DIR  = os.path.join(ROOT_DIR, "state")
QUEUE_PATH = os.path.join(STATE_DIR, "reply_queue.json")
HUNTER_PORT = 10000

# Tool declarations for Claude — passed as the `tools` argument to messages.create.
# web_search is included separately by the agent (server tool, different shape).
TOOL_DEFS = [
    {
        "name": "x_search_keyword",
        "description": (
            "Search X (Twitter) for posts matching a keyword/phrase via Hunter's "
            "logged-in Chrome. Returns the top posts by engagement from the Top "
            "tab — these are typically from the last 1-3 days. Use this when "
            "the operator wants viral posts about a topic, recent reactions to "
            "a product/event, or to find a specific post they remember seeing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string",
                            "description": "Search keyword or phrase. Avoid quotes unless an exact-match phrase."},
                "limit":   {"type": "integer",
                            "description": "Max posts to return (1-15, default 8). Higher = more variety, more tokens.",
                            "minimum": 1, "maximum": 15},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "x_fetch_url",
        "description": (
            "Fetch a specific X tweet by URL. Returns its author, text, likes, "
            "replies, age. Use when the operator references a specific tweet "
            "(URL pasted in chat) so you can read it before reacting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Tweet URL (x.com/<handle>/status/<id>)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "draft_post",
        "description": (
            "Send a TEXT post draft to the operator's Telegram for approval. "
            "The draft appears as a card with approve/regen/reject buttons. "
            "Use this when the operator wants something they could actually "
            "publish on X. For quote-tweets, set target_url to the source. "
            "For original posts (no quote), leave target_url empty. The post "
            "text must already be in the operator's voice and respect X's 280 "
            "char limit. Do NOT include hashtags or URLs unless asked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text":       {"type": "string", "description": "The post body, ≤280 chars."},
                "target_url": {"type": "string", "description": "Source tweet URL if this is a quote-tweet, else ''.", "default": ""},
                "angle":      {"type": "string", "description": "1-line note shown on the card explaining your angle.", "default": ""},
            },
            "required": ["text"],
        },
    },
    {
        "name": "reply_text",
        "description": (
            "Send a plain-text message to the operator's Telegram chat. Use "
            "this for research summaries, clarifying questions, status updates, "
            "or any output that ISN'T a draft post. Supports Markdown. Up to "
            "~3500 chars per message; longer text will be split by the runtime."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Message body. Markdown allowed."},
            },
            "required": ["text"],
        },
    },
]


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_count(s: str) -> int:
    if not s: return 0
    s = s.strip().upper().replace(",", "")
    import re
    m = re.match(r"^([0-9.]+)\s*([KMB])?$", s)
    if not m: return 0
    n = float(m.group(1))
    return int(n * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2) or ""])


def _age_min_from_iso(iso: str) -> int:
    if not iso: return -1
    try:
        from datetime import timezone
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
        return int((now - dt).total_seconds() / 60)
    except Exception:
        return -1


# ── tool: x_search_keyword ───────────────────────────────────────────────────

def x_search_keyword(keyword: str, limit: int = 8) -> str:
    """Search X Top tab via Hunter's Chrome. Returns JSON string of top posts."""
    import ai_news_scout as _ans  # reuse PROFILE_TWEETS_JS
    q = urllib.parse.quote_plus(keyword)
    url = f"https://x.com/search?q={q}&src=typed_query&f=top"
    seen = {}
    ws = _chrome.connect(HUNTER_PORT)
    try:
        with chrome_lock(HUNTER_PORT):
            _chrome.navigate(ws, url, wait=4.0)
            time.sleep(1.5)
            for _ in range(3):
                raw = _chrome.eval_js(ws, _ans.PROFILE_TWEETS_JS)
                try:
                    batch = json.loads(raw) if raw else []
                except Exception:
                    batch = []
                for c in batch:
                    if c.get("id") and c["id"] not in seen:
                        seen[c["id"]] = c
                _chrome.eval_js(ws, "window.scrollBy(0, 1500)")
                time.sleep(1.5)
    finally:
        try: ws.close()
        except Exception: pass

    posts = []
    for c in seen.values():
        posts.append({
            "author":   c.get("author", ""),
            "url":      c.get("url", ""),
            "text":     (c.get("text") or "")[:500],
            "likes":    _parse_count(c.get("likesTxt", "")),
            "replies":  _parse_count(c.get("repliesTxt", "")),
            "age_min":  _age_min_from_iso(c.get("iso", "")),
        })
    posts.sort(key=lambda x: x["likes"], reverse=True)
    posts = posts[:max(1, min(15, limit))]
    return json.dumps({"keyword": keyword, "count": len(posts), "posts": posts},
                      ensure_ascii=False)


# ── tool: x_fetch_url ────────────────────────────────────────────────────────

def x_fetch_url(url: str) -> str:
    import topic_research as _tr
    ws = _chrome.connect(HUNTER_PORT)
    try:
        seed = _tr.fetch_seed_tweet(ws, url)
    finally:
        try: ws.close()
        except Exception: pass
    if not seed:
        return json.dumps({"error": "couldn't open that tweet — link might be private, deleted, or rate-limited", "url": url})
    return json.dumps({
        "author":   seed.get("author", ""),
        "url":      seed.get("url", ""),
        "text":     (seed.get("text") or "")[:1200],
        "likes":    seed.get("likes", 0),
        "replies":  seed.get("replies", 0),
        "age_min":  seed.get("age_minutes", -1),
    }, ensure_ascii=False)


# ── tool: draft_post ─────────────────────────────────────────────────────────

def draft_post(text: str, target_url: str = "", angle: str = "") -> str:
    """Create a TG approve-card entry. Returns confirmation string."""
    text = (text or "").strip()
    if len(text) < 5:
        return json.dumps({"ok": False, "error": "draft text too short"})
    if len(text) > 280:
        return json.dumps({"ok": False, "error": f"draft is {len(text)} chars, X limit is 280"})

    token = os.environ.get("TELEGRAM_BOT_TOKEN_REPOST", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID_REPOST", "")
    if not token or not chat:
        return json.dumps({"ok": False, "error": "repost env not configured"})

    entry = {
        "id":          f"agent_{int(time.time())}_{abs(hash(text)) % 100000:05d}",
        "kind":        "quote" if target_url else "original",
        "reply_text":  text,
        "op_summary":  "",
        "reply_angle": angle[:200],
        "status":      "pending",
        "queued_at":   _ts(),
        "telegram_message_id": 0,
        "source":      "agent",
        "agent":       True,
    }
    if target_url:
        entry.update({
            "source_keyword": "agent",
            "target":         _extract_handle(target_url),
            "target_url":     target_url,
            "target_text":    "",   # agent already knows context; no need to re-render here
            "post_likes":     0,
            "post_replies":   0,
            "post_age_min":   0,
        })
    else:
        entry["source_context"] = (angle or "agent-generated original post")[:300]

    # Append to queue under lock
    with file_lock("reply_queue", on_wait=lambda s: None):
        with open(QUEUE_PATH) as f:
            cur = json.load(f)
        cur.append(entry)
        with open(QUEUE_PATH, "w") as f:
            json.dump(cur, f, indent=2, ensure_ascii=False)

    # Push the card to TG
    try:
        msg_id = _tg.send_reply_card(entry, bot_token=token, chat_id=chat)
        if msg_id:
            entry["telegram_message_id"] = msg_id
            with file_lock("reply_queue", on_wait=lambda s: None):
                with open(QUEUE_PATH) as f:
                    cur = json.load(f)
                for i, e in enumerate(cur):
                    if e.get("id") == entry["id"]:
                        cur[i] = entry; break
                with open(QUEUE_PATH, "w") as f:
                    json.dump(cur, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"TG send failed: {e}"})

    return json.dumps({"ok": True, "card_id": entry["id"], "tg_message_id": msg_id,
                       "note": "draft sent to operator; they'll see approve/regen/reject buttons"})


def _extract_handle(url: str) -> str:
    import re
    m = re.search(r"x\.com/([A-Za-z0-9_]+)/status/", url or "")
    return m.group(1) if m else ""


# ── tool: reply_text ─────────────────────────────────────────────────────────

def reply_text(text: str) -> str:
    """Send a plain TG message to the operator. Returns confirmation."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN_REPOST", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID_REPOST", "")
    if not token or not chat:
        return json.dumps({"ok": False, "error": "repost env not configured"})
    text = (text or "").strip()
    if not text:
        return json.dumps({"ok": False, "error": "empty text"})

    # Split if too long for one TG message
    CHUNK = 3800
    chunks = []
    if len(text) <= CHUNK:
        chunks = [text]
    else:
        buf = ""
        for para in text.split("\n\n"):
            if len(buf) + len(para) + 2 > CHUNK:
                chunks.append(buf); buf = para
            else:
                buf = (buf + "\n\n" + para) if buf else para
        if buf: chunks.append(buf)

    for c in chunks:
        try:
            _tg._call("sendMessage", {
                "chat_id": chat, "text": c, "parse_mode": "Markdown",
                "disable_web_page_preview": "true",
            }, bot_token=token)
        except Exception as e:
            # Markdown might have broken on a stray char — retry plaintext
            try:
                _tg._call("sendMessage", {
                    "chat_id": chat, "text": c,
                    "disable_web_page_preview": "true",
                }, bot_token=token)
            except Exception as e2:
                return json.dumps({"ok": False, "error": f"TG send failed: {e2}"})
    return json.dumps({"ok": True, "chunks": len(chunks)})


# ── dispatcher ───────────────────────────────────────────────────────────────

def dispatch(name: str, inp: dict) -> str:
    """Route a tool call from Claude to its implementation."""
    try:
        if name == "x_search_keyword":
            return x_search_keyword(inp.get("keyword", ""), int(inp.get("limit", 8)))
        if name == "x_fetch_url":
            return x_fetch_url(inp.get("url", ""))
        if name == "draft_post":
            return draft_post(inp.get("text", ""),
                              inp.get("target_url", ""),
                              inp.get("angle", ""))
        if name == "reply_text":
            return reply_text(inp.get("text", ""))
        return json.dumps({"error": f"unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": f"tool {name} crashed: {e}"})
