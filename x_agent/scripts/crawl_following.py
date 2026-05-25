"""
Crawl the following lists of confirmed target accounts and find
accounts followed by multiple targets — strong signal for similar niche.

Usage:
    python3 scripts/crawl_following.py --port 10002 --max-following 200
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env; env.load()
import chrome as _chrome

STATE_DIR = os.path.join(ROOT_DIR, "state")

SEEDS = [
    "jbryanporter", "ConnorShowler", "mikefutia", "Seanfrank",
    "daviefogarty", "codyplof", "AnthonyEclipse", "apprealworld",
    "TheecomMike", "thedennis", "youderian",
    "ConnorGillivan", "Manu_Sisti", "Codie_Sanchez", "7FigSaykho", "agazdecki",
]

# accounts to skip (seeds themselves + obvious noise)
SKIP = set(h.lower() for h in SEEDS) | {
    "x", "twitter", "elonmusk", "verified", "xdevelopers",
    "jeffbezos", "amazon", "shopify", "google", "meta", "apple",
}


def get_following(ws, handle: str, max_accounts: int = 200) -> list[str]:
    """Visit /{handle}/following and scroll to collect handles."""
    _chrome.navigate(ws, f"https://x.com/{handle}/following", wait=5)
    _chrome.bring_to_front(ws)
    time.sleep(2)

    handles = set()
    prev_count = 0
    stale_rounds = 0

    for _ in range(20):  # max 20 scrolls
        # extract all user cells
        js = """
        (function() {
            var cells = document.querySelectorAll('[data-testid="UserCell"]');
            var out = [];
            cells.forEach(function(c) {
                var a = c.querySelector('a[href^="/"]');
                if (a) {
                    var m = a.href.match(/x\\.com\\/([^/]+)$/);
                    if (m) out.push(m[1]);
                }
            });
            return out.join(',');
        })()
        """
        raw = _chrome.eval_js(ws, js)
        if raw:
            for h in raw.split(","):
                h = h.strip()
                if h and h.lower() not in SKIP and not h.startswith("i/"):
                    handles.add(h)

        if len(handles) >= max_accounts:
            break

        if len(handles) == prev_count:
            stale_rounds += 1
            if stale_rounds >= 3:
                break
        else:
            stale_rounds = 0

        prev_count = len(handles)
        _chrome.scroll_down(ws, px=1200)
        time.sleep(1.5)

    return list(handles)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",          type=int, default=10002)
    parser.add_argument("--max-following", type=int, default=200)
    args = parser.parse_args()

    ws = _chrome.connect(args.port)
    _chrome.set_viewport(ws, 1280, 1800)

    followed_by = defaultdict(set)  # handle -> set of seeds that follow them

    for i, seed in enumerate(SEEDS):
        print(f"[{i+1}/{len(SEEDS)}] crawling @{seed}/following ...")
        try:
            following = get_following(ws, seed, max_accounts=args.max_following)
            print(f"  found {len(following)} accounts")
            for h in following:
                followed_by[h].add(seed)
        except Exception as e:
            print(f"  error: {e}")
        time.sleep(2)

    ws.close()

    # rank by how many seeds follow them
    ranked = sorted(followed_by.items(), key=lambda x: len(x[1]), reverse=True)

    # write results
    out_path = os.path.join(STATE_DIR, "following_candidates.json")
    result = [
        {"handle": h, "followed_by_count": len(seeds), "followed_by": sorted(seeds)}
        for h, seeds in ranked
        if len(seeds) >= 2  # followed by at least 2 seeds
    ]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nDone. {len(result)} accounts followed by >=2 seeds → {out_path}")
    print("\nTop 40:")
    for r in result[:40]:
        print(f"  {r['followed_by_count']:2d}x  @{r['handle']:30s}  followed by: {', '.join(r['followed_by'])}")


if __name__ == "__main__":
    main()
