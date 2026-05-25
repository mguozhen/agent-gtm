"""Long-running engagement tracker for Hunter's X account.

Two data sources feed state/reply_engagement.json:

  1) QUEUE SEEDING (every 60s) — picks up posted entries from reply_queue.json
     (engage_daemon, buildlog_drafts, quote_scout → telegram_bridge approval).
     Fast path: catches automated posts within ~1min of approval.

  2) PROFILE SWEEP (every 15min) — scrapes Hunter's `/with_replies` profile.
     Authoritative path: catches MANUAL posts (X app, web), boost_hunter,
     runner.py, hunter.py, and anything else that bypasses the queue.
     Also re-reads engagement (likes/replies/rts) for ALL known tweets in
     one shot, much faster than per-permalink navigation.

Finalization: once `posted_at + 24h` has elapsed, the next sweep snapshot
becomes the final_* values and engaged = any > 0.

Concurrency:
  - state/reply_engagement.json writes under file_lock("reply_engagement")
  - Chrome access under chrome_lock(port=10000); defers to bridge if a
    fresh pending entry exists in the approval queue.
"""
import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env; env.load()
from lock import file_lock, chrome_lock
from chrome import connect, navigate, eval_js, ping, scroll_down

QUEUE_PATH = os.path.join(ROOT_DIR, "state", "reply_queue.json")
ENGAGEMENT_PATH = os.path.join(ROOT_DIR, "state", "reply_engagement.json")
LOG_PATH = os.path.join(ROOT_DIR, "logs", "reply-scorer.log")

HUNTER_HANDLE = "GuoHunter95258"
HUNTER_PORT = 10000
SEED_INTERVAL_S = 60
# Profile sweep replaces per-permalink check passes. One sweep snapshots
# every visible Hunter tweet's engagement in a single navigation — way
# cheaper than navigating to N permalinks. Runs every 15min.
SWEEP_INTERVAL_S = 900
# Skip a sweep if there's a fresh pending entry the user might be about
# to approve — bridge gets priority on Chrome. Threshold in seconds.
DEFER_IF_PENDING_AGE_S = 300
# Don't keep sweeping a tweet forever — once 24h has elapsed we lock in
# final_* and skip future engagement updates for it.
FINALIZE_AGE_MIN = 1440

_stop = False


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}\n"
    sys.stdout.write(line); sys.stdout.flush()
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except Exception:
        pass


