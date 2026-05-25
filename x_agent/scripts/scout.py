"""
Scout — find candidate reply-target accounts in the 5K–30K follower band
for Hunter's reply queue.

Reads scout_config.json (keywords + seed accounts), drives Hunter's Chrome
(port 10003) through X search + profile pages, scores each candidate against
a partial rubric, and writes a ranked CSV to state/scout_candidates.csv.

Three phases:
  1. search    keyword → tweets → unique authors  (+ seed accounts injected)
  2. profile   visit each author profile, pull follower count + last ~10 tweets
  3. score     apply size + reply-density + cadence sub-scores
               (OP-engages-with-replies stays for manual eyeballing)

Usage:
    python3 scout.py                          # full funnel
    python3 scout.py --phase=search           # only run keyword searches
    python3 scout.py --phase=profile          # only profile-screen existing authors
    python3 scout.py --limit=15               # cap search results per keyword
    python3 scout.py --max-authors=120        # cap profile screen pool
    python3 scout.py --config path/to/x.json  # use an alternate config
"""
import argparse
import csv
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
import fetch  as _fetch
import login  as _login
from lock import chrome_lock

DEFAULT_CONFIG = os.path.join(ROOT_DIR, "scout_config.json")
STATE_DIR      = os.path.join(ROOT_DIR, "state")
LOG_DIR        = os.path.join(ROOT_DIR, "logs", "scout")
HUNTER_HANDLE  = "GuoHunter95258"
HUNTER_PORT    = 10000  # overridable via scout_config.json "hunter_port"

DISCOVERY_MIN_LIKES   = 5      # tweets with fewer likes at search time → drop the author
PROFILE_RETRY_SLEEP   = (2, 4) # short retry — catches render races; rate-limit needs the bailout below
CONSECUTIVE_EMPTY_BAIL = 3     # this many empties in a row after some successes → assume rate-limited, stop phase 2


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
    """'12.4K' → 12400, '1.2M' → 1200000, '1,234' → 1234, '' → 0"""
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

def _state_path(name: str) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, name)

def _ensure_hunter_chrome() -> bool:
    if _chrome.ping(HUNTER_PORT):
        return True
    _log(f"chrome on port {HUNTER_PORT} ({HUNTER_HANDLE}) down — relaunching")
    profile_dir = os.path.join(ROOT_DIR, "chrome-profiles", HUNTER_HANDLE)
    if not os.path.exists(profile_dir):
        _log(f"  no profile dir at {profile_dir}")
        return False
    try:
        _login.launch_chrome(HUNTER_PORT, profile_dir)
        _login.ensure_page_tab(HUNTER_PORT)
        return _chrome.ping(HUNTER_PORT)
    except Exception as e:
        _log(f"  relaunch failed: {e}")
        return False

def _extract_handle(author_text: str, url: str) -> str:
    m = re.search(r"x\.com/([A-Za-z0-9_]+)/status/", url or "")
    if m:
        return m.group(1)
    m = re.search(r"@([A-Za-z0-9_]+)", author_text or "")
    return m.group(1) if m else ""


# ── phase 1: keyword search → authors ─────────────────────────────────────────

def collect_authors(config: dict, limit_per_kw: int) -> dict:
    """Returns {handle: {handle, seen_in: [..], sample_urls: [..]}}."""
    authors: dict = {}

    for cluster, kws in config["keyword_clusters"].items():
        for kw in kws:
            for mode in ("top", "live"):
                _log(f"search [{cluster}/{mode}] {kw}")
                try:
                    tweets = _fetch.search(HUNTER_PORT, kw, mode=mode, limit=limit_per_kw)
                except Exception as e:
                    _log(f"  search error: {e}")
                    continue
                for t in tweets:
                    # Fix 3: drop low-engagement tweets at discovery time. A tweet
                    # with <5 likes is almost certainly noise (spam/crypto bots
                    # that happened to use the keyword) — skip its author.
                    if t.get("likes", 0) < DISCOVERY_MIN_LIKES:
                        continue
                    handle = _extract_handle(t.get("author", ""), t.get("url", ""))
                    if not handle or handle.lower() == HUNTER_HANDLE.lower():
                        continue
                    entry = authors.setdefault(handle, {
                        "handle": handle, "seen_in": set(), "sample_urls": [],
                    })
                    entry["seen_in"].add(f"{cluster}:{kw[:40]}")
                    if len(entry["sample_urls"]) < 3 and t.get("url"):
                        entry["sample_urls"].append(t["url"])
                time.sleep(random.uniform(3, 6))

    for raw in config.get("seed_accounts", []):
        h = raw.lstrip("@")
        entry = authors.setdefault(h, {"handle": h, "seen_in": set(), "sample_urls": []})
        entry["seen_in"].add("seed")

    return authors


# ── phase 2: profile screen ───────────────────────────────────────────────────

