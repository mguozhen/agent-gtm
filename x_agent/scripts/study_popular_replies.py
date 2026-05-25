"""study_popular_replies — sample 200+ popular replies from Hunter's For You
feed and produce a pattern-analysis report.

Pipeline:
  1. Open Hunter's home (For You), scroll N times, extract permalinks of
     tweets that already advertise high reply counts.
  2. For each tweet, open the detail page, scroll, scrape reply cards
     (reuses THREAD_REPLIES_JS from harvest_replies).
  3. Keep replies with ≥ min_likes_reply (default 50), drop the OP's
     self-replies and ultra-short ones, until target_replies are collected.
  4. Save corpus to state/popular_replies_study_<ts>.json.
  5. Pipe to Claude → markdown report in logs/popular_replies_study/.

Usage:
    python3 scripts/study_popular_replies.py
    python3 scripts/study_popular_replies.py --target 200 --min-reply-likes 30
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;       env.load()
import chrome   as _chrome
import generate as _generate
from lock import chrome_lock

HUNTER_PORT   = 10000
STATE_DIR     = os.path.join(ROOT_DIR, "state")
LOG_DIR       = os.path.join(ROOT_DIR, "logs", "popular_replies_study")


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


def _parse_count(s: str) -> int:
    if not s: return 0
    s = s.strip().replace(",", "").upper()
    mult = 1
    if s and s[-1] == "K": mult, s = 1_000, s[:-1]
    elif s and s[-1] == "M": mult, s = 1_000_000, s[:-1]
    elif s and s[-1] == "B": mult, s = 1_000_000_000, s[:-1]
    try: return int(float(s) * mult)
    except ValueError: return 0


# JS for scraping the For You feed — extract tweet URLs that have BOTH a high
# reply count and reasonable engagement. We sort by reply count later because
# we want threads with lots of replies (so we have lots of popular replies to
# sample from), not just high-like tweets.
FEED_JS = r"""
(function() {
    var arts = document.querySelectorAll('article[data-testid="tweet"]');
    var out = [];
    arts.forEach(function(el) {
        var head = (el.innerText || '').slice(0, 200);
        if (/Pinned/i.test(head)) return;
        if (/Promoted/i.test(head)) return;
        if (/Replying to/i.test(head)) return;

        var urlEl = el.querySelector('a[href*="/status/"]');
        var url = urlEl ? urlEl.href : '';
        if (!url) return;
        var idMatch = url.match(/status[/](\d+)/);
        var id = idMatch ? idMatch[1] : '';
        var authorMatch = url.match(/x\.com\/([A-Za-z0-9_]+)\/status\//);
        var author = authorMatch ? authorMatch[1] : '';

        var likeEl  = el.querySelector('[data-testid="like"]');
        var likeTxt = likeEl ? likeEl.innerText.replace(/[^0-9KMB.,]/g,'') : '';
        var replyEl = el.querySelector('[data-testid="reply"]');
        var replyTxt = replyEl ? replyEl.innerText.replace(/[^0-9KMB.,]/g,'') : '';

        var textEl = el.querySelector('[data-testid="tweetText"]');
        var text   = textEl ? textEl.innerText.trim().slice(0,500) : '';

        out.push({id:id, url:url, author:author, text:text,
                  likesTxt:likeTxt, repliesTxt:replyTxt});
    });
    return JSON.stringify(out);
})()
"""


def _thread_replies_js() -> str:
    """Reuse harvest_replies.THREAD_REPLIES_JS for per-tweet reply scraping."""
    import harvest_replies as _hr
    return _hr.THREAD_REPLIES_JS


def collect_feed(ws, scrolls: int = 12) -> list:
    """Open /home, scroll, return unique tweet permalinks ranked by reply count."""
    seen = {}
    with chrome_lock(HUNTER_PORT, on_wait=lambda s: _log(f"  lock wait {s:.0f}s")):
        _chrome.navigate(ws, "https://x.com/home", wait=4.0)
        time.sleep(1.5)
        for i in range(scrolls):
            raw = _chrome.eval_js(ws, FEED_JS)
            try:
                cards = json.loads(raw) if raw else []
            except Exception:
                cards = []
            for c in cards:
                if not c.get("id") or c["id"] in seen:
                    continue
                c["likes"]   = _parse_count(c.get("likesTxt", ""))
                c["replies"] = _parse_count(c.get("repliesTxt", ""))
                seen[c["id"]] = c
            _log(f"  feed scroll #{i+1}: {len(cards)} cards visible, "
                 f"{len(seen)} unique so far")
            _chrome.eval_js(ws, "window.scrollBy(0, 1800)")
            time.sleep(2.5)
    ranked = sorted(seen.values(), key=lambda x: x["replies"], reverse=True)
    return ranked


def fetch_thread_replies(ws, post_url: str, scrolls: int = 3) -> list:
    """Open a tweet detail page, scroll, return reply cards (raw JSON parsed)."""
    with chrome_lock(HUNTER_PORT, on_wait=lambda s: _log(f"  lock wait {s:.0f}s")):
        try:
            _chrome.navigate(ws, post_url, wait=4.0)
        except Exception as e:
            _log(f"    nav failed: {e}")
            return []
        for _ in range(scrolls):
            _chrome.eval_js(ws, "window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2.0)
        raw = _chrome.eval_js(ws, _thread_replies_js())
    try:
        return json.loads(raw) if raw else []
    except Exception:
        return []


def collect_replies(ws, feed: list, target: int, min_likes: int,
                    op_handle_for_url: dict, top_per_thread: int = 8,
                    incremental_save_path: str = "") -> list:
    """Walk feed (highest-reply-count first), extract popular replies, until
    `target` collected. "Popular" = top K replies per thread by likes (X
    reply-level likes rarely break double digits, so absolute thresholds don't
    work — relative-rank within a thread is the right signal)."""
    out = []
    for i, tweet in enumerate(feed):
        if len(out) >= target:
            break
        url       = tweet["url"]
        op_handle = (op_handle_for_url.get(url)
                     or tweet.get("author") or "").lower()
        op_likes  = tweet.get("likes", 0)
        _log(f"[{i+1}/{len(feed)}] thread @{op_handle} "
             f"({tweet.get('replies',0)} replies, {op_likes}♥): {url}")

        # Wrap per-thread fetch so a single chrome_lock timeout / nav error
        # doesn't kill the whole run.
        try:
            cards = fetch_thread_replies(ws, url)
        except Exception as e:
            _log(f"    fetch error: {e} — skipping thread")
            time.sleep(3.0)
            continue

        # Build a list of eligible replies (drop OP's own, ultra-short, no-likes)
        eligible = []
        for c in cards:
            author = (c.get("author") or "").lower()
            if not author or author == op_handle:
                continue
            text = (c.get("text") or "").strip()
            if len(text) < 25:
                continue
            likes = _parse_count(c.get("likes_raw", ""))
            # Sanity cap: a reply CANNOT plausibly have more likes than the OP
            # times some headroom. When the JS gets a garbled likeTxt the parsed
            # number can be absurd (we've seen 26M on a 50♥ OP). Treat as 0.
            if op_likes > 0 and likes > op_likes * 5:
                likes = 0
            if likes < min_likes:
                continue
            eligible.append({"author": author, "text": text, "likes": likes,
                             "url": c.get("url", ""), "position": c.get("position", -1)})

        # Sort by likes desc and take top K — these are the thread's "popular" replies
        eligible.sort(key=lambda r: r["likes"], reverse=True)
        picked = eligible[:top_per_thread]

        for r in picked:
            out.append({
                "op_handle":   op_handle,
                "op_text":     tweet.get("text", "")[:500],
                "op_url":      url,
                "op_likes":    op_likes,
                "op_replies":  tweet.get("replies", 0),
                "reply_author": r["author"],
                "reply_text":   r["text"],
                "reply_likes":  r["likes"],
                "reply_url":    r["url"],
                "position":     r["position"],
            })
            if len(out) >= target:
                break

        _log(f"    {len(eligible)} eligible, kept top {len(picked)} "
             f"(max {picked[0]['likes'] if picked else 0}♥, "
             f"min {picked[-1]['likes'] if picked else 0}♥) — total: {len(out)}")

        # Incremental save — so a crash at thread 37 doesn't lose threads 1-36.
        if incremental_save_path:
            try:
                with open(incremental_save_path, "w") as f:
                    json.dump({"collected_at": _ts(), "n": len(out),
                               "replies": out}, f, indent=2, ensure_ascii=False)
            except Exception as e:
                _log(f"    incremental save failed: {e}")

        if len(out) >= target:
            break
        time.sleep(2.0)
    return out


def analyze_corpus(corpus: list) -> str:
    """Ask Claude to read the corpus and produce a structured pattern report."""
    sample_lines = []
    for r in corpus[:120]:  # cap context — 120 examples is plenty for analysis
        sample_lines.append(
            f"OP @{r['op_handle']} ({r['op_likes']}♥, {r['op_replies']} replies): "
            f"{r['op_text'][:200]}\n"
            f"REPLY @{r['reply_author']} ({r['reply_likes']}♥): {r['reply_text'][:400]}"
        )
    examples_block = "\n\n---\n\n".join(sample_lines)

    prompt = f"""You are analyzing {len(corpus)} popular replies scraped from X's "For You"
feed for a builder/founder operating account in the AI niche. The replies were
filtered: each got ≥ 30 likes, posted under high-engagement original tweets.

Your job: produce a tight, actionable pattern report. The reader will use it to
tune the prompt of an LLM that drafts replies to viral AI posts.

OUTPUT — markdown report with these sections, IN ORDER:

## 1. Length & shape
- Median word/char count, common length bands, share of one-liners vs multi-line
- Common structural shapes (one-liner zinger / 2-3 sentence take / list / question / quote-and-react / setup-and-punchline)

## 2. Casing & punctuation
- Lowercase-only vs sentence case vs ALL CAPS share
- Use of ellipses, em-dashes, line breaks
- Capitalization of nouns (brand names, model names)

## 3. Tone & stance
- Approximate share: agree-and-build / disagree-with-reason / sarcastic-zinger /
  joke-or-pun / informational / question-back / personal-anecdote / hijack-the-thread
- Which tones get the MOST likes (cite a few high-like examples)

## 4. Topic relation
- How often does the reply stay on OP's literal topic vs broaden vs hijack?
- Does it usually cite a specific number/anecdote/comparison or stay abstract?

## 5. Style markers used by the top performers
- Specific opener patterns ("the real X is...", "what people miss:", "interesting that...", etc.)
- Specific closers (rhetorical question, call to action, "so...", nothing/dropoff)
- Emoji use, hashtag use, link use, @-mention use

## 6. Distinct ACTIONABLE rules for the reply generator
- 5-10 numbered, concrete, copy-pasteable rules. Each must be specific enough
  to verify ("under 200 chars", "no hashtags ever", "lead with a concrete number
  or a contrarian claim", etc.) — NOT vague platitudes.

## 7. 5 best exemplars
- Pick 5 high-like replies and explain in one line WHY each worked. Show the
  reply text verbatim.

CORPUS (newest 120 examples shown):

{examples_block}
"""
    return _generate._call_claude(
        [{"role": "user", "content": prompt}], max_tokens=4000)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target",          type=int, default=200,
                   help="target number of popular replies to collect")
    p.add_argument("--min-reply-likes", type=int, default=1,
                   help="minimum likes on a reply to consider (default 1 — X reply-level likes are tiny; we use top-K-per-thread ranking, not absolute floor)")
    p.add_argument("--top-per-thread", type=int, default=8,
                   help="take this many highest-liked replies from each thread")
    p.add_argument("--feed-scrolls",    type=int, default=12,
                   help="how many times to scroll For You feed")
    p.add_argument("--no-analyze",      action="store_true",
                   help="just scrape, skip Claude analysis")
    args = p.parse_args()

    ws = _chrome.connect(HUNTER_PORT)
    try:
        _log(f"=== study_popular_replies: target={args.target} replies, "
             f"min={args.min_reply_likes}♥ ===")
        _log("Phase 1: collect feed tweets")
        feed = collect_feed(ws, scrolls=args.feed_scrolls)
        # Keep only threads with >= 10 replies advertised, since otherwise
        # there isn't enough material to mine.
        feed = [t for t in feed if t.get("replies", 0) >= 10]
        _log(f"  feed pool: {len(feed)} candidate threads (≥10 replies each)")

        _log("Phase 2: scrape per-thread replies")
        op_handle_for_url = {t["url"]: t.get("author", "") for t in feed}
        os.makedirs(STATE_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        incremental_path = os.path.join(STATE_DIR,
                                        f"popular_replies_study_{stamp}.json")
        corpus = collect_replies(ws, feed, args.target, args.min_reply_likes,
                                 op_handle_for_url, top_per_thread=args.top_per_thread,
                                 incremental_save_path=incremental_path)
        _log(f"  collected {len(corpus)} popular replies")
    finally:
        try: ws.close()
        except Exception: pass

    if not corpus:
        _log("empty corpus — bailing before analysis")
        return

    # Final save (overwrites incremental snapshot with full corpus + metadata)
    with open(incremental_path, "w") as f:
        json.dump({"collected_at": _ts(), "min_likes": args.min_reply_likes,
                   "n": len(corpus), "replies": corpus}, f, indent=2, ensure_ascii=False)
    _log(f"  saved corpus: {incremental_path}")

    if args.no_analyze:
        return

    _log("Phase 3: Claude pattern analysis")
    report = analyze_corpus(corpus)
    report_path = os.path.join(LOG_DIR, f"analysis_{stamp}.md")
    with open(report_path, "w") as f:
        f.write(f"# Popular replies study — {stamp}\n\n")
        f.write(f"_Corpus: {len(corpus)} replies, min {args.min_reply_likes}♥ each, "
                f"scraped from Hunter's For You feed._\n\n")
        f.write(report)
    _log(f"  saved report: {report_path}")
    print(f"\n=== REPORT ===\n{report}\n=== END ===")


if __name__ == "__main__":
    main()
