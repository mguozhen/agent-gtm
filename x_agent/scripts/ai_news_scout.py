"""ai_news_scout — visit a curated list of AI accounts on Hunter's Chrome,
pick the strongest recent posts, draft a reaction in one of three modes
(quote_take / original_reframe / counter_take), and push each draft through
the standard reply_queue.json + Telegram approve/regen/reject flow.

Usage (intended for periodic launchd):
    python3 scripts/ai_news_scout.py
    python3 scripts/ai_news_scout.py --max-drafts 1 --dry-run
    python3 scripts/ai_news_scout.py --mode original_reframe   # force a single mode

Reads the "ai_news_scout" block in engage_config.json. See that block's
comments for filter/mode tuning.
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

import env;       env.load()
import chrome   as _chrome
import telegram as _tg
import generate as _generate
from lock import chrome_lock, file_lock

DEFAULT_CONFIG = os.path.join(ROOT_DIR, "engage_config.json")
STATE_DIR      = os.path.join(ROOT_DIR, "state")
QUEUE_PATH     = os.path.join(STATE_DIR, "reply_queue.json")
SEEN_PATH      = os.path.join(STATE_DIR, "ai_news_seen.json")
ROTATION_PATH  = os.path.join(STATE_DIR, "ai_news_rotation.json")
LOG_DIR        = os.path.join(ROOT_DIR, "logs", "ai_news_scout")

# quote_take / counter_take → 'quote' card; original_reframe → 'original' card.
MODE_TO_KIND = {
    "quote_take":       "quote",
    "counter_take":     "quote",
    "original_reframe": "original",
}


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _parse_count(s: str) -> int:
    if not s: return 0
    s = s.strip().upper().replace(",", "")
    m = re.match(r"^([0-9.]+)\s*([KMB])?$", s)
    if not m: return 0
    n = float(m.group(1))
    suffix = m.group(2) or ""
    return int(n * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix])


def _parse_age_min(rel: str) -> int:
    """Returns age in minutes, or -1 if unparseable. X shows absolute dates
    ('May 14') for posts >24h, which we can't convert without knowing the
    current year — caller treats -1 as 'skip age check' rather than 'ancient'."""
    if not rel: return -1
    m = re.search(r"(\d+)\s*([smhdw])", rel.lower())
    if not m: return -1
    n = int(m.group(1)); u = m.group(2)
    return {"s": 0, "m": n, "h": n*60, "d": n*60*24, "w": n*60*24*7}[u]


# Local profile-timeline scraper. Differs from engage_daemon.SEARCH_RESULTS_JS
# in one critical way: we read time.getAttribute('datetime') (an ISO timestamp
# X always emits, even when the visible text is "May 14") so we can compute
# exact age. The visible-text fallback in the search variant fails on profile
# pages because X switches to absolute dates after 24h.
PROFILE_TWEETS_JS = r"""
(function() {
    var arts = document.querySelectorAll('article[data-testid="tweet"]');
    var out = [];
    arts.forEach(function(el) {
        var head = (el.innerText || '').slice(0, 300);
        if (/Pinned/i.test(head)) return;
        if (/reposted/i.test(head)) return;
        if (/Replying to/i.test(head)) return;
        if (/Promoted/i.test(head)) return;

        var textEl = el.querySelector('[data-testid="tweetText"]');
        var text = textEl ? textEl.innerText.trim() : '';
        if (!text) return;
        var urlEl = el.querySelector('a[href*="/status/"]');
        var url = urlEl ? urlEl.href : '';
        if (!url) return;
        var idMatch = url.match(/status[/](\d+)/);
        var id = idMatch ? idMatch[1] : '';
        var authorMatch = url.match(/x\.com\/([A-Za-z0-9_]+)\/status\//);
        var author = authorMatch ? authorMatch[1] : '';

        var replyEl = el.querySelector('[data-testid="reply"]');
        var replyTxt = replyEl ? replyEl.innerText.replace(/[^0-9KMB.,]/g,'') : '';
        var likeEl = el.querySelector('[data-testid="like"]');
        var likeTxt = likeEl ? likeEl.innerText.replace(/[^0-9KMB.,]/g,'') : '';

        var timeEl = el.querySelector('time');
        var iso = timeEl ? (timeEl.getAttribute('datetime') || '') : '';

        out.push({id:id, url:url, text:text.slice(0,800), author:author,
                  repliesTxt:replyTxt, likesTxt:likeTxt, iso:iso});
    });
    return JSON.stringify(out);
})()
"""

def _age_min_from_iso(iso: str) -> int:
    """Returns age in minutes from an ISO timestamp like '2026-05-16T10:30:00.000Z'.
    Returns -1 if iso is empty/unparseable."""
    if not iso:
        return -1
    try:
        # Normalize 'Z' suffix to '+00:00' for fromisoformat (Python <3.11)
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        from datetime import timezone
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
        return int((now - dt).total_seconds() / 60)
    except Exception:
        return -1


def _fetch_keyword_candidates(ws, port: int, keyword: str, limit: int) -> list:
    """Search X (Latest tab) for the keyword, scrape recent articles. Reuses
    PROFILE_TWEETS_JS because the article markup is the same on search results,
    and we benefit from the same reposted/replying-to filtering + ISO parsing."""
    import urllib.parse
    q = urllib.parse.quote_plus(keyword)
    with chrome_lock(port, on_wait=lambda s: _log(f"  lock wait {s:.0f}s")):
        try:
            # f=top (Top tab) sorts by engagement, surfacing viral content from
            # the last few days regardless of author. f=live (Latest) is the wrong
            # default for a catch-all because it sorts by recency and is overwhelmed
            # by low-engagement junk from anonymous accounts.
            _chrome.navigate(ws, f"https://x.com/search?q={q}&src=typed_query&f=top",
                             wait=4.0)
        except Exception as e:
            _log(f"  search '{keyword}' nav failed: {e}")
            return []
        time.sleep(1.5)
        _chrome.eval_js(ws, "window.scrollBy(0, 600)")
        time.sleep(1.5)
        raw = _chrome.eval_js(ws, PROFILE_TWEETS_JS)
    if not raw:
        return []
    try:
        cands = json.loads(raw)
    except Exception:
        return []
    cleaned = []
    for c in cands:
        # No author-match filtering for search results — any author is valid.
        c["likes"]       = _parse_count(c.get("likesTxt", ""))
        c["replies"]     = _parse_count(c.get("repliesTxt", ""))
        c["age_minutes"] = _age_min_from_iso(c.get("iso", ""))
        c["_via"]        = f"kw:{keyword}"
        cleaned.append(c)
    return cleaned[:limit]


def _fetch_profile_posts(ws, port: int, account: str, limit: int) -> list:
    """Navigate to the account's profile, scrape recent posts. Returns at most
    `limit` cleaned candidates with parsed likes/replies/age."""
    with chrome_lock(port, on_wait=lambda s: _log(f"  lock wait {s:.0f}s")):
        try:
            _chrome.navigate(ws, f"https://x.com/{account}", wait=3.5)
        except Exception as e:
            _log(f"  navigate {account} failed: {e}")
            return []
        # Scroll past the pinned-post height so newer content renders.
        _chrome.eval_js(ws, "window.scrollTo(0, 600)")
        time.sleep(1.5)
        raw = _chrome.eval_js(ws, PROFILE_TWEETS_JS)
    if not raw:
        return []
    try:
        cands = json.loads(raw)
    except Exception:
        return []
    cleaned = []
    for c in cands:
        # The profile page also shows tweets the account RT'd or replied to;
        # the JS already drops "reposted"/"Replying to" headers but X
        # occasionally varies the prefix. Belt-and-suspenders: only keep posts
        # whose URL author matches the profile we requested.
        if (c.get("author") or "").lower() != account.lower():
            continue
        c["likes"]       = _parse_count(c.get("likesTxt", ""))
        c["replies"]     = _parse_count(c.get("repliesTxt", ""))
        c["age_minutes"] = _age_min_from_iso(c.get("iso", ""))
        cleaned.append(c)
    return cleaned[:limit]


def _should_skip(c: dict, filters: dict) -> str:
    if c.get("likes", 0) < filters.get("min_post_likes", 200):
        return f"low_likes:{c.get('likes',0)}"
    age = c.get("age_minutes", -1)
    # With ISO-based parsing, age == -1 only happens when the <time> element
    # is missing entirely — treat that as suspicious, not "fresh".
    if age < 0:
        return "no_timestamp"
    if age > filters.get("max_post_age_minutes", 1440):
        return f"too_old:{age}m"
    if len(c.get("text") or "") < filters.get("min_post_chars", 40):
        return "too_short"
    return ""


def _entry_already_seen(c: dict, seen: dict, window_hours: int) -> bool:
    tid = c.get("id", "")
    if not tid:
        return False
    rec = seen.get(tid)
    if not rec:
        return False
    try:
        when = datetime.fromisoformat(rec)
    except Exception:
        return False
    age_h = (datetime.now() - when).total_seconds() / 3600.0
    return age_h < window_hours


def _pick_modes(forced: str, modes: list, n: int) -> list:
    """Pick the `n` modes to generate for ONE news item. With --mode set,
    always returns [forced]. Otherwise rotates the starting offset across
    runs so when n < len(modes) we still see variety over time."""
    if forced:
        return [forced]
    if not modes or n <= 0:
        return []
    if n >= len(modes):
        return list(modes)
    state = _load(ROTATION_PATH, {"i": 0})
    start = int(state.get("i", 0)) % len(modes)
    state["i"] = (start + n) % len(modes)
    _save(ROTATION_PATH, state)
    return [modes[(start + k) % len(modes)] for k in range(n)]


def _build_entry(mode: str, draft: dict, src: dict, handle: str) -> dict:
    """Construct a reply_queue entry shaped for telegram.send_reply_card.
    Entries with kind='original' don't need target/target_url/target_text but
    we keep source_context populated so the operator sees the news context."""
    kind = MODE_TO_KIND[mode]
    base = {
        "id":             f"news_{int(time.time())}_{src.get('id','x')[-8:]}",
        "kind":           kind,
        "reply_text":     (draft.get("reply") or "").strip(),
        "op_summary":     draft.get("op_summary", ""),
        "reply_angle":    draft.get("reply_angle", ""),
        "status":         "pending",
        "queued_at":      _ts(),
        "telegram_message_id": 0,
        "news_mode":      mode,
        "news_source":    src.get("author", ""),
        "news_url":       src.get("url", ""),
        "news_likes":     src.get("likes", 0),
    }
    if kind == "quote":
        base.update({
            "source":         "ai_news",
            "source_keyword": f"news/@{src.get('author','')}",
            "target":         src.get("author", ""),
            "target_url":     src.get("url", ""),
            "target_text":    src.get("text", ""),
            "post_likes":     src.get("likes", 0),
            "post_replies":   src.get("replies", 0),
            "post_age_min":   src.get("age_minutes", 0),
        })
    else:  # original — reframe
        ctx = (f"News by @{src.get('author','')} ({src.get('likes',0)} likes): "
               f"{(src.get('text') or '')[:400]}\n\nlink: {src.get('url','')}")
        base.update({
            "source":         "ai_news",
            "source_context": ctx,
        })
    return base


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config",     default=DEFAULT_CONFIG)
    p.add_argument("--max-drafts", type=int, default=0,
                   help="override ai_news_scout.max_drafts_per_run")
    p.add_argument("--mode",       default="",
                   choices=["", "quote_take", "original_reframe", "counter_take"],
                   help="force a single mode for this run (skips rotation)")
    p.add_argument("--dry-run",    action="store_true")
    args = p.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)
    ncfg = cfg.get("ai_news_scout", {})
    if not ncfg.get("enabled", True):
        _log("ai_news_scout disabled in config; exit")
        return

    port      = cfg["hunter_port"]
    handle    = cfg["hunter_handle"]
    accounts  = ncfg.get("accounts", [])
    if not accounts:
        _log("no ai_news_scout.accounts configured; exit")
        return

    accts_per_run   = ncfg.get("accounts_per_run", 6)
    posts_per_acct  = ncfg.get("posts_per_account", 3)
    filters         = ncfg.get("filters", {})
    modes           = ncfg.get("variant_modes",
                               ["quote_take", "original_reframe", "counter_take"])
    variants_per    = ncfg.get("variants_per_item", 1)
    max_drafts      = args.max_drafts or ncfg.get("max_drafts_per_run", 2)
    dedup_h         = ncfg.get("dedup_window_hours", 72)
    max_chars       = (cfg.get("generation", {}) or {}).get("max_post_chars", 280)

    chosen = random.sample(accounts, min(accts_per_run, len(accounts)))
    _log(f"ai_news_scout run — handle={handle}, accounts={chosen}, "
         f"max_drafts={max_drafts}, modes={modes}, "
         f"min_likes={filters.get('min_post_likes', 200)}")

    seen = _load(SEEN_PATH, {})
    ws   = _chrome.connect(port)
    drafted = 0
    try:
        # --- Source 1: curated commentator accounts ---
        account_cands = []
        for acct in chosen:
            _log(f"  scrape: @{acct}")
            try:
                posts = _fetch_profile_posts(ws, port, acct, posts_per_acct)
            except Exception as e:
                _log(f"    fetch error: {e}")
                continue
            _log(f"    got {len(posts)} candidate posts")
            for p in posts:
                p["_via"] = f"acct:@{acct}"
            account_cands.extend(posts)
            time.sleep(random.uniform(2.0, 4.0))

        # --- Source 2: keyword catch-all (viral posts regardless of author) ---
        kw_cfg   = ncfg.get("keyword_catchall", {})
        kw_cands = []
        if kw_cfg.get("enabled", False):
            kw_list  = kw_cfg.get("keywords", [])
            kw_per   = kw_cfg.get("keywords_per_run", 4)
            kw_limit = kw_cfg.get("results_per_keyword", 8)
            chosen_kws = random.sample(kw_list, min(kw_per, len(kw_list)))
            _log(f"  keyword catch-all: {chosen_kws}")
            for kw in chosen_kws:
                _log(f"    search: {kw}")
                try:
                    cs = _fetch_keyword_candidates(ws, port, kw, kw_limit)
                except Exception as e:
                    _log(f"      fetch error: {e}")
                    continue
                _log(f"      got {len(cs)} candidates")
                kw_cands.extend(cs)
                time.sleep(random.uniform(2.0, 4.0))

        # Filter each pool with its own thresholds (kw floor is stricter), then
        # merge — dedup by tweet id (accounts win in a tie via list order).
        kw_filters = kw_cfg.get("filters", {}) if kw_cfg.get("enabled") else {}
        seen_ids = set()
        all_cands = []
        for c in account_cands:
            why = _should_skip(c, filters)
            if why:
                _log(f"    skip {c.get('_via','?')} (acct) @{c.get('author','?')} "
                     f"({c.get('likes',0)}♥): {why}")
                continue
            if c.get("id") in seen_ids:
                continue
            seen_ids.add(c.get("id"))
            all_cands.append(c)
        for c in kw_cands:
            if c.get("id") in seen_ids:
                continue
            why = _should_skip(c, kw_filters) if kw_filters else _should_skip(c, filters)
            if why:
                _log(f"    skip {c.get('_via','?')} @{c.get('author','?')} "
                     f"({c.get('likes',0)}♥): {why}")
                continue
            seen_ids.add(c.get("id"))
            all_cands.append(c)
        _log(f"  pool: {len(account_cands)} from accounts + {len(kw_cands)} from "
             f"keywords → {len(all_cands)} after filter/dedup")

        # Strongest first — top likes have the best shot at being "news".
        all_cands.sort(key=lambda x: x.get("likes", 0), reverse=True)

        for c in all_cands:
            if drafted >= max_drafts:
                break
            if _entry_already_seen(c, seen, dedup_h):
                _log(f"    skip @{c.get('author','?')}: dedup_seen")
                continue

            picked = _pick_modes(args.mode, modes, variants_per)
            for mode in picked:
                if drafted >= max_drafts:
                    break
                try:
                    draft = _generate.generate_news_take(
                        mode=mode,
                        source_handle=c.get("author", ""),
                        source_post_text=c.get("text", ""),
                        handle=handle, max_chars=max_chars,
                    )
                except Exception as e:
                    _log(f"    generate failed ({mode}): {e}")
                    continue
                txt = (draft.get("reply") or "").strip()
                if not txt or len(txt) < 20:
                    _log(f"    weak draft from {mode}; skip")
                    continue

                entry = _build_entry(mode, draft, c, handle)
                if args.dry_run:
                    _log(f"  [dry-run] {mode} on @{c.get('author','')} "
                         f"({c.get('likes',0)} likes) → {txt[:140]}")
                    drafted += 1
                    continue

                try:
                    import reply_scorer as _rs
                    _rs.score_entry(entry)
                except Exception as _e:
                    _log(f"  scorer error (non-fatal): {_e}")

                with file_lock("reply_queue", on_wait=_log):
                    cur = _load(QUEUE_PATH, [])
                    cur.append(entry)
                    _save(QUEUE_PATH, cur)
                try:
                    # Route repost-flow drafts to the dedicated repost bot+chat
                    # (X_autorepost_bot DM) so they don't mix with reply cards
                    # in the main x-agent chat. Falls back to defaults if env
                    # vars aren't set.
                    repost_token = os.environ.get("TELEGRAM_BOT_TOKEN_REPOST", "")
                    repost_chat  = os.environ.get("TELEGRAM_CHAT_ID_REPOST", "")
                    msg_id = _tg.send_reply_card(entry,
                                                 bot_token=repost_token,
                                                 chat_id=repost_chat)
                    if msg_id:
                        entry["telegram_message_id"] = msg_id
                        with file_lock("reply_queue", on_wait=_log):
                            cur = _load(QUEUE_PATH, [])
                            for i, e in enumerate(cur):
                                if e.get("id") == entry["id"]:
                                    cur[i] = entry
                                    _save(QUEUE_PATH, cur)
                                    break
                except Exception as e:
                    _log(f"    TG send failed: {e}")

                drafted += 1
                _log(f"queued {mode} {entry['id']}: @{c.get('author','')} "
                     f"({c.get('likes',0)}♥) → {txt[:80]}")

            seen[c.get("id", "")] = datetime.now().isoformat()
            _save(SEEN_PATH, seen)

        if drafted == 0:
            _log("no qualifying news items; nothing queued")
    finally:
        try: ws.close()
        except Exception: pass


if __name__ == "__main__":
    main()
