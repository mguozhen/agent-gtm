"""
Harvest "winning replies" from target accounts' recent threads to build the
few-shot library that engage_daemon uses to generate Hunter's replies.

A reply is "winning" if either:
  (A) op_responded — the OP (target account) replied to it
                     [heuristic: a tweet by OP appears within
                      `op_responded_lookahead` cards after the reply]
  (B) high_likes   — reply's likes ≥ hot_threshold_likes_multiplier × median(thread)
                     AND ≥ hot_threshold_likes_floor

Drives Hunter's Chrome (port from engage_config.json). Bails out of phase 2 on
consecutive empties (suggests X rate-limited the session) — same pattern as scout.

Usage:
    python3 harvest_replies.py                # full run, updates winning_replies.json
    python3 harvest_replies.py --target X     # only harvest target X
    python3 harvest_replies.py --posts 3      # override posts_per_target
    python3 harvest_replies.py --dry-run      # log what would be saved, don't write JSON
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;   env.load()
import chrome as _chrome
import login  as _login

DEFAULT_CONFIG = os.path.join(ROOT_DIR, "engage_config.json")
STATE_DIR      = os.path.join(ROOT_DIR, "state")
LOG_DIR        = os.path.join(ROOT_DIR, "logs", "harvest")
LIBRARY_PATH   = os.path.join(STATE_DIR, "winning_replies.json")

CONSECUTIVE_EMPTY_BAIL = 4   # threads in a row with zero replies extracted → assume rate-limit


# ── logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_count(s: str) -> int:
    if not s:
        return 0
    s = s.strip().replace(",", "")
    mult = 1
    if s and s[-1] in "Kk":   mult, s = 1_000, s[:-1]
    elif s and s[-1] in "Mm": mult, s = 1_000_000, s[:-1]
    elif s and s[-1] in "Bb": mult, s = 1_000_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _ensure_chrome(port: int, handle: str) -> bool:
    if _chrome.ping(port):
        return True
    _log(f"chrome on port {port} ({handle}) down — relaunching")
    profile_dir = os.path.join(ROOT_DIR, "chrome-profiles", handle)
    if not os.path.exists(profile_dir):
        _log(f"  no profile dir at {profile_dir}")
        return False
    try:
        _login.launch_chrome(port, profile_dir)
        _login.ensure_page_tab(port)
        return _chrome.ping(port)
    except Exception as e:
        _log(f"  relaunch failed: {e}")
        return False


# ── JS extractors ─────────────────────────────────────────────────────────────

# Pull recent original posts from a target's profile.
PROFILE_POSTS_JS = r"""
(function() {
    var articles = document.querySelectorAll('article[data-testid="tweet"]');
    var out = [];
    articles.forEach(function(el) {
        try {
            // Skip if this card is a retweet — header text contains "reposted"
            var headerTxt = (el.innerText || '').slice(0, 300);
            if (/reposted/i.test(headerTxt)) return;

            var textEl = el.querySelector('[data-testid="tweetText"]');
            var text   = textEl ? textEl.innerText.trim() : '';
            var urlEl  = el.querySelector('a[href*="/status/"]');
            var url    = urlEl ? urlEl.href : '';

            // Reply count present
            var replyEl = el.querySelector('[data-testid="reply"]');
            var replyTxt = replyEl ? replyEl.innerText.replace(/[^0-9KMB.,]/g,'') : '';

            // Skip cards that are replies (have "Replying to" preamble)
            var isReply = /Replying to/i.test(headerTxt);

            if (url && text && !isReply) {
                out.push({url: url, text: text.slice(0, 400), replies: replyTxt, isReply: false});
            }
        } catch(e) {}
    });
    return JSON.stringify(out);
})()
"""

# Pull every reply card on a tweet detail page.
# Also pulls the "Replying to @X" preamble so we can build the true reply-parent graph.
THREAD_REPLIES_JS = r"""
(function() {
    var articles = document.querySelectorAll('article[data-testid="tweet"]');
    var out = [];
    articles.forEach(function(el, idx) {
        try {
            var textEl = el.querySelector('[data-testid="tweetText"]');
            var text   = textEl ? textEl.innerText.trim() : '';

            // Author handle: User-Name → first <a href="/handle">
            var userArea = el.querySelector('[data-testid="User-Name"]');
            var author = '';
            if (userArea) {
                var links = userArea.querySelectorAll('a[href^="/"]');
                for (var i = 0; i < links.length; i++) {
                    var href = links[i].getAttribute('href');
                    var m = href.match(/^\/([A-Za-z0-9_]+)$/);
                    if (m) { author = m[1]; break; }
                }
            }

            // "Replying to @X" preamble — tells us this card's parent.
            // X renders this above the tweet text; we read from the article header.
            var head = (el.innerText || '').slice(0, 200);
            var rmatch = head.match(/Replying to\s+@([A-Za-z0-9_]+)/);
            var replyingTo = rmatch ? rmatch[1] : '';

            var likeEl  = el.querySelector('[data-testid="like"]');
            var likeTxt = likeEl ? likeEl.innerText.replace(/[^0-9KMB.,]/g,'') : '';

            var urlEl = el.querySelector('a[href*="/status/"]');
            var url   = urlEl ? urlEl.href : '';

            if (text && author) {
                out.push({position: idx, author: author, replyingTo: replyingTo,
                          text: text.slice(0, 800), likes_raw: likeTxt, url: url});
            }
        } catch(e) {}
    });
    return JSON.stringify(out);
})()
"""

# Generic low-quality replies that hurt the library if included.
GENERIC_REPLY_MIN_CHARS = 30


# ── core ──────────────────────────────────────────────────────────────────────

def fetch_target_posts(ws, handle: str, max_posts: int, min_replies: int) -> list:
    """Visit a target's profile, scroll deep, return up to N original posts
    that already advertise at least `min_replies` replies. This skips
    thread-continuation posts ("Link to the paper:") and zero-reply tweets,
    which otherwise eat thread-fetch budget for nothing."""
    _chrome.navigate(ws, f"https://x.com/{handle}", wait=3.5)
    for _ in range(4):
        _chrome.eval_js(ws, "window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2.5)

    raw = _chrome.eval_js(ws, PROFILE_POSTS_JS)
    try:
        posts = json.loads(raw) if raw else []
    except Exception:
        return []

    eligible = []
    for p in posts:
        replies_n = _parse_count(p.get("replies", ""))
        if replies_n < min_replies:
            continue
        p["replies_n"] = replies_n
        eligible.append(p)
        if len(eligible) >= max_posts:
            break
    return eligible


def fetch_thread_replies(ws, post_url: str) -> list:
    """Visit a post detail page, scroll-to-bottom several times, return all reply cards."""
    _chrome.navigate(ws, post_url, wait=4.0)
    for _ in range(4):
        _chrome.eval_js(ws, "window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2.5)

    raw = _chrome.eval_js(ws, THREAD_REPLIES_JS)
    try:
        cards = json.loads(raw) if raw else []
    except Exception:
        return []

    for c in cards:
        c["likes"] = _parse_count(c.get("likes_raw", ""))
    return cards


def identify_winners(target_handle: str, post_text: str, post_url: str,
                     cards: list, cfg: dict) -> list:
    """
    cards[0] is typically the OP tweet itself. Replies follow.
    Apply: (A) OP-responded via "Replying to @X" parent graph, (B) high-likes.
    Drop generic short replies — they teach the bot the wrong style.
    """
    if len(cards) < 2:
        return []

    # Drop the first card if it's by OP (the OP tweet itself).
    replies = []
    for c in cards:
        if not replies and c["author"].lower() == target_handle.lower():
            continue  # skip leading OP card
        replies.append(c)

    if len(replies) < cfg["harvest"]["min_replies_per_post"]:
        return []

    # Compute median like-count across non-OP replies.
    non_op = [r for r in replies if r["author"].lower() != target_handle.lower()]
    if not non_op:
        return []
    sorted_likes = sorted(r["likes"] for r in non_op)
    median = sorted_likes[len(sorted_likes) // 2]
    like_threshold = max(
        cfg["harvest"]["hot_threshold_likes_floor"],
        median * cfg["harvest"]["hot_threshold_likes_multiplier"]
    )

    # OP-responded: X renders conversation flat for top-level replies (no
    # "Replying to" preamble in DOM), so the parent-graph approach doesn't
    # work. Use positional heuristic instead — if the next 1-2 cards after
    # reply R are by OP, OP responded to R. False-positive rate is low because
    # X groups OP's response immediately after the reply it answers.
    target_l  = target_handle.lower()
    lookahead = cfg["harvest"]["op_responded_lookahead"]

    winners   = []
    seen_urls = set()
    for i, r in enumerate(replies):
        if r["author"].lower() == target_l:
            continue
        if len(r["text"]) < GENERIC_REPLY_MIN_CHARS:
            continue  # drop "Great work!" / "100%" / "Sounds cool" / etc.
        reasons = []
        for j in range(i + 1, min(i + 1 + lookahead, len(replies))):
            if replies[j]["author"].lower() == target_l:
                reasons.append("op_responded")
                break
        if r["likes"] >= like_threshold:
            reasons.append("high_likes")
        if not reasons:
            continue
        url = r.get("url") or ""
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        winners.append({
            "target_handle":       target_handle,
            "target_post_url":     post_url,
            "target_post_text":    post_text,
            "reply_author":        r["author"],
            "reply_text":          r["text"],
            "reply_url":           url,
            "reply_likes":         r["likes"],
            "thread_median_likes": median,
            "reasons":             reasons,
            "harvested_at":        _ts(),
        })
    return winners


def load_library() -> list:
    if not os.path.exists(LIBRARY_PATH):
        return []
    try:
        with open(LIBRARY_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def save_library(library: list):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LIBRARY_PATH, "w") as f:
        json.dump(library, f, indent=2, ensure_ascii=False)


def dedupe_library(library: list) -> list:
    """Dedupe by reply_url. Keep the most recent harvested_at."""
    by_url: dict = {}
    for w in library:
        k = w.get("reply_url") or (w.get("reply_author"), w.get("reply_text"))
        prev = by_url.get(k)
        if not prev or w.get("harvested_at", "") > prev.get("harvested_at", ""):
            by_url[k] = w
    return list(by_url.values())


# ── orchestration ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--target", default=None, help="only harvest this target handle")
    parser.add_argument("--posts",  type=int, default=None, help="override posts_per_target")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    targets = [args.target] if args.target else cfg["target_accounts"]
    posts_per_target = args.posts if args.posts else cfg["harvest"]["posts_per_target"]
    delay_lo, delay_hi = cfg["harvest"]["page_delay_sec"]

    port   = cfg["hunter_port"]
    handle = cfg["hunter_handle"]
    _log(f"harvest start — {len(targets)} targets × {posts_per_target} posts each, "
         f"page delay {delay_lo}-{delay_hi}s")

    if not _ensure_chrome(port, handle):
        _log("hunter chrome unavailable — abort")
        sys.exit(1)

    ws = _chrome.connect(port)
    # X pauses content loading on tabs with document.hidden=true (Page
    # Visibility API). The CDP-controlled tab is hidden by default → reply
    # lists never populate. Bring it to front + force a tall viewport so
    # virtualized cards have room to render.
    _chrome.bring_to_front(ws)
    _chrome.set_viewport(ws, width=1280, height=1600)
    new_winners: list = []
    consecutive_empty = 0

    try:
        for ti, target in enumerate(targets, 1):
            _log(f"[{ti}/{len(targets)}] target @{target}")
            try:
                posts = fetch_target_posts(
                    ws, target, posts_per_target,
                    min_replies=cfg["harvest"]["min_replies_per_post"],
                )
            except Exception as e:
                _log(f"  profile error: {e}")
                continue
            if not posts:
                _log(f"  no posts found — skipping target")
                continue
            _log(f"  {len(posts)} posts to scan")
            time.sleep(random.uniform(delay_lo, delay_hi))

            for pi, p in enumerate(posts, 1):
                _log(f"  [{pi}/{len(posts)}] {p['url']}")
                try:
                    cards = fetch_thread_replies(ws, p["url"])
                except Exception as e:
                    _log(f"    thread error: {e}")
                    continue

                if not cards:
                    consecutive_empty += 1
                    _log(f"    empty thread fetch ({consecutive_empty} in a row)")
                    if consecutive_empty >= CONSECUTIVE_EMPTY_BAIL:
                        _log(f"    {CONSECUTIVE_EMPTY_BAIL} empties — assuming rate-limit; bailing")
                        raise SystemExit(0)
                    continue
                consecutive_empty = 0

                winners = identify_winners(target, p["text"], p["url"], cards, cfg)
                if winners:
                    _log(f"    +{len(winners)} winner(s)")
                    new_winners.extend(winners)
                else:
                    _log(f"    no winners ({len(cards)} cards scanned)")

                time.sleep(random.uniform(delay_lo, delay_hi))
    finally:
        ws.close()

    _log(f"harvest done — {len(new_winners)} new winning replies captured")
    if args.dry_run:
        preview_path = os.path.join(STATE_DIR, "winning_replies.preview.json")
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(preview_path, "w") as f:
            json.dump(new_winners, f, indent=2, ensure_ascii=False)
        _log(f"dry-run — preview saved to {preview_path} (library at {LIBRARY_PATH} untouched)")
        for w in new_winners[:5]:
            _log(f"  sample: @{w['reply_author']} ({w['reasons']}, {w['reply_likes']} likes) — "
                 f"\"{w['reply_text'][:120]}\"")
        return

    library = load_library()
    library.extend(new_winners)
    library = dedupe_library(library)
    save_library(library)
    _log(f"library now {len(library)} total entries → {LIBRARY_PATH}")

    # Brief stats
    by_target: dict = {}
    by_reason: dict = {"op_responded": 0, "high_likes": 0}
    for w in library:
        by_target[w["target_handle"]] = by_target.get(w["target_handle"], 0) + 1
        for r in w["reasons"]:
            by_reason[r] = by_reason.get(r, 0) + 1
    _log(f"by reason: {by_reason}")
    _log(f"by target: {sorted(by_target.items(), key=lambda x: -x[1])[:10]}")


if __name__ == "__main__":
    main()
