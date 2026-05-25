"""Fetch candidate tweets from X search via browser CDP."""
import time
import sys
import os

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from chrome import connect, navigate, get_tweets, scroll_down, eval_js

MIN_LIKES = 0        # include all tweets; caller can filter by likes
MAX_TEXT_LEN = 2000  # truncate very long threads


def _wait_for_tweets(ws, timeout: int = 15) -> list[dict]:
    """Poll until tweets appear in the DOM or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        tweets = get_tweets(ws)
        if tweets:
            return tweets
        # Check if the page is still loading
        ready = eval_js(ws, "document.readyState")
        time.sleep(1.5)
    return []


def search(port: int, keyword: str, mode: str = "top", limit: int = 20) -> list[dict]:
    """
    Search X for keyword and return candidate tweets.
    mode: "top" | "live" (X search tabs)
    Returns list of {id, text, url, likes, author}.
    """
    f = "top" if mode == "top" else "live"
    url = f"https://x.com/search?q={_encode(keyword)}&src=typed_query&f={f}"

    ws = connect(port)
    try:
        navigate(ws, url, wait=3.0)
        tweets = _wait_for_tweets(ws, timeout=15)

        # Scroll once to load more results
        if len(tweets) < 5:
            scroll_down(ws, px=600)
            time.sleep(2.0)
            tweets = get_tweets(ws)

        candidates = []
        seen_ids: set[str] = set()
        for t in tweets:
            if not t.get("id") or not t.get("text"):
                continue
            if t["id"] in seen_ids:
                continue
            if t.get("likes", 0) < MIN_LIKES:
                continue
            seen_ids.add(t["id"])
            candidates.append({
                "id": t["id"],
                "text": t["text"][:MAX_TEXT_LEN],
                "url": t.get("url", f"https://x.com/i/status/{t['id']}"),
                "likes": t.get("likes", 0),
                "author": t.get("author", ""),
            })
            if len(candidates) >= limit:
                break

        return candidates
    finally:
        ws.close()


def get_profile_tweets(port: int, handle: str, limit: int = 10) -> list[dict]:
    """
    Fetch recent tweets from a specific user's profile.
    Used for matrix boost (engaging with partner accounts).
    """
    ws = connect(port)
    try:
        navigate(ws, f"https://x.com/{handle}", wait=3.0)
        tweets = _wait_for_tweets(ws, timeout=15)

        seen_ids: set[str] = set()
        results = []
        for t in tweets:
            if not t.get("id") or not t.get("text"):
                continue
            if t["id"] in seen_ids:
                continue
            seen_ids.add(t["id"])
            results.append(t)
            if len(results) >= limit:
                break
        return results
    finally:
        ws.close()


def _encode(keyword: str) -> str:
    """URL-encode keyword for X search."""
    import urllib.parse
    return urllib.parse.quote_plus(keyword)
