"""topic_research — one-shot: given a topic keyword, search X Top-tab for
viral posts about it, generate 1 take per post (rotating across the 3 modes),
push each draft to the @X_autorepost_bot chat for approval.

Triggered by the repost bridge when the user DMs a keyword (or sends a
screenshot — the bridge extracts the topic via Claude vision and calls this).

Usage:
    python3 scripts/topic_research.py --topic "Hermes"
    python3 scripts/topic_research.py --topic "GPT-5"  --max-drafts 5
    python3 scripts/topic_research.py --topic "Mike Krieger" --dry-run
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;       env.load()
import chrome   as _chrome
import telegram as _tg
import generate as _generate
from lock import chrome_lock, file_lock

STATE_DIR     = os.path.join(ROOT_DIR, "state")
QUEUE_PATH    = os.path.join(STATE_DIR, "reply_queue.json")
SEEN_PATH     = os.path.join(STATE_DIR, "topic_research_seen.json")
ROTATION_PATH = os.path.join(STATE_DIR, "topic_research_rotation.json")
LOG_DIR       = os.path.join(ROOT_DIR, "logs", "topic_research")
HUNTER_PORT   = 10000
HUNTER_HANDLE = "GuoHunter95258"
MODES         = ["quote_take", "original_reframe", "counter_take"]
MODE_TO_KIND  = {"quote_take": "quote", "counter_take": "quote",
                 "original_reframe": "original"}


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


def _load(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path) as f: return json.load(f)
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
    return int(n * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2) or ""])


def _age_min_from_iso(iso: str) -> int:
    if not iso: return -1
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
        return int((now - dt).total_seconds() / 60)
    except Exception:
        return -1


def _count_from_instruction(instruction: str, default: int = 1, cap: int = 10) -> int:
    """Parse a leading number out of the operator's instruction so phrases like
    'give me 5 takes' or '3 sarcastic reactions' produce that many variants.
    Falls back to `default` when no count is specified. Capped at `cap`."""
    if not instruction:
        return default
    m = re.search(
        r"\b(\d{1,2})\s*(takes?|variants?|cards?|options?|posts?|reactions?|ideas?|versions?|drafts?)\b",
        instruction.lower())
    if not m:
        return default
    try:
        n = int(m.group(1))
    except ValueError:
        return default
    return max(1, min(cap, n))


def _next_mode() -> str:
    """Rotate across modes so consecutive cards aren't all the same kind."""
    state = _load(ROTATION_PATH, {"i": 0})
    i = int(state.get("i", 0)) % len(MODES)
    state["i"] = (i + 1) % len(MODES)
    _save(ROTATION_PATH, state)
    return MODES[i]


# Reuse ai_news_scout's profile/search JS — same article markup on /search results.
def _search_js() -> str:
    import ai_news_scout as _ans
    return _ans.PROFILE_TWEETS_JS


# JS to extract the single seed tweet from a detail page. The first
# article[data-testid="tweet"] on a status URL is always the target post.
SEED_TWEET_JS = r"""
(function() {
    var el = document.querySelector('article[data-testid="tweet"]');
    if (!el) return '';
    var textEl = el.querySelector('[data-testid="tweetText"]');
    var text = textEl ? textEl.innerText.trim() : '';
    var urlEl = el.querySelector('a[href*="/status/"]');
    var url = urlEl ? urlEl.href : '';
    var idMatch = url.match(/status[/](\d+)/);
    var id = idMatch ? idMatch[1] : '';
    var authorMatch = url.match(/x\.com\/([A-Za-z0-9_]+)\/status\//);
    var author = authorMatch ? authorMatch[1] : '';
    var likeEl = el.querySelector('[data-testid="like"]');
    var likeTxt = likeEl ? likeEl.innerText.replace(/[^0-9KMB.,]/g,'') : '';
    var replyEl = el.querySelector('[data-testid="reply"]');
    var replyTxt = replyEl ? replyEl.innerText.replace(/[^0-9KMB.,]/g,'') : '';
    var timeEl = el.querySelector('time');
    var iso = timeEl ? (timeEl.getAttribute('datetime') || '') : '';
    return JSON.stringify({id:id, url:url, text:text.slice(0,1200), author:author,
                           likesTxt:likeTxt, repliesTxt:replyTxt, iso:iso});
})()
"""


