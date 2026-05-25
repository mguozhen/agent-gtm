"""
buildlog_drafts — produce 'building in public' post drafts from recent git
activity across configured repos. Pushes drafts into the same reply_queue.json
+ Telegram approve/regen/reject flow used by replies.

Usage (one-shot — intended for daily cron / launchd):
    python3 scripts/buildlog_drafts.py
    python3 scripts/buildlog_drafts.py --hours 48 --drafts 5 --dry-run

Reads engage_config.json for hunter_handle. Repos are taken from a
"buildlog" block in engage_config.json (see post_config below). If absent,
falls back to the x-agent repo itself.

Each commit is summarized with: subject line, short stat (files/+/-), short
diff sample. The generator gets a packed CONTEXT string and produces 1
draft per "build chunk" (a commit cluster). Drafts go through the standard
TG approve flow.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env;       env.load()
import telegram as _tg
import generate as _generate
from lock import file_lock

DEFAULT_CONFIG = os.path.join(ROOT_DIR, "engage_config.json")
STATE_DIR      = os.path.join(ROOT_DIR, "state")
QUEUE_PATH     = os.path.join(STATE_DIR, "reply_queue.json")
LOG_DIR        = os.path.join(ROOT_DIR, "logs", "buildlog")


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a") as f:
        f.write(line + "\n")


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _git(repo: str, *args: str) -> str:
    """Run `git -C repo args...`. Returns stdout (stripped) or '' on failure."""
    try:
        r = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return ""
        return r.stdout.strip()
    except Exception:
        return ""


def _collect_commits(repo: str, hours: int) -> list:
    """Return a list of recent commits with subject + stat + short diff sample."""
    since = f"{hours} hours ago"
    log = _git(repo, "log", f"--since={since}", "--no-merges",
               "--pretty=format:%H%x1f%s%x1f%an%x1f%ad", "--date=iso")
    if not log:
        return []
    out = []
    for line in log.split("\n"):
        parts = line.split("\x1f")
        if len(parts) < 4:
            continue
        sha, subject, author, date = parts
        stat = _git(repo, "show", "--stat", "--format=", sha)
        diff = _git(repo, "show", "--unified=0", "--format=", sha)
        # Keep diffs small; the LLM doesn't need every line
        if len(diff) > 4000:
            diff = diff[:4000] + "\n... [truncated]"
        out.append({
            "sha":     sha[:10],
            "subject": subject,
            "author":  author,
            "date":    date,
            "stat":    stat,
            "diff":    diff,
        })
    return out


def _pack_context(repo_name: str, commits: list) -> str:
    """Pack a list of commits into a single CONTEXT string for the generator.
    Keeps the most useful signal — subject + stat. Diff is included only for
    the most recent commit to give the model something concrete."""
    lines = [f"Repo: {repo_name}"]
    for i, c in enumerate(commits):
        lines.append(f"- ({c['date'][:10]}) {c['subject']}")
        if c["stat"]:
            stat_line = " · ".join(s.strip() for s in c["stat"].splitlines()[-1:])
            if stat_line:
                lines.append(f"    {stat_line}")
    # Add the most recent commit's diff sample for a concrete grounding
    if commits:
        most_recent = commits[0]
        if most_recent["diff"]:
            lines.append("")
            lines.append("Most recent diff (truncated):")
            lines.append(most_recent["diff"][:1500])
    return "\n".join(lines)


def _chunk_commits(commits: list, max_chunks: int) -> list:
    """Group commits into up to max_chunks clusters by recency. Each cluster
    becomes one post. For small commit counts (<= max_chunks) each commit is
    its own chunk; for larger sets we bucket sequentially."""
    if not commits:
        return []
    if len(commits) <= max_chunks:
        return [[c] for c in commits]
    per = max(1, len(commits) // max_chunks)
    chunks = []
    for i in range(0, len(commits), per):
        chunks.append(commits[i:i+per])
        if len(chunks) >= max_chunks:
            break
    return chunks


def _gh_get(path: str):
    """GET against api.github.com with optional GITHUB_TOKEN auth. Returns
    parsed JSON or None on failure."""
    url = f"https://api.github.com/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "x-agent-buildlog",
    })
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        _log(f"  gh {path}: HTTP {e.code}")
        return None
    except Exception as e:
        _log(f"  gh {path}: {e}")
        return None


def _collect_github_commits(repo: str, hours: int, limit: int = 25) -> list:
    """Fetch recent commits from GitHub REST API. repo = 'org/name'. Returns a
    commit-record list shaped like _collect_commits (local-git mode)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    rows = _gh_get(f"repos/{repo}/commits?since={since}&per_page={limit}")
    if not isinstance(rows, list) or not rows:
        return []
    out = []
    for r in rows:
        sha = (r.get("sha") or "")[:10]
        commit = r.get("commit") or {}
        subject = (commit.get("message") or "").splitlines()[0]
        author  = (commit.get("author") or {}).get("name", "?")
        date    = (commit.get("author") or {}).get("date", "")
        # Per-commit detail (files + stat). We only need this for the most
        # recent few to keep API calls bounded — _pack_context() only uses
        # the stat for non-recent commits anyway.
        out.append({
            "sha":     sha,
            "subject": subject,
            "author":  author,
            "date":    date,
            "stat":    "",   # filled lazily below if we want a diff sample
            "diff":    "",
        })
    # For the FIRST (most recent) commit, fetch the patch so the generator has
    # something concrete to ground in.
    if out:
        det = _gh_get(f"repos/{repo}/commits/{rows[0].get('sha')}")
        if isinstance(det, dict):
            files = det.get("files") or []
            patches = []
            for f in files[:6]:
                patch = f.get("patch") or ""
                if patch:
                    patches.append(f"--- {f.get('filename','?')}\n{patch[:800]}")
            out[0]["diff"] = "\n".join(patches)[:3000]
            stats = det.get("stats") or {}
            out[0]["stat"] = (f"{stats.get('total',0)} ±  "
                              f"+{stats.get('additions',0)} "
                              f"-{stats.get('deletions',0)}")
    return out


