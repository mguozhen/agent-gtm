"""
Reply quality test for VOC_ai.
Pulls ~3 live posts per keyword via Chrome, generates replies with the VOC_ai
playbook, and prints results for review.

Usage:
    python3 scripts/reply_quality_test.py --port 10002 --keywords 4 --posts-per 3
"""
import argparse
import json
import os
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env; env.load()
import chrome as _chrome
import generate as _gen


ENGAGE_CONFIG = os.path.join(ROOT_DIR, "accounts", "VOC_ai", "engage_config.json")


def search_posts(ws, query: str, max_posts: int = 5) -> list[dict]:
    """Search X for query, return up to max_posts posts as {author, text}."""
    encoded = query.replace(" ", "%20").replace('"', '%22')
    url = f"https://x.com/search?q={encoded}&f=live"
    _chrome.navigate(ws, url, wait=5)
    _chrome.bring_to_front(ws)
    time.sleep(3)

    posts = []
    seen = set()
    for _ in range(6):
        js = """
        (function() {
            var arts = document.querySelectorAll('article[data-testid="tweet"]');
            var out = [];
            arts.forEach(function(a) {
                var txt = a.querySelector('[data-testid="tweetText"]');
                var handle = a.querySelector('a[href*="/status/"] span');
                var links = a.querySelectorAll('a[href*="/status/"]');
                var url = '';
                links.forEach(function(l) {
                    if (l.href && l.href.includes('/status/') && !url) url = l.href;
                });
                if (txt && txt.innerText.trim().length > 20) {
                    var authorLink = a.querySelector('a[role="link"][href^="/"]');
                    var author = '';
                    if (authorLink) {
                        var m = authorLink.href.match(/x\.com\/([^/]+)$/);
                        if (m) author = m[1];
                    }
                    out.push({author: author, text: txt.innerText.trim(), url: url});
                }
            });
            return JSON.stringify(out);
        })()
        """
        raw = _chrome.eval_js(ws, js)
        if raw:
            try:
                items = json.loads(raw)
                for item in items:
                    key = item.get("url") or item.get("text", "")[:60]
                    if key not in seen and item.get("text"):
                        seen.add(key)
                        posts.append(item)
            except Exception:
                pass

        if len(posts) >= max_posts:
            break
        _chrome.scroll_down(ws, px=800)
        time.sleep(1.5)

    return posts[:max_posts]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",        type=int, default=10002)
    parser.add_argument("--keywords",    type=int, default=4,
                        help="how many keywords to test (sampled from engage_config)")
    parser.add_argument("--posts-per",   type=int, default=3,
                        help="posts to pull per keyword")
    args = parser.parse_args()

    with open(ENGAGE_CONFIG) as f:
        cfg = json.load(f)

    keywords = cfg["keyword_engage"]["keywords"][:args.keywords]
    handle   = cfg["hunter_handle"]  # "VOC_ai"

    ws = _chrome.connect(args.port)
    _chrome.set_viewport(ws, 1280, 1800)

    results = []

    for kw in keywords:
        print(f"\n{'='*60}")
        print(f"KEYWORD: {kw}")
        print('='*60)

        posts = search_posts(ws, kw.strip('"'), max_posts=args.posts_per)
        if not posts:
            print("  (no posts found)")
            continue

        for p in posts:
            author = p.get("author", "unknown")
            text   = p.get("text", "")
            print(f"\n  @{author}: {text[:200]}")
            print(f"  {'─'*50}")

            try:
                reply = _gen.generate_reply(handle, text, tweet_author=author)
                print(f"  REPLY: {reply}")
                results.append({
                    "keyword": kw,
                    "author":  author,
                    "post":    text[:300],
                    "reply":   reply,
                })
            except Exception as e:
                print(f"  ERROR: {e}")

            time.sleep(1)

        time.sleep(2)

    ws.close()

    out_path = os.path.join(ROOT_DIR, "state", "reply_quality_test.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n\nDone. {len(results)} replies → {out_path}")


if __name__ == "__main__":
    main()
