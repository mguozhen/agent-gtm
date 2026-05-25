"""
Thin Telegram Bot API client used by engage_daemon (send_reply_card) and
telegram_bridge (long-poll for button callbacks).

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment (.env at root).
No external deps — uses urllib + json.
"""
import json
import os
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")
    return t


def _chat_id() -> str:
    c = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not c:
        raise RuntimeError("TELEGRAM_CHAT_ID not set in .env")
    return c


def _call(method: str, params: dict, timeout: int = 30,
          bot_token: str = "") -> dict:
    """Generic Telegram API call. If `bot_token` is provided, use it instead of
    the default env-driven token (lets ai_news_scout route cards to a separate
    bot/channel without changing global state)."""
    token = bot_token or _token()
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def send_reply_card(entry: dict, bot_token: str = "", chat_id: str = "") -> int:
    """Post a reply/quote/original-post card to the configured chat with the
    standard approve/regen/reject buttons. Returns Telegram message_id or 0.

    Branches on entry["kind"] — defaults to "reply" for backwards compat:
      reply    — replying under someone else's post (current behavior)
      quote    — quote-tweeting someone's post with our take
      original — posting an original tweet (no target)

    If the entry has been annotated by reply_scorer (predicted_engagement,
    prediction_reasons), the warning/strong line is prepended above the card
    body so the operator sees the call-out before scrolling."""
    kind = entry.get("kind", "reply")
    reply = entry["reply_text"]
    src = entry.get("source", "target")
    op_summary  = entry.get("op_summary",  "")
    reply_angle = entry.get("reply_angle", "")

    # Optional: scorer prediction line. Empty if no prediction or medium.
    try:
        from reply_scorer import warning_line as _warn_line
        score_line = _warn_line(entry)
    except Exception:
        score_line = ""
    score_prefix = (score_line + "\n\n") if score_line else ""

    # Optional: target follower count. Populated by engage_daemon when it
    # navigates to the target's profile (and lazily by scout_candidates.csv
    # as a fallback). Rendered as e.g. " · 12K followers" on the card.
    try:
        import author_info as _ai
        _followers_n = _ai.followers(entry.get("target", ""))
        _followers_str = (f" · {_ai.format_followers(_followers_n)} followers"
                          if _followers_n is not None else "")
    except Exception:
        _followers_str = ""

    if kind == "original":
        # No OP, no target. Source is buildlog/manual; show context if present.
        source_tag = {
            "buildlog": "🛠 BUILDLOG",
            "manual":   "✍️ MANUAL",
        }.get(src, f"📝 {src.upper()}")
        ctx = entry.get("source_context", "")
        ctx_block = f"_Context:_ {ctx[:300]}\n\n" if ctx else ""
        angle_block = f"💬 _Angle:_ {reply_angle}\n\n" if reply_angle else ""
        text = (
            f"{score_prefix}"
            f"{source_tag} — original post\n\n"
            f"{ctx_block}{angle_block}"
            f"*Draft ({len(reply)} chars):*\n{reply}"
        )
    elif kind == "quote":
        op = (entry.get("target_text") or "")[:600]
        likes_str = f" · {entry['post_likes']} likes" if entry.get("post_likes") is not None else ""
        source_tag = (f"🔁 QT — KW: {entry.get('source_keyword','?')}"
                      if src == "keyword" else "🔁 QT")
        summary_block = (
            f"📝 _OP says:_ {op_summary}\n💬 _Your angle:_ {reply_angle}\n\n"
            if (op_summary or reply_angle) else ""
        )
        text = (
            f"{score_prefix}"
            f"{source_tag}\n"
            f"*Quoting @{entry['target']}*{_followers_str}{likes_str}\n\n"
            f"{summary_block}"
            f"*OP:*\n{op}\n\n"
            f"*Your QT ({len(reply)} chars):*\n{reply}\n\n"
            f"[Open OP]({entry['target_url']})"
        )
    else:  # reply (default)
        op = (entry.get("target_text") or "")[:600]
        source_tag = "🎯 TGT" if src == "target" else f"🔎 KW: {entry.get('source_keyword','?')}"
        likes_str = f" · {entry['post_likes']} likes" if entry.get("post_likes") is not None else ""
        summary_block = (
            f"📝 _OP says:_ {op_summary}\n💬 _Your reply:_ {reply_angle}\n\n"
            if (op_summary or reply_angle) else ""
        )
        text = (
            f"{score_prefix}"
            f"{source_tag}\n"
            f"*@{entry['target']}*{_followers_str} — age {entry.get('post_age_min','?')}m · "
            f"{entry.get('post_replies','?')} replies{likes_str}\n\n"
            f"{summary_block}"
            f"*OP:*\n{op}\n\n"
            f"*Reply ({len(reply)} chars):*\n{reply}\n\n"
            f"[Open OP]({entry['target_url']})"
        )

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"approve:{entry['id']}"},
            {"text": "🔄 Regen",   "callback_data": f"regen:{entry['id']}"},
            {"text": "❌ Reject",  "callback_data": f"reject:{entry['id']}"},
        ]]
    }
    try:
        r = _call("sendMessage", {
            "chat_id": chat_id or _chat_id(),
            "text":    text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
            "reply_markup": json.dumps(keyboard),
        }, bot_token=bot_token)
        if r.get("ok"):
            return r["result"]["message_id"]
    except Exception:
        return 0
    return 0


def send_text(text: str, reply_to_message_id: int = 0) -> int:
    params = {"chat_id": _chat_id(), "text": text, "parse_mode": "Markdown",
              "disable_web_page_preview": "true"}
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
    try:
        r = _call("sendMessage", params)
        return r["result"]["message_id"] if r.get("ok") else 0
    except Exception:
        return 0


def edit_card(message_id: int, text: str, footer: str = "") -> bool:
    """Used by the bridge to update a reply card after action — strips buttons."""
    new_text = text + ("\n\n" + footer if footer else "")
    try:
        r = _call("editMessageText", {
            "chat_id":    _chat_id(),
            "message_id": message_id,
            "text":       new_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
        })
        return r.get("ok", False)
    except Exception:
        return False


def answer_callback(callback_id: str, text: str = ""):
    try:
        _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
    except Exception:
        pass


def get_updates(offset: int = 0, timeout: int = 25) -> list:
    """Long-poll for updates. Returns list of update objects."""
    url = API.format(token=_token(), method="getUpdates")
    params = urllib.parse.urlencode({"offset": offset, "timeout": timeout}).encode("utf-8")
    req = urllib.request.Request(url, data=params, method="POST")
    with urllib.request.urlopen(req, timeout=timeout + 10) as r:
        d = json.loads(r.read())
    return d.get("result", []) if d.get("ok") else []