def _build_drafts(repos: list, hours: int, max_drafts: int) -> list:
    """Collect commits from every configured repo, then chunk *globally* —
    each repo's commits become up to max_drafts chunks, with the total trimmed
    to max_drafts. Earlier per-repo division starved producers when one repo
    was silent (incident 2026-05-16: 5 max drafts, multica had 0 commits,
    only 2 buildlog drafts emerged instead of 5)."""
    repo_commits = []
    for r in repos:
        name = r.get("name") or os.path.basename((r.get("path") or r.get("github") or "").rstrip("/"))
        commits = []
        if r.get("github"):
            commits = _collect_github_commits(r["github"], hours)
            src = f"github:{r['github']}"
        elif r.get("path"):
            if not os.path.isdir(os.path.join(r["path"], ".git")):
                _log(f"  skip {name}: not a git repo at {r['path']}")
                continue
            commits = _collect_commits(r["path"], hours)
            src = f"path:{r['path']}"
        else:
            _log(f"  skip {name}: needs 'path' or 'github' key")
            continue
        if not commits:
            _log(f"  {name} ({src}): no commits in last {hours}h")
            continue
        _log(f"  {name} ({src}): {len(commits)} commit(s) in last {hours}h")
        repo_commits.append((name, commits))

    if not repo_commits:
        return []

    # Distribute the draft budget proportionally to commit volume across active
    # repos. Ensures a busy repo can fill the quota when others are silent.
    total_commits = sum(len(cs) for _, cs in repo_commits)
    out = []
    for name, commits in repo_commits:
        share = max(1, round(max_drafts * len(commits) / total_commits))
        for chunk in _chunk_commits(commits, share):
            out.append({"repo_name": name, "context": _pack_context(name, chunk)})
    return out[:max_drafts]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",   default=DEFAULT_CONFIG)
    parser.add_argument("--hours",    type=int, default=24)
    parser.add_argument("--drafts",   type=int, default=1,
                        help="max drafts to queue this run")
    parser.add_argument("--dry-run",  action="store_true",
                        help="print drafts; don't queue or send to TG")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    handle = cfg["hunter_handle"]
    post_cfg = cfg.get("buildlog", {})
    repos = post_cfg.get("repos") or [{"path": ROOT_DIR, "name": "x-agent"}]
    max_chars = (cfg.get("generation", {}) or {}).get("max_post_chars", 280)

    _log(f"buildlog run — handle={handle}, hours={args.hours}, drafts={args.drafts}, "
         f"repos={[r.get('name') or r['path'] for r in repos]}")

    chunks = _build_drafts(repos, args.hours, args.drafts)
    if not chunks:
        _log("no commit activity to draft from; nothing queued")
        return

    for c in chunks:
        try:
            g = _generate.generate_buildlog_post(
                handle=handle, context=c["context"], max_chars=max_chars,
            )
        except Exception as e:
            _log(f"  generate failed for {c['repo_name']}: {e}")
            continue
        reply = (g.get("reply") or "").strip()
        if not reply or len(reply) < 20:
            _log(f"  generator returned weak/empty draft for {c['repo_name']}, skipping")
            continue

        entry = {
            "id":             f"bl_{int(time.time())}_{c['repo_name']}",
            "kind":           "original",
            "source":         "buildlog",
            "source_repo":    c["repo_name"],
            "source_context": c["context"],
            "reply_text":     reply,
            "op_summary":     g.get("op_summary", ""),
            "reply_angle":    g.get("reply_angle", ""),
            "status":         "pending",
            "queued_at":      _ts(),
            "telegram_message_id": 0,
        }

        if args.dry_run:
            _log(f"  [dry-run] {c['repo_name']} → {reply[:120]}")
            continue

        try:
            import reply_scorer as _rs
            _rs.score_entry(entry)
        except Exception as _e:
            _log(f"  scorer error (non-fatal): {_e}")

        # Atomic append under file lock — protects against engage_daemon,
        # quote_scout, and telegram_bridge writing the queue concurrently.
        with file_lock("reply_queue", on_wait=_log):
            cur = _load(QUEUE_PATH, [])
            cur.append(entry)
            _save(QUEUE_PATH, cur)
        try:
            msg_id = _tg.send_reply_card(entry)
            if msg_id:
                entry["telegram_message_id"] = msg_id
                with file_lock("reply_queue", on_wait=_log):
                    cur = _load(QUEUE_PATH, [])
                    for i, e in enumerate(cur):
                        if e.get("id") == entry["id"]:
                            cur[i] = entry
                            _save(QUEUE_PATH, cur)
                            break
        except Exception as e:
            _log(f"  TG send failed: {e}")
        _log(f"queued draft {entry['id']}: {reply[:80]}")


if __name__ == "__main__":
    main()
