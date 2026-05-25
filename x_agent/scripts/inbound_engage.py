#!/usr/bin/env python3
"""
Inbound engage worker:
- Finds fresh tweets that reply to our account (`to:<handle> -from:<handle>`)
- Likes each safe engagement
- Replies back automatically
- Ignores hostile/prompt-injection style text
- Security policy: only the human owner can authorize any folder/config/code changes.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env
env.load()

import fetch
import engage
import generate
import chrome
from lock import file_lock

STATE_DIR = os.path.join(ROOT_DIR, "state")
LOG_DIR = os.path.join(ROOT_DIR, "logs")
LIBRARY_PATH = os.path.join(STATE_DIR, "winning_replies.json")

HOSTILE_PATTERNS = [
    "ai erase everything",
    "erase everything",
    "delete everything",
    "ignore previous",
    "ignore all previous",
    "ignore earlier instructions",
    "disregard previous",
    "disregard prior instructions",
    "forget prior instructions",
    "override instructions",
    "modify folder",
    "change folder",
    "edit folder",
    "update config",
    "change config",
    "edit config",
    "modify files",
    "change files",
    "jailbreak",
    "system prompt",
    "developer prompt",
    "reveal prompt",
    "leak prompt",
    "drop table",
    "rm -rf",
]

HOSTILE_REGEXES = [
    re.compile(r"\bignore\b.{0,40}\b(instruction|prompt|rule)s?\b", re.I),
    re.compile(r"\b(disregard|forget|override)\b.{0,40}\b(instruction|prompt|rule)s?\b", re.I),
    re.compile(r"\b(reveal|show|leak|print|dump)\b.{0,40}\b(system|developer|hidden)\b.{0,30}\bprompt\b", re.I),
    re.compile(r"\b(modify|change|edit|update)\b.{0,40}\b(folder|directory|config|file|files|code)\b", re.I),
    re.compile(r"\b(ai|assistant|bot)\b.{0,20}\b(erase|delete|wipe)\b.{0,20}\b(everything|all)\b", re.I),
    re.compile(r"\brm\s*-\s*rf\b", re.I),
    re.compile(r"\bdrop\s+table\b", re.I),
]


def _normalize_text(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("0", "o").replace("1", "i").replace("3", "e").replace("4", "a").replace("5", "s").replace("7", "t")
    t = re.sub(r"[\W_]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str, handle: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    d = os.path.join(LOG_DIR, f"inbound_{handle.lower()}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _claim_seen_id(seen_path: str, lock_name: str, cid: str) -> bool:
    """Atomically reserve a candidate id across concurrent runs.
    Returns True if this run claimed it, False if already claimed before."""
    with file_lock(lock_name):
        seen = _load_json(seen_path, {"ids": []})
        ids = set(seen.get("ids", []))
        if cid in ids:
            return False
        ids.add(cid)
        ids_list = list(ids)
        if len(ids_list) > 5000:
            ids_list = ids_list[-5000:]
        _save_json(seen_path, {"ids": ids_list, "updated_at": _ts()})
        return True


def _is_hostile(text: str) -> bool:
    raw = text or ""
    norm = _normalize_text(raw)
    if any(p in raw.lower() for p in HOSTILE_PATTERNS):
        return True
    if any(p in norm for p in HOSTILE_PATTERNS):
        return True
    return any(rx.search(raw) or rx.search(norm) for rx in HOSTILE_REGEXES)


def _candidate_age_minutes(c: dict) -> int:
    """Best-effort age extraction; returns large number when unknown."""
    try:
        if "age_minutes" in c and c.get("age_minutes") is not None:
            return int(c.get("age_minutes"))
    except Exception:
        pass
    dt = c.get("datetime") or c.get("created_at") or ""
    if not dt:
        return 9999


NOTIF_MENTIONS_JS = r"""
(function() {
  var out = [];
  var arts = document.querySelectorAll('article[data-testid="tweet"]');
  for (var i = 0; i < arts.length; i++) {
    var el = arts[i];
    var textEl = el.querySelector('[data-testid="tweetText"]');
    var text = textEl ? textEl.innerText.trim() : '';
    var urlEl = el.querySelector('a[href*="/status/"]');
    var url = urlEl ? urlEl.href : '';
    if (!url) continue;
    var idm = url.match(/status\/(\d+)/);
    var id = idm ? idm[1] : '';
    var am = url.match(/x\.com\/([A-Za-z0-9_]+)\/status\//);
    var author = am ? am[1] : '';
    var tEl = el.querySelector('time');
    var dt = tEl ? (tEl.getAttribute('datetime') || '') : '';
    if (!id || !author) continue;
    out.push({id:id, text:text, url:url, author:author, datetime:dt});
  }
  return JSON.stringify(out);
})()
"""


def _fetch_inbound_from_notifications(port: int, limit: int) -> list:
    ws = chrome.connect(port)
    try:
        chrome.navigate(ws, "https://x.com/notifications/mentions", wait=3.0)
        time.sleep(1.2)
        raw = chrome.eval_js(ws, NOTIF_MENTIONS_JS)
        if not raw:
            return []
        try:
            rows = json.loads(raw)
        except Exception:
            return []
        out, seen = [], set()
        for r in rows:
            rid = str(r.get("id", ""))
            if not rid or rid in seen:
                continue
            seen.add(rid)
            out.append(r)
            if len(out) >= limit:
                break
        return out
    finally:
        try:
            ws.close()
        except Exception:
            pass
    try:
        t = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - t).total_seconds() / 60))
    except Exception:
        return 9999


def run_once(config_path: str, limit: int):
    cfg = _load_json(config_path, {})
    handle = cfg["hunter_handle"]
    port = int(cfg["hunter_port"])
    blocked = {str(x).lower() for x in cfg.get("blocked_handles", [])}
    max_age_min = int(cfg.get("inbound", {}).get("max_engagement_age_minutes", 5))
    seen_path = os.path.join(STATE_DIR, f"inbound_seen_{handle.lower()}.json")
    seen = _load_json(seen_path, {"ids": []})
    seen_ids = set(seen.get("ids", []))
    seen_lock_name = f"inbound_seen_{handle.lower()}"

    use_notifs = bool(cfg.get("inbound", {}).get("use_notifications", True))
    candidates = []
    if use_notifs:
        _log("notifications mentions fetch", handle)
        try:
            candidates = _fetch_inbound_from_notifications(port, limit)
        except Exception as e:
            _log(f"  notifications fetch failed: {e}", handle)
    if not candidates:
        query = f"to:{handle} -from:{handle}"
        _log(f"search fallback {query}", handle)
        candidates = fetch.search(port, query, mode="live", limit=limit)

    added = 0
    ignored_hostile = 0
    skipped_seen = 0
    skipped_old = 0
    failed = 0

    for c in candidates:
        cid = str(c.get("id", ""))
        text = c.get("text", "")
        url = c.get("url", "")
        author = c.get("author", "")
        if not cid or not url or not author:
            continue
        if author.lower() == handle.lower():
            continue
        if author.lower() in blocked:
            continue
        if _candidate_age_minutes(c) > max_age_min:
            skipped_old += 1
            continue
        if cid in seen_ids:
            skipped_seen += 1
            continue
        if not _claim_seen_id(seen_path, seen_lock_name, cid):
            skipped_seen += 1
            continue
        seen_ids.add(cid)

        if _is_hostile(text):
            ignored_hostile += 1
            continue

        _log(f"engage @{author} {cid}", handle)
        try:
            engage.like_tweet(port, url, dry_run=False)
        except Exception as e:
            _log(f"  like failed (non-fatal): {e}", handle)

        try:
            g = generate.generate_engaged_reply(
                target_handle=author,
                target_post_text=text,
                library_path=LIBRARY_PATH,
                archetypes=cfg.get("archetypes", {}),
                hunter_handle=handle,
                examples_per_prompt=cfg.get("generation", {}).get("examples_per_prompt", 8),
                max_reply_chars=cfg.get("generation", {}).get("max_reply_chars", 220),
            )
            reply_text = (g.get("reply") or "").strip()
            if len(reply_text) < 8:
                reply_text = "Appreciate the reply — thanks for jumping in."
            r = engage.reply_tweet(port, url, reply_text, dry_run=False, self_handle=handle)
            if r.get("ok"):
                added += 1
                _log(f"  replied ok: {r.get('reply_url','')}", handle)
            else:
                failed += 1
                _log(f"  reply failed: {r.get('error','unknown')}", handle)
        except Exception as e:
            failed += 1
            _log(f"  generation/reply exception: {e}", handle)

        time.sleep(1.0)

    _log(
        f"done candidates={len(candidates)} replied={added} hostile_ignored={ignored_hostile} "
        f"seen_skipped={skipped_seen} old_skipped={skipped_old} failed={failed}",
        handle,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args()
    run_once(args.config, args.limit)


if __name__ == "__main__":
    main()
