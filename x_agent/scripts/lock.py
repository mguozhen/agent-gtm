"""Cross-process file lock for serializing access to a Chrome instance.

engage_daemon and telegram_bridge both drive Hunter's Chrome on hunter_port —
without coordination, their navigations race and cause:
  - reply composers wiped mid-paste (submit_button_disabled_or_missing)
  - reply landing on the wrong tweet (incident 2026-05-15 / antirez)
  - false logout detections (daemon's home-page check reads a search DOM)

Usage:
    from lock import chrome_lock
    with chrome_lock(port):
        # navigate, click, eval_js — uninterrupted by the other process
        ...

The lock is keyed by port, so different Chrome instances don't contend.
fcntl.flock on a file in state/locks/. POSIX-only — fine for this macOS box.
"""
import contextlib
import fcntl
import os
import time

LOCK_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state", "locks",
)


@contextlib.contextmanager
def file_lock(name: str, timeout: float = 30.0, on_wait=None):
    """Generic named file lock — same mechanism as chrome_lock but keyed by a
    string instead of a port. Use this around load→modify→save sequences for
    shared state files (e.g. state/reply_queue.json). Multiple writers without
    it produce the lost-update bug (incident 2026-05-16 00:30: engage_daemon
    overwrote a buildlog draft because both held stale in-memory queues)."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
    path = os.path.join(LOCK_DIR, f"file_{safe}.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.time() + timeout
        notified = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not notified and on_wait:
                    try: on_wait(f"file lock '{name}' busy — waiting")
                    except Exception: pass
                    notified = True
                if time.time() >= deadline:
                    raise TimeoutError(
                        f"file lock '{name}' not acquired within {timeout}s"
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            try: fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception: pass
    finally:
        try: os.close(fd)
        except Exception: pass


@contextlib.contextmanager
def chrome_lock(port: int, timeout: float = 90.0, on_wait=None):
    """Exclusive lock on Chrome at `port`. Blocks up to `timeout` seconds.

    on_wait(msg) — optional callback fired the first time we have to wait, so
    the caller can log "chrome busy — waiting." Logging in a tight retry loop
    would be noisy, so we only call it once per acquisition.
    """
    os.makedirs(LOCK_DIR, exist_ok=True)
    path = os.path.join(LOCK_DIR, f"chrome_{port}.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.time() + timeout
        notified = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not notified and on_wait:
                    try: on_wait(f"chrome port {port} busy — waiting")
                    except Exception: pass
                    notified = True
                if time.time() >= deadline:
                    raise TimeoutError(
                        f"chrome lock on port {port} not acquired within {timeout}s"
                    )
                time.sleep(0.4)
        try:
            yield
        finally:
            try: fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception: pass
    finally:
        try: os.close(fd)
        except Exception: pass
