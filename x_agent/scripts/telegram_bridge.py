"""
Telegram bridge — listens for inline-button callbacks on reply cards and
applies the action to state/reply_queue.json. Runs as a separate process from
engage_daemon (both share the queue file).

Usage:
    python3 telegram_bridge.py                 # run bridge
    python3 telegram_bridge.py --discover-chat # one-shot: wait for an inbound
                                               # message, print the chat_id, exit
                                               # (use this to fill TELEGRAM_CHAT_ID)

Setup checklist (one-time):
  1. Open Telegram → message @BotFather → /newbot → follow prompts → get token
  2. Add to .env: TELEGRAM_BOT_TOKEN=<token>
  3. Message your bot once (any text — "hi")
  4. Run: python3 telegram_bridge.py --discover-chat → it'll print your chat ID
  5. Add to .env: TELEGRAM_CHAT_ID=<id>
  6. Run normally: python3 telegram_bridge.py
"""
import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;   env.load()

# --- Variant switch: same bridge code can run twice, once for the main bot
# and once for the @X_autorepost_bot (ai_news_scout's repost channel). Set
# BRIDGE_VARIANT=repost in the launchd plist for the second instance. Swapping
# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID here means every downstream _tg call
# (send_reply_card, answer_callback, edit_card, get_updates) automatically
# uses the repost bot without further plumbing.
_VARIANT = os.environ.get("BRIDGE_VARIANT", "main")
if _VARIANT == "repost":
    rt = os.environ.get("TELEGRAM_BOT_TOKEN_REPOST", "")
    rc = os.environ.get("TELEGRAM_CHAT_ID_REPOST", "")
    if not rt or not rc:
        print("BRIDGE_VARIANT=repost requires TELEGRAM_BOT_TOKEN_REPOST + "
              "TELEGRAM_CHAT_ID_REPOST in .env", flush=True)
        sys.exit(1)
    os.environ["TELEGRAM_BOT_TOKEN"] = rt
    os.environ["TELEGRAM_CHAT_ID"]   = rc
elif _VARIANT == "voc_ai":
    vt = os.environ.get("TELEGRAM_BOT_TOKEN_VOC_AI", "")
    vc = os.environ.get("TELEGRAM_CHAT_ID_VOC_AI", "")
    if not vt or not vc:
        print("BRIDGE_VARIANT=voc_ai requires TELEGRAM_BOT_TOKEN_VOC_AI + "
              "TELEGRAM_CHAT_ID_VOC_AI in .env", flush=True)
        sys.exit(1)
    os.environ["TELEGRAM_BOT_TOKEN"] = vt
    os.environ["TELEGRAM_CHAT_ID"]   = vc
elif _VARIANT == "hunter":
    ht = os.environ.get("TELEGRAM_BOT_TOKEN_HUNTER", "")
    hc = os.environ.get("TELEGRAM_CHAT_ID_HUNTER", "")
    if not ht or not hc:
        print("BRIDGE_VARIANT=hunter requires TELEGRAM_BOT_TOKEN_HUNTER + "
              "TELEGRAM_CHAT_ID_HUNTER in .env", flush=True)
        sys.exit(1)
    os.environ["TELEGRAM_BOT_TOKEN"] = ht
    os.environ["TELEGRAM_CHAT_ID"]   = hc

import telegram as _tg
import engage  as _engage
import generate as _generate
import post     as _post
from lock import file_lock

