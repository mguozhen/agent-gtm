"""
Login manager for x-agent.

New account:   python3 login.py --new VocAiSage
Existing:      python3 login.py VocAiSage
"""
import json, os, subprocess, sys, time, urllib.request

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
ACCOUNTS_DIR  = os.path.join(ROOT_DIR, "accounts")
PROFILES_DIR  = os.path.join(ROOT_DIR, "chrome-profiles")

START_PORT = 10000


def _used_ports() -> set[int]:
    ports = set()
    if not os.path.exists(ACCOUNTS_DIR):
        return ports
    for handle in os.listdir(ACCOUNTS_DIR):
        cfg = os.path.join(ACCOUNTS_DIR, handle, "config.json")
        if os.path.exists(cfg):
            try:
                p = json.load(open(cfg)).get("chrome_port")
                if p:
                    ports.add(p)
            except Exception:
                pass
    return ports


def find_free_port() -> int:
    used = _used_ports()
    port = START_PORT
    while port in used:
        port += 1
    return port


def chrome_running(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{port}/json", timeout=3)
        return True
    except Exception:
        return False


def ensure_page_tab(port: int):
    """Open a blank tab if Chrome has no page tabs."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/json", timeout=5) as r:
            tabs = json.loads(r.read())
        if any(t.get("type") == "page" for t in tabs):
            return
        req = urllib.request.Request(
            f"http://localhost:{port}/json/new?url=about:blank", method="PUT"
        )
        urllib.request.urlopen(req, timeout=5)
        time.sleep(1)
    except Exception as e:
        print(f"[login] warn: could not open tab: {e}")


def launch_chrome(port: int, profile_dir: str):
    subprocess.Popen(
        [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            # Park the window off-screen so Page.bringToFront (needed so X
            # doesn't pause the tab via Page Visibility API) doesn't yank the
            # window onto the active display every navigation. Tab stays
            # visible to the page; only the OS-level window is hidden.
            "--window-position=-32000,-32000",
            "--window-size=400,300",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"[login] launching Chrome on port {port} ...")
    for _ in range(20):
        time.sleep(1)
        if chrome_running(port):
            print(f"[login] Chrome ready on port {port}")
            return
    raise RuntimeError("Chrome did not start within 20 s")


def _create_account_files(handle: str, port: int):
    account_dir = os.path.join(ACCOUNTS_DIR, handle)
    os.makedirs(os.path.join(account_dir, "logs"), exist_ok=True)

    config_path = os.path.join(account_dir, "config.json")
    with open(config_path, "w") as f:
        json.dump({
            "handle": handle,
            "chrome_port": port,
            "daily_limits": {
                "posts": 0,
                "replies": 20,
                "likes": 0,
            },
        }, f, indent=2)

    for fname in ("soul.md", "playbook.md"):
        dest = os.path.join(account_dir, fname)
        if not os.path.exists(dest):
            with open(dest, "w") as f:
                f.write(f"# {handle} — {fname.replace('.md','').capitalize()}\n\n[Fill in content]\n")

    targets_path = os.path.join(account_dir, "targets.json")
    if not os.path.exists(targets_path):
        with open(targets_path, "w") as f:
            json.dump({
                "keywords": [],
                "matrix_accounts": [],
            }, f, indent=2)

    print(f"[login] account files created at accounts/{handle}/")
    print(f"[login] fill in: soul.md  playbook.md  targets.json")


def new_account(handle: str):
    account_dir = os.path.join(ACCOUNTS_DIR, handle)
    if os.path.exists(account_dir):
        print(f"[login] ERROR: '{handle}' already exists. Use: python3 login.py {handle}")
        sys.exit(1)

    port = find_free_port()
    profile_dir = os.path.join(PROFILES_DIR, handle)
    os.makedirs(profile_dir, exist_ok=True)

    if not chrome_running(port):
        launch_chrome(port, profile_dir)
    ensure_page_tab(port)

    _create_account_files(handle, port)

    print(f"\n[login] Chrome is open on port {port}.")
    print(f"[login] Go to x.com in that window and log in as @{handle}.")
    print(f"[login] When done, press Enter to confirm and close.")
    input()
    print(f"[login] Session saved to chrome-profiles/{handle}/")
    print(f"[login] Next run: python3 login.py {handle}")


def existing_account(handle: str):
    config_path = os.path.join(ACCOUNTS_DIR, handle, "config.json")
    if not os.path.exists(config_path):
        print(f"[login] ERROR: '{handle}' not found. Use: python3 login.py --new {handle}")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    port = config["chrome_port"]
    profile_dir = os.path.join(PROFILES_DIR, handle)

    if chrome_running(port):
        print(f"[login] Chrome already running on port {port} for @{handle}")
    else:
        if not os.path.exists(profile_dir):
            print(f"[login] ERROR: profile not found at {profile_dir}")
            print(f"[login] Run: python3 login.py --new {handle}")
            sys.exit(1)
        launch_chrome(port, profile_dir)

    ensure_page_tab(port)
    print(f"[login] @{handle} ready on port {port}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--new":
        new_account(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] not in ("--new", "--help"):
        existing_account(sys.argv[1])
    else:
        print("Usage:")
        print("  python3 login.py --new {handle}   # first-time setup, opens Chrome for manual login")
        print("  python3 login.py {handle}          # relaunch Chrome with saved session")
        sys.exit(0)
