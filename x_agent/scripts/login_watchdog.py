"""Poll each X-account Chrome instance on a cron, send a Telegram notification
when the account's session flips state (logged-in → logged-out, or back).

Each account is wired to its own Telegram bot so notifications land in
separate chats. Configured via env vars:

    TELEGRAM_BOT_TOKEN_HUNTER   chat token for hunter (@GuoHunter95258, port 10000)
    TELEGRAM_CHAT_ID_HUNTER

    TELEGRAM_BOT_TOKEN_MGUO     chat token for mguo (@hunter_solvea, port 10004)
    TELEGRAM_CHAT_ID_MGUO

    TELEGRAM_BOT_TOKEN_SHULEX   chat token for shulex (@VOC_ai, port 10005)
    TELEGRAM_CHAT_ID_SHULEX

State persisted to state/login_watchdog.json so repeated runs don't re-notify
for the same event.

Usage:
    python3 login_watchdog.py                # one-shot poll all accounts
    python3 login_watchdog.py --discover hunter   # print chat_id for that bot
    python3 login_watchdog.py --test hunter        # send a test ping
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;  env.load()

STATE_DIR  = os.path.join(ROOT_DIR, "state")
STATE_PATH = os.path.join(STATE_DIR, "login_watchdog.json")
LOG_DIR    = os.path.join(ROOT_DIR, "logs", "login_watchdog")

# (internal_name, X handle, chrome debug port)
ACCOUNTS = [
    ("hunter", "@GuoHunter95258", 10000),
    ("mguo",   "@hunter_solvea",  10004),
    ("shulex", "@VOC_ai",         10002),
]

AUTO_LOGIN = {
    "shulex": os.path.join(SCRIPTS_DIR, "auto_login_voc_ai.py"),
}
AUTO_LOGIN_COOLDOWN_SEC = 20 * 60

API = "https://api.telegram.org/bot{token}/{method}"


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


def _env_for(account: str) -> tuple[str, str]:
    """(token, chat_id) for the given account. Empty strings if not configured."""
    a = account.upper()
    return (os.environ.get(f"TELEGRAM_BOT_TOKEN_{a}", ""),
            os.environ.get(f"TELEGRAM_CHAT_ID_{a}", ""))


def _tg_call(token: str, method: str, params: dict, timeout: int = 15) -> dict:
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def send_notification(account: str, text: str) -> bool:
    token, chat_id = _env_for(account)
    if not token or not chat_id:
        _log(f"  {account}: token/chat_id not set — skipping notification")
        return False
    try:
        r = _tg_call(token, "sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
        })
        return bool(r.get("ok"))
    except Exception as e:
        _log(f"  {account}: telegram error: {e}")
        return False


def _verify_authenticated(port: int):
    """Open a brief CDP WebSocket (under chrome_lock to avoid racing daemons)
    and look for `AppTabBar_Profile_Link` — the X sidebar element that only
    renders for authenticated users. Returns True, False, or None on failure.
    Same selector boost_hunter.py:95 uses for the inverse purpose."""
    try:
        import chrome as _chrome
        from lock import chrome_lock
    except Exception as e:
        _log(f"  port {port}: import failed in verify: {e}")
        return None
    try:
        with chrome_lock(port, timeout=15.0):
            ws = _chrome.connect(port, timeout=8)
            try:
                href = _chrome.eval_js(ws, """(function(){
                    var el = document.querySelector('a[data-testid="AppTabBar_Profile_Link"]');
                    return el ? (el.getAttribute('href')||'') : '';
                })()""")
                return bool(href and href != "/")
            finally:
                ws.close()
    except Exception as e:
        _log(f"  port {port}: verify failed: {e}")
        return None


def check_login_state(port: int) -> str:
    """Inspect Chrome at `port`. Tab-URL heuristic first (cheap, no CDP
    attach), then for URL-suggested logged_in tabs do a DOM-level verify
    against AppTabBar_Profile_Link — without this, the signup wall at
    `https://x.com/` (no auth keyword) was being misread as logged_in
    (incident 2026-05-19 10:07: false 'back online' notification).

    Returns:
      logged_in   — definite (DOM has profile-link sidebar)
      logged_out  — explicit /i/flow/login tab open
      unreachable — Chrome port is not responding
      unknown     — reachable but state can't be confirmed (Chrome is on a
                    non-auth URL but no profile link in DOM, e.g. signup wall)
    """
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/json", timeout=5) as r:
            tabs = json.loads(r.read())
    except Exception:
        return "unreachable"

    x_urls = [t.get("url", "") for t in tabs
              if t.get("type") == "page" and "x.com" in t.get("url", "")]
    if not x_urls:
        return "unknown"

    auth_keywords = ("/i/flow/login", "/i/flow/signup", "/login", "/account/access")
    has_login = any(any(k in u for k in auth_keywords) for u in x_urls)
    has_app   = any(not any(k in u for k in auth_keywords) for u in x_urls)
    if has_app:
        verified = _verify_authenticated(port)
        if verified is True:
            return "logged_in"
        if verified is False:
            # URL looks app-y but no auth-only sidebar — likely signup wall.
            # If we ALSO see an explicit /i/flow/login tab, call that logged_out;
            # otherwise stay 'unknown' rather than risk a false logged_out flap.
            return "logged_out" if has_login else "unknown"
        return "unknown"  # verify couldn't run
    if has_login:
        return "logged_out"
    return "unknown"


def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(s: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(s, f, indent=2)


STALE_NOTIFY_AFTER_SEC = 2 * 3600   # 2h of unknown/unreachable → notify once


def run_once():
    state = _load_state()
    now = time.time()
    for name, handle, port in ACCOUNTS:
        cur = check_login_state(port)
        entry = state.get(name, {})
        prev = entry.get("state", "")
        _log(f"{name} (port {port}, {handle}): {prev or '<new>'} → {cur}")

        if cur in ("logged_in", "logged_out") and prev and cur != prev:
            if cur == "logged_out":
                send_notification(name,
                    f"⚠️ *{handle}* logged OUT\n"
                    f"_chrome port {port}_\n"
                    f"This account can't post / boost until re-logged in.")
                script = AUTO_LOGIN.get(name)
                last_auto = float(entry.get("last_auto_login_at", 0) or 0)
                if script and os.path.exists(script) and (now - last_auto) >= AUTO_LOGIN_COOLDOWN_SEC:
                    try:
                        subprocess.Popen(
                            ["/usr/bin/python3", "-u", script],
                            cwd=ROOT_DIR,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        entry["last_auto_login_at"] = now
                        _log(f"  {name}: auto-login triggered")
                    except Exception as e:
                        _log(f"  {name}: auto-login launch failed: {e}")
            elif cur == "logged_in" and prev == "logged_out":
                send_notification(name,
                    f"✅ *{handle}* back online\n_chrome port {port}_")

        # Stale-unknown alert: if previous baseline was logged_in but Chrome has
        # gone dark (unknown/unreachable) for STALE_NOTIFY_AFTER_SEC, notify
        # once — the watchdog can't *confirm* a logout from this state, but a
        # long stretch of silence is itself a problem worth flagging.
        if cur in ("unknown", "unreachable"):
            if prev == "logged_in":
                stale_since = entry.get("stale_since") or now
                entry["stale_since"] = stale_since
                if not entry.get("stale_alerted") and (now - stale_since) >= STALE_NOTIFY_AFTER_SEC:
                    hours = int((now - stale_since) / 3600)
                    send_notification(name,
                        f"⚠️ *{handle}* session unverifiable for {hours}+ h\n"
                        f"_chrome port {port}, state={cur}_\n"
                        f"Chrome may be dead or showing no x.com tab — "
                        f"watchdog can't tell if you're still logged in.")
                    entry["stale_alerted"] = True
                state[name] = entry
        else:
            # Returned to a definite state — clear stale tracking.
            entry.pop("stale_since", None)
            entry.pop("stale_alerted", None)

        # Persist baseline only on definite states so a temporarily down Chrome
        # doesn't reset our 'logged_in' anchor.
        if cur in ("logged_in", "logged_out"):
            entry.update({"state": cur, "ts": _ts(),
                          "port": port, "handle": handle})
            state[name] = entry
    _save_state(state)


def discover_chat(account: str):
    """Long-poll the account's bot once, print the first chat_id we see,
    then exit. Use after sending the bot any message from your phone."""
    token, _ = _env_for(account)
    if not token:
        print(f"TELEGRAM_BOT_TOKEN_{account.upper()} not set in .env")
        sys.exit(1)
    print(f"waiting for an inbound message to the {account} bot (send anything)...")
    offset = 0
    deadline = time.time() + 120
    while time.time() < deadline:
        url = API.format(token=token, method="getUpdates")
        data = urllib.parse.urlencode({"offset": offset, "timeout": 25}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=35) as r:
            d = json.loads(r.read())
        for u in d.get("result", []):
            offset = u["update_id"] + 1
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("id"):
                print(f"\nchat_id: {chat['id']}")
                print(f"\nAdd to .env:")
                print(f"  TELEGRAM_CHAT_ID_{account.upper()}={chat['id']}")
                return
    print("timed out — send a message to the bot and re-run")


def send_test(account: str):
    # Underscores in 'login_watchdog' would trip Markdown V1's italic parser;
    # use plain text for the smoke-test ping.
    ok = send_notification(account,
        f"test ping from login watchdog ({account}) at {_ts()}")
    print("sent" if ok else "FAILED — check token + chat_id")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--discover", choices=["hunter", "mguo", "shulex"],
                   help="print chat_id for that account's bot, then exit")
    p.add_argument("--test", choices=["hunter", "mguo", "shulex"],
                   help="send a test notification, then exit")
    args = p.parse_args()

    if args.discover:
        discover_chat(args.discover)
        return
    if args.test:
        send_test(args.test)
        return
    run_once()


if __name__ == "__main__":
    main()