def _load(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def _save(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _seed_from_queue():
    """Find posted queue entries we haven't started tracking yet. Fast path
    so automation-posted tweets show up in /score within ~1min instead of
    waiting up to 15min for the next profile sweep."""
    queue = _load(QUEUE_PATH, [])
    with file_lock("reply_engagement", on_wait=_log):
        history = _load(ENGAGEMENT_PATH, {})
        added = 0
        for q in queue:
            if q.get("status") != "posted":
                continue
            url = q.get("reply_url_actual", "")
            if not url:
                continue
            if url in history:
                continue
            posted_at = q.get("posted_at", _ts())
            history[url] = {
                "queue_id":    q.get("id", ""),
                "kind":        q.get("kind", "reply"),
                "target":      q.get("target", ""),
                "target_url":  q.get("target_url", ""),
                "reply_text":  q.get("reply_text", ""),
                "reply_url":   url,
                "posted_at":   posted_at,
                "source":      "queue",
                "checks":      [],
                "final_at":    "",
                "engaged":     False,
            }
            added += 1
        if added:
            _save(ENGAGEMENT_PATH, history)
            _log(f"seeded {added} new posted entries from queue")


def _scrape_profile(ws):
    """Navigate Hunter's /with_replies tab, scroll while CAPTURING AT EACH
    STEP, and return every own-tweet seen with engagement metrics + kind.

    X's profile aggressively virtualizes — tweets scrolled past are removed
    from the DOM, so a one-shot scrape at the end only sees the current
    viewport (incident 2026-05-16: only 3 of 22 today's tweets captured).
    Solution: extract on every scroll step and merge by id. Slightly slower
    but captures the full day.

    Returns list of dicts:
        {url, id, when (iso), kind, target, text, likes, replies, rts}
    """
    try:
        navigate(ws, f"https://x.com/{HUNTER_HANDLE}/with_replies", wait=6.0)
    except Exception as e:
        _log(f"  profile navigate failed: {e}")
        return []

    # The extractor JS is identical to a one-shot scrape, just run repeatedly.
    extract_js = _build_extract_js()
    seen = {}  # id → latest record
    # 30 scrolls at 900px reaches ~24-48h of history depending on posting
    # density. Per-step wait long enough for the virtualizer to load the
    # next chunk into DOM.
    for step in range(30):
        try:
            chunk_raw = eval_js(ws, extract_js)
            for it in (json.loads(chunk_raw) if chunk_raw else []):
                # Last-write-wins on duplicates so later passes (which may
                # have fresher engagement numbers) win.
                seen[it["id"]] = it
        except Exception as e:
            _log(f"  scroll {step} extract error: {e}")
        scroll_down(ws, px=900)
        time.sleep(1.3)

    return list(seen.values())


def _build_extract_js():
    """JS extractor for own-tweets in the current DOM viewport. Returns
    a JSON string. Same logic as the original one-shot scrape, factored out
    so the scroll loop can call it repeatedly."""
    return r"""
    (function(handle){
        function num(s){
            if (!s) return 0;
            s = (s || '').replace(/[^0-9.kKmM]/g,'');
            if (!s) return 0;
            var n = parseFloat(s);
            if (/k/i.test(s)) n *= 1000;
            if (/m/i.test(s)) n *= 1000000;
            return Math.round(n);
        }
        // Helper: extract the @handle the article belongs to (first
        // User-Name block, second line is usually "@handle"). Returns
        // empty string if not found.
        function articleAuthor(art) {
            var u = art.querySelector('[data-testid="User-Name"]');
            if (!u) return '';
            var m = (u.innerText || '').match(/@(\w+)/);
            return m ? m[1] : '';
        }
        var arts = document.querySelectorAll('article[data-testid="tweet"]');
        var out = [];
        var seen = {};
        for (var i=0; i<arts.length; i++) {
            var el = arts[i];
            var links = el.querySelectorAll('a[href*="/status/"]');
            var href = '';
            var ownHref = '';
            for (var j=0; j<links.length; j++) {
                var h = links[j].getAttribute('href') || '';
                if (h.indexOf('/analytics') !== -1) continue;
                if (h.indexOf('/photo') !== -1) continue;
                if (h.indexOf('/video') !== -1) continue;
                if (!href) href = h;
                if (h.indexOf('/' + handle + '/status/') === 0 && !ownHref) ownHref = h;
            }
            if (!ownHref) continue;  // skip articles that aren't Hunter's own tweet
            var m = ownHref.match(/status[/](\d+)/);
            if (!m) continue;
            var id = m[1];
            if (seen[id]) continue;
            seen[id] = true;

            // For "connected thread" replies (Mode B): X stacks the parent
            // tweet directly above ours with no "Replying to" header. The
            // preceding article's author IS the reply target.
            var prevAuthor = '';
            if (i > 0) {
                var prev = arts[i-1];
                var pa = articleAuthor(prev);
                if (pa && pa.toLowerCase() !== handle.toLowerCase()) {
                    prevAuthor = pa;
                }
            }

            var timeEl = el.querySelector('time');
            var when = timeEl ? timeEl.getAttribute('datetime') : '';

            // The outer article contains the FIRST tweetText (Hunter's own
            // post body). For QTs, the embedded quoted tweet also has a
            // tweetText, but we want the outer one for the reply_text field.
            var allTextEls = el.querySelectorAll('[data-testid="tweetText"]');
            var text = allTextEls.length ? allTextEls[0].innerText : '';

            // Kind detection. Four signals checked in order:
            //   1. "Replying to @X" header — standalone-reply rendering
            //   2. Embedded quoted tweet → 2+ User-Name blocks → quote
            //   3. Preceding article on the page is a different author →
            //      connected-thread reply (Mode B), parent is that author
            //   4. Otherwise original
            // Why we layer these: X has two reply-rendering modes on the
            // /with_replies tab. Mode A shows "Replying to @X" text. Mode B
            // stacks the parent tweet directly above ours and OMITS the
            // header (the visual connection is supposed to imply it). The
            // preceding-article check is the fix for incident 2026-05-16:
            // 2 replies (to @SRKDAN and @TTrimoreau) were rendered Mode B
            // and got falsely classified as 'original'.
            var elText = el.innerText || '';
            var rm = elText.match(/Replying to\s+@(\w+)/);
            var userNames = el.querySelectorAll('[data-testid="User-Name"]');
            var kind, target;
            if (rm) {
                kind = 'reply';
                target = rm[1];
            } else if (userNames.length >= 2) {
                kind = 'quote';
                var qtAuthorText = userNames[1].innerText || '';
                var qm = qtAuthorText.match(/@(\w+)/);
                target = qm ? qm[1] : '';
            } else if (prevAuthor) {
                kind = 'reply';
                target = prevAuthor;
            } else {
                kind = 'original';
                target = '';
            }

            var likeEl  = el.querySelector('[data-testid="like"]');
            var replyEl = el.querySelector('[data-testid="reply"]');
            var rtEl    = el.querySelector('[data-testid="retweet"]');

            out.push({
                url:     'https://x.com' + ownHref,
                id:      id,
                when:    when,
                kind:    kind,
                target:  target,
                text:    text.slice(0, 400),
                likes:   num(likeEl  ? likeEl.innerText  : ''),
                replies: num(replyEl ? replyEl.innerText : ''),
                rts:     num(rtEl    ? rtEl.innerText    : '')
            });
        }
        return JSON.stringify(out);
    })('""" + HUNTER_HANDLE + """')
    """


def _iso_to_local_ts(iso: str) -> str:
    """X returns datetimes in UTC ISO 8601 (e.g. '2026-05-16T20:16:47.000Z').
    Convert to local-time YYYY-MM-DD HH:MM:SS to match the rest of the system."""
    if not iso:
        return ""
    try:
        from datetime import timezone
        dt = datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _run_sweep_pass():
    """Single Chrome navigation to Hunter's /with_replies tab. For every
    own-tweet visible:
      - if not in engagement records → seed it (catches manual posts, posts
        from boost_hunter, runner, hunter — anything bypassing the queue)
      - if already known → append a fresh check to its history; finalize if
        24h has elapsed since posted_at
    """
    if not ping(HUNTER_PORT):
        _log(f"hunter chrome not reachable on {HUNTER_PORT} — skipping sweep")
        return
    if _has_fresh_pending_entry():
        _log("sweep deferred (fresh pending entry, bridge priority)")
        return

    with chrome_lock(HUNTER_PORT, on_wait=_log):
        try:
            ws = connect(HUNTER_PORT)
        except Exception as e:
            _log(f"chrome connect failed: {e}")
            return
        try:
            tweets = _scrape_profile(ws)
        finally:
            try: ws.close()
            except Exception: pass

    if not tweets:
        _log("sweep: 0 tweets parsed")
        return

    with file_lock("reply_engagement", on_wait=_log):
        history = _load(ENGAGEMENT_PATH, {})
        added = 0
        updated = 0
        finalized = 0
        now = datetime.now()
        for t in tweets:
            url = t["url"]
            posted_local = _iso_to_local_ts(t.get("when", "")) or _ts()
            metrics = {"likes": t["likes"], "replies": t["replies"], "rts": t["rts"]}
            check_entry = {"at": _ts(), "source": "sweep", **metrics}

            if url not in history:
                history[url] = {
                    "queue_id":    "",
                    "kind":        t["kind"],
                    "target":      t["target"],
                    "target_url":  "",
                    "reply_text":  t["text"],
                    "reply_url":   url,
                    "posted_at":   posted_local,
                    "source":      "profile_sweep",
                    "checks":      [check_entry],
                    "final_at":    "",
                    "engaged":     (metrics["likes"] + metrics["replies"] + metrics["rts"]) > 0,
                }
                added += 1
            else:
                rec = history[url]
                if rec.get("final_at"):
                    continue  # don't touch finalized
                rec.setdefault("checks", []).append(check_entry)
                # Kind/target precedence (incident 2026-05-16 17:00 — sweep
                # downgraded 3 confirmed replies to 'original' because X's
                # profile DOM didn't render the 'Replying to' indicator for
                # them):
                #
                #   - If record has a target already (i.e. it's a known
                #     reply or quote from the queue), the queue's classification
                #     is GROUND TRUTH. Do not touch kind or target.
                #   - If record has no target (originals, or sweep-only
                #     records without ground truth), let the sweep update
                #     classification — these benefit from the heuristics.
                if not rec.get("target"):
                    rec["kind"] = t["kind"]
                    if t["target"]:
                        rec["target"] = t["target"]
                if not rec.get("reply_text") and t.get("text"):
                    rec["reply_text"] = t["text"]
                updated += 1

            # Finalization: if posted_at + FINALIZE_AGE_MIN has passed, lock
            # in current metrics as final.
            rec = history[url]
            posted_dt = _parse_ts(rec.get("posted_at", ""))
            if posted_dt and not rec.get("final_at"):
                age_min = (now - posted_dt).total_seconds() / 60.0
                if age_min >= FINALIZE_AGE_MIN:
                    rec["final_likes"]   = metrics["likes"]
                    rec["final_replies"] = metrics["replies"]
                    rec["final_rts"]     = metrics["rts"]
                    rec["final_at"]      = _ts()
                    rec["engaged"]       = (metrics["likes"] + metrics["replies"] + metrics["rts"]) > 0
                    finalized += 1

        _save(ENGAGEMENT_PATH, history)
        _log(f"sweep: +{added} new, {updated} refreshed, {finalized} finalized "
             f"({len(tweets)} tweets seen)")


def _fetch_engagement_permalink_fallback(ws, reply_url):
    """Per-permalink engagement fetch. Superseded by `_scrape_profile` for
    Hunter's own tweets (sweep gets them all in one nav). Kept around for
    cases where the sweep can't find a tweet — e.g. an older reply that's
    fallen off the visible profile feed but we still want a one-off check.
    Not currently wired into the daemon loop.

    Reply permalinks render the parent tweet first and the focal (our) tweet
    second; we match the article whose internal /status/<id> link matches
    the URL we navigated to."""
    try:
        navigate(ws, reply_url, wait=4.0)
    except Exception as e:
        _log(f"  navigate failed for {reply_url}: {e}")
        return {}

    # Extract the focal tweet id from the URL.
    import re as _re
    m = _re.search(r"/status/(\d+)", reply_url)
    if not m:
        return {}
    focal_id = m.group(1)

    js = """
    (function(focalId){
        function num(s){
            if (!s) return 0;
            s = s.replace(/[^0-9.kKmM]/g,'');
            if (!s) return 0;
            var n = parseFloat(s);
            if (/k/i.test(s)) n *= 1000;
            if (/m/i.test(s)) n *= 1000000;
            return Math.round(n);
        }
        var arts = document.querySelectorAll('article[data-testid="tweet"]');
        if (!arts.length) return JSON.stringify({err:'no_article'});
        // Find the article whose own /status/<id> link matches the focal id.
        var el = null;
        for (var i = 0; i < arts.length; i++) {
            var links = arts[i].querySelectorAll('a[href*="/status/"]');
            for (var j = 0; j < links.length; j++) {
                var lm = links[j].getAttribute('href').match(/status[/](\\d+)/);
                if (lm && lm[1] === focalId) { el = arts[i]; break; }
            }
            if (el) break;
        }
        if (!el) return JSON.stringify({err:'focal_not_found'});
        var like = el.querySelector('[data-testid="like"]');
        var reply = el.querySelector('[data-testid="reply"]');
        var rt = el.querySelector('[data-testid="retweet"]');
        return JSON.stringify({
            likes: num(like ? like.innerText : ''),
            replies: num(reply ? reply.innerText : ''),
            rts: num(rt ? rt.innerText : '')
        });
    })('""" + focal_id + """')
    """
    raw = eval_js(ws, js)
    try:
        d = json.loads(raw or "{}")
    except Exception:
        return {}
    if d.get("err"):
        _log(f"  extract err for {reply_url}: {d['err']}")
        return {}
    return {
        "likes": int(d.get("likes", 0) or 0),
        "replies": int(d.get("replies", 0) or 0),
        "rts": int(d.get("rts", 0) or 0),
    }


def _has_fresh_pending_entry():
    """Yield to the bridge if a recently-queued entry is still awaiting
    approval — the user might tap Approve any second and the bridge will
    need Chrome immediately. Avoids the case where scorer holds the lock
    for 15s while the user is staring at a card."""
    try:
        with open(QUEUE_PATH) as f:
            queue = json.load(f)
    except (FileNotFoundError, ValueError):
        return False
    now = datetime.now()
    for q in queue:
        if q.get("status") != "pending":
            continue
        qa = _parse_ts(q.get("queued_at", ""))
        if qa and (now - qa).total_seconds() <= DEFER_IF_PENDING_AGE_S:
            return True
    return False


def _run_check_pass_DEPRECATED():
    """Old per-permalink check pass. Replaced by `_run_sweep_pass` which
    snapshots every Hunter tweet's engagement in one profile scrape. Kept
    here as a reference but not called from main()."""
    history = _load(ENGAGEMENT_PATH, {})
    due = []  # _due_records removed with the sweep migration
    if not due:
        return
    if not ping(HUNTER_PORT):
        _log(f"hunter chrome not reachable on {HUNTER_PORT} — skipping check pass")
        return
    if _has_fresh_pending_entry():
        _log(f"check pass: {len(due)} records due — deferring (fresh pending entry, bridge priority)")
        return
    _log(f"check pass: {len(due)} records due")

    with chrome_lock(HUNTER_PORT, on_wait=_log):
        try:
            ws = connect(HUNTER_PORT)
        except Exception as e:
            _log(f"chrome connect failed: {e}")
            return
        try:
            for url in due:
                rec = history.get(url)
                if not rec:
                    continue
                metrics = _fetch_engagement(ws, url)
                if not metrics:
                    # Slide next check 30min into the future on failure rather
                    # than dropping it. Prevents a stuck record from blocking
                    # the rest of the queue.
                    rec["next_check_at"] = (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
                    continue
                check = {
                    "at": _ts(),
                    "likes": metrics["likes"],
                    "replies": metrics["replies"],
                    "rts": metrics["rts"],
                }
                rec.setdefault("checks", []).append(check)
                next_at = _next_check_at_for(rec["posted_at"], rec["checks"])
                rec["next_check_at"] = next_at
                # Finalize once we've completed all planned checks.
                if not next_at:
                    rec["final_likes"]   = metrics["likes"]
                    rec["final_replies"] = metrics["replies"]
                    rec["final_rts"]     = metrics["rts"]
                    rec["final_at"]      = _ts()
                    rec["engaged"]       = (metrics["likes"] + metrics["replies"] + metrics["rts"]) > 0
                    _log(f"  finalized {url} → likes={metrics['likes']} replies={metrics['replies']} rts={metrics['rts']} engaged={rec['engaged']}")
                else:
                    _log(f"  checked {url} → likes={metrics['likes']} (next {next_at})")
                # Politeness gap between profile hits — short so the
                # chrome_lock isn't held longer than necessary if the bridge
                # is waiting behind us.
                time.sleep(0.8)
        finally:
            try: ws.close()
            except Exception: pass

    with file_lock("reply_engagement", on_wait=_log):
        cur = _load(ENGAGEMENT_PATH, {})
        # Merge: prefer our just-updated records but keep ones we didn't touch.
        for url, rec in history.items():
            cur[url] = rec
        _save(ENGAGEMENT_PATH, cur)


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True


def main():
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT,  _handle_sigterm)
    _log("reply-scorer daemon up")
    last_seed = 0.0
    last_sweep = 0.0
    while not _stop:
        now = time.time()
        if now - last_seed >= SEED_INTERVAL_S:
            try:
                _seed_from_queue()
            except Exception as e:
                _log(f"seed pass error: {e}")
            last_seed = now
        if now - last_sweep >= SWEEP_INTERVAL_S:
            try:
                _run_sweep_pass()
            except Exception as e:
                _log(f"sweep pass error: {e}")
            last_sweep = now
        time.sleep(5)
    _log("reply-scorer daemon down")


if __name__ == "__main__":
    main()
