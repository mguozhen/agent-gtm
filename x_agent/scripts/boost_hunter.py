"""Boost hunter's posts from booster accounts.

For each booster account configured in engage_config.json -> boost.boosters,
fetch hunter's recent posts and like + retweet + reply from the booster.

Usage:
    python3 boost_hunter.py                # all boosters, live
    python3 boost_hunter.py --dry-run      # no actions, just print
    python3 boost_hunter.py --booster mguo # one booster only
    python3 boost_hunter.py --max 3        # only first N hunter posts per booster
"""
import argparse
import json
import os
import random
import sys
import time
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;     env.load()
import chrome  as _chrome
import engage  as _engage
import fetch   as _fetch
import generate as _generate
import logger  as _logger
import login   as _login

ACCOUNTS_DIR = os.path.join(ROOT_DIR, "accounts")
PROFILES_DIR = os.path.join(ROOT_DIR, "chrome-profiles")
CONFIG_PATH  = os.path.join(ROOT_DIR, "engage_config.json")

DEFAULTS = {
    "boosters":          ["mguo", "shulex"],
    "max_posts":         3,
    "per_booster_delay": [60, 180],
    "between_actions":   [20, 60],
    "between_tweets":    [90, 240],
    "comment_mode":      "reply",        # "reply" or "quote"
    "actions":           ["like", "retweet", "comment"],
}


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(f"engage_config.json not found at {CONFIG_PATH}")
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    hunter_handle = cfg.get("hunter_handle")
    if not hunter_handle:
        raise SystemExit("hunter_handle missing in engage_config.json")
    boost = {**DEFAULTS, **cfg.get("boost", {})}
    return {"hunter_handle": hunter_handle, "boost": boost}


def _load_booster(handle: str) -> dict:
    path = os.path.join(ACCOUNTS_DIR, handle, "config.json")
    if not os.path.exists(path):
        raise SystemExit(f"booster '{handle}' has no config at {path}")
    with open(path) as f:
        return json.load(f)


def _state_path(account_dir: str) -> str:
    return os.path.join(account_dir, "logs", "boosted_hunter.json")


