"""web_research — runs a Claude web-search query (using the web_search_20250305
tool) on behalf of the repost bot. Sends the synthesized answer to the repost
chat as plain text (no card, no approve/regen buttons — it's an answer, not a
draft).

Invoked by telegram_bridge when the intent classifier returns mode='research'.

Usage:
    python3 scripts/web_research.py --query "find recent Slack CEO interviews" --context "Slack"
"""
import argparse
import os
import sys
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;       env.load()
import telegram as _tg
import generate as _generate

LOG_DIR = os.path.join(ROOT_DIR, "logs", "web_research")


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


def _send_long(token: str, chat_id: str, text: str):
    """Telegram has a 4096-char message limit. Split on paragraph boundaries
    when over budget so the answer doesn't get truncated."""
    CHUNK = 3800  # leave headroom for markdown
    if len(text) <= CHUNK:
        _tg._call("sendMessage", {
            "chat_id": chat_id, "text": text,
            "parse_mode": "Markdown", "disable_web_page_preview": "true",
        }, bot_token=token)
        return
    buf = ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 > CHUNK:
            _tg._call("sendMessage", {
                "chat_id": chat_id, "text": buf,
                "parse_mode": "Markdown", "disable_web_page_preview": "true",
            }, bot_token=token)
            buf = para
        else:
            buf = (buf + "\n\n" + para) if buf else para
    if buf:
        _tg._call("sendMessage", {
            "chat_id": chat_id, "text": buf,
            "parse_mode": "Markdown", "disable_web_page_preview": "true",
        }, bot_token=token)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query",   required=True, help="the research question")
    p.add_argument("--context", default="",     help="optional topic/anchor")
    p.add_argument("--max-uses", type=int, default=5,
                   help="cap on web_search tool calls per question")
    args = p.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN_REPOST", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID_REPOST", "")
    if not token or not chat:
        _log("TELEGRAM_BOT_TOKEN_REPOST / TELEGRAM_CHAT_ID_REPOST not set — bailing")
        return

    _log(f"web_research: query={args.query!r}  context={args.context!r}")
    try:
        answer = _generate.research_with_web_search(
            args.query, context=args.context, max_uses=args.max_uses)
    except Exception as e:
        _log(f"research failed: {e}")
        try:
            _tg._call("sendMessage", {
                "chat_id": chat,
                "text":    f"⚠️ research failed: `{e}`",
                "parse_mode": "Markdown",
            }, bot_token=token)
        except Exception:
            pass
        return

    _log(f"answer length: {len(answer)} chars")
    try:
        _send_long(token, chat, answer)
    except Exception as e:
        _log(f"TG send failed: {e}")
        # Retry once without Markdown parsing in case formatting broke
        try:
            _tg._call("sendMessage", {
                "chat_id": chat, "text": answer[:3800],
                "disable_web_page_preview": "true",
            }, bot_token=token)
        except Exception as e2:
            _log(f"plain-text retry also failed: {e2}")


if __name__ == "__main__":
    main()