_VARIANT_CONFIGS = {
    "repost": None,  # repost bridge doesn't need an engage config
    "voc_ai": os.path.join(ROOT_DIR, "accounts", "VOC_ai", "engage_config.json"),
    "hunter": os.path.join(ROOT_DIR, "accounts", "GuoHunter95258", "engage_config.json"),
}
CONFIG_PATH = _VARIANT_CONFIGS.get(_VARIANT) or os.path.join(ROOT_DIR, "engage_config.json")
STATE_DIR   = os.path.join(ROOT_DIR, "state")
QUEUE_PATH  = os.path.join(STATE_DIR, "reply_queue.json")
# Per-variant offset file so bridge processes don't fight each other.
_OFFSET_SUFFIX = {"repost": "_repost", "voc_ai": "_voc_ai", "hunter": "_hunter"}.get(_VARIANT, "")
OFFSET_PATH = os.path.join(STATE_DIR, f"telegram_offset{_OFFSET_SUFFIX}.json")
LOG_DIR     = os.path.join(ROOT_DIR, "logs", f"telegram{_OFFSET_SUFFIX}")

_stop = False


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def _save(path, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _find_entry(queue: list, entry_id: str) -> int:
    for i, q in enumerate(queue):
        if q.get("id") == entry_id:
            return i
    return -1


def _find_entry_by_msg_id(queue: list, msg_id: int) -> int:
    for i, q in enumerate(queue):
        if int(q.get("telegram_message_id", 0) or 0) == int(msg_id or 0):
            return i
    return -1


LIBRARY_PATH = os.path.join(STATE_DIR, "winning_replies.json")


def _append_to_library(q: dict):
    """Append an approved reply to winning_replies.json so future few-shot
    samples include Hunter's validated picks — this is how the system 'learns'
    from approvals."""
    lib = _load(LIBRARY_PATH, [])
    lib.append({
        "target_handle":       q["target"],
        "target_post_url":     q["target_url"],
        "target_post_text":    q["target_text"],
        "reply_author":        "GuoHunter95258",
        "reply_text":          q["reply_text"],
        "reply_url":           q.get("reply_url_actual", ""),
        "reply_likes":         0,
        "thread_median_likes": 0,
        "reasons":             ["hunter_approved"],
        "harvested_at":        _ts(),
        "source":              q.get("source", "target"),
    })
    _save(LIBRARY_PATH, lib)
    _log(f"  + appended to winning library (now {len(lib)} entries)")


def _execute(q: dict, cfg: dict) -> dict:
    """Dispatch the approve action by entry kind. Returns the executor result
    dict (always has 'ok' bool)."""
    kind = q.get("kind", "reply")
    port = cfg["hunter_port"]
    if kind == "original":
        return _post.post_tweet(port, q["reply_text"],
                                handle=cfg["hunter_handle"], dry_run=False)
    if kind == "quote":
        return _engage.quote_tweet(port, q["target_url"], q["reply_text"],
                                   dry_run=False)
    # reply (default)
    return _engage.reply_tweet(
        port,
        q["target_url"],
        q["reply_text"],
        dry_run=False,
        self_handle=str(cfg.get("hunter_handle", "GuoHunter95258")),
    )


def _regenerate(q: dict, cfg: dict) -> dict:
    """Dispatch the regen action by entry kind. Returns {reply, op_summary,
    reply_angle}."""
    kind = q.get("kind", "reply")
    if kind == "original":
        return _generate.generate_buildlog_post(
            handle=cfg["hunter_handle"],
            context=q.get("source_context", ""),
            max_chars=cfg["generation"].get("max_post_chars", 280),
        )
    if kind == "quote":
        return _generate.generate_quote_tweet(
            target_handle=q["target"],
            target_post_text=q["target_text"],
            handle=cfg["hunter_handle"],
            max_chars=cfg["generation"].get("max_post_chars", 280),
        )
    # reply (default)
    return _generate.generate_engaged_reply(
        target_handle=q["target"],
        target_post_text=q["target_text"],
        library_path=LIBRARY_PATH,
        archetypes=cfg.get("archetypes", {}),
        hunter_handle=cfg["hunter_handle"],
        examples_per_prompt=cfg["generation"]["examples_per_prompt"],
        max_reply_chars=cfg["generation"]["max_reply_chars"],
    )


def _summary_block(q: dict) -> str:
    """Short block used in the footer-edited card after an action lands. Renders
    differently for original posts (no OP) vs reply/quote (has OP + target)."""
    kind = q.get("kind", "reply")
    if kind == "original":
        return f"*Draft:* {q.get('reply_text','')[:400]}"
    return (f"*@{q.get('target','?')}* — age {q.get('post_age_min','?')}m\n"
            f"*OP:* {(q.get('target_text') or '')[:400]}\n"
            f"*{'QT' if kind == 'quote' else 'Reply'}:* {q.get('reply_text','')}")


def _handle_callback(cq: dict, cfg: dict, state: dict):
    data = cq.get("data", "")  # "approve:1234567890" etc.
    cb_id = cq.get("id", "")
    msg_id = int((((cq.get("message") or {}).get("message_id")) or 0))
    if ":" not in data:
        _tg.answer_callback(cb_id, "bad callback")
        return
    action, entry_id = data.split(":", 1)

    queue = _load(QUEUE_PATH, [])
    idx = _find_entry(queue, entry_id)
    if idx < 0:
        # Fallback: old cards can survive queue-id churn; match by TG message id.
        fb = _find_entry_by_msg_id(queue, msg_id)
        if fb < 0:
            _log(f"callback miss: action={action} entry_id={entry_id} msg_id={msg_id} queue_size={len(queue)}")
            _tg.answer_callback(cb_id, "entry not found")
            return
        idx = fb
        entry_id = str(queue[idx].get("id", entry_id))
        _log(f"callback fallback by msg_id: action={action} msg_id={msg_id} -> entry_id={entry_id}")
    q = queue[idx]

    if q.get("status") != "pending":
        _tg.answer_callback(cb_id, f"already {q.get('status')}")
        return

    kind = q.get("kind", "reply")
    msg_id = q.get("telegram_message_id", 0)
    summary = _summary_block(q)

    if action == "approve":
        _tg.answer_callback(cb_id, "posting...")
        try:
            res = _execute(q, cfg)
        except Exception as e:
            res = {"ok": False, "error": f"exception: {e}"}
        if res.get("ok"):
            q["status"] = "posted"; q["posted_at"] = _ts()
            if res.get("reply_url"):
                q["reply_url_actual"] = res["reply_url"]
            elif res.get("url"):
                q["reply_url_actual"] = res["url"]
            footer = "✅ *posted*"
            # Only feed the winning-replies library from reply kind — quote and
            # original have no OP context that the few-shot generator can use.
            if kind == "reply":
                try:
                    _append_to_library(q)
                    footer = "✅ *posted* + added to winning library"
                except Exception as e:
                    _log(f"  library append failed: {e}")
                    footer = "✅ *posted* (library append failed)"
            _log(f"posted {kind} ({entry_id})")
        else:
            q["status"] = "post_failed"; q["error"] = res.get("error", "")
            footer = f"❌ *post failed*: {q['error']}"
            _log(f"post FAILED for {kind} ({entry_id}): {q['error']}")
        if msg_id:
            _tg.edit_card(msg_id, summary, footer)

    elif action == "reject":
        q["status"] = "rejected"; q["rejected_at"] = _ts()
        state["pending_reason_for"] = entry_id
        _save(OFFSET_PATH, state)
        _tg.answer_callback(cb_id, "rejected — reply with reason")
        if msg_id:
            _tg.edit_card(msg_id, summary,
                          "❌ *rejected* — reply with reason → auto-regen with new constraint\n"
                          "(or `drop` = save reason, no regen · `skip` = no reason, no regen)")
        _log(f"rejected {kind} ({entry_id}); awaiting reason")

    elif action == "regen":
        _tg.answer_callback(cb_id, "regenerating...")
        try:
            g = _regenerate(q, cfg)
            q["reply_text"]  = g.get("reply", "")
            q["op_summary"]  = g.get("op_summary", "")
            q["reply_angle"] = g.get("reply_angle", "")
            new_msg_id = _tg.send_reply_card(q)
            if new_msg_id:
                q["telegram_message_id"] = new_msg_id
            if msg_id:
                _tg.edit_card(msg_id, summary, "🔄 *regenerated — see new card*")
            _log(f"regenerated {kind} ({entry_id})")
        except Exception as e:
            _tg.answer_callback(cb_id, f"regen failed: {e}")
            _log(f"regen failed for {entry_id}: {e}")
    else:
        _tg.answer_callback(cb_id, "unknown action")
        return

    # Reload-merge-save under lock so we don't clobber writes from other
    # producers (engage_daemon, buildlog_drafts, quote_scout) that ran while
    # the executor was busy.
    with file_lock("reply_queue", on_wait=_log):
        cur = _load(QUEUE_PATH, [])
        cur_idx = _find_entry(cur, entry_id)
        if cur_idx < 0 and msg_id:
            cur_idx = _find_entry_by_msg_id(cur, msg_id)
        if cur_idx >= 0:
            cur[cur_idx] = q
            _save(QUEUE_PATH, cur)
        else:
            _log(f"  warning: entry {entry_id} vanished from queue before save")


import re as _re
TWEET_URL_RE = _re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]+/status/\d+",
    _re.IGNORECASE,
)


