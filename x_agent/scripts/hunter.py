"""
Hunter monitor daemon — polls a partner account's profile and amplifies new tweets
from the configured responder accounts.

Usage:
    python3 hunter.py                 # run daemon (default config)
    python3 hunter.py --dry-run       # log what would happen, don't post
    python3 hunter.py --once          # poll once, then exit
    python3 hunter.py --process-backlog   # also engage with already-existing tweets on first scan

Defaults:
    hunter handle:   GuoHunter95258
    responders:      VOC_ai (port 10002), SolveaCX (port 10001)
    poll interval:   120-180 s (random)
    action delay:    30-180 s (random) between each of the 4 actions per new tweet
    actions per new tweet:
        VOC_ai    quote
        VOC_ai    reply
        SolveaCX  quote
        SolveaCX  reply
"""
import argparse
import json
import os
import random
import signal
import sys
import time
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;     env.load()
import chrome  as _chrome
import fetch   as _fetch
import engage  as _engage
import generate as _generate
import logger  as _logger
import login   as _login

ACCOUNTS_DIR = os.path.join(ROOT_DIR, "accounts")
STATE_DIR    = os.path.join(ROOT_DIR, "state")
HUNTER_LOGS  = os.path.join(ROOT_DIR, "logs", "hunter")

# ── Config (override via env vars or CLI args) ────────────────────────────────
HUNTER_HANDLE = os.environ.get("HUNTER_HANDLE", "GuoHunter95258")
RESPONDERS    = [
    {"handle": "VOC_ai",   "port": 10002},
    {"handle": "SolveaCX", "port": 10001},
]
POLL_INTERVAL  = (30, 60)     # seconds, random — tight enough to catch posts ~within a minute
ACTION_DELAY   = (10, 30)     # seconds, random
TWEET_DELAY    = (60, 240)    # seconds between processing two tweets in same batch
POLL_PORT      = 10003         # GuoHunter95258's own Chrome — keeps VOC_ai/SolveaCX free for actions

_stop = False


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(HUNTER_LOGS, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(HUNTER_LOGS, f"{day}.log"), "a") as f:
        f.write(line + "\n")


def _state_path() -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, "hunter_seen.json")


def _load_seen() -> dict:
    p = _state_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_seen(seen: dict):
    with open(_state_path(), "w") as f:
        json.dump(seen, f, indent=2)


def _ensure_chrome(handle: str, port: int) -> bool:
    """Make sure Chrome is alive on the given port; relaunch if not. Returns True if usable."""
    if _chrome.ping(port):
        return True
    _log(f"chrome on port {port} ({handle}) is down — attempting relaunch")
    profile_dir = os.path.join(ROOT_DIR, "chrome-profiles", handle)
    if not os.path.exists(profile_dir):
        _log(f"  no profile dir for {handle} at {profile_dir} — cannot relaunch")
        return False
    try:
        _login.launch_chrome(port, profile_dir)
        _login.ensure_page_tab(port)
        return _chrome.ping(port)
    except Exception as e:
        _log(f"  relaunch failed: {e}")
        return False


def _fetch_hunter_tweets(limit: int = 20) -> list[dict]:
    # Polling Hunter's own profile via Hunter's own Chrome — doesn't interfere
    # with VOC_ai/SolveaCX Chromes that may be doing daily-runner work.
    if not _ensure_chrome(HUNTER_HANDLE, POLL_PORT):
        return []
    try:
        return _fetch.get_profile_tweets(POLL_PORT, HUNTER_HANDLE, limit=limit)
    except Exception as e:
        _log(f"fetch error: {e}")
        return []


def _run_one_action(responder: dict, tweet: dict, mode: str, dry_run: bool) -> bool:
    """
    mode = "quote" | "reply"
    Returns True on success.
    """
    handle = responder["handle"]
    port   = responder["port"]
    base   = os.path.join(ACCOUNTS_DIR, handle)

    if not _ensure_chrome(handle, port):
        _log(f"  [{handle}] chrome unavailable — skipping {mode}")
        return False

    try:
        text = _generate.generate_amplify_comment(
            handle, tweet["text"], mode=mode, hunter_author=HUNTER_HANDLE,
        )
    except Exception as e:
        _log(f"  [{handle}] generate failed: {e}")
        return False

    if not text or len(text) < 8:
        _log(f"  [{handle}] empty {mode} text — skipping")
        return False

    _log(f"  [{handle}] {mode}: {text[:90]}")

    try:
        if mode == "quote":
            result = _engage.quote_tweet(port, tweet["url"], text, dry_run=dry_run)
        else:
            result = _engage.reply_tweet(port, tweet["url"], text, dry_run=dry_run)
    except Exception as e:
        _log(f"  [{handle}] {mode} error: {e}")
        return False

    if result.get("ok"):
        _logger.log_action(base, mode, tweet["id"], tweet["url"], ok=True, note=text[:100])
        return True
    else:
        _logger.log_action(base, mode, tweet["id"], tweet["url"], ok=False, note=result.get("error", ""))
        _log(f"  [{handle}] {mode} failed: {result.get('error')}")
        return False