def fetch_seed_tweet(ws, url: str) -> dict:
    """Navigate to a specific tweet URL, extract the seed post fields."""
    with chrome_lock(HUNTER_PORT, on_wait=lambda s: _log(f"  lock wait {s:.0f}s")):
        try:
            _chrome.navigate(ws, url, wait=4.0)
        except Exception as e:
            _log(f"  seed nav failed: {e}")
            return {}
        time.sleep(1.5)
        raw = _chrome.eval_js(ws, SEED_TWEET_JS)
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except Exception:
        return {}
    if not d.get("text") or not d.get("id"):
        return {}
    d["likes"]       = _parse_count(d.get("likesTxt", ""))
    d["replies"]     = _parse_count(d.get("repliesTxt", ""))
    d["age_minutes"] = _age_min_from_iso(d.get("iso", ""))
    return d


def _topic_from_tweet(seed: dict) -> str:
    """Ask Claude to distill a 2-5 word searchable topic from the seed tweet
    so we can find related posts. Falls back to the first 8 words if the call
    fails."""
    txt = (seed.get("text") or "").strip()
    if not txt:
        return ""
    try:
        prompt = (f"Distill the topic of this X post into a 2-5 word searchable "
                  f"keyword/phrase suitable for X search. NOT generic words like "
                  f"'AI' alone. Return ONLY the keyword, no quotes, no preamble.\n\n"
                  f"POST: {txt[:500]}")
        out = _generate._call_claude(
            [{"role": "user", "content": prompt}], max_tokens=80).strip().strip('"')
        return out[:80]
    except Exception:
        return " ".join(txt.split()[:6])


def fetch_topic_posts(ws, topic: str, limit: int = 20) -> list:
    """Search X Top-tab for the topic, scroll a few times to load enough
    candidates, return up to `limit` parsed cards. The Top tab is virtualized
    so cards above the viewport unmount — we accumulate via tweet id."""
    q = urllib.parse.quote_plus(topic)
    url = f"https://x.com/search?q={q}&src=typed_query&f=top"
    seen_ids = {}
    with chrome_lock(HUNTER_PORT, on_wait=lambda s: _log(f"  lock wait {s:.0f}s")):
        try:
            _chrome.navigate(ws, url, wait=4.0)
        except Exception as e:
            _log(f"  search nav failed: {e}")
            return []
        time.sleep(1.5)
        # Scroll 4 times to load ~20-40 results (Top tab loads in waves)
        for i in range(4):
            raw = _chrome.eval_js(ws, _search_js())
            try:
                batch = json.loads(raw) if raw else []
            except Exception:
                batch = []
            for c in batch:
                tid = c.get("id", "")
                if tid and tid not in seen_ids:
                    seen_ids[tid] = c
            _chrome.eval_js(ws, "window.scrollBy(0, 1500)")
            time.sleep(1.8)
    out = list(seen_ids.values())
    for c in out:
        c["likes"]       = _parse_count(c.get("likesTxt", ""))
        c["replies"]     = _parse_count(c.get("repliesTxt", ""))
        c["age_minutes"] = _age_min_from_iso(c.get("iso", ""))
    out.sort(key=lambda x: x.get("likes", 0), reverse=True)
    return out[:limit]


def _build_entry(mode: str, draft: dict, src: dict, topic: str) -> dict:
    kind = MODE_TO_KIND[mode]
    base = {
        "id":              f"topic_{int(time.time())}_{src.get('id','x')[-8:]}",
        "kind":            kind,
        "reply_text":      (draft.get("reply") or "").strip(),
        "op_summary":      draft.get("op_summary", ""),
        "reply_angle":     draft.get("reply_angle", ""),
        "status":          "pending",
        "queued_at":       _ts(),
        "telegram_message_id": 0,
        "news_mode":       mode,
        "topic":           topic,
        "source_handle":   src.get("author", ""),
        "source_url":      src.get("url", ""),
        "source_likes":    src.get("likes", 0),
    }
    if kind == "quote":
        base.update({
            "source":         "topic_research",
            "source_keyword": f"topic:{topic}",
            "target":         src.get("author", ""),
            "target_url":     src.get("url", ""),
            "target_text":    src.get("text", ""),
            "post_likes":     src.get("likes", 0),
            "post_replies":   src.get("replies", 0),
            "post_age_min":   max(0, src.get("age_minutes", 0)),
        })
    else:  # original — reframe
        ctx = (f"Topic: {topic}\nSeen via @{src.get('author','')} ({src.get('likes',0)} likes): "
               f"{(src.get('text') or '')[:400]}\n\nlink: {src.get('url','')}")
        base.update({
            "source":         "topic_research",
            "source_context": ctx,
        })
    return base