def _spawn_topic_research(topic: str = "", url: str = "", instruction: str = ""):
    """Fire-and-forget topic_research subprocess so the bridge keeps polling."""
    import subprocess
    script = os.path.join(SCRIPTS_DIR, "topic_research.py")
    log_path = os.path.join(LOG_DIR, f"intake_{datetime.now():%Y-%m-%d}.log")
    if url:
        args = [script, "--url", url]
        if instruction:
            args += ["--instruction", instruction]
    else:
        args = [script, "--topic", topic]
        if instruction:
            args += ["--instruction", instruction]
    with open(log_path, "a") as logf:
        subprocess.Popen(["/usr/bin/python3", "-u"] + args,
                         cwd=ROOT_DIR, stdout=logf, stderr=subprocess.STDOUT,
                         close_fds=True)


def _spawn_repost_agent_text(msg: dict, text: str):
    """Fire-and-forget repost_agent subprocess for a text message."""
    import subprocess
    script = os.path.join(SCRIPTS_DIR, "repost_agent.py")
    log_path = os.path.join(LOG_DIR, f"agent_{datetime.now():%Y-%m-%d}.log")
    _log(f"spawn repost_agent (text): {text[:120]!r}")
    with open(log_path, "a") as logf:
        subprocess.Popen(["/usr/bin/python3", "-u", script, "--text", text],
                         cwd=ROOT_DIR, stdout=logf, stderr=subprocess.STDOUT,
                         close_fds=True)


