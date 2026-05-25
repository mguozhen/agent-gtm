"""Like and reply to tweets via X.com browser CDP."""
import json
import time
import random
import sys
import os

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from chrome import connect, navigate, eval_js, paste_text, type_text, click_testid, _send
from lock import chrome_lock


def like_tweet(port: int, tweet_url: str, dry_run: bool = False) -> dict:
    """
    Like a tweet by navigating to its URL and clicking the like button.
    Returns {"ok": bool, "error": str | None}.
    """
    if dry_run:
        print(f"  [like] dry-run: {tweet_url}")
        return {"ok": True, "dry_run": True}

    with chrome_lock(port):
        ws = connect(port, timeout=60)
        try:
            navigate(ws, tweet_url, wait=4.0)

            # Check if already liked (aria-label contains "Liked" vs "Like")
            already = eval_js(ws, """
                (function() {
                    var btn = document.querySelector('[data-testid="like"]');
                    if (!btn) return 'not found';
                    var label = btn.getAttribute('aria-label') || '';
                    return label.toLowerCase().includes('liked') ? 'already' : 'ok';
                })()
            """)
            if already == "already":
                return {"ok": True, "error": "already_liked"}
            if already == "not found":
                return {"ok": False, "error": "like_button_not_found"}

            clicked = click_testid(ws, "like")
            if clicked == "not found":
                return {"ok": False, "error": "like_button_not_found"}

            time.sleep(1.5)
            return {"ok": True, "error": None}
        finally:
            ws.close()


def retweet_tweet(port: int, tweet_url: str, dry_run: bool = False) -> dict:
    """
    Plain retweet (no comment). Opens the retweet menu and clicks "Repost".
    Returns {"ok": bool, "error": str | None}.
    """
    if dry_run:
        print(f"  [retweet] dry-run: {tweet_url}")
        return {"ok": True, "dry_run": True}

    with chrome_lock(port):
        ws = connect(port, timeout=60)
        try:
            navigate(ws, tweet_url, wait=4.0)

            # If aria-label on retweet button already says "Reposted", skip
            already = eval_js(ws, """
                (function() {
                    var btn = document.querySelector('[data-testid="retweet"]')
                           || document.querySelector('[data-testid="unretweet"]');
                    if (!btn) return 'not found';
                    if (btn.getAttribute('data-testid') === 'unretweet') return 'already';
                    var label = (btn.getAttribute('aria-label') || '').toLowerCase();
                    if (label.includes('undo repost') || label.includes('reposted')) return 'already';
                    return 'ok';
                })()
            """)
            if already == "already":
                return {"ok": True, "error": "already_retweeted"}
            if already == "not found":
                return {"ok": False, "error": "retweet_button_not_found"}

            clicked = click_testid(ws, "retweet")
            if clicked == "not found":
                return {"ok": False, "error": "retweet_button_not_found"}
            time.sleep(1.5)

            confirmed = eval_js(ws, """
                (function() {
                    var item = document.querySelector('[data-testid="retweetConfirm"]');
                    if (item) { item.click(); return 'ok'; }
                    var items = document.querySelectorAll('[role="menuitem"]');
                    for (var i = 0; i < items.length; i++) {
                        var t = (items[i].innerText || '').toLowerCase();
                        if (t.includes('repost') && !t.includes('quote')) {
                            items[i].click();
                            return 'ok-menu';
                        }
                    }
                    return 'not found';
                })()
            """)
            if confirmed == "not found":
                return {"ok": False, "error": "retweet_confirm_not_found"}
            time.sleep(2.0)
            return {"ok": True, "error": None}
        finally:
            ws.close()


