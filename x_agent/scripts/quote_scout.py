"""
quote_scout — one-shot: search X for high-engagement posts on configured
keywords, draft 1-3 quote-tweets in the operator's voice, push them through
the standard reply_queue + Telegram approve/regen/reject flow.

Usage (intended for periodic launchd):
    python3 scripts/quote_scout.py
    python3 scripts/quote_scout.py --max-drafts 2 --dry-run

Reads engage_config.json. Honors a "quote_scout" config block:
  {
    "enabled":          true,
    "keywords":         ["..."]           # falls back to keyword_engage.keywords
    "keywords_per_run": 3,
    "results_per_keyword": 8,
    "filters": {
        "min_post_likes":      50,
        "max_post_age_minutes": 360,
        "skip_targets":         true
    },
    "max_drafts_per_run": 2,
    "dedup_window_hours": 48
  }

Default filters are stricter than the engage_daemon's keyword sweep — a quote
tweet costs more on Hunter's audience than a reply, so we only quote posts
with real traction.
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
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
SEEN_PATH      = os.path.join(STATE_DIR, "quote_scout_seen.json")
LOG_DIR        = os.path.join(ROOT_DIR, "logs", "quote_scout")

# Reuse the engage_daemon's search-results JS by importing it lazily — keeps
# the parsing logic in one place.
def _search_js():
    import engage_daemon as _ed
    return _ed.SEARCH_RESULTS_JS


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


def _parse_age_min(dt_str: str) -> int:
    if not dt_str: return 999
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return max(0, int(delta.total_seconds() / 60))
    except Exception:
        return 999


def _fetch_candidates(ws, port: int, keyword: str, limit: int) -> list:
    q = urllib.parse.quote_plus(keyword)
    with chrome_lock(port, on_wait=lambda s: _log(f"  lock wait {s:.0f}s")):
        _chrome.navigate(ws, f"https://x.com/search?q={q}&src=typed_query&f=live",
                         wait=4.0)
        time.sleep(1.5)
        _chrome.eval_js(ws, "window.scrollBy(0, 600)")
        time.sleep(1.5)
        raw = _chrome.eval_js(ws, _search_js())
    if not raw:
        return []
    try:
        cands = json.loads(raw)
    except Exception:
        return []
    for c in cands:
        c["replies"]     = _parse_count(c.get("repliesTxt", ""))
        c["likes"]       = _parse_count(c.get("likesTxt", ""))
        c["age_minutes"] = _parse_age_min(c.get("datetime", ""))
    return cands[:limit]


def _should_skip(c: dict, filters: dict, target_set: set) -> str:
    if c.get("likes", 0) < filters.get("min_post_likes", 50):
        return f"low_likes:{c.get('likes',0)}"
    if c.get("age_minutes", 999) > filters.get("max_post_age_minutes", 360):
        return f"too_old:{c.get('age_minutes',0)}m"
    if filters.get("skip_targets", True):
        author = (c.get("author") or "").lstrip("@").lower()
        if author in target_set:
            return f"is_target:{author}"
    if len((c.get("text") or "")) < 30:
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",       default=DEFAULT_CONFIG)
    parser.add_argument("--max-drafts",   type=int, default=0,
                        help="override quote_scout.max_drafts_per_run")
    parser.add_argument("--dry-run",      action="store_true")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    qcfg = cfg.get("quote_scout", {})
    if not qcfg.get("enabled", True):
        _log("quote_scout disabled in config; exit")
        return

    port      = cfg["hunter_port"]
    handle    = cfg["hunter_handle"]
    keywords  = qcfg.get("keywords") or cfg.get("keyword_engage", {}).get("keywords", [])
    if not keywords:
        _log("no keywords configured; exit")
        return

    kw_per_run = qcfg.get("keywords_per_run", 3)
    per_kw     = qcfg.get("results_per_keyword", 8)
    filters    = qcfg.get("filters", {})
    max_drafts = args.max_drafts or qcfg.get("max_drafts_per_run", 2)
    dedup_h    = qcfg.get("dedup_window_hours", 48)
    max_chars  = (cfg.get("generation", {}) or {}).get("max_post_chars", 280)

    tg_cfg     = cfg.get("telegram", {})
    tg_token   = os.environ.get(tg_cfg.get("bot_token_env", ""), "") if tg_cfg else ""
    tg_chat_id = os.environ.get(tg_cfg.get("chat_id_env", ""), "") if tg_cfg else ""

    target_set = {t.lower() for t in cfg.get("target_accounts", [])}

    chosen_kws = random.sample(keywords, min(kw_per_run, len(keywords)))
    _log(f"quote_scout run — handle={handle}, kws={chosen_kws}, "
         f"max_drafts={max_drafts}, min_likes={filters.get('min_post_likes', 50)}")

    seen = _load(SEEN_PATH, {})
    ws   = _chrome.connect(port)

    drafted = 0
    try:
        all_cands = []
        for kw in chosen_kws:
            if drafted >= max_drafts:
                break
            _log(f"  search: {kw}")
            try:
                cs = _fetch_candidates(ws, port, kw, per_kw)
            except Exception as e:
                _log(f"    fetch error: {e}")
                continue
            for c in cs:
                c["_kw"] = kw
            all_cands.extend(cs)
            time.sleep(random.uniform(2.0, 4.0))

        # Rank by likes (desc) — quote-tweet only the highest-traction posts
        all_cands.sort(key=lambda x: x.get("likes", 0), reverse=True)

        for c in all_cands:
            if drafted >= max_drafts:
                break
            why = _should_skip(c, filters, target_set)
            if why:
                continue
            if _entry_already_seen(c, seen, dedup_h):
                continue
            try:
                g = _generate.generate_quote_tweet(
                    target_handle=(c.get("author") or "").lstrip("@"),
                    target_post_text=c.get("text", ""),
                    handle=handle, max_chars=max_chars,
                )
            except Exception as e:
                _log(f"    generate failed: {e}")
                continue
            qt = (g.get("reply") or "").strip()
            if not qt or len(qt) < 20:
                _log("    generator returned weak/empty draft; skip")
                continue

            entry = {
                "id":             f"qt_{int(time.time())}_{c.get('id','x')[-8:]}",
                "kind":           "quote",
                "source":         "keyword",
                "source_keyword": c.get("_kw", ""),
                "target":         (c.get("author") or "").lstrip("@"),
                "target_url":     c.get("url", ""),
                "target_text":    c.get("text", ""),
                "post_likes":     c.get("likes", 0),
                "post_replies":   c.get("replies", 0),
                "post_age_min":   c.get("age_minutes", 0),
                "reply_text":     qt,
                "op_summary":     g.get("op_summary", ""),
                "reply_angle":    g.get("reply_angle", ""),
                "status":         "pending",
                "queued_at":      _ts(),
                "telegram_message_id": 0,
            }
            if args.dry_run:
                _log(f"  [dry-run] @{entry['target']} ({entry['post_likes']} likes) → "
                     f"{qt[:120]}")
                drafted += 1
                continue
            try:
                import reply_scorer as _rs
                _rs.score_entry(entry)
            except Exception as _e:
                _log(f"  scorer error (non-fatal): {_e}")
            # Atomic append under file lock to avoid clobbering concurrent
            # writers (engage_daemon, buildlog_drafts, telegram_bridge).
            with file_lock("reply_queue", on_wait=_log):
                cur = _load(QUEUE_PATH, [])
                cur.append(entry)
                _save(QUEUE_PATH, cur)
            try:
                msg_id = _tg.send_reply_card(entry, bot_token=tg_token, chat_id=tg_chat_id)
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
            seen[c.get("id", "")] = datetime.now().isoformat()
            _save(SEEN_PATH, seen)
            drafted += 1
            _log(f"queued QT {entry['id']}: @{entry['target']} ({entry['post_likes']}♥) → "
                 f"{qt[:80]}")

        if drafted == 0:
            _log("no qualifying candidates; nothing queued")
    finally:
        try: ws.close()
        except Exception: pass


if __name__ == "__main__":
    main()