def _spawn_repost_agent_photo(msg: dict, photo_variants: list, caption: str = ""):
    """Download the largest photo variant, save to a temp file, spawn agent.
    The agent ingests the image via --image-path (base64'd internally) so
    Claude sees the screenshot natively via vision."""
    import subprocess, tempfile, urllib.request, urllib.parse
    file_id = photo_variants[-1].get("file_id", "")
    if not file_id:
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getFile?file_id={urllib.parse.quote(file_id)}",
            timeout=15) as r:
            d = json.loads(r.read())
        fp = d["result"]["file_path"]
        with urllib.request.urlopen(
            f"https://api.telegram.org/file/bot{token}/{fp}", timeout=30) as r:
            img = r.read()
        suf = ".jpg" if fp.lower().endswith((".jpg", ".jpeg")) else ".png"
        with tempfile.NamedTemporaryFile(suffix=suf, delete=False) as tmp:
            tmp.write(img); tmp_path = tmp.name
    except Exception as e:
        _log(f"photo intake: download failed: {e}")
        try:
            _tg.send_text("⚠️ couldn't download that screenshot, try again?",
                          reply_to_message_id=msg.get("message_id", 0))
        except Exception: pass
        return

    script = os.path.join(SCRIPTS_DIR, "repost_agent.py")
    log_path = os.path.join(LOG_DIR, f"agent_{datetime.now():%Y-%m-%d}.log")
    _log(f"spawn repost_agent (photo): {tmp_path}  caption={caption[:80]!r}")
    args = [script, "--image-path", tmp_path]
    if caption:
        args += ["--text", caption]
    with open(log_path, "a") as logf:
        import subprocess as _sp
        _sp.Popen(["/usr/bin/python3", "-u"] + args,
                  cwd=ROOT_DIR, stdout=logf, stderr=_sp.STDOUT,
                  close_fds=True)