PROFILE_TWEETS_JS = r"""
(function() {
    var articles = document.querySelectorAll('article[data-testid="tweet"]');
    var out = [];
    articles.forEach(function(el) {
        try {
            var textEl  = el.querySelector('[data-testid="tweetText"]');
            var text    = textEl ? textEl.innerText.trim() : '';
            var urlEl   = el.querySelector('a[href*="/status/"]');
            var url     = urlEl ? urlEl.href : '';
            var replyEl = el.querySelector('[data-testid="reply"]');
            var likeEl  = el.querySelector('[data-testid="like"]');
            var rtEl    = el.querySelector('[data-testid="retweet"]');
            var replyTxt = replyEl ? replyEl.innerText.replace(/[^0-9KMB.,]/g,'') : '';
            var likeTxt  = likeEl  ? likeEl.innerText.replace(/[^0-9KMB.,]/g,'')  : '';
            var rtTxt    = rtEl    ? rtEl.innerText.replace(/[^0-9KMB.,]/g,'')    : '';
            if (url) out.push({url: url, text: text.slice(0, 200),
                               replies: replyTxt, likes: likeTxt, retweets: rtTxt});
        } catch(e) {}
    });
    return JSON.stringify(out);
})()
"""

FOLLOWERS_JS = r"""
(function() {
    // Try both the strict-suffix selector and the broader contains selector.
    // X's profile header sometimes splits "12.4K" and "Followers" across nested
    // spans, so we also fall back to the link's parent innerText.
    var sel = 'a[href$="/followers"], a[href$="/verified_followers"], a[href*="/followers"]';
    var links = document.querySelectorAll(sel);
    for (var i = 0; i < links.length; i++) {
        var a = links[i];
        var direct = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim();
        var parent = a.parentElement ? a.parentElement.innerText.replace(/\s+/g, ' ').trim() : '';
        var combined = direct + ' | ' + parent;
        var m = combined.match(/([0-9][0-9,.]*[KMB]?)\s*Followers?/i);
        if (m) return m[1];
    }
    return '';
})()
"""

def _fetch_profile_meta_once(ws, handle: str) -> dict:
    """Navigate + scroll + extract. Returns whatever we got (may be empty)."""
    with chrome_lock(HUNTER_PORT, on_wait=_log):
        _chrome.navigate(ws, f"https://x.com/{handle}", wait=3.5)
        # Fix 1: scroll once so the profile timeline lazy-loads ~10 tweets
        # rather than just the pinned + first real tweet (which gave us
        # tweets_fetched ≈ 2 in the sample run).
        _chrome.scroll_down(ws, px=1500)
        time.sleep(1.5)

        raw_followers = _chrome.eval_js(ws, FOLLOWERS_JS)
        raw_tweets    = _chrome.eval_js(ws, PROFILE_TWEETS_JS)

    followers = _parse_count(raw_followers)
    try:
        tweets = json.loads(raw_tweets) if raw_tweets else []
    except Exception:
        tweets = []

    for t in tweets:
        t["replies_n"]  = _parse_count(t.get("replies", ""))
        t["likes_n"]    = _parse_count(t.get("likes", ""))
        t["retweets_n"] = _parse_count(t.get("retweets", ""))

    sample = tweets[:10]
    avg = lambda key: (sum(t[key] for t in sample) / len(sample)) if sample else 0.0

    return {
        "handle":          handle,
        "followers":       followers,
        "followers_raw":   raw_followers,
        "tweets_fetched":  len(sample),
        "avg_replies":     avg("replies_n"),
        "avg_likes":       avg("likes_n"),
        "avg_retweets":    avg("retweets_n"),
    }


def fetch_profile_meta(ws, handle: str) -> dict:
    """Wrapper with one retry on empty result (handles transient rate-limit
    or slow page render — the seed cluster in the first sample all came back
    empty in a row, suggesting X throttled the session)."""
    meta = _fetch_profile_meta_once(ws, handle)
    if meta["followers"] == 0 and meta["tweets_fetched"] == 0:
        delay = random.uniform(*PROFILE_RETRY_SLEEP)
        _log(f"    empty — retry after {delay:.0f}s")
        time.sleep(delay)
        meta = _fetch_profile_meta_once(ws, handle)
    return meta


# ── phase 3: score ────────────────────────────────────────────────────────────

def score(meta: dict, fmin: int, fmax: int) -> dict:
    f = meta["followers"]
    if   fmin <= f <= fmax:     size = 5
    elif f > fmax and f <= fmax * 3:   size = 3
    elif f > fmax * 3 and f <= fmax * 10: size = 2
    elif f > fmax * 10:         size = 1
    elif f >= fmin // 2:        size = 3
    elif f >= fmin // 5:        size = 2
    else:                       size = 1

    r = meta["avg_replies"]
    if   20 <= r <= 80:                       reply = 5
    elif (10 <= r < 20) or (80 < r <= 150):   reply = 4
    elif (5  <= r < 10) or (150 < r <= 300):  reply = 3
    elif r > 0:                                reply = 2
    else:                                      reply = 1

    cadence = 5 if meta["tweets_fetched"] >= 5 else (3 if meta["tweets_fetched"] >= 2 else 1)

    # rubric weights: size×3 + reply×3 + cadence×1 = 35 max (OP-engages×3 left for manual → +15 → 50)
    partial = size * 3 + reply * 3 + cadence * 1
    return {**meta, "size_score": size, "reply_score": reply,
            "cadence_score": cadence, "partial_score": partial, "partial_max": 35}


