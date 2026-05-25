"""Thin Telegram client for the coach bot (separate token + chat from the
approval bridge).

Uses COACH_BOT_TOKEN and COACH_CHAT_ID from .env. Kept distinct from the
existing telegram.py wrapper so messages don't mix into the approval thread.
"""
import json
import os
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    t = os.environ.get("COACH_BOT_TOKEN", "")
    if not t:
        raise RuntimeError("COACH_BOT_TOKEN not set in .env")
    return t


def _chat_id() -> str:
    c = os.environ.get("COACH_CHAT_ID", "")
    if not c:
        raise RuntimeError("COACH_CHAT_ID not set in .env")
    return c


def _call(method: str, params: dict, timeout: int = 30) -> dict:
    url = API.format(token=_token(), method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def send_text(text: str, reply_markup: dict = None) -> int:
    params = {
        "chat_id": _chat_id(),
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    try:
        r = _call("sendMessage", params)
        return r["result"]["message_id"] if r.get("ok") else 0
    except Exception:
        return 0


def edit_text(message_id: int, text: str) -> bool:
    try:
        r = _call("editMessageText", {
            "chat_id": _chat_id(),
            "message_id": message_id,
            "text": text,
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
    """Long-poll. Returns list of update objects."""
    url = API.format(token=_token(), method="getUpdates")
    params = urllib.parse.urlencode({"offset": offset, "timeout": timeout}).encode("utf-8")
    req = urllib.request.Request(url, data=params, method="POST")
    with urllib.request.urlopen(req, timeout=timeout + 10) as r:
        d = json.loads(r.read())
    return d.get("result", []) if d.get("ok") else []