def _spawn_web_research(query: str, context: str = ""):
    """Fire-and-forget web_research subprocess. Sends the answer back to the
    repost chat as a plain TG message (no card)."""
    import subprocess
    script = os.path.join(SCRIPTS_DIR, "web_research.py")
    log_path = os.path.join(LOG_DIR, f"intake_{datetime.now():%Y-%m-%d}.log")
    args = [script, "--query", query]
    if context:
        args += ["--context", context]
    with open(log_path, "a") as logf:
        subprocess.Popen(["/usr/bin/python3", "-u"] + args,
                         cwd=ROOT_DIR, stdout=logf, stderr=subprocess.STDOUT,
                         close_fds=True)


def _handle_keyword_intake(msg: dict, text: str, state: dict):
    """User DMed text. Behavior:
    - If the message contains a URL AND extra text → dispatch URL+instruction
    - If the message is JUST a URL → save as pending intent, ASK what to do
    - If the message is plain text and the bridge is awaiting an instruction
      (handled in _handle_message via _pending_intent_for_repost) → won't reach here
    - Otherwise (plain keyword/phrase) → save as pending intent, ASK what to do

    'Always ask if not specified' — no more hardcoded mode rotation, no
    assumed defaults. The instruction shapes everything: count, tone, action.
    """
    stripped = text.strip()
    url_match = TWEET_URL_RE.search(stripped)

    if url_match:
        url = url_match.group(0)
        # Everything else in the message is the instruction (before AND after the URL)
        before = stripped[:url_match.start()].strip()
        after  = stripped[url_match.end():].strip()
        import re as __re
        after = __re.sub(r'^\?s=\d+', '', after).strip()
        instruction = " ".join(s for s in (before, after) if s).strip()
        if instruction:
            _log(f"intake (URL+instruction): {url}  ::  {instruction!r}")
            try:
                _tg.send_text(
                    f"🔗 working on that tweet:\n_{instruction[:200]}_\n\n_cards coming…_",
                    reply_to_message_id=msg.get("message_id", 0))
            except Exception:
                pass
            _spawn_topic_research(url=url, instruction=instruction)
            return
        # URL only — ask what they want
        _stash_pending_intent(msg, state, kind="url", content=url)
        return

    # Plain text, no URL — could be a keyword
    keyword = stripped[:120]
    _stash_pending_intent(msg, state, kind="keyword", content=keyword)


def _stash_pending_intent(msg: dict, state: dict, kind: str, content: str, hint: str = ""):
    """Save the input to the IN-MEMORY state, ask the operator what to do
    with it. Mutating state (not just disk) is critical: the bridge loop's
    end-of-iteration _save(OFFSET_PATH, state) would otherwise overwrite any
    disk-only changes — which is why the bot kept asking 'what do you want?'
    repeatedly on every new message."""
    state["pending_intent_for_repost"] = {
        "kind":    kind,           # 'url' | 'keyword' | 'screenshot'
        "content": content,
        "hint":    hint,           # e.g. screenshot vision excerpt
        "ts":      _ts(),
        "reply_to_message_id": msg.get("message_id", 0),
    }
    _save(OFFSET_PATH, state)
    label = {"url": "tweet", "keyword": "topic", "screenshot": "screenshot"}.get(kind, kind)
    hint_block = f"\n_{hint[:160]}_" if hint else ""
    body = (
        f"Got that {label}: *{content[:120]}*{hint_block}\n\n"
        f"What do you want me to do? Examples:\n"
        f"  • _give me 5 sarcastic takes_\n"
        f"  • _3 builder reframes with a number_\n"
        f"  • _disagree with a specific counter-example_\n"
        f"  • _find related viral posts and react_\n"
        f"  • _just summarize it in my voice_"
    )
    try:
        _tg.send_text(body, reply_to_message_id=msg.get("message_id", 0))
    except Exception:
        pass
    _log(f"intake ({kind}) → pending intent saved, awaiting instruction. content={content[:80]!r}")