# ── orchestration ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["search", "profile", "all"], default="all")
    parser.add_argument("--limit", type=int, default=20, help="search results per keyword")
    parser.add_argument("--max-authors", type=int, default=200,
                        help="cap profile screen pool (after dedupe)")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    global HUNTER_PORT
    HUNTER_PORT = config.get("hunter_port", HUNTER_PORT)
    fmin = config.get("follower_min", 5000)
    fmax = config.get("follower_max", 30000)
    _log(f"config: {args.config} (band {fmin}–{fmax}, "
         f"{sum(len(v) for v in config['keyword_clusters'].values())} keywords, "
         f"{len(config.get('seed_accounts', []))} seeds)")

    if not _ensure_hunter_chrome():
        _log("hunter chrome unavailable — abort")
        sys.exit(1)

    authors_path    = _state_path("scout_authors.json")
    candidates_path = _state_path("scout_candidates.csv")

    # ── phase 1 ────────────────────────────────────────────────────────────
    if args.phase in ("search", "all"):
        _log("phase 1: keyword search")
        authors = collect_authors(config, limit_per_kw=args.limit)
        serialized = {h: {**v, "seen_in": sorted(v["seen_in"])} for h, v in authors.items()}
        with open(authors_path, "w") as f:
            json.dump(serialized, f, indent=2)
        _log(f"phase 1 done — {len(serialized)} unique authors → {authors_path}")
        if args.phase == "search":
            return
    else:
        with open(authors_path) as f:
            serialized = json.load(f)
        _log(f"loaded {len(serialized)} authors from {authors_path}")

    # ── phase 2 + 3 ────────────────────────────────────────────────────────
    handles = list(serialized.keys())[: args.max_authors]
    _log(f"phase 2: profile screen ({len(handles)} authors)")

    ws = _chrome.connect(HUNTER_PORT)
    rows = []
    consecutive_empty = 0
    successes_so_far  = 0
    try:
        for i, handle in enumerate(handles, 1):
            _log(f"  [{i}/{len(handles)}] @{handle}")
            try:
                meta = fetch_profile_meta(ws, handle)
            except Exception as e:
                _log(f"    error: {e}")
                continue
            if meta["followers"] == 0 and meta["tweets_fetched"] == 0:
                _log(f"    empty/unreachable — skipping")
                consecutive_empty += 1
                if successes_so_far >= 5 and consecutive_empty >= CONSECUTIVE_EMPTY_BAIL:
                    _log(f"    {CONSECUTIVE_EMPTY_BAIL} empties in a row after {successes_so_far} successes — "
                         f"assuming X rate-limited the session; bailing out of phase 2 "
                         f"({len(handles)-i} authors unprocessed)")
                    break
                continue
            consecutive_empty = 0
            successes_so_far += 1
            if meta["followers"] > fmax * 30:
                _log(f"    {meta['followers_raw']} too big — drop")
                continue
            scored = score(meta, fmin, fmax)
            scored["seen_in"] = ", ".join(serialized[handle].get("seen_in", []))[:200]
            rows.append(scored)
            time.sleep(random.uniform(3, 6))
    finally:
        ws.close()

    rows.sort(key=lambda r: (r["partial_score"], r["avg_replies"]), reverse=True)

    with open(candidates_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "handle", "followers", "followers_raw", "in_band",
            "avg_replies", "avg_likes", "avg_retweets", "tweets_fetched",
            "size_score", "reply_score", "cadence_score",
            "partial_score", "partial_max", "seen_in",
        ])
        for r in rows:
            in_band = fmin <= r["followers"] <= fmax
            w.writerow([
                r["handle"], r["followers"], r["followers_raw"],
                "YES" if in_band else "no",
                round(r["avg_replies"], 1), round(r["avg_likes"], 1),
                round(r["avg_retweets"], 1), r["tweets_fetched"],
                r["size_score"], r["reply_score"], r["cadence_score"],
                r["partial_score"], r["partial_max"], r["seen_in"],
            ])

    _log(f"done — {len(rows)} scored → {candidates_path}")

    in_band = [r for r in rows if fmin <= r["followers"] <= fmax][:15]
    _log(f"top {len(in_band)} in-band candidates:")
    for r in in_band:
        _log(f"  @{r['handle']:<28} {r['followers_raw']:>7}  "
             f"replies~{r['avg_replies']:>5.1f}  score {r['partial_score']}/{r['partial_max']}")
    _log("note: OP-engages-with-replies (rubric x3) is unscored — eyeball top 15 before locking in")


if __name__ == "__main__":
    main()
