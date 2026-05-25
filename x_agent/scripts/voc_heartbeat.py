#!/usr/bin/env python3
"""VOC heartbeat -> Multica issue updater.

Runs as a tiny periodic job. It gathers VOC runtime health and upserts a single
issue in Multica ("VOC_ai Heartbeat"), keeping its description current.
"""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path("/Users/siliconno3/x_agent")
STATE = ROOT / "state"
LOGS = ROOT / "logs"

ISSUE_TITLE = "VOC_ai Heartbeat"
SERVER_URL = "https://multica-ai.shulex.com"
WORKSPACE_ID = "2facfc22-f1cf-47be-bdf2-80fcf8386d3c"


def _run(cmd: List[str], stdin: Optional[str] = None) -> Tuple[int, str, str]:
    p = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = p.communicate(stdin)
    return p.returncode, out, err


def _json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open() as f:
            return json.load(f)
    except Exception:
        return default


def _file_meta(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    st = path.stat()
    return {
        "exists": True,
        "bytes": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


def _pgrep(pattern: str) -> list[str]:
    rc, out, _ = _run(["pgrep", "-fal", pattern])
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _build_status() -> dict:
    q = _json(STATE / "reply_queue.json", [])
    off = _json(STATE / "telegram_offset_voc_ai.json", {})
    seen = _json(STATE / "engage_seen_voc_ai.json", {})

    queue_total = len(q)
    pending = sum(1 for e in q if e.get("status") == "pending")
    posted = sum(1 for e in q if e.get("status") == "posted")
    failed = sum(1 for e in q if e.get("status") == "post_failed")

    engage = _pgrep(r"/Users/siliconno3/x_agent/scripts/engage_daemon.py --config /Users/siliconno3/x_agent/accounts/VOC_ai/engage_config.json")
    bridge = _pgrep(r"/Users/siliconno3/x_agent/scripts/telegram_bridge.py")
    quote = _pgrep(r"/Users/siliconno3/x_agent/scripts/quote_scout.py --config /Users/siliconno3/x_agent/accounts/VOC_ai/engage_config.json")

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "services": {
            "engage_voc_ai": {"running": bool(engage), "matches": engage[:3]},
            "telegram_bridge_voc_ai": {"running": bool(bridge), "matches": bridge[:6]},
            "quote_scout_voc_ai": {"running_now": bool(quote), "matches": quote[:3]},
        },
        "queue": {
            "total": queue_total,
            "pending": pending,
            "posted": posted,
            "post_failed": failed,
        },
        "state_files": {
            "reply_queue": _file_meta(STATE / "reply_queue.json"),
            "engage_seen_voc_ai": _file_meta(STATE / "engage_seen_voc_ai.json"),
            "telegram_offset_voc_ai": _file_meta(STATE / "telegram_offset_voc_ai.json"),
            "telegram_offset_voc_ai_value": off,
            "engage_seen_voc_ai_keys": len(seen.keys()) if isinstance(seen, dict) else None,
        },
        "logs": {
            "engage_voc_out": _file_meta(LOGS / "engage-vocai-daemon.out.log"),
            "engage_voc_err": _file_meta(LOGS / "engage-vocai-daemon.err.log"),
            "bridge_voc_out": _file_meta(LOGS / "telegram-bridge-voc-ai.out.log"),
            "bridge_voc_err": _file_meta(LOGS / "telegram-bridge-voc-ai.err.log"),
            "quote_voc_out": _file_meta(LOGS / "quote_scout_voc_ai_launchd.log"),
            "quote_voc_err": _file_meta(LOGS / "quote_scout_voc_ai_launchd.err"),
        },
    }


def _render_description(status: dict) -> str:
    pretty = json.dumps(status, indent=2, ensure_ascii=False)
    return (
        "# VOC_ai Heartbeat\n\n"
        "Auto-updated by `/Users/siliconno3/x_agent/scripts/voc_heartbeat.py`.\n\n"
        "```json\n" + pretty + "\n```\n"
    )


def _multica_base() -> list[str]:
    return [
        "multica",
        "--server-url", SERVER_URL,
        "--workspace-id", WORKSPACE_ID,
    ]


def _find_issue_id() -> Optional[str]:
    rc, out, _ = _run(_multica_base() + ["issue", "search", ISSUE_TITLE, "--output", "json", "--include-closed"])
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except Exception:
        return None
    for it in data.get("issues", []):
        if it.get("title") == ISSUE_TITLE:
            return it.get("id")
    return None


def _create_issue(desc: str) -> Optional[str]:
    cmd = _multica_base() + ["issue", "create", "--title", ISSUE_TITLE, "--description-stdin", "--status", "todo", "--output", "json"]
    rc, out, err = _run(cmd, stdin=desc)
    if rc != 0:
        print(f"[heartbeat] create failed: {err.strip()}")
        return None
    try:
        return json.loads(out).get("id")
    except Exception:
        return None


def _update_issue(issue_id: str, desc: str) -> bool:
    cmd = _multica_base() + ["issue", "update", issue_id, "--description-stdin", "--output", "json"]
    rc, _, err = _run(cmd, stdin=desc)
    if rc != 0:
        print(f"[heartbeat] update failed: {err.strip()}")
        return False
    return True


def main():
    status = _build_status()
    desc = _render_description(status)
    issue_id = _find_issue_id()
    if not issue_id:
        issue_id = _create_issue(desc)
        if not issue_id:
            raise SystemExit(1)
        print(f"[heartbeat] created issue {issue_id}")
        return
    ok = _update_issue(issue_id, desc)
    if ok:
        print(f"[heartbeat] updated issue {issue_id} @ {int(time.time())}")


if __name__ == "__main__":
    main()
