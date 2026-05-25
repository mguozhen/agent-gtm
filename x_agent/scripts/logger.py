"""JSONL logger for x-agent. One file per day per account."""
import json
import os
from datetime import date


def _log_dir(account_dir: str) -> str:
    d = os.path.join(account_dir, "logs")
    os.makedirs(d, exist_ok=True)
    return d


# ── Daily action log ─────────────────────────────────────────────

def log_action(account_dir: str, action: str, tweet_id: str, url: str = "", ok: bool = True, note: str = ""):
    """Append one action to today's JSONL log. action = post|reply|like|follow."""
    path = os.path.join(_log_dir(account_dir), f"{date.today()}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps({
            "action": action,
            "tweet_id": tweet_id,
            "url": url,
            "ok": ok,
            "note": note,
        }) + "\n")


def replied_authors_today(account_dir: str) -> set:
    """Return lowercase set of authors we've already replied to today."""
    path = os.path.join(_log_dir(account_dir), f"{date.today()}.jsonl")
    if not os.path.exists(path):
        return set()
    authors = set()
    for line in open(path):
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("action") == "reply" and e.get("ok"):
            url = e.get("url", "")
            if url:
                author = url.split("/")[3].lower()
                authors.add(author)
    return authors


def today_stats(account_dir: str) -> dict:
    path = os.path.join(_log_dir(account_dir), f"{date.today()}.jsonl")
    if not os.path.exists(path):
        return {"posts": 0, "replies": 0, "likes": 0, "errors": 0}
    entries = [json.loads(l) for l in open(path) if l.strip()]
    return {
        "posts":   sum(1 for e in entries if e.get("action") == "post"  and e.get("ok")),
        "replies": sum(1 for e in entries if e.get("action") == "reply" and e.get("ok")),
        "likes":   sum(1 for e in entries if e.get("action") == "like"  and e.get("ok")),
        "errors":  sum(1 for e in entries if not e.get("ok")),
    }


# ── Replied / liked deduplication ────────────────────────────────

def load_replied(account_dir: str) -> dict:
    """Returns {tweet_id: url} of all tweets we've replied to."""
    path = os.path.join(_log_dir(account_dir), "replied.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_replied(account_dir: str, replied: dict):
    path = os.path.join(_log_dir(account_dir), "replied.json")
    with open(path, "w") as f:
        json.dump(replied, f, indent=2)


def mark_replied(account_dir: str, tweet_id: str, url: str = ""):
    replied = load_replied(account_dir)
    replied[tweet_id] = url
    save_replied(account_dir, replied)


def load_liked(account_dir: str) -> dict:
    """Returns {tweet_id: url} of all tweets we've liked."""
    path = os.path.join(_log_dir(account_dir), "liked.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_liked(account_dir: str, liked: dict):
    path = os.path.join(_log_dir(account_dir), "liked.json")
    with open(path, "w") as f:
        json.dump(liked, f, indent=2)


def mark_liked(account_dir: str, tweet_id: str, url: str = ""):
    liked = load_liked(account_dir)
    liked[tweet_id] = url
    save_liked(account_dir, liked)


def load_posted(account_dir: str) -> list[dict]:
    """Returns list of our own posts: [{date, text, url}]."""
    path = os.path.join(_log_dir(account_dir), "posted.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def save_posted(account_dir: str, posts: list[dict]):
    path = os.path.join(_log_dir(account_dir), "posted.json")
    with open(path, "w") as f:
        json.dump(posts, f, indent=2)


def log_posted(account_dir: str, text: str, url: str = ""):
    posts = load_posted(account_dir)
    posts.append({"date": str(date.today()), "text": text, "url": url})
    save_posted(account_dir, posts)


def posted_today(account_dir: str) -> list[str]:
    """Return text of posts made today (for duplicate detection)."""
    posts = load_posted(account_dir)
    today = str(date.today())
    return [p["text"] for p in posts if p.get("date") == today]


# ── Growth tracking (replaces reddit karma) ───────────────────────

def log_growth(account_dir: str, followers: int, following: int, tweets: int):
    """Snapshot daily follower/tweet counts."""
    path = os.path.join(_log_dir(account_dir), "growth.json")
    history = []
    if os.path.exists(path):
        with open(path) as f:
            history = json.load(f)
    today = str(date.today())
    history = [e for e in history if e.get("date") != today]
    history.append({"date": today, "followers": followers, "following": following, "tweets": tweets})
    with open(path, "w") as f:
        json.dump(history, f, indent=2)