def _load_state(account_dir: str) -> dict:
    p = _state_path(account_dir)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def _save_state(account_dir: str, state: dict):
    p = _state_path(account_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(state, f, indent=2)


def _ensure_chrome(handle: str, port: int):
    if _chrome.ping(port):
        return
    profile_dir = os.path.join(PROFILES_DIR, handle)
    os.makedirs(profile_dir, exist_ok=True)
    print(f"[boost] chrome not running on port {port}; launching for {handle}")
    _login.launch_chrome(port, profile_dir)


def _detect_x_handle(port: int) -> str:
    """Read the logged-in X handle from the browser (side nav profile link)."""
    ws = _chrome.connect(port)
    try:
        _chrome.navigate(ws, "https://x.com/home", wait=2.5)
        handle = _chrome.eval_js(ws, """
            (function(){
                var el = document.querySelector('a[data-testid="AppTabBar_Profile_Link"]');
                if (el) {
                    var h = (el.getAttribute('href') || '').replace(/^\\//, '');
                    if (h) return h;
                }
                return '';
            })()
        """)
        return (handle or "").strip()
    finally:
        ws.close()


def _sleep(span):
    lo, hi = span
    t = random.uniform(lo, hi)
    time.sleep(t)


SHORT_TEXT_THRESHOLD = 25  # chars; below this we don't bother asking Claude

REFUSAL_MARKERS = (
    "i don't have the full context",
    "could you share the complete tweet",
    "could you provide",
    "as an ai",
)


def _short_reaction(text: str) -> str:
    """Pick a canned reaction for ultra-short hunter tweets that Claude can't
    meaningfully engage with (e.g. 'ai video!')."""
    t = text.lower()
    if "video" in t:
        return random.choice(["cool video", "nice video", "love this", "🔥"])
    if "pic" in t or "image" in t or "photo" in t:
        return random.choice(["clean", "nice shot", "love this"])
    if "ship" in t or "launch" in t or "shipped" in t:
        return random.choice(["lfg", "let's go", "🔥"])
    return random.choice(["lfg", "this", "love it", "agreed", "🔥"])


def _looks_like_refusal(s: str) -> bool:
    low = s.lower()
    return any(m in low for m in REFUSAL_MARKERS)


def _boost_one(booster_handle: str, hunter_handle: str, boost_cfg: dict,
               max_posts: int, dry_run: bool):
    cfg = _load_booster(booster_handle)
    port = cfg["chrome_port"]
    account_dir = os.path.join(ACCOUNTS_DIR, booster_handle)

    print(f"\n[boost] === {booster_handle} (port {port}) → @{hunter_handle} ===")
    _ensure_chrome(booster_handle, port)

    x_handle = _detect_x_handle(port)
    if not x_handle:
        print(f"[boost] WARN — could not detect X handle for {booster_handle}; verify will likely fail")
        x_handle = booster_handle  # fallback
    print(f"[boost] {booster_handle} is logged in as @{x_handle}")

    print(f"[boost] fetching @{hunter_handle} profile from {booster_handle}'s browser")
    try:
        tweets = _fetch.get_profile_tweets(port, hunter_handle, limit=max_posts * 3)
    except Exception as e:
        print(f"[boost] fetch failed: {e}")
        return {"likes": 0, "retweets": 0, "replies": 0}

    # Keep only original hunter tweets (no retweets / replies). The author check is
    # not super precise on X — `author` from get_tweets is the display name span,
    # which may include @handle. We accept anything; the URL is authoritative.
    hunter_lower = hunter_handle.lower()
    own = [t for t in tweets if t.get("id") and t.get("url") and hunter_lower in t.get("url", "").lower()]
    state = _load_state(account_dir)
    fresh = [t for t in own if t["id"] not in state][:max_posts]
    print(f"[boost] {len(tweets)} fetched, {len(own)} look like hunter's, {len(fresh)} unseen")

    actions = set(boost_cfg["actions"])
    mode    = boost_cfg["comment_mode"]
    stats   = {"likes": 0, "retweets": 0, "replies": 0}

    for i, tw in enumerate(fresh):
        tid = tw["id"]
        url = tw["url"]
        text = tw.get("text", "")
        print(f"\n[boost] tweet {i+1}/{len(fresh)}: {url}")
        print(f"        {text[:120]}")

        entry = {"url": url, "ts": datetime.utcnow().isoformat() + "Z", "actions": {}}

        if "like" in actions:
            r = _engage.like_tweet(port, url, dry_run=dry_run)
            entry["actions"]["like"] = r
            if r.get("ok"):
                stats["likes"] += 1
                if not dry_run:
                    _logger.log_action(account_dir, "like", tid, url, ok=True, note=f"boost:{hunter_handle}")
            else:
                if not dry_run:
                    _logger.log_action(account_dir, "like", tid, url, ok=False, note=r.get("error", ""))
            _sleep(boost_cfg["between_actions"])

        if "retweet" in actions:
            r = _engage.retweet_tweet(port, url, dry_run=dry_run)
            entry["actions"]["retweet"] = r
            if r.get("ok"):
                stats["retweets"] += 1
                if not dry_run:
                    _logger.log_action(account_dir, "retweet", tid, url, ok=True, note=f"boost:{hunter_handle}")
            else:
                if not dry_run:
                    _logger.log_action(account_dir, "retweet", tid, url, ok=False, note=r.get("error", ""))
            _sleep(boost_cfg["between_actions"])

        if "comment" in actions:
            comment = ""
            if len(text.strip()) < SHORT_TEXT_THRESHOLD:
                comment = _short_reaction(text)
                print(f"        [comment] short tweet ({len(text.strip())} chars) → canned: {comment!r}")
            else:
                try:
                    comment = _generate.generate_amplify_comment(
                        handle=booster_handle, hunter_text=text, mode=mode, hunter_author=hunter_handle,
                    )
                except Exception as e:
                    print(f"        [comment] generation failed: {e}")
                    comment = ""
                comment = (comment or "").strip()
                if _looks_like_refusal(comment):
                    print(f"        [comment] model refused — falling back to canned reaction")
                    comment = _short_reaction(text)

            if not comment.strip():
                print("        [comment] skipped (empty)")
                entry["actions"]["comment"] = {"ok": False, "error": "empty_generation"}
            else:
                print(f"        [comment] → {comment[:120]}")
                if mode == "quote":
                    r = _engage.quote_tweet(port, url, comment, dry_run=dry_run)
                else:
                    r = _engage.reply_tweet(port, url, comment, dry_run=dry_run, self_handle=x_handle)
                entry["actions"]["comment"] = r
                if r.get("ok"):
                    stats["replies"] += 1
                    if not dry_run:
                        _logger.log_action(account_dir, "reply", tid, url, ok=True, note=comment[:100])
                else:
                    if not dry_run:
                        _logger.log_action(account_dir, "reply", tid, url, ok=False, note=r.get("error", ""))

        # Only persist state for live runs — dry-runs shouldn't poison the dedupe log
        if not dry_run:
            state[tid] = entry
            _save_state(account_dir, state)

        if i < len(fresh) - 1:
            _sleep(boost_cfg["between_tweets"])

    print(f"[boost] {booster_handle} done — likes={stats['likes']} retweets={stats['retweets']} replies={stats['replies']}")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--booster", help="Run for a single booster handle only")
    ap.add_argument("--max", type=int, help="Override max_posts")
    args = ap.parse_args()

    cfg = _load_config()
    hunter_handle = cfg["hunter_handle"]
    boost_cfg     = cfg["boost"]
    max_posts     = args.max if args.max else boost_cfg["max_posts"]

    boosters = [args.booster] if args.booster else boost_cfg["boosters"]

    print(f"[boost] hunter=@{hunter_handle}  boosters={boosters}  max_posts={max_posts}  dry_run={args.dry_run}")
    totals = {"likes": 0, "retweets": 0, "replies": 0}
    for i, h in enumerate(boosters):
        try:
            s = _boost_one(h, hunter_handle, boost_cfg, max_posts, args.dry_run)
            for k, v in s.items():
                totals[k] += v
        except SystemExit:
            raise
        except Exception as e:
            print(f"[boost] booster '{h}' failed: {e}")
        if i < len(boosters) - 1:
            _sleep(boost_cfg["per_booster_delay"])

    print(f"\n[boost] TOTALS — likes={totals['likes']} retweets={totals['retweets']} replies={totals['replies']}")


if __name__ == "__main__":
    main()
