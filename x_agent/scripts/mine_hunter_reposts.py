"""mine_hunter_reposts — one-shot discovery: scroll Hunter's profile, extract
the authors of every "You reposted" item, count + rank.

The output is a candidate account list you can paste into
engage_config.json → ai_news_scout.accounts. The premise: anyone Hunter has
already reposted is, by definition, the kind of voice that fits the brand.

Usage:
    python3 scripts/mine_hunter_reposts.py
    python3 scripts/mine_hunter_reposts.py --scrolls 20 --top 30
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;     env.load()
import chrome as _chrome
from lock import chrome_lock

HUNTER_HANDLE = "GuoHunter95258"
HUNTER_PORT   = 10000

# JS that walks the timeline and pulls (author, text, url) ONLY from reposted
# items. We detect reposts by the "<X> reposted" / "You reposted" header that
# X renders above the article — `socialContext` is the data-testid X uses.
REPOSTS_JS = r"""
(function() {
    var arts = document.querySelectorAll('article[data-testid="tweet"]');
    var out = [];
    arts.forEach(function(el) {
        var ctx = el.querySelector('[data-testid="socialContext"]');
        var ctxText = ctx ? (ctx.innerText || '') : '';
        if (!/reposted/i.test(ctxText)) return;

        var urlEl = el.querySelector('a[href*="/status/"]');
        var url = urlEl ? urlEl.href : '';
        if (!url) return;
        var authorMatch = url.match(/x\.com\/([A-Za-z0-9_]+)\/status\//);
        var author = authorMatch ? authorMatch[1] : '';
        if (!author) return;

        var textEl = el.querySelector('[data-testid="tweetText"]');
        var text   = textEl ? textEl.innerText.trim().slice(0,300) : '';

        out.push({author: author, url: url, text: text});
    });
    return JSON.stringify(out);
})()
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scrolls", type=int, default=15,
                   help="how many scroll-passes to do (each loads ~5-10 more posts)")
    p.add_argument("--top",     type=int, default=30,
                   help="show top-N reposted authors")
    args = p.parse_args()

    ws = _chrome.connect(HUNTER_PORT)
    seen_urls = set()
    counter   = Counter()
    samples   = {}  # author -> first repost url we saw, for spot-checking

    try:
        with chrome_lock(HUNTER_PORT, on_wait=lambda s: print(f"  lock wait {s:.0f}s")):
            _chrome.navigate(ws, f"https://x.com/{HUNTER_HANDLE}", wait=4.0)
            time.sleep(1.5)

            for i in range(args.scrolls):
                raw = _chrome.eval_js(ws, REPOSTS_JS)
                try:
                    posts = json.loads(raw) if raw else []
                except Exception:
                    posts = []
                new = 0
                for p in posts:
                    if p["url"] in seen_urls:
                        continue
                    seen_urls.add(p["url"])
                    a = p["author"]
                    # Skip Hunter's own posts that happen to also appear (shouldn't,
                    # but belt-and-suspenders against X showing your own re-shared content).
                    if a.lower() == HUNTER_HANDLE.lower():
                        continue
                    counter[a] += 1
                    samples.setdefault(a, p["url"])
                    new += 1
                print(f"  scroll #{i+1}: {len(posts)} cards visible, "
                      f"{new} new reposts, {len(seen_urls)} total reposts seen")
                _chrome.eval_js(ws, "window.scrollBy(0, 1400)")
                time.sleep(2.0)
    finally:
        try: ws.close()
        except Exception: pass

    print(f"\nTop {args.top} accounts Hunter has reposted (out of {len(counter)} unique):\n")
    for author, n in counter.most_common(args.top):
        print(f"  {n:3d} × @{author}    {samples[author]}")

    # Also dump JSON for easy copy-paste into config
    accts = [a for a, _ in counter.most_common(args.top)]
    print("\nJSON list for engage_config.json → ai_news_scout.accounts:\n")
    print(json.dumps(accts, indent=2))

    # Save a snapshot for reference
    out_dir = os.path.join(ROOT_DIR, "logs", "ai_news_scout")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir,
                            f"hunter_reposts_{datetime.now():%Y-%m-%d_%H%M}.json")
    with open(out_path, "w") as f:
        json.dump({"counts": dict(counter), "samples": samples}, f, indent=2)
    print(f"\nSaved snapshot: {out_path}")


if __name__ == "__main__":
    main()
