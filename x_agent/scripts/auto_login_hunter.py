"""Drive the Chrome on hunter's port (10000) through the X login flow using
saved credentials. Mirror of auto_login_shulex.py — see that file for the
state-machine logic.
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

PORT     = 10000
HANDLE   = "GuoHunter95258"
USER     = "zhen.guo@anker.com"
PASSWORD = "solvea2026!"

# Optional 2FA backup code, supplied via --backup-code CLI flag.
BACKUP_CODE = ""

MAX_STEPS = 16


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
    if s.get("has_home"):
        return "logged_in"
    if "/home" in s.get("url", "") and not any("login" in b["text"].lower()
                                               for b in s.get("buttons", [])):
        return "logged_in"
    inputs = s.get("inputs", [])
    body = s.get("body_head", "") or ""
    body_l = body.lower()
    # 2FA backup-code page (Chinese: 备份代码 / English: backup code)
    if "备份代码" in body or "backup code" in body_l:
        return "backup_code"
    # 2FA TOTP / SMS code page — show a "use backup code" affordance
    if ("验证码" in body and ("身份验证" in body or "auth" in body_l)) or \
       any("使用备份代码" in (b.get("text") or "") or "use backup" in (b.get("text") or "").lower()
           for b in s.get("buttons", [])):
        return "twofa"
    if any(i["type"] == "password" for i in inputs):
        return "password"
    if "unusual login activity" in body_l or any(
        b.get("testid", "").startswith("ocfEnterText") for b in s.get("buttons", [])
    ):
        return "challenge"
    if any(i.get("autocomplete") == "username" for i in inputs):
        return "email"
    if any(i["type"] in ("text", "email") for i in inputs):
        return "email"
    if "arkose" in body_l or "captcha" in body_l:
        return "captcha"
    return "unknown"


def _type_chars(ws, text: str):
    # one-char-at-a-time Input.insertText so React's onChange fires per stroke.
    # value='' clearing is intentionally omitted — it bypasses React state and
    # confuses X's submit gating on the Chinese-locale flow.
    for ch in text:
        _c._send(ws, "Input.insertText", {"text": ch}, msg_id=80)
        time.sleep(0.05)


def type_into_first_visible(ws, exclude_password=True, text="") -> bool:
    res = _c.eval_js(ws, f"""
        (function() {{
            var inps = document.querySelectorAll('input');
            for (var i = 0; i < inps.length; i++) {{
                var r = inps[i].getBoundingClientRect();
                if (r.width === 0) continue;
                if ({"true" if exclude_password else "false"} && inps[i].type === 'password') continue;
                inps[i].focus();
                return 'focused';
            }}
            return 'not_found';
        }})()
    """)
    if res != "focused":
        print(f"  focus_first: {res}")
        return False
    _type_chars(ws, text)
    val = _c.eval_js(ws, "document.activeElement && document.activeElement.value || ''")
    print(f"  after-type val_len={len(val)} val={val!r}")
    if len(val) != len(text):
        return False
    return True


def type_into_password(ws, text: str) -> bool:
    res = _c.eval_js(ws, """
        (function() {
            var inps = document.querySelectorAll('input[type="password"]');
            for (var i = 0; i < inps.length; i++) {
                var r = inps[i].getBoundingClientRect();
                if (r.width === 0) continue;
                inps[i].focus();
                return 'focused';
            }
            return 'not_found';
        })()
    """)
    if res != "focused":
        print(f"  focus_pwd: {res}")
        return False
    _type_chars(ws, text)
    return True


def click_button_text(ws, *substrings) -> bool:
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
        # Input.insertText silently fails when Chrome isn't macOS-frontmost
        # ([[x-agent-focus-emulation]] incident). Force visibility='visible'.
        try:
            _c._send(ws, "Emulation.setFocusEmulationEnabled",
                     {"enabled": True}, msg_id=90)
        except Exception as e:
            print(f"  warn: setFocusEmulationEnabled failed: {e}")

        current = _c.eval_js(ws, "location.href")
        if "/i/flow/login" not in current and "/home" not in current:
            _c.navigate(ws, "https://x.com/i/flow/login", wait=3.0)

        for step in range(MAX_STEPS):
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
                if not click_button_text(ws, "next", "下一步"):
                    print("[X] no Next button")
                    return
                time.sleep(1.0)
            elif st == "challenge":
                if not type_into_first_visible(ws, exclude_password=True, text=HANDLE):
                    print("[X] couldn't type handle")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "next", "下一步"):
                    print("[X] no Next on challenge")
                    return
                time.sleep(1.0)
            elif st == "password":
                if not type_into_password(ws, PASSWORD):
                    print("[X] couldn't type password")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "log in", "login", "登录"):
                    print("[X] no Log in button")
                    return
                time.sleep(3.0)
            elif st == "twofa":
                # Click "use backup code" link to switch into backup-code mode
                if not click_button_text(ws, "使用备份代码", "use backup code", "备份代码"):
                    print("[X] no 'use backup code' link on 2FA page")
                    return
                time.sleep(2.0)
            elif st == "backup_code":
                if not BACKUP_CODE:
                    print("[X] backup-code step but no --backup-code supplied")
                    return
                code = BACKUP_CODE.replace(" ", "")
                if not type_into_first_visible(ws, exclude_password=True, text=code):
                    print("[X] couldn't type backup code")
                    return
                time.sleep(0.6)
                if not click_button_text(ws, "next", "下一步", "verify", "确认", "log in", "登录"):
                    print("[X] no submit button on backup-code page")
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
    if "--backup-code" in sys.argv:
        i = sys.argv.index("--backup-code")
        BACKUP_CODE = sys.argv[i + 1]
    main()