def quote_tweet(port: int, tweet_url: str, comment: str, dry_run: bool = False) -> dict:
    """
    Quote-tweet (repost with comment): navigate to the tweet, open the retweet menu,
    click "Quote", paste comment, submit.
    Returns {"ok": bool, "error": str | None}.
    """
    if dry_run:
        print(f"  [quote] dry-run → {tweet_url}: {comment[:60]}")
        return {"ok": True, "dry_run": True}

    def _qlog(s):
        print(f"[quote_tweet] {s}", flush=True)

    with chrome_lock(port):
        ws = connect(port, timeout=60)
        try:
            _qlog(f"navigate → {tweet_url}")
            navigate(ws, tweet_url, wait=5.0)

            # Spoof focus so Draft.js processes Input.insertText even when
            # Chrome isn't the frontmost macOS app. Same fix that reply_tweet
            # has — without this, the paste below silently no-ops when Chrome
            # is backgrounded and submit appears to succeed but no post lands.
            try:
                _send(ws, "Emulation.setFocusEmulationEnabled",
                      {"enabled": True}, msg_id=43)
                _qlog("focus emulation: enabled")
            except Exception as e:
                _qlog(f"focus emulation set: {e} (continuing)")

            # Open the retweet menu
            opened = eval_js(ws, """
                (function() {
                    var btn = document.querySelector('[data-testid="retweet"]');
                    if (!btn) return 'not found';
                    btn.click();
                    return 'ok';
                })()
            """)
            _qlog(f"retweet menu: {opened}")
            if opened == "not found":
                return {"ok": False, "error": "retweet_button_not_found"}
            time.sleep(1.5)

            # Click "Quote" item in the menu. Prefer the menu-item click (opens
            # an INLINE modal on the same page — focus emulation works there).
            # The /compose/post anchor was the previous default; it triggers a
            # full page navigation where Input.insertText silently no-ops even
            # with focus emulation enabled (observed 2026-05-17 on three
            # successive approvals — composer stayed empty after paste:ok).
            quoted = eval_js(ws, """
                (function() {
                    // Preferred: menuitem click — opens an inline compose modal
                    var items = document.querySelectorAll('[role="menuitem"]');
                    for (var i = 0; i < items.length; i++) {
                        var t = (items[i].innerText || '').toLowerCase();
                        if (t.includes('quote')) { items[i].click(); return 'ok-menu'; }
                    }
                    // Fallback: anchor that navigates to /compose/post
                    var a = document.querySelector('a[href*="/compose/post"]');
                    if (a) { a.click(); return 'ok-link'; }
                    return 'not found';
                })()
            """)
            _qlog(f"quote option clicked: {quoted}")
            if quoted == "not found":
                return {"ok": False, "error": "quote_option_not_found"}
            time.sleep(3.0)

            # Poll for the QT composer to mount (target the labeled one, not
            # any random contenteditable on the source page). X labels the
            # QT compose textarea as tweetTextarea_0 inside the dialog.
            focused = "no_box"
            for _attempt in range(8):
                focused = eval_js(ws, """
                    (function() {
                        var box = document.querySelector('[data-testid="tweetTextarea_0"]');
                        if (!box) {
                            var boxes = document.querySelectorAll('[contenteditable="true"]');
                            box = boxes[boxes.length - 1];
                        }
                        if (!box) return 'no_box';
                        box.click();
                        box.focus();
                        return 'focused';
                    })()
                """)
                if focused == "focused":
                    break
                time.sleep(0.5)
            _qlog(f"composer focus: {focused}")
            if focused != "focused":
                return {"ok": False, "error": focused}
            time.sleep(0.5)

            # Type via per-char keyDown/keyUp — Input.insertText is silently
            # dropped by Draft.js inside the QT modal even with focus emulation
            # (confirmed 2026-05-17: composer stayed empty after paste:ok across
            # five approval attempts; dispatchKeyEvent landed the text first try
            # and produced status 2056102001352847644).
            result = type_text(ws, comment)
            _qlog(f"type_text: {result}")
            if result != "ok":
                return {"ok": False, "error": f"type_failed: {result}"}
            time.sleep(1.0)

            # Read back composer text so we know paste actually landed (the
            # silent-paste-failure that the focus-emulation fix is supposed
            # to address would surface here as empty/wrong text).
            pasted = eval_js(ws, """
                (function() {
                    var box = document.querySelector('[data-testid="tweetTextarea_0"]');
                    if (!box) {
                        var boxes = document.querySelectorAll('[contenteditable="true"]');
                        box = boxes[boxes.length - 1];
                    }
                    return box ? box.innerText.slice(0, 120) : '';
                })()
            """)
            _qlog(f"composer now contains: {pasted!r}")
            if not pasted or len(pasted) < 10:
                return {"ok": False, "error": f"composer_empty_after_paste:{pasted!r}"}
            # Sanity-check that what's in the composer roughly matches the
            # intended comment (catches paste-into-wrong-box too).
            head = comment[:30].lower()
            if head and head[:20] not in pasted.lower():
                _qlog(f"  WARNING: composer text doesn't start with comment head {head!r}")

            clicked = click_testid(ws, "tweetButton")
            if clicked == "not found":
                clicked = click_testid(ws, "tweetButtonInline")
            _qlog(f"submit click: {clicked}")
            if clicked == "not found":
                return {"ok": False, "error": "submit_button_not_found"}
            time.sleep(5.0)

            # STRONG verify: navigate to Hunter's profile, check if his
            # newest tweet matches the comment we just tried to post.
            # The "is the last contenteditable empty" check that lived here
            # before was a false positive: after a failed submit, the source
            # page's inline reply box appears empty by default, returning
            # ok=True even though nothing landed.
            try:
                navigate(ws, "https://x.com/GuoHunter95258", wait=4.0)
                time.sleep(1.5)
                raw = eval_js(ws, """
                    (function() {
                        var arts = document.querySelectorAll('article[data-testid="tweet"]');
                        for (var i = 0; i < Math.min(5, arts.length); i++) {
                            var el = arts[i];
                            var head = (el.innerText || '').slice(0,60);
                            if (/Pinned/i.test(head)) continue;
                            if (/Reposted/i.test(head)) continue;
                            var textEl = el.querySelector('[data-testid="tweetText"]');
                            var t = textEl ? textEl.innerText.slice(0, 200) : '';
                            var urlEl = el.querySelector('a[href*="/status/"]');
                            var url = urlEl ? urlEl.href : '';
                            return JSON.stringify({text: t, url: url});
                        }
                        return '';
                    })()
                """)
                latest = json.loads(raw) if raw else {}
            except Exception as e:
                _qlog(f"verify nav failed: {e}")
                latest = {}
            latest_text = (latest.get("text") or "").strip()
            head = comment[:40].strip().lower()
            _qlog(f"latest tweet on profile: {latest_text[:80]!r}")
            if head and head[:25] in latest_text.lower():
                _qlog(f"VERIFIED posted: {latest.get('url')}")
                return {"ok": True, "error": None, "url": latest.get("url", "")}
            _qlog(f"VERIFY FAILED — latest tweet doesn't match comment head {head[:25]!r}")
            return {"ok": False,
                    "error": f"post_not_visible_on_profile (latest={latest_text[:60]!r})"}
        finally:
            ws.close()


