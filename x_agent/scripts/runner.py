"""Main runner — orchestrates a full daily session for one account.

Usage:
    python3 runner.py SolveaCX
    python3 runner.py SolveaCX --dry-run
"""
import json, os, random, sys, time
from datetime import date

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;      env.load()
import chrome   as _chrome
import fetch    as _fetch
import post     as _post
import engage   as _engage
import generate as _generate
import logger   as _logger
import login    as _login

ACCOUNTS_DIR = os.path.join(ROOT_DIR, "accounts")

DELAY_BETWEEN_REPLIES = (60, 150)   # seconds

# All self-operated accounts — never reply to these
OWN_ACCOUNTS = {"solveacx", "vocaisage", "voc_ai", "shulexhunter"}


def _load_account(handle: str) -> dict:
    base = os.path.join(ACCOUNTS_DIR, handle)
    config_path = os.path.join(base, "config.json")
    if not os.path.exists(config_path):
        print(f"[runner] ERROR: account '{handle}' not found at {base}")
        sys.exit(1)
    with open(config_path) as f:
        config = json.load(f)
    targets_path = os.path.join(ROOT_DIR, "targets.json")
    with open(targets_path) as f:
        targets = json.load(f)
    config["_base"]    = base
    config["_targets"] = targets
    return config


def _ensure_chrome(account: dict):
    port = account["chrome_port"]
    handle = account["handle"]
    if _chrome.ping(port):
        return
    print(f"[runner] Chrome not running on port {port}, launching...")
    profile_dir = os.path.join(ROOT_DIR, "chrome-profiles", handle)
    _login.launch_chrome(port, profile_dir)


def _run_posts(account: dict, limit: int, dry_run: bool) -> int:
    if limit <= 0:
        return 0

    base   = account["_base"]
    port   = account["chrome_port"]
    handle = account["handle"]

    print(f"[posts] will post {limit} tweet(s)")
    done = 0

    for i in range(limit):
        print(f"\n[post {i+1}/{limit}] generating...")
        text = _generate.generate_post(handle)
        if not text:
            print("  [skip] empty content generated")
            continue
        print(f"  → {text[:100]}...")

        result = _post.post_tweet(port, text, handle=handle, dry_run=dry_run)
        print(f"  result: {result}")

        if result["ok"] and not dry_run:
            tweet_url = result.get("url", "")
            _logger.log_posted(base, text, url=tweet_url)
            _logger.log_action(base, "post", "", url=tweet_url, ok=True, note=text[:100])
            done += 1
        elif not result["ok"]:
            _logger.log_action(base, "post", "", ok=False, note=result.get("error", ""))

        if i < limit - 1:
            time.sleep(random.uniform(30, 90))

    return done


def _run_replies(account: dict, limit: int, dry_run: bool) -> int:
    if limit <= 0:
        return 0

    base    = account["_base"]
    port    = account["chrome_port"]
    handle  = account["handle"]

    keywords = account["_targets"].get("keywords", [])
    if not keywords:
        print("[replies] no keywords configured in targets.json")
        return 0

    print(f"[replies] will post up to {limit} replies across {len(keywords)} keywords")

    random.shuffle(keywords)
    replied = _logger.load_replied(base)
    replied_authors_today = _logger.replied_authors_today(base)
    done    = 0

    for kw in keywords:
        if done >= limit:
            break

        print(f"\n[search] '{kw}'")
        try:
            tweets = _fetch.search(port, kw, limit=10)
        except Exception as e:
            print(f"  [error] fetch failed: {e}")
            continue

        new_tweets = [
            t for t in tweets
            if t["id"] not in replied
            and t.get("author", "").lower() not in OWN_ACCOUNTS
            and t.get("author", "").lower() not in replied_authors_today
        ]
        print(f"  {len(tweets)} found, {len(new_tweets)} unseen")

        for tweet in new_tweets:
            if done >= limit:
                break

            print(f"\n  [reply] @{tweet['author']}: {tweet['text'][:70]}")
            reply_text = _generate.generate_reply(handle, tweet["text"], tweet.get("author", ""))
            if not reply_text or len(reply_text) < 10:
                print("  [skip] empty reply, skipping")
                continue
            print(f"  → {reply_text[:100]}")

            result = _engage.reply_tweet(port, tweet["url"], reply_text, dry_run=dry_run)
            print(f"  result: {result}")

            if result["ok"]:
                if not dry_run:
                    _logger.mark_replied(base, tweet["id"], tweet["url"])
                    _logger.log_action(base, "reply", tweet["id"], tweet["url"], ok=True, note=reply_text[:100])
                replied[tweet["id"]] = tweet["url"]
                replied_authors_today.add(tweet.get("author", "").lower())
                done += 1
            else:
                _logger.log_action(base, "reply", tweet["id"], tweet["url"], ok=False, note=result.get("error", ""))

            if done < limit:
                delay = random.randint(*DELAY_BETWEEN_REPLIES)
                print(f"  [wait] {delay}s")
                if not dry_run:
                    time.sleep(delay)

    return done


def run(handle: str, dry_run: bool = False):
    print(f"[runner] {handle} | date={date.today()} | dry_run={dry_run}")

    account = _load_account(handle)
    limits  = account.get("daily_limits", {})

    _ensure_chrome(account)

    # Posts
    posts_done = _run_posts(account, limits.get("posts", 0), dry_run)

    # Replies
    replies_done = _run_replies(account, limits.get("replies", 0), dry_run)

    # Summary
    print(f"\n[runner] done — posts={posts_done} replies={replies_done}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("handle")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.handle, dry_run=args.dry_run)