def _handle_screenshot_intake(msg: dict, photo_variants: list, state: dict):
    """User sent a screenshot. Download largest variant, ask Claude vision to
    extract the topic, ack, then spawn topic_research."""
    import urllib.request, urllib.parse

    # Pick the largest photo variant (last in the list — Telegram orders ascending)
    file_id = photo_variants[-1].get("file_id", "")
    if not file_id:
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    api   = f"https://api.telegram.org/bot{token}"

    try:
        # 1. getFile → file_path
        with urllib.request.urlopen(
            f"{api}/getFile?file_id={urllib.parse.quote(file_id)}", timeout=15) as r:
            d = json.loads(r.read())
        if not d.get("ok"):
            _log(f"intake screenshot: getFile failed: {d}")
            return
        fp = d["result"]["file_path"]
        # 2. Download bytes
        with urllib.request.urlopen(
            f"https://api.telegram.org/file/bot{token}/{fp}", timeout=30) as r:
            img_bytes = r.read()
        media = "image/jpeg" if fp.lower().endswith(".jpg") or fp.lower().endswith(".jpeg") else "image/png"
    except Exception as e:
        _log(f"intake screenshot: download failed: {e}")
        try:
            _tg.send_text("⚠️ couldn't download that screenshot, try again?",
                          reply_to_message_id=msg.get("message_id", 0))
        except Exception:
            pass
        return

    # 3. Ack early so the user sees something while vision runs
    try:
        _tg.send_text("📸 reading screenshot…",
                      reply_to_message_id=msg.get("message_id", 0))
    except Exception:
        pass

    # 4. Claude vision → topic
    try:
        info = _generate.extract_topic_from_screenshot(img_bytes, media_type=media)
    except Exception as e:
        _log(f"intake screenshot: vision failed: {e}")
        try:
            _tg.send_text(f"⚠️ vision failed: {e}")
        except Exception:
            pass
        return

    topic = (info or {}).get("topic", "").strip()
    handle = (info or {}).get("author_handle", "")
    conf   = (info or {}).get("confidence", "low")
    _log(f"intake (screenshot): topic={topic!r} handle=@{handle} conf={conf}")

    if not topic:
        try:
            _tg.send_text("⚠️ couldn't identify a topic in that screenshot. "
                          "Try a clearer crop of the post text?")
        except Exception:
            pass
        return

    hint = (f"from @{handle} (vision confidence: {conf})"
            if handle else f"vision confidence: {conf}")
    _stash_pending_intent(msg, state, kind="screenshot", content=topic, hint=hint)