def _draft_and_queue(candidates: list, topic: str, max_drafts: int,
                     seen: dict, repost_token: str, repost_chat: str,
                     forced_mode: str = "", dry_run: bool = False) -> int:
    """Run the generator over candidates, push cards. Returns count queued."""
    queued = 0
    for c in candidates:
        if queued >= max_drafts:
            break
        mode = forced_mode or _next_mode()
        try:
            draft = _generate.generate_news_take(
                mode=mode,
                source_handle=c.get("author", ""),
                source_post_text=c.get("text", ""),
                handle=HUNTER_HANDLE, max_chars=280,
            )
        except Exception as e:
            _log(f"    generate failed ({mode}): {e}")
            continue
        txt = (draft.get("reply") or "").strip()
        if not txt or len(txt) < 20:
            _log(f"    weak draft from {mode}; skip")
            continue

        entry = _build_entry(mode, draft, c, topic)
        if dry_run:
            _log(f"  [dry-run] {mode} @{c.get('author','')} ({c.get('likes',0)}♥) "
                 f"→ {txt[:140]}")
            queued += 1
            continue

        with file_lock("reply_queue", on_wait=_log):
            cur = _load(QUEUE_PATH, [])
            cur.append(entry)
            _save(QUEUE_PATH, cur)
        try:
            msg_id = _tg.send_reply_card(entry, bot_token=repost_token,
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

        if c.get("id"):
            seen[c["id"]] = datetime.now().isoformat()
            _save(SEEN_PATH, seen)
        queued += 1
        _log(f"queued {mode} {entry['id']}: @{c.get('author','')} "
             f"({c.get('likes',0)}♥) → {txt[:80]}")
    return queued


def run_from_url_with_instruction(url: str, instruction: str,
                                  max_drafts: int = 1,
                                  dry_run: bool = False) -> int:
    """URL+instruction flow: open the tweet, generate N variants that follow
    the operator's free-text instruction. No mode rotation, no related-post
    mining — instruction provides the focus; variants are just attempts at
    the same goal so the operator can pick the best phrasing."""
    repost_token = os.environ.get("TELEGRAM_BOT_TOKEN_REPOST", "")
    repost_chat  = os.environ.get("TELEGRAM_CHAT_ID_REPOST", "")
    if not repost_token or not repost_chat:
        _log("TELEGRAM_BOT_TOKEN_REPOST / TELEGRAM_CHAT_ID_REPOST not set — bailing")
        return 0

    # Let the operator's instruction override max_drafts when they explicitly
    # say "give me N takes" — capped by the CLI ceiling so we never exceed it.
    parsed_n = _count_from_instruction(instruction, default=max_drafts)
    max_drafts = min(max_drafts if max_drafts > 1 else parsed_n, 10)
    _log(f"topic_research URL+instruction: url={url}  n={max_drafts}")
    _log(f"  instruction: {instruction!r}")
    ws = _chrome.connect(HUNTER_PORT)
    queued = 0
    try:
        seed = fetch_seed_tweet(ws, url)
        if not seed:
            _log("  seed fetch returned nothing — bailing")
            try:
                _tg._call("sendMessage", {
                    "chat_id": repost_chat,
                    "text":    f"_couldn't open that tweet: {url}_",
                    "parse_mode": "Markdown",
                }, bot_token=repost_token)
            except Exception:
                pass
            return 0
        _log(f"  seed: @{seed.get('author','')} ({seed.get('likes',0)}♥) "
             f"{seed.get('text','')[:120]}")

        seen = _load(SEEN_PATH, {})
        for i in range(max_drafts):
            try:
                draft = _generate.generate_custom_take(
                    source_handle=seed.get("author", ""),
                    source_post_text=seed.get("text", ""),
                    instruction=instruction,
                    handle=HUNTER_HANDLE, max_chars=280,
                )
            except Exception as e:
                _log(f"    generate failed: {e}")
                continue
            txt = (draft.get("reply") or "").strip()
            if not txt or len(txt) < 20:
                _log(f"    weak draft #{i+1}; skip")
                continue

            # Build entry — these are quote-kind cards (we have a target tweet)
            # but tagged with the instruction so operator sees the intent.
            entry = _build_entry("quote_take", draft, seed, instruction[:80])
            entry["news_mode"]   = "custom"
            entry["instruction"] = instruction[:300]

            if dry_run:
                _log(f"  [dry-run] variant {i+1}/{max_drafts} → {txt[:140]}")
                queued += 1
                continue

            with file_lock("reply_queue", on_wait=_log):
                cur = _load(QUEUE_PATH, [])
                cur.append(entry)
                _save(QUEUE_PATH, cur)
            try:
                msg_id = _tg.send_reply_card(entry, bot_token=repost_token,
                                             chat_id=repost_chat)
                if msg_id:
                    entry["telegram_message_id"] = msg_id
                    with file_lock("reply_queue", on_wait=_log):
                        cur = _load(QUEUE_PATH, [])
                        for j, e in enumerate(cur):
                            if e.get("id") == entry["id"]:
                                cur[j] = entry
                                _save(QUEUE_PATH, cur)
                                break
            except Exception as e:
                _log(f"    TG send failed: {e}")

            queued += 1
            _log(f"queued variant {i+1}/{max_drafts}: {txt[:80]}")
    finally:
        try: ws.close()
        except Exception: pass
    return queued


def run_from_url(url: str, max_drafts: int = 5, min_likes: int = 1,
                 dry_run: bool = False) -> int:
    """URL flow: open the specific tweet, generate all 3 modes on it (3 cards),
    then take its topic and search for related popular posts to fill out to 5.
    """
    repost_token = os.environ.get("TELEGRAM_BOT_TOKEN_REPOST", "")
    repost_chat  = os.environ.get("TELEGRAM_CHAT_ID_REPOST", "")
    if not repost_token or not repost_chat:
        _log("TELEGRAM_BOT_TOKEN_REPOST / TELEGRAM_CHAT_ID_REPOST not set — bailing")
        return 0

    _log(f"topic_research URL flow: url={url}, max={max_drafts}")
    ws = _chrome.connect(HUNTER_PORT)
    total_queued = 0
    try:
        seed = fetch_seed_tweet(ws, url)
        if not seed:
            _log("  seed fetch returned nothing — bailing")
            try:
                _tg._call("sendMessage", {
                    "chat_id": repost_chat,
                    "text":    f"_couldn't open that tweet — link might be private, "
                               f"deleted, or rate-limited. URL: {url}_",
                    "parse_mode": "Markdown",
                }, bot_token=repost_token)
            except Exception:
                pass
            return 0
        _log(f"  seed: @{seed.get('author','')} ({seed.get('likes',0)}♥) "
             f"{seed.get('text','')[:120]}")

        # Distill topic for related-post search
        topic = _topic_from_tweet(seed)
        _log(f"  derived topic: {topic!r}")

        seen = _load(SEEN_PATH, {})

        # Phase 1: all 3 modes on the seed tweet (up to 3 cards)
        seed_drafts_max = min(3, max_drafts)
        for mode in MODES[:seed_drafts_max]:
            n = _draft_and_queue([seed], topic, 1, seen, repost_token,
                                 repost_chat, forced_mode=mode, dry_run=dry_run)
            total_queued += n

        # Phase 2: fill the rest with related posts found via topic search
        remaining = max_drafts - total_queued
        if remaining > 0 and topic:
            _log(f"  phase 2: search related on topic={topic!r}, need {remaining}")
            cands = fetch_topic_posts(ws, topic, limit=20)
            # Drop the seed itself + already-seen + too-short
            related = []
            seed_id = seed.get("id", "")
            for c in cands:
                tid = c.get("id", "")
                if not tid or tid == seed_id:
                    continue
                if tid in seen:
                    continue
                if c.get("likes", 0) < min_likes:
                    continue
                if len(c.get("text") or "") < 40:
                    continue
                related.append(c)
            related.sort(key=lambda x: x.get("likes", 0), reverse=True)
            related = related[:remaining]
            _log(f"  related found: {len(related)}")
            total_queued += _draft_and_queue(related, topic, remaining, seen,
                                             repost_token, repost_chat,
                                             dry_run=dry_run)
    finally:
        try: ws.close()
        except Exception: pass
    return total_queued


def run_from_topic_with_instruction(topic: str, instruction: str,
                                    max_drafts: int = 1,
                                    min_likes: int = 1,
                                    max_age_min: int = 4320,
                                    dry_run: bool = False) -> int:
    """Keyword + instruction flow: search X for `topic`, take top viral posts,
    apply the operator's free-text instruction to each via generate_custom_take.
    Number of variants = max_drafts; if fewer than that survive the search,
    fill the rest with additional variants on the strongest seed.
    """
    repost_token = os.environ.get("TELEGRAM_BOT_TOKEN_REPOST", "")
    repost_chat  = os.environ.get("TELEGRAM_CHAT_ID_REPOST", "")
    if not repost_token or not repost_chat:
        _log("repost env not set — bailing"); return 0

    parsed_n = _count_from_instruction(instruction, default=max_drafts)
    max_drafts = min(max_drafts if max_drafts > 1 else parsed_n, 10)
    _log(f"topic_research: topic+instruction → topic='{topic}'  n={max_drafts}  "
         f"instr={instruction[:120]!r}")
    ws = _chrome.connect(HUNTER_PORT)
    queued = 0
    try:
        cands = fetch_topic_posts(ws, topic, limit=20)
        eligible = []
        seen = _load(SEEN_PATH, {})
        for c in cands:
            tid = c.get("id", "")
            if not tid or tid in seen: continue
            if c.get("likes", 0) < min_likes: continue
            age = c.get("age_minutes", -1)
            if age >= 0 and age > max_age_min: continue
            if len(c.get("text") or "") < 40: continue
            eligible.append(c)
        eligible.sort(key=lambda x: x.get("likes", 0), reverse=True)
        _log(f"  eligible: {len(eligible)}")

        # Pick targets — distinct posts first, then repeat the strongest if
        # we need more variants than there are good seeds.
        targets = list(eligible[:max_drafts])
        while len(targets) < max_drafts and eligible:
            targets.append(eligible[0])

        if not targets:
            try:
                _tg._call("sendMessage", {
                    "chat_id": repost_chat,
                    "text": f"_no posts found for '{topic}' — try a different keyword?_",
                    "parse_mode": "Markdown",
                }, bot_token=repost_token)
            except Exception: pass
            return 0

        for i, seed in enumerate(targets):
            try:
                draft = _generate.generate_custom_take(
                    source_handle=seed.get("author", ""),
                    source_post_text=seed.get("text", ""),
                    instruction=instruction,
                    handle=HUNTER_HANDLE, max_chars=280,
                )
            except Exception as e:
                _log(f"    generate failed ({i+1}): {e}"); continue
            txt = (draft.get("reply") or "").strip()
            if not txt or len(txt) < 20:
                _log(f"    weak draft #{i+1}; skip"); continue

            entry = _build_entry("quote_take", draft, seed, topic)
            entry["news_mode"]   = "custom"
            entry["instruction"] = instruction[:300]

            if dry_run:
                _log(f"  [dry-run] {i+1}: @{seed.get('author','')} → {txt[:140]}")
                queued += 1; continue

            with file_lock("reply_queue", on_wait=_log):
                cur = _load(QUEUE_PATH, []); cur.append(entry)
                _save(QUEUE_PATH, cur)
            try:
                msg_id = _tg.send_reply_card(entry, bot_token=repost_token,
                                             chat_id=repost_chat)
                if msg_id:
                    entry["telegram_message_id"] = msg_id
                    with file_lock("reply_queue", on_wait=_log):
                        cur = _load(QUEUE_PATH, [])
                        for j, e in enumerate(cur):
                            if e.get("id") == entry["id"]:
                                cur[j] = entry
                                _save(QUEUE_PATH, cur); break
            except Exception as e:
                _log(f"    TG send failed: {e}")
            if seed.get("id"):
                seen[seed["id"]] = datetime.now().isoformat()
                _save(SEEN_PATH, seen)
            queued += 1
            _log(f"queued {i+1}/{max_drafts}: @{seed.get('author','')} → {txt[:80]}")
    finally:
        try: ws.close()
        except Exception: pass
    return queued


def run(topic: str, max_drafts: int = 5, min_likes: int = 1,
        max_age_min: int = 4320, dry_run: bool = False) -> int:
    """Returns the number of cards queued."""
    repost_token = os.environ.get("TELEGRAM_BOT_TOKEN_REPOST", "")
    repost_chat  = os.environ.get("TELEGRAM_CHAT_ID_REPOST", "")
    if not repost_token or not repost_chat:
        _log("TELEGRAM_BOT_TOKEN_REPOST / TELEGRAM_CHAT_ID_REPOST not set — bailing")
        return 0

    _log(f"topic_research: topic='{topic}', max={max_drafts}, "
         f"min_likes={min_likes}, max_age={max_age_min}m")

    ws = _chrome.connect(HUNTER_PORT)
    queued = 0
    try:
        cands = fetch_topic_posts(ws, topic, limit=20)
        _log(f"  fetched {len(cands)} raw candidates")

        # Filter + dedup
        seen = _load(SEEN_PATH, {})
        eligible = []
        for c in cands:
            tid = c.get("id", "")
            if not tid:
                continue
            if tid in seen:
                continue
            if c.get("likes", 0) < min_likes:
                continue
            age = c.get("age_minutes", -1)
            if age >= 0 and age > max_age_min:
                continue
            if len(c.get("text") or "") < 40:
                continue
            eligible.append(c)
        eligible.sort(key=lambda x: x.get("likes", 0), reverse=True)
        eligible = eligible[:max_drafts]
        _log(f"  eligible after filter: {len(eligible)}")

        queued = _draft_and_queue(eligible, topic, max_drafts, seen,
                                  repost_token, repost_chat, dry_run=dry_run)

        if queued == 0:
            # Acknowledge to user that we found nothing — otherwise the bot
            # looks dead after they sent a topic.
            try:
                _tg._call("sendMessage", {
                    "chat_id": repost_chat,
                    "text":    f"_topic '{topic}' returned no viral posts "
                               f"(min {min_likes}♥, last {max_age_min // 60}h). "
                               f"Try a different keyword?_",
                    "parse_mode": "Markdown",
                }, bot_token=repost_token)
            except Exception:
                pass
    finally:
        try: ws.close()
        except Exception: pass
    return queued


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--topic", help="search keyword/phrase")
    g.add_argument("--url",   help="specific tweet URL — research that tweet + related posts")
    p.add_argument("--instruction", default="",
                   help="free-text directive for URL flow ('give me a sarcastic take', 'turn this into a builder reframe'). When set with --url, skips mode rotation and related-post mining; generates N variants on the instruction.")
    p.add_argument("--max-drafts", type=int, default=1,
                   help="upper bound on cards (default 1). When --instruction is set, the script also scans it for a number like '5 takes' and uses that — this flag is the ceiling.")
    p.add_argument("--min-likes",  type=int, default=1,
                   help="floor on OP likes (default 1). X's Top tab already sorts by engagement, so what comes back is already the top of available — the floor mostly skips zero-engagement junk. The keyword itself is the quality signal.")
    p.add_argument("--max-age-min", type=int, default=4320,
                   help="max OP age in minutes (default 4320 = 72h)")
    p.add_argument("--dry-run",    action="store_true")
    args = p.parse_args()
    if args.url:
        if args.instruction.strip():
            n = run_from_url_with_instruction(args.url, args.instruction,
                                              max_drafts=args.max_drafts,
                                              dry_run=args.dry_run)
        else:
            n = run_from_url(args.url, max_drafts=args.max_drafts,
                             min_likes=args.min_likes, dry_run=args.dry_run)
    elif args.instruction.strip():
        n = run_from_topic_with_instruction(args.topic, args.instruction,
                                            max_drafts=args.max_drafts,
                                            min_likes=args.min_likes,
                                            max_age_min=args.max_age_min,
                                            dry_run=args.dry_run)
    else:
        n = run(args.topic, max_drafts=args.max_drafts, min_likes=args.min_likes,
                max_age_min=args.max_age_min, dry_run=args.dry_run)
    print(f"queued: {n}")


if __name__ == "__main__":
    main()
