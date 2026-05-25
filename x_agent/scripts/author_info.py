"""Persistent cache for target-account metadata (currently just follower
count). Populated opportunistically by engage_daemon's fetch_latest_post
(same profile navigation that grabs the latest post also reads the follower
count — zero extra Chrome work).

Read by telegram.send_reply_card so approval cards show the target's
follower count alongside post stats. Falls back to scout_candidates.csv
for accounts that haven't been profiled yet.
"""
import csv
import json
import os
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
CACHE_PATH = os.path.join(ROOT_DIR, "state", "author_followers.json")
SCOUT_CSV  = os.path.join(ROOT_DIR, "state", "scout_candidates.csv")

# Cache the file reads to avoid hammering disk on every card render.
_cache = None
_cache_mtime = 0.0
_scout = None
_scout_mtime = 0.0


def _load_cache():
    """Returns {handle_lower: {followers: int, ts: epoch}}."""
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(CACHE_PATH)
    except FileNotFoundError:
        _cache = {}; _cache_mtime = 0.0
        return _cache
    if _cache is None or mtime != _cache_mtime:
        try:
            with open(CACHE_PATH) as f:
                _cache = json.load(f)
            _cache_mtime = mtime
        except (ValueError, OSError):
            _cache = {}
    return _cache


def _load_scout():
    """Returns {handle_lower: followers_int} from scout_candidates.csv."""
    global _scout, _scout_mtime
    try:
        mtime = os.path.getmtime(SCOUT_CSV)
    except FileNotFoundError:
        _scout = {}; _scout_mtime = 0.0
        return _scout
    if _scout is None or mtime != _scout_mtime:
        out = {}
        try:
            with open(SCOUT_CSV) as f:
                for row in csv.DictReader(f):
                    h = (row.get("handle") or "").strip().lower()
                    try:
                        out[h] = int(row.get("followers") or 0)
                    except (ValueError, TypeError):
                        pass
            _scout = out
            _scout_mtime = mtime
        except OSError:
            _scout = {}
    return _scout


def followers(handle: str):
    """Best-effort follower count for @handle. Returns int or None.
    Precedence:
      1) live author_followers.json cache (most accurate, recently updated)
      2) scout_candidates.csv (snapshot, may be days old)
      3) None — caller shows '?' / omits.
    """
    if not handle:
        return None
    h = handle.lstrip("@").lower()
    cache = _load_cache()
    entry = cache.get(h)
    if entry and isinstance(entry.get("followers"), int):
        return entry["followers"]
    scout = _load_scout()
    if h in scout and scout[h] > 0:
        return scout[h]
    return None


def set_followers(handle: str, n: int):
    """Write-through cache update. Called by engage_daemon when it sees a
    fresh follower count on a target's profile page."""
    if not handle or not isinstance(n, int) or n < 0:
        return
    h = handle.lstrip("@").lower()
    cache = _load_cache()
    cache[h] = {"followers": int(n), "ts": int(time.time())}
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2)
    os.replace(tmp, CACHE_PATH)


def format_followers(n) -> str:
    """Render an int as a human-friendly count: 1234 → '1.2K', 23000 → '23K',
    1500000 → '1.5M'. Empty string for None."""
    if not isinstance(n, int) or n < 0:
        return ""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        return f"{n//1000}K"
    if n >= 1_000:
        return f"{n/1000:.1f}K".replace(".0K", "K")
    return str(n)