def _consume_pending_intent(msg: dict, instruction: str, intent: dict, state: dict):
    """Operator replied to the 'what should I do?' prompt. Dispatch the saved
    input + the freshly-typed instruction to topic_research."""
    kind = intent.get("kind", "")
    content = intent.get("content", "")
    if not content:
        state["pending_intent_for_repost"] = None
        return
    instruction = instruction.strip()
    _log(f"consume pending intent: kind={kind}  content={content!r}  "
         f"instruction={instruction[:120]!r}")
    state["pending_intent_for_repost"] = None

    # Classify: is this a research question (return synthesized answer) or a
    # draft request (run the X scrape → TG card pipeline)?
    try:
        plan = _generate.classify_intent(content, instruction)
    except Exception as e:
        _log(f"  classify failed ({e}); defaulting to draft")
        plan = {"mode": "draft", "reasoning": f"classifier_error:{e}"}
    mode = plan.get("mode", "draft")
    why  = plan.get("reasoning", "")
    _log(f"  classified: mode={mode}  why={why!r}")

    try:
        _tg.send_text(
            f"✓ {('researching' if mode == 'research' else 'drafting')}: "
            f"_{instruction[:160]}_\n_(working on {kind}: {content[:80]})_",
            reply_to_message_id=msg.get("message_id", 0))
    except Exception:
        pass

    if mode == "research":
        # The instruction itself is the query; content is anchor context.
        # For URL kind, the URL is part of the context the researcher needs.
        ctx = content if kind != "url" else f"the X tweet at {content}"
        _spawn_web_research(query=instruction, context=ctx)
        return

    # mode == "draft"
    if kind == "url":
        _spawn_topic_research(url=content, instruction=instruction)
    else:
        _spawn_topic_research(topic=content, instruction=instruction)


