"""Drive the VOC_ai Chrome profile through X login using Google auth."""
import json
import os
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env; env.load()
import chrome as _c
from lock import chrome_lock

PORT = int(os.environ.get("VOC_AI_X_PORT", "10002"))
HANDLE = os.environ.get("VOC_AI_X_HANDLE", "VOC_ai")
USER = os.environ.get("VOC_AI_X_USER", "")
PASSWORD = os.environ.get("VOC_AI_X_PASSWORD", "")
MAX_STEPS = 20


def _page_ws_for_port(port: int):
    tabs = _c.list_tabs(port)
    pages = [t for t in tabs if t.get("type") == "page"]
    preferred = []
    for t in pages:
        url = t.get("url", "")
        if "accounts.google.com" in url or "x.com" in url:
            preferred.append(t)
    if not preferred:
        preferred = pages
    if not preferred:
        raise RuntimeError(f"No page tabs on port {port}")
    target = preferred[-1]
    import websocket
    return websocket.create_connection(
        target["webSocketDebuggerUrl"],
        timeout=20,
        header=["Origin: devtools://devtools"],
    )


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
                    aria: i.getAttribute('aria-label') || '',
                });
            });
            var buttons = [];
            document.querySelectorAll('button, [role="button"], div[role="button"]').forEach(function(b) {
                var rect = b.getBoundingClientRect();
                if (rect.width === 0) return;
                var text = (b.innerText || b.textContent || '').trim();
                if (!text) return;
                buttons.push({text: text.slice(0, 60),
                              testid: b.getAttribute('data-testid') || ''});
            });
            return {
                url: location.href,
                title: document.title,
                inputs: inputs,
                buttons: buttons.slice(0, 25),
                body_head: (document.body ? document.body.innerText : '').slice(0, 800),
                has_home: !!document.querySelector('[data-testid="primaryColumn"], [data-testid="AppTabBar_Home_Link"]'),
            };
        })())
    """)
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}


def detect_step(s: dict) -> str:
    url = s.get("url", "")
    body = (s.get("body_head", "") or "").lower()
    inputs = s.get("inputs", [])
    buttons = s.get("buttons", [])

    if s.get("has_home"):
        return "logged_in"
    if "/home" in url and "login" not in url:
        return "logged_in"
    if "accounts.google.com" in url:
        if any(i["type"] == "password" for i in inputs):
            return "google_password"
        if any(i["type"] in ("text", "email") for i in inputs):
            return "google_email"
        if any("continue" in (b.get("text") or "").lower() or "next" in (b.get("text") or "").lower()
               for b in buttons):
            return "google_continue"
    if any("google" in (b.get("text") or "").lower() for b in buttons):
        return "x_google"
    if any(i["type"] == "password" for i in inputs):
        return "x_password"
    if "unusual login activity" in body or any(
        b.get("testid", "").startswith("ocfEnterText") for b in buttons
    ):
        return "x_challenge"
    if any(i.get("autocomplete") == "username" for i in inputs):
        return "x_email"
    if any(i["type"] in ("text", "email") for i in inputs):
        return "x_email"
    if "captcha" in body or "arkose" in body:
        return "captcha"
    return "unknown"


def _type_chars(ws, text: str):
    for ch in text:
        _c._send(ws, "Input.insertText", {"text": ch}, msg_id=80)
        time.sleep(0.05)


def type_into_first_visible(ws, text="", include_password=False) -> bool:
    res = _c.eval_js(ws, f"""
        (function() {{
            var inps = document.querySelectorAll('input');
            for (var i = 0; i < inps.length; i++) {{
                var r = inps[i].getBoundingClientRect();
                if (r.width === 0) continue;
                if (!{str(include_password).lower()} && inps[i].type === 'password') continue;
                inps[i].focus();
                try {{
                    inps[i].select();
                    document.execCommand('delete', false, null);
                }} catch(e) {{}}
                return 'focused';
            }}
            return 'not_found';
        }})()
    """)
    if res != "focused":
        return False
    _type_chars(ws, text)
    return True


def click_button_text(ws, *substrings) -> bool:
    js = """
        (function() {
            var wants = %s;
            var els = document.querySelectorAll('button, [role="button"], div[role="button"]');
            for (var i = 0; i < els.length; i++) {
                var e = els[i];
                var r = e.getBoundingClientRect();
                if (r.width === 0) continue;
                var t = (e.innerText || e.textContent || '').trim().toLowerCase();
                for (var j = 0; j < wants.length; j++) {
                    if (t.indexOf(wants[j]) >= 0) {
                        e.click();
                        return 'clicked:' + t.slice(0, 50);
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
    if not USER or not PASSWORD:
        print("[X] missing VOC_AI_X_USER or VOC_AI_X_PASSWORD in environment")
        return

    with chrome_lock(PORT, on_wait=lambda m: print(f"  [wait] {m}")):
        ws = _page_ws_for_port(PORT)
        _c.bring_to_front(ws)
        current = _c.eval_js(ws, "location.href")
        if "x.com" not in current and "accounts.google.com" not in current:
            _c.navigate(ws, "https://x.com/i/flow/login", wait=3.0)

        for step in range(MAX_STEPS):
            try:
                ws.close()
            except Exception:
                pass
            ws = _page_ws_for_port(PORT)
            _c.bring_to_front(ws)

            t0 = time.time()
            while time.time() - t0 < 10:
                s = js_state(ws)
                if s.get("has_home") or s.get("inputs") or s.get("buttons"):
                    break
                time.sleep(0.5)

            st = detect_step(s)
            print(f"[step {step}] state={st!r} url={s.get('url')!r}")

            if st == "logged_in":
                print(f"✅ logged in as @{HANDLE}")
                return
            if st == "x_google":
                if not click_button_text(ws, "sign in with google", "continue with google", "google"):
                    print("[X] no Google button")
                    return
                time.sleep(2.0)
                continue
            if st == "google_email":
                if not type_into_first_visible(ws, text=USER):
                    print("[X] couldn't type Google email")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "next"):
                    print("[X] no Next button on Google email step")
                    return
                time.sleep(2.0)
                continue
            if st == "google_password":
                if not type_into_first_visible(ws, text=PASSWORD, include_password=True):
                    print("[X] couldn't type Google password")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "next"):
                    print("[X] no Next button on Google password step")
                    return
                time.sleep(3.0)
                continue
            if st == "google_continue":
                if not click_button_text(ws, "continue", "allow", "next"):
                    print("[X] no Continue/Allow button on Google step")
                    return
                time.sleep(2.0)
                continue
            if st == "x_challenge":
                if not type_into_first_visible(ws, text=HANDLE):
                    print("[X] couldn't type X handle challenge")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "next", "下一步"):
                    print("[X] no Next button on X challenge")
                    return
                time.sleep(2.0)
                continue
            if st == "x_email":
                if not type_into_first_visible(ws, text=USER):
                    print("[X] couldn't type X email")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "next", "下一步"):
                    print("[X] no Next button on X email step")
                    return
                time.sleep(2.0)
                continue
            if st == "x_password":
                if not type_into_first_visible(ws, text=PASSWORD, include_password=True):
                    print("[X] couldn't type X password")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "log in", "login"):
                    print("[X] no Log in button on X password step")
                    return
                time.sleep(3.0)
                continue
            if st == "captcha":
                print("[X] CAPTCHA / Arkose challenge — needs manual hands.")
                return

            print(f"[X] unknown step. body: {s.get('body_head', '')[:250]!r}")
            return

        print(f"[X] exceeded {MAX_STEPS} steps without landing on home")


if __name__ == "__main__":
    main()