def reply_tweet(port: int, tweet_url: str, reply_text: str,
                dry_run: bool = False, verbose: bool = True,
                self_handle: str = "guohunter95258") -> dict:
    """
    Reply to a tweet by navigating to its URL, opening the reply composer, and
    submitting. Verifies by re-navigating to the OP and looking for our reply
    card — the old "reply box cleared" check returned ok on silent failures.

    Returns {"ok": bool, "error": str|None, "reply_url": str|None}.
    """
    if dry_run:
        print(f"  [reply] dry-run → {tweet_url}: {reply_text[:60]}")
        return {"ok": True, "dry_run": True}

    def _log(msg):
        if verbose:
            print(f"  [reply] {msg}", flush=True)

    import json as _json
    import re as _re

    # Extract the tweet ID so we can verify the page we land on is actually
    # the OP we wanted. engage_daemon + telegram_bridge share Chrome on
    # hunter_port; without this check, a concurrent navigation can leave a
    # stale DOM, and "click reply on arts[0]" hits the wrong tweet (incident
    # 2026-05-15: a Zuckerberg-context reply was posted to antirez).
    m = _re.search(r"status/(\d+)", tweet_url or "")
    expected_id = m.group(1) if m else ""

    # Hold the lock for the entire body — navigation, paste, submit, AND verify.
    # The composer can be wiped between paste and submit if another process
    # navigates Chrome out from under us (incident 2026-05-15 17:10 cwolferesearch:
    # submit_button_disabled_or_missing after composer cleared).
    with chrome_lock(port, on_wait=_log):
        ws = connect(port, timeout=60)
        try:
            # Auto-clean any lingering compose modal/draft state so the run is
            # hands-off and not blocked by "Leave site?" style interruptions.
            _ = eval_js(ws, """
                (function() {
                    try {
                        var closeBtn = document.querySelector('[data-testid="app-bar-close"]')
                                   || document.querySelector('[aria-label*="Close"][role="button"]');
                        if (closeBtn) closeBtn.click();
                    } catch(e) {}
                    try {
                        var discard = document.querySelector('[data-testid="confirmationSheetConfirm"]')
                                   || Array.from(document.querySelectorAll('button,div[role="button"]'))
                                       .find(function(b){ return /discard|leave/i.test((b.innerText||'').trim()); });
                        if (discard) discard.click();
                    } catch(e) {}
                    return 'ok';
                })()
            """)
            time.sleep(0.4)

            _log(f"navigate → {tweet_url}")
            navigate(ws, tweet_url, wait=5.0)

            # Spoof focus so Draft.js processes Input.insertText even when
            # Chrome isn't the frontmost macOS app. Done here (after navigate,
            # not in connect) because Chrome's CDP socket can be backlogged
            # on a fresh WS — putting it after navigate's 5s settle gives
            # the response room to arrive within the read timeout.
            # Wrapped in try/except so a slow response on a busy Chrome
            # doesn't block the rest of the reply flow (the CDP command is
            # still processed by Chrome whether or not we read the reply).
            try:
                _send(ws, "Emulation.setFocusEmulationEnabled",
                      {"enabled": True}, msg_id=43)
            except Exception as e:
                _log(f"  focus emulation set: {e} (continuing)")

            if expected_id:
                actual_id = eval_js(ws, """
                    (function() {
                        var arts = document.querySelectorAll('article[data-testid="tweet"]');
                        if (!arts.length) return '';
                        var a = arts[0].querySelector('a[href*="/status/"]');
                        if (!a) return '';
                        var m = a.href.match(/status[/](\\d+)/);
                        return m ? m[1] : '';
                    })()
                """)
                if actual_id != expected_id:
                    _log(f"  PREFLIGHT MISMATCH: page shows {actual_id!r}, expected {expected_id!r} — aborting")
                    return {
                        "ok": False,
                        "error": f"preflight_mismatch:expected={expected_id},got={actual_id or 'empty'}",
                        "reply_url": None,
                    }

            _log("clicking OP reply button (first article only)")
            clicked_reply = eval_js(ws, """
                (function() {
                    var arts = document.querySelectorAll('article[data-testid="tweet"]');
                    if (!arts.length) return 'no_articles';
                    var btn = arts[0].querySelector('[data-testid="reply"]');
                    if (!btn) return 'no_reply_button';
                    btn.click();
                    return 'clicked';
                })()
            """)
            _log(f"  → {clicked_reply}")
            if clicked_reply != "clicked":
                return {"ok": False, "error": clicked_reply, "reply_url": None}
            time.sleep(2.5)

            # Inspect what opened
            dialog_state = eval_js(ws, """
                JSON.stringify({
                    dialogs: document.querySelectorAll('[role="dialog"]').length,
                    composer: !!document.querySelector('[data-testid="tweetTextarea_0"]'),
                    cte_count: document.querySelectorAll('[contenteditable="true"]').length
                })
            """)
            _log(f"  after click: {dialog_state}")

            _log("focus composer")
            # Retry the composer-mount probe: X's reply dialog hydrates after
            # click, and the contenteditable sometimes mounts ~500ms later
            # (incident 2026-05-16 16:03 — dialog open but composer:false →
            # no_box). Poll for up to 3s in 500ms steps before giving up.
            focused = "no_box"
            for _attempt in range(6):
                focused = eval_js(ws, """
                    (function() {
                        var box = document.querySelector('[data-testid="tweetTextarea_0"]');
                        if (!box) {
                            var boxes = document.querySelectorAll('[contenteditable="true"]');
                            box = boxes[boxes.length - 1];
                        }
                        if (!box) return 'no_box';
                        box.click();
                        box.focus();
                        return 'focused';
                    })()
                """)
                if focused == "focused":
                    break
                time.sleep(0.5)
            _log(f"  → {focused}")
            if focused != "focused":
                return {"ok": False, "error": focused, "reply_url": None}
            time.sleep(0.5)

            _log(f"paste {len(reply_text)} chars")
            result = paste_text(ws, reply_text)
            _log(f"  → {result}")
            # `paste_text` returns 'ok' (execCommand path) or 'ok-fallback'
            # (ClipboardEvent path). Both can succeed; execCommand is deprecated
            # and increasingly returns false on newer Chrome, so accept either.
            # The real check is the composer-content read 1.5s later.
            if result not in ("ok", "ok-fallback"):
                return {"ok": False, "error": f"paste_failed: {result}", "reply_url": None}
            time.sleep(1.5)

            pasted = eval_js(ws, """
                (function() {
                    var box = document.querySelector('[data-testid="tweetTextarea_0"]');
                    if (!box) {
                        var boxes = document.querySelectorAll('[contenteditable="true"]');
                        box = boxes[boxes.length - 1];
                    }
                    return box ? box.innerText.slice(0, 80) : '';
                })()
            """)
            _log(f"  composer now: {pasted!r}")
            # Fallback when Input.insertText/execCommand doesn't land in Draft.js.
            # Mirrors quote_tweet's robust path: clear + per-key typing.
            if not pasted or len(pasted.strip()) < 8:
                _log("  composer empty after paste; fallback to type_text")
                _ = eval_js(ws, """
                    (function() {
                        var box = document.querySelector('[data-testid="tweetTextarea_0"]');
                        if (!box) {
                            var boxes = document.querySelectorAll('[contenteditable="true"]');
                            box = boxes[boxes.length - 1];
                        }
                        if (!box) return 'no_box';
                        box.focus();
                        try {
                            document.execCommand('selectAll', false, null);
                            document.execCommand('delete', false, null);
                        } catch(e) {}
                        return 'ok';
                    })()
                """)
                t = type_text(ws, reply_text)
                _log(f"  type_text fallback: {t}")
                time.sleep(1.0)
                pasted = eval_js(ws, """
                    (function() {
                        var box = document.querySelector('[data-testid="tweetTextarea_0"]');
                        if (!box) {
                            var boxes = document.querySelectorAll('[contenteditable="true"]');
                            box = boxes[boxes.length - 1];
                        }
                        return box ? box.innerText.slice(0, 80) : '';
                    })()
                """)
                _log(f"  composer after type fallback: {pasted!r}")
                if not pasted or len(pasted.strip()) < 8:
                    return {"ok": False, "error": "composer_empty_after_fallback", "reply_url": None}

            _log("submit (enabled button inside dialog)")
            clicked = eval_js(ws, """
                (function() {
                    // Prefer the submit button inside the topmost open dialog.
                    // The page also has a disabled background "tweetButtonInline" —
                    // clicking that does nothing because it's not enabled.
                    var dialogs = document.querySelectorAll('[role="dialog"]');
                    for (var i = dialogs.length - 1; i >= 0; i--) {
                        var btn = dialogs[i].querySelector('[data-testid="tweetButton"]')
                               || dialogs[i].querySelector('[data-testid="tweetButtonInline"]');
                        if (btn && btn.getAttribute('aria-disabled') !== 'true') {
                            btn.click();
                            return 'clicked-dialog';
                        }
                    }
                    // No dialog (e.g. inline timeline reply) — fall back
                    var inline = document.querySelector('[data-testid="tweetButtonInline"]');
                    if (inline && inline.getAttribute('aria-disabled') !== 'true') {
                        inline.click();
                        return 'clicked-inline';
                    }
                    return 'no_enabled_submit';
                })()
            """)
            _log(f"  → {clicked}")
            if clicked == "no_enabled_submit":
                _log("  submit disabled; fallback to keyboard submit")
                try:
                    # Ctrl+Enter, then Cmd+Enter as fallback submit shortcuts.
                    _send(ws, "Input.dispatchKeyEvent", {
                        "type": "keyDown", "key": "Enter", "code": "Enter",
                        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 36,
                        "modifiers": 2
                    }, msg_id=61)
                    _send(ws, "Input.dispatchKeyEvent", {
                        "type": "keyUp", "key": "Enter", "code": "Enter",
                        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 36,
                        "modifiers": 2
                    }, msg_id=62)
                    time.sleep(0.8)
                    _send(ws, "Input.dispatchKeyEvent", {
                        "type": "keyDown", "key": "Enter", "code": "Enter",
                        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 36,
                        "modifiers": 4
                    }, msg_id=63)
                    _send(ws, "Input.dispatchKeyEvent", {
                        "type": "keyUp", "key": "Enter", "code": "Enter",
                        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 36,
                        "modifiers": 4
                    }, msg_id=64)
                    clicked = "clicked-shortcut"
                except Exception as e:
                    _log(f"  submit shortcut failed: {e}")
                    return {"ok": False, "error": "submit_button_disabled_or_missing", "reply_url": None}
            time.sleep(5.0)

            # Look for error toast / lingering dialog
            post_state = eval_js(ws, """
                (function() {
                    var toast = document.querySelector('[data-testid="toast"]');
                    return JSON.stringify({
                        toast: toast ? toast.innerText.slice(0, 200) : '',
                        dialogs: document.querySelectorAll('[role="dialog"]').length
                    });
                })()
            """)
            _log(f"  post-submit: {post_state}")

            # Real verification: re-navigate to OP and scan for our reply card
            _log("verify — re-navigate to OP")
            navigate(ws, tweet_url, wait=4.0)
            for _ in range(2):
                eval_js(ws, "window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            snippet = reply_text[:50].replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
            verify_js = """
                (function() {
                    var snip = '""" + snippet + """';
                    var arts = document.querySelectorAll('article[data-testid="tweet"]');
                    for (var i = 0; i < arts.length; i++) {
                        var el = arts[i];
                        var ua = el.querySelector('[data-testid="User-Name"]');
                        var author = '';
                        if (ua) {
                            var links = ua.querySelectorAll('a[href^="/"]');
                            for (var j = 0; j < links.length; j++) {
                                var m = links[j].getAttribute('href').match(/^\\/([A-Za-z0-9_]+)$/);
                                if (m) { author = m[1]; break; }
                            }
                        }
                        if (author.toLowerCase() !== '""" + self_handle.lower().replace("'", "\\'") + """') continue;
                        var te = el.querySelector('[data-testid="tweetText"]');
                        var text = te ? te.innerText : '';
                        if (text.indexOf(snip) !== -1) {
                            var ue = el.querySelector('a[href*="/status/"]');
                            return ue ? ue.href : 'found_no_url';
                        }
                    }
                    return '';
                })()
            """
            verified = eval_js(ws, verify_js)
            _log(f"  verified: {verified!r}")

            if verified:
                return {"ok": True, "error": None, "reply_url": verified}
            return {"ok": False, "error": "not_found_after_post", "reply_url": None}
        finally:
            ws.close()


def run_engage_batch(
    port: int,
    account_dir: str,
    tweets: list[dict],
    replied: dict,
    liked: dict,
    reply_fn,           # callable(tweet_text, tweet_author) -> str
    max_replies: int = 5,
    max_likes: int = 10,
    dry_run: bool = False,
) -> dict:
    """
    Batch engage: like + reply to a list of candidate tweets.
    Skips already-replied and already-liked tweets.
    reply_fn: function that takes (tweet_text, author) and returns reply string.
    Returns {"replies": int, "likes": int}.
    """
    import logger as _logger

    replies_done = 0
    likes_done = 0
    random.shuffle(tweets)

    for tweet in tweets:
        tid = tweet["id"]
        url = tweet["url"]
        text = tweet["text"]
        author = tweet.get("author", "")

        # Like
        if likes_done < max_likes and tid not in liked:
            print(f"  [like] {url[:60]}")
            result = like_tweet(port, url, dry_run=dry_run)
            if result["ok"]:
                _logger.mark_liked(account_dir, tid, url)
                _logger.log_action(account_dir, "like", tid, url, ok=True)
                liked[tid] = url
                likes_done += 1
            else:
                _logger.log_action(account_dir, "like", tid, url, ok=False, note=result.get("error", ""))
            time.sleep(random.uniform(8, 20))

        # Reply
        if replies_done < max_replies and tid not in replied:
            reply_text = reply_fn(text, author)
            if not reply_text or len(reply_text) < 10:
                continue
            print(f"  [reply] {url[:60]}")
            print(f"    → {reply_text[:80]}")
            result = reply_tweet(port, url, reply_text, dry_run=dry_run)
            if result["ok"]:
                _logger.mark_replied(account_dir, tid, url)
                _logger.log_action(account_dir, "reply", tid, url, ok=True, note=reply_text[:100])
                replied[tid] = url
                replies_done += 1
            else:
                _logger.log_action(account_dir, "reply", tid, url, ok=False, note=result.get("error", ""))
            time.sleep(random.uniform(60, 150))

    return {"replies": replies_done, "likes": likes_done}