def _handle_message(msg: dict, cfg: dict, state: dict):
    """Capture rejection reasons. Behavior:
    - Plain text → save reason + auto-regen + send new card
    - 'drop' / 'stop' → save reason, no regen
    - 'skip' / 'none' / '-' / 'no' → no reason saved, no regen

    REPOST VARIANT EXTRA: also accept topic-intake messages.
    - photo  → Claude vision extracts topic → spawn topic_research subprocess
    - text   → treat as keyword → spawn topic_research subprocess
    Only kicks in when no pending rejection-reason is awaiting input.
    """
    chat = msg.get("chat", {}) or {}
    expected_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if expected_chat and str(chat.get("id")) != str(expected_chat):
        return

    pending = state.get("pending_reason_for")
    text    = (msg.get("text") or "").strip()
    photo   = msg.get("photo") or []  # list of size variants, last = largest

    # Repost-variant: hand EVERY message to the agent loop. No hardcoded
    # routing — Claude inside repost_agent.py decides what to do (ask, search
    # the web, scrape X, draft a post, follow up on prior context, etc.) based
    # on conversation history persisted in state/repost_chat_history.json.
    if _VARIANT == "repost" and not pending:
        if photo:
            _spawn_repost_agent_photo(msg, photo, text)
            return
        if text:
            _spawn_repost_agent_text(msg, text)
            return
        return

    if not text:
        return
    if not pending:
        return

    queue = _load(QUEUE_PATH, [])
    idx = _find_entry(queue, pending)
    if idx < 0:
        state["pending_reason_for"] = None
        _save(OFFSET_PATH, state)
        return

    q = queue[idx]
    lower = text.lower()
    no_reason  = lower in {"skip", "none", "-", "no", "no reason"}
    drop_only  = lower in {"drop", "stop", "stop regen", "no regen"}

    q["rejection_reason"] = "" if no_reason else text[:300]
    q["reason_logged_at"] = _ts()
    with file_lock("reply_queue", on_wait=_log):
        cur = _load(QUEUE_PATH, [])
        cur_idx = _find_entry(cur, pending)
        if cur_idx >= 0:
            cur[cur_idx] = q
            _save(QUEUE_PATH, cur)
    state["pending_reason_for"] = None
    _save(OFFSET_PATH, state)

    # Always acknowledge the reason capture
    if no_reason:
        ack = "✓ no reason logged, no regen"
    elif drop_only:
        ack = "✓ reason saved, no regen"
    else:
        ack = f"✓ reason saved: _{text[:80]}_\n\nregenerating with this constraint..."
    try:
        _tg.send_text(ack, reply_to_message_id=msg.get("message_id", 0))
    except Exception:
        pass
    _log(f"reason for {pending}: {'<skip>' if no_reason else ('<drop>' if drop_only else text[:80])}")

    # If user asked for no regen, stop here
    if no_reason or drop_only:
        return

    # Auto-regen: generate a NEW draft of the same kind — the just-saved
    # rejection lives in the negative-few-shot block (reply kind) or as extra
    # context (quote/original) so the new draft avoids what Hunter flagged.
    import time as _time
    try:
        g = _regenerate(q, cfg)
    except Exception as e:
        _log(f"  auto-regen generate failed: {e}")
        try: _tg.send_text(f"⚠️ regen failed: {e}")
        except Exception: pass
        return

    new_text = g.get("reply", "")
    if not new_text or len(new_text) < 10:
        try: _tg.send_text("⚠️ regen produced empty reply, skipping")
        except Exception: pass
        return

    new_entry = {**q}
    new_entry.update({
        "id":                     f"{q['id']}_r{int(_time.time())}",
        "reply_text":             new_text,
        "op_summary":             g.get("op_summary", ""),
        "reply_angle":            g.get("reply_angle", ""),
        "status":                 "pending",
        "queued_at":              _ts(),
        "previous_entry_id":      q["id"],
        "previous_reject_reason": q.get("rejection_reason", ""),
    })
    # Drop status fields from the prior entry
    for k in ("rejected_at", "reason_logged_at", "rejection_reason",
              "telegram_message_id", "posted_at", "reply_url_actual", "error"):
        new_entry.pop(k, None)

    with file_lock("reply_queue", on_wait=_log):
        cur = _load(QUEUE_PATH, [])
        cur.append(new_entry)
        _save(QUEUE_PATH, cur)

    try:
        new_msg_id = _tg.send_reply_card(new_entry)
        if new_msg_id:
            new_entry["telegram_message_id"] = new_msg_id
            with file_lock("reply_queue", on_wait=_log):
                cur = _load(QUEUE_PATH, [])
                for i, e in enumerate(cur):
                    if e.get("id") == new_entry["id"]:
                        cur[i] = new_entry
                        _save(QUEUE_PATH, cur)
                        break
    except Exception as e:
        _log(f"  send new card failed: {e}")
    _log(f"  auto-regen: new entry {new_entry['id']}")


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover-chat", action="store_true",
                        help="wait for an inbound message and print its chat_id")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT,  _handle_sigterm)

    if args.discover_chat:
        # Set TELEGRAM_CHAT_ID="0" temporarily so _chat_id() won't be needed
        os.environ.setdefault("TELEGRAM_CHAT_ID", "0")
        print("waiting for an inbound message to your bot... (send any text)")
        offset = 0
        while not _stop:
            updates = _tg.get_updates(offset=offset, timeout=25)
            for u in updates:
                offset = u["update_id"] + 1
                chat = (u.get("message") or {}).get("chat") or (u.get("callback_query") or {}).get("message", {}).get("chat") or {}
                if chat.get("id"):
                    print(f"\nchat_id: {chat['id']}")
                    print(f"chat type: {chat.get('type', '?')}")
                    print(f"\nAdd this to .env:\n  TELEGRAM_CHAT_ID={chat['id']}")
                    return
        return

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    _log("telegram bridge starting")
    state = _load(OFFSET_PATH, {"offset": 0})

    while not _stop:
        try:
            updates = _tg.get_updates(offset=state["offset"], timeout=25)
        except Exception as e:
            _log(f"getUpdates error: {e}")
            time.sleep(5)
            continue

        for u in updates:
            state["offset"] = u["update_id"] + 1
            cq = u.get("callback_query")
            if cq:
                try:
                    _handle_callback(cq, cfg, state)
                except Exception as e:
                    _log(f"callback error: {e}")
            msg = u.get("message")
            if msg:
                try:
                    _handle_message(msg, cfg, state)
                except Exception as e:
                    _log(f"message error: {e}")
        _save(OFFSET_PATH, state)

    _log("telegram bridge stopped")


if __name__ == "__main__":
    main()