def _process_tweet(tweet: dict, dry_run: bool):
    """For a new Hunter tweet, run quote+reply from each responder with random delays."""
    _log(f"new tweet from @{HUNTER_HANDLE}: {tweet['url']}")
    _log(f"  text: {tweet['text'][:160]}")

    plan = []
    for responder in RESPONDERS:
        for mode in ("quote", "reply"):
            plan.append((responder, mode))

    for i, (responder, mode) in enumerate(plan):
        if _stop:
            _log("stop signal — interrupting plan")
            return
        _run_one_action(responder, tweet, mode, dry_run)
        if i < len(plan) - 1:
            delay = random.randint(*ACTION_DELAY)
            _log(f"  waiting {delay}s before next action")
            _sleep(delay)


def _sleep(seconds: int):
    """Interruptible sleep that respects _stop."""
    end = time.time() + seconds
    while not _stop and time.time() < end:
        time.sleep(min(2, end - time.time()))


def _handle_sigterm(signum, frame):
    global _stop
    _log(f"received signal {signum} — shutting down")
    _stop = True


def run_once(dry_run: bool, process_backlog: bool, seen: dict) -> int:
    """One poll cycle. Returns number of tweets acted on."""
    tweets = _fetch_hunter_tweets(limit=20)
    if not tweets:
        _log("no tweets fetched")
        return 0

    new_tweets = [t for t in tweets if t["id"] not in seen]
    if not new_tweets:
        return 0

    # On first run (empty state), default to marking all current tweets as seen
    # without acting on them — avoids amplifying the entire backlog.
    if not seen and not process_backlog:
        _log(f"first run — marking {len(new_tweets)} existing tweets as seen (no actions)")
        for t in new_tweets:
            seen[t["id"]] = {
                "first_seen": _ts(),
                "url": t["url"],
                "text": t["text"][:200],
                "skipped_backlog": True,
            }
        _save_seen(seen)
        return 0

    # X profile feed is newest-first; reverse so we engage oldest-new first
    new_tweets.reverse()
    _log(f"found {len(new_tweets)} new tweet(s)")
    acted = 0

    for i, t in enumerate(new_tweets):
        if _stop:
            break
        # Mark seen BEFORE processing — protects against re-engagement on crash
        seen[t["id"]] = {
            "first_seen": _ts(),
            "url": t["url"],
            "text": t["text"][:200],
        }
        _save_seen(seen)

        _process_tweet(t, dry_run)
        acted += 1

        if i < len(new_tweets) - 1 and not _stop:
            delay = random.randint(*TWEET_DELAY)
            _log(f"waiting {delay}s before next tweet")
            _sleep(delay)

    return acted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="generate but don't post")
    parser.add_argument("--once", action="store_true", help="run one cycle then exit")
    parser.add_argument("--process-backlog", action="store_true",
                        help="on first run, engage with tweets already on the profile (default: skip)")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT,  _handle_sigterm)

    _log(f"hunter starting — target=@{HUNTER_HANDLE}  dry_run={args.dry_run}  backlog={args.process_backlog}")
    _log(f"responders: {[r['handle'] for r in RESPONDERS]}")

    seen = _load_seen()
    _log(f"loaded {len(seen)} previously-seen tweets")

    while not _stop:
        try:
            n = run_once(args.dry_run, args.process_backlog, seen)
            if n:
                _log(f"acted on {n} tweet(s) this cycle")
        except Exception as e:
            _log(f"cycle error: {e}")

        if args.once or _stop:
            break

        wait = random.randint(*POLL_INTERVAL)
        _log(f"next poll in {wait}s")
        _sleep(wait)

    _log("hunter stopped")


if __name__ == "__main__":
    main()
