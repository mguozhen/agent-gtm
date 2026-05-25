"""Drive the Chrome on shulex's port (10005) through the X login flow using
saved credentials. State-machine style: inspects the current page on each tick
and dispatches the right action, so it works whether we start at /flow/login
fresh, mid-challenge, or already past the email step.

Stops and reports if X presents a CAPTCHA / Arkose challenge that needs human
hands.
"""
import json
import os
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;   env.load()
import chrome as _c
from lock import chrome_lock

PORT     = 10005
HANDLE   = "VOC_ai"
USER     = "shulextech@gmail.com"
PASSWORD = "Shulex0828#"

MAX_STEPS = 12   # safety cap to prevent infinite loops


def js_state(ws) -> dict:
    raw = _c.eval_js(ws, """
        JSON.stringify((function() {
            var inputs = [];
            document.querySelectorAll('input').forEach(function(i) {
                var rect = i.getBoundingClientRect();
                if (rect.width === 0) return;
                inputs.push({
                    name: i.name || '',
                    type: i.type || '',
                    autocomplete: i.autocomplete || '',
                    value_len: (i.value || '').length,
                });
            });
            var buttons = [];
            document.querySelectorAll('button, [role="button"]').forEach(function(b) {
                var rect = b.getBoundingClientRect();
                if (rect.width === 0) return;
                var text = (b.innerText || '').trim();
                if (!text) return;
                buttons.push({text: text.slice(0, 40),
                              testid: b.getAttribute('data-testid') || ''});
            });
            return {
                url: location.href,
                title: document.title,
                inputs: inputs,
                buttons: buttons.slice(0, 15),
                body_head: (document.body ? document.body.innerText : '').slice(0, 400),
                has_home: !!document.querySelector('[data-testid="primaryColumn"], [data-testid="AppTabBar_Home_Link"]'),
            };
        })())
    """)
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}


def detect_step(s: dict) -> str:
    """Map page state → one of: logged_in | email | challenge | password |
    captcha | unknown."""
    if s.get("has_home"):
        return "logged_in"
    if "/home" in s.get("url", "") and not any("login" in b["text"].lower()
                                               for b in s.get("buttons", [])):
        return "logged_in"
    inputs = s.get("inputs", [])
    if any(i["type"] == "password" for i in inputs):
        return "password"
    body = s.get("body_head", "").lower()
    if "unusual login activity" in body or any(
        b.get("testid", "").startswith("ocfEnterText") for b in s.get("buttons", [])
    ):
        return "challenge"
    if any(i.get("autocomplete") == "username" for i in inputs):
        return "email"
    if any(i["type"] in ("text", "email") for i in inputs):
        # Best-guess: first-step email field even if autocomplete is missing.
        return "email"
    if "arkose" in body or "captcha" in body or "verify" in body:
        return "captcha"
    return "unknown"


def type_into_first_visible(ws, exclude_password=True, text="") -> bool:
    """Focus + clear the first visible non-password input, then CDP-type text."""
    res = _c.eval_js(ws, f"""
        (function() {{
            var inps = document.querySelectorAll('input');
            for (var i = 0; i < inps.length; i++) {{
                var r = inps[i].getBoundingClientRect();
                if (r.width === 0) continue;
                if ({"true" if exclude_password else "false"} && inps[i].type === 'password') continue;
                inps[i].focus();
                try {{ inps[i].select(); document.execCommand('delete', false, null); }} catch(e) {{}}
                inps[i].value = '';
                return 'focused';
            }}
            return 'not_found';
        }})()
    """)
    if res != "focused":
        print(f"  focus_first: {res}")
        return False
    r = _c._send(ws, "Input.insertText", {"text": text}, msg_id=80)
    if r.get("error"):
        print(f"  insertText error: {r.get('error')}")
        return False
    return True


def type_into_password(ws, text: str) -> bool:
    """Focus the password input then type."""
    res = _c.eval_js(ws, """
        (function() {
            var inps = document.querySelectorAll('input[type="password"]');
            for (var i = 0; i < inps.length; i++) {
                var r = inps[i].getBoundingClientRect();
                if (r.width === 0) continue;
                inps[i].focus();
                inps[i].value = '';
                return 'focused';
            }
            return 'not_found';
        })()
    """)
    if res != "focused":
        print(f"  focus_pwd: {res}")
        return False
    r = _c._send(ws, "Input.insertText", {"text": text}, msg_id=81)
    if r.get("error"):
        print(f"  insertText pwd error: {r.get('error')}")
        return False
    return True


def click_button_text(ws, *substrings) -> bool:
    """Click the first visible button whose text contains any of the given
    substrings (case-insensitive)."""
    js = """
        (function() {
            var wants = %s;
            var els = document.querySelectorAll('button, [role="button"]');
            for (var i = 0; i < els.length; i++) {
                var e = els[i];
                var r = e.getBoundingClientRect();
                if (r.width === 0) continue;
                var t = (e.innerText || '').trim().toLowerCase();
                for (var j = 0; j < wants.length; j++) {
                    if (t.indexOf(wants[j]) >= 0) {
                        e.click();
                        return 'clicked:' + t.slice(0, 30);
                    }
                }
            }
            return 'not_found';
        })()
    """ % json.dumps([s.lower() for s in substrings])
    res = _c.eval_js(ws, js)
    print(f"  click {substrings}: {res}")
    return res.startswith("clicked:")


def main():
    with chrome_lock(PORT, on_wait=lambda m: print(f"  [wait] {m}")):
        ws = _c.connect(PORT)
        _c.bring_to_front(ws)

        # If not on login flow, navigate there once.
        current = _c.eval_js(ws, "location.href")
        if "/i/flow/login" not in current and "/home" not in current:
            _c.navigate(ws, "https://x.com/i/flow/login", wait=3.0)

        for step in range(MAX_STEPS):
            # Wait for stable state (poll up to ~10s for inputs/buttons to mount)
            t0 = time.time()
            while time.time() - t0 < 10:
                s = js_state(ws)
                if s.get("has_home") or s.get("inputs") or any(
                    b.get("testid", "").startswith("ocfEnterText")
                    for b in s.get("buttons", [])
                ):
                    break
                time.sleep(0.7)

            st = detect_step(s)
            print(f"\n[step {step}] state={st!r} url={s.get('url')!r} "
                  f"inputs={[(i['type'],i.get('autocomplete','')) for i in s.get('inputs', [])]} "
                  f"buttons={[b['text'] for b in s.get('buttons', [])]}")

            if st == "logged_in":
                print(f"\n✅ logged in as @{HANDLE}")
                return
            elif st == "email":
                if not type_into_first_visible(ws, exclude_password=True, text=USER):
                    print("[X] couldn't type email")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "next"):
                    print("[X] no Next button")
                    return
                time.sleep(1.0)
            elif st == "challenge":
                if not type_into_first_visible(ws, exclude_password=True, text=HANDLE):
                    print("[X] couldn't type handle")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "next"):
                    print("[X] no Next on challenge")
                    return
                time.sleep(1.0)
            elif st == "password":
                if not type_into_password(ws, PASSWORD):
                    print("[X] couldn't type password")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "log in", "login"):
                    print("[X] no Log in button")
                    return
                time.sleep(3.0)
            elif st == "captcha":
                print("[X] CAPTCHA / Arkose challenge — needs manual hands.")
                print(f"  body: {s.get('body_head')[:200]!r}")
                return
            else:
                print(f"[X] unknown step. body: {s.get('body_head')[:200]!r}")
                return

        print(f"[X] exceeded {MAX_STEPS} steps without landing on home")


if __name__ == "__main__":
    main()
