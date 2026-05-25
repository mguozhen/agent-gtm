"""Post an original tweet via X.com browser CDP."""
import time
import sys
import os

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from chrome import connect, navigate, eval_js, paste_text, click_testid, get_tweets, _send
from lock import chrome_lock


def post_tweet(port: int, text: str, handle: str = "", dry_run: bool = False) -> dict:
    """
    Post an original tweet. Holds chrome_lock so we don't race with
    engage_daemon / telegram_bridge on the same Chrome instance.
    Returns {"ok": bool, "error": str | None, "url": str}.
    """
    if dry_run:
        print(f"  [post] dry-run: {text[:80]}")
        return {"ok": True, "dry_run": True}

    with chrome_lock(port):
        ws = connect(port)
        try:
            navigate(ws, "https://x.com/home", wait=4.0)

            # Spoof focus so Draft.js processes Input.insertText even when
            # Chrome isn't the frontmost macOS app. Without this, paste below
            # silently no-ops when Chrome is backgrounded and the submit
            # appears to succeed but no post lands (the bridge then marks the
            # entry "posted" even though X received nothing).
            try:
                _send(ws, "Emulation.setFocusEmulationEnabled",
                      {"enabled": True}, msg_id=43)
            except Exception as e:
                print(f"[post_tweet] focus emulation set: {e} (continuing)",
                      flush=True)

            # Focus the compose box — click the "What is happening?" area
            focused = eval_js(ws, """
                (function() {
                    var box = document.querySelector('[data-testid="tweetTextarea_0"]');
                    if (!box) box = document.querySelector('[contenteditable="true"]');
                    if (!box) return 'not found';
                    box.click();
                    box.focus();
                    return 'ok';
                })()
            """)
            if focused == "not found":
                return {"ok": False, "error": "compose_box_not_found"}
            time.sleep(1.0)

            # paste_text uses CDP Input.insertText (primary) which fires the
            # beforeinput/input events Draft.js needs to enable the submit
            # button. Accept both 'ok' and 'ok-fallback' as success.
            result = paste_text(ws, text)
            if result not in ("ok", "ok-fallback"):
                return {"ok": False, "error": f"paste_failed: {result}"}
            time.sleep(1.5)

            # Click the tweet submit button
            clicked = click_testid(ws, "tweetButtonInline")
            if clicked == "not found":
                clicked = click_testid(ws, "tweetButton")
            if clicked == "not found":
                return {"ok": False, "error": "submit_button_not_found"}
            time.sleep(4.0)

            # Verify: compose box should be empty after successful post
            remaining = eval_js(ws, """
                (function() {
                    var box = document.querySelector('[data-testid="tweetTextarea_0"]');
                    if (!box) box = document.querySelector('[contenteditable="true"]');
                    return box ? box.innerText.trim() : '';
                })()
            """)
            if remaining and len(remaining) > 10:
                return {"ok": False, "error": "compose_not_cleared_after_post"}

            # Navigate to profile page to grab the URL of the just-posted tweet
            tweet_url = ""
            if handle:
                try:
                    navigate(ws, f"https://x.com/{handle}", wait=3.0)
                    tweets = get_tweets(ws)
                    if tweets:
                        tweet_url = tweets[0].get("url", "")
                except Exception:
                    pass

            return {"ok": True, "error": None, "url": tweet_url}
        finally:
            ws.close()
