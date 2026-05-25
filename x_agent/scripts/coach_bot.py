"""Reply-quality coach. Separate Telegram bot from the approval bridge.

Two jobs:
  1) Conversational: long-polls for commands (/score /why /top /flops /target
     /gate /pause /resume /help). Free-text questions get a Haiku answer with
     concrete reply citations.
  2) Scheduled pushes: morning digest (08:00), evening warning (21:00),
     weekly pattern report (Sun 08:30), and emergency push when today's rate
     is < 5% with >=10 replies sent OR 3-day rolling rate is < 5%.

Reports are written to be concrete: actual reply text, target names, parent
post snippets, and an explanation of WHY a reply landed or died — not just
percentages. Numbers are evidence, not the report.

State lives in state/coach_state.json:
    {
      "gate": "strict"|"normal"|"loose",
      "paused_targets": [handle, ...],
      "last_morning_push": "YYYY-MM-DD",
      "last_evening_push": "YYYY-MM-DD",
      "last_weekly_push":  "YYYY-MM-DD",
      "last_emergency_at": "YYYY-MM-DD HH:MM:SS",
      "tg_offset":         <int>,
    }
"""
import argparse
import json
import os
import signal
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import env; env.load()
import coach as _tg
from lock import file_lock

ENGAGEMENT_PATH = os.path.join(ROOT_DIR, "state", "reply_engagement.json")
QUEUE_PATH      = os.path.join(ROOT_DIR, "state", "reply_queue.json")
STATE_PATH      = os.path.join(ROOT_DIR, "state", "coach_state.json")
LOG_PATH        = os.path.join(ROOT_DIR, "logs", "coach-bot.log")

TARGET_RATE = 0.10  # 10% target the user set
EMERGENCY_DAILY_RATE = 0.05
EMERGENCY_MIN_REPLIES = 10
EMERGENCY_ROLLING_RATE = 0.05
EMERGENCY_COOLDOWN_MIN = 90  # don't re-fire emergency more than every 90min

POLL_INTERVAL_S = 2
SCHEDULE_CHECK_INTERVAL_S = 60

_stop = False


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    line = f"[{_ts()}] {msg}\n"
    sys.stdout.write(line); sys.stdout.flush()
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except Exception:
        pass


def _load(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def _save_state(state: dict):
    with file_lock("coach_state", on_wait=_log):
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_PATH)


def _load_state() -> dict:
    s = _load(STATE_PATH, {})
    s.setdefault("gate", "normal")
    s.setdefault("paused_targets", [])
    s.setdefault("last_morning_push", "")
    s.setdefault("last_evening_push", "")
    s.setdefault("last_weekly_push", "")
    s.setdefault("last_emergency_at", "")
    s.setdefault("tg_offset", 0)
    return s


# ---- stats ---------------------------------------------------------------


def _parse_ts(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _records_in_window(history: dict, days: int) -> list:
    """Posted entries whose posted_at falls within the last `days` CALENDAR
    days, where day 0 = today (since 00:00 local). days=1 → today only;
    days=3 → today + 2 prior calendar days; days=7 → today + 6 prior. This
    matches user mental model ('how many today?' means since midnight)
    rather than a 24h rolling window."""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = today_start - timedelta(days=days - 1)
    out = []
    for rec in history.values():
        p = _parse_ts(rec.get("posted_at", ""))
        if not p or p < cutoff:
            continue
        out.append(rec)
    return out


def _split(records):
    """Returns (engaged, dead, unmeasured).

    Uses the LATEST check (not just final) so a reply that picked up 2 likes
    in its first 1h re-fetch counts immediately, not 23h later. This matches
    user expectation: 'I can see engagement on my profile, why isn't /score
    showing it?'

    - engaged:    at least one check exists AND latest check has any
                  (likes + replies + rts) > 0
    - dead:       at least one check exists AND latest check is all zeros
    - unmeasured: no checks yet (queued, not re-fetched once)
    """
    engaged, dead, unmeasured = [], [], []
    for r in records:
        checks = r.get("checks") or []
        if not checks:
            unmeasured.append(r)
            continue
        last = checks[-1]
        if (last.get("likes", 0) + last.get("replies", 0) + last.get("rts", 0)) > 0:
            engaged.append(r)
        else:
            dead.append(r)
    return engaged, dead, unmeasured


def _rate(records, kind=None):
    """Engagement rate over MEASURED records (at least one check completed).
    Returns (rate, engaged_count, measured_count). `kind` filters to one of
    'reply' | 'original' | 'quote' (None = all)."""
    if kind is not None:
        records = [r for r in records if (r.get("kind") or "reply") == kind]
    eng, dead, _ = _split(records)
    measured = len(eng) + len(dead)
    if measured == 0:
        return 0.0, 0, 0
    return len(eng) / measured, len(eng), measured


def _by_kind(records):
    """Return {kind: [records]} split. Unknown kinds become 'reply' for
    backwards compat with old queue entries that didn't set the field."""
    out = {"reply": [], "original": [], "quote": []}
    for r in records:
        k = r.get("kind") or "reply"
        out.setdefault(k, []).append(r)
    return out


def _by_target(records: list) -> dict:
    """{target: {total, engaged, rate}}"""
    buckets = defaultdict(lambda: {"total": 0, "engaged": 0})
    for r in records:
        if not r.get("final_at"):
            continue
        t = r.get("target", "?") or "?"
        buckets[t]["total"] += 1
        if r.get("engaged"):
            buckets[t]["engaged"] += 1
    out = {}
    for t, v in buckets.items():
        v["rate"] = (v["engaged"] / v["total"]) if v["total"] else 0.0
        out[t] = v
    return out


def _top_records(records: list, n: int = 5, key: str = "engaged") -> list:
    """Top N finalized records by engagement (likes+replies+rts) or by being
    dead (key='dead' returns most-recent dead). Used for /top and /flops."""
    fin = [r for r in records if r.get("final_at")]
    if key == "dead":
        dead = [r for r in fin if not r.get("engaged")]
        dead.sort(key=lambda r: r.get("posted_at", ""), reverse=True)
        return dead[:n]
    def score(r):
        return (r.get("final_likes", 0) + 2 * r.get("final_replies", 0)
                + r.get("final_rts", 0))
    fin.sort(key=score, reverse=True)
    return fin[:n]


def _stats_pack(days: int = 1) -> dict:
    """Build a kind-segmented stats pack. Each window (today/rolling/week)
    carries an overall rate AND per-kind rates (reply/original/quote) so the
    coach can report against the 10% target for replies specifically while
    still surfacing originals/QTs separately."""
    history = _load(ENGAGEMENT_PATH, {})
    today_recs = _records_in_window(history, days)
    rolling_recs = _records_in_window(history, 3)
    week_recs = _records_in_window(history, 7)

    def _sum_metrics(recs):
        """Sum likes/replies/rts across records using each record's latest
        check. Records with no checks contribute 0."""
        L = R = T = 0
        for rec in recs:
            checks = rec.get("checks") or []
            if not checks:
                continue
            last = checks[-1]
            L += int(last.get("likes",   0) or 0)
            R += int(last.get("replies", 0) or 0)
            T += int(last.get("rts",     0) or 0)
        return L, R, T

    def _window(recs):
        rate, eng, measured = _rate(recs)
        bk = _by_kind(recs)
        unmeasured = sum(1 for r in recs if not (r.get("checks") or []))
        finalized = sum(1 for r in recs if r.get("final_at"))
        L, R, T = _sum_metrics(recs)
        out = {
            "rate": rate, "engaged": eng, "measured": measured,
            "finalized": finalized, "unmeasured": unmeasured,
            "total_posted": len(recs),
            "likes": L, "replies": R, "rts": T,
            "by_kind": {},
        }
        for k in ("reply", "original", "quote"):
            recs_k = [r for r in recs if (r.get("kind") or "reply") == k]
            kr, ke, km = _rate(recs, kind=k)
            kf = sum(1 for r in recs_k if r.get("final_at"))
            ku = sum(1 for r in recs_k if not (r.get("checks") or []))
            kL, kR, kT = _sum_metrics(recs_k)
            out["by_kind"][k] = {
                "rate": kr, "engaged": ke, "measured": km,
                "finalized": kf, "unmeasured": ku,
                "total_posted": len(bk.get(k, [])),
                "likes": kL, "replies": kR, "rts": kT,
            }
        return out

    # Reply-only buckets feed target/winner/flop views — the 10% target was
    # set against replies, and originals/QTs don't have meaningful "target"
    # accounts, so by_target only counts replies.
    reply_week = [r for r in week_recs if (r.get("kind") or "reply") == "reply"]
    reply_today = [r for r in today_recs if (r.get("kind") or "reply") == "reply"]
    return {
        "today":   _window(today_recs),
        "rolling": _window(rolling_recs),
        "week":    _window(week_recs),
        "by_target_week": _by_target(reply_week),
        "top_today": _top_records(today_recs, n=3),
        "top_week":  _top_records(week_recs, n=5),
        "flops_today": _top_records(reply_today, n=5, key="dead"),
    }


# ---- formatting ----------------------------------------------------------


def _fmt_pct(r: float) -> str:
    return f"{int(round(r * 100))}%"


def _fmt_rec_block(label: str, r: dict, with_reason: bool = False) -> str:
    """Render one reply with target context. Plain text — telegram Markdown
    is fiddly with special chars in tweet bodies, so we use minimal formatting."""
    target = r.get("target", "?")
    parent = (r.get("target_url", "")
              or "").replace("https://", "")
    reply = (r.get("reply_text", "") or "")[:280]
    likes = r.get("final_likes", 0)
    replies = r.get("final_replies", 0)
    rts = r.get("final_rts", 0)
    eng = "engaged" if r.get("engaged") else "dead"
    head = f"{label}: @{target}  →  {likes}♥ {replies}💬 {rts}🔁  ({eng})"
    body = f"   {reply}"
    return f"{head}\n{body}"


def _human_target_summary(by_target, top_n=3):
    """Top hit-rate and worst hit-rate targets with >=3 finalized samples."""
    rows = [(t, v) for t, v in by_target.items() if v["total"] >= 3]
    rows.sort(key=lambda x: x[1]["rate"])
    bad = rows[:top_n]
    good = sorted(rows, key=lambda x: -x[1]["rate"])[:top_n]
    out = []
    if good:
        out.append("Best targets (week):")
        for t, v in good:
            out.append(f"   @{t}  {v['engaged']}/{v['total']}  ({_fmt_pct(v['rate'])})")
    if bad:
        out.append("Worst targets (week):")
        for t, v in bad:
            out.append(f"   @{t}  {v['engaged']}/{v['total']}  ({_fmt_pct(v['rate'])})")
    return out


# ---- Claude explanation layer --------------------------------------------


def _explain(stats: dict, mode: str) -> str:
    """Call Haiku to write a concrete report with citations to actual replies.
    `mode` is 'morning' | 'evening' | 'emergency' | 'weekly' | 'why'.

    We pass small stats summaries + actual reply text (truncated) so Haiku can
    quote them and give a real human explanation of patterns. Falls back to a
    plain-text report if the Anthropic SDK isn't available or the call fails."""
    # Build a compact JSON view that includes actual reply citations.
    def _slim(r: dict) -> dict:
        return {
            "target":       r.get("target", ""),
            "target_text":  (r.get("target_url", "") or "")[:80],
            "reply_text":   (r.get("reply_text", "") or "")[:240],
            "likes":        r.get("final_likes", 0),
            "replies":      r.get("final_replies", 0),
            "rts":          r.get("final_rts", 0),
            "engaged":      r.get("engaged", False),
            "posted_at":    r.get("posted_at", ""),
        }

    payload = {
        "today":   stats["today"],
        "rolling": stats["rolling"],
        "week":    stats["week"],
        "winners_today": [_slim(r) for r in stats["top_today"]],
        "flops_today":   [_slim(r) for r in stats["flops_today"]],
        "winners_week":  [_slim(r) for r in stats["top_week"]],
        "by_target_week_top": sorted(
            stats["by_target_week"].items(),
            key=lambda kv: kv[1]["total"], reverse=True
        )[:12],
    }

    intro = {
        "morning":   "Write the morning reply-quality digest (push at 08:00).",
        "evening":   "Write the evening warning (push at 21:00). Give the user time to course-correct before bedtime.",
        "emergency": "Write an emergency alert. The user is below the 5% engagement floor — algorithm risk.",
        "weekly":    "Write the weekly pattern report (push Sunday morning). Be more thorough — surface 3-5 patterns.",
        "why":       "The user just asked WHY their engagement rate is what it is right now. Be specific.",
    }.get(mode, "Write a reply-quality summary.")

    prompt = f"""You are the reply-quality coach for an X/Twitter automation system.
The user posts three kinds of content: REPLIES (under others' posts),
ORIGINALS (their own buildlog posts), and QUOTE TWEETS. The 10% engagement
target is on REPLIES specifically — that's what the algorithm punishes when
it drops. Report on all three but anchor the headline + recommendations on
replies.

{intro}

Hard rules for the output:
- Concrete: quote actual reply text from the data below, name actual @handles.
- Lead with reply engagement vs the 10% target. Then note originals and QTs
  separately ("originals: 2/3, quotes: 0/0"). Don't mush them together.
- Explain WHY a reply landed or died (open question? generic agreement? big
  account vs small? post age? reply length?). Don't just report numbers.
- Numbers are evidence, not the report.
- Be tight: 8-16 short lines. Plain text. No headers like "## Section".
  Use minimal formatting: a blank line between sections, short labels like
  "WHAT WORKED" or "RECOMMENDED" in caps when you need a break.
- End with 1-2 concrete next actions the user can take right now (e.g.
  /pause <handle>, /gate strict, target accounts in the 5k-50k follower band).
- Telegram Markdown is fragile — avoid backticks, asterisks inside quoted
  tweet bodies, square brackets, or stray underscores.

Data (JSON):
{json.dumps(payload, ensure_ascii=False, indent=2)}

Now write the report.
"""

    try:
        import anthropic
    except ImportError:
        return _explain_fallback(stats, mode)
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        _log(f"  haiku call failed: {e}")
        return _explain_fallback(stats, mode)


def _explain_fallback(stats: dict, mode: str) -> str:
    """No-LLM report. Used when Anthropic SDK is unavailable or call fails.
    Still concrete: cites actual reply text and target names. Headline rate
    is REPLY rate — that's what the 10% target tracks."""
    lines = []
    head = {"morning": "☕ Morning digest", "evening": "🌙 Evening check",
            "emergency": "⚠️ Algo risk", "weekly": "📊 Weekly report",
            "why": "Breakdown"}.get(mode, "Coach")
    lines.append(head)
    tr = stats["today"]["by_kind"]["reply"]
    r3r = stats["rolling"]["by_kind"]["reply"]
    wr = stats["week"]["by_kind"]["reply"]
    lines.append(f"Replies today: {tr['engaged']}/{tr['finalized']} ({_fmt_pct(tr['rate'])})  "
                 f"·  3d {_fmt_pct(r3r['rate'])}  ·  7d {_fmt_pct(wr['rate'])}")
    to = stats["today"]["by_kind"]["original"]
    tq = stats["today"]["by_kind"]["quote"]
    lines.append(f"Originals today: {to['engaged']}/{to['finalized']} ({_fmt_pct(to['rate'])}) · "
                 f"Quotes: {tq['engaged']}/{tq['finalized']} ({_fmt_pct(tq['rate'])})")
    lines.append("")
    if stats["top_today"]:
        lines.append("WHAT WORKED")
        for r in stats["top_today"][:3]:
            lines.append(_fmt_rec_block("•", r))
            lines.append("")
    if stats["flops_today"]:
        lines.append("WHAT DIED")
        for r in stats["flops_today"][:3]:
            lines.append(_fmt_rec_block("•", r))
            lines.append("")
    targ_lines = _human_target_summary(stats["by_target_week"], top_n=3)
    if targ_lines:
        lines.extend(targ_lines)
    return "\n".join(lines)


# ---- commands ------------------------------------------------------------


HELP = (
    "*Coach bot — commands*\n"
    "/score — today / 3d / 7d engagement rate\n"
    "/why — concrete breakdown of today's rate\n"
    "/top — top 5 replies of the last 7 days\n"
    "/flops — 5 most recent dead replies (today)\n"
    "/target @handle — hit rate replying under that account\n"
    "/gate strict|normal|loose — tightness of the pre-send warning gate\n"
    "/pause @handle — blacklist a target (scorer will flag all future)\n"
    "/resume @handle — un-blacklist\n"
    "/paused — list paused targets\n"
    "/help — this menu\n"
    "(Free text → a Haiku answer with citations from your reply history.)"
)


def _cmd_score() -> str:
    """Per-kind score. Shows both absolute engagement (likes/replies/rts
    totals) AND the binary hit rate (% of posts that got ANY engagement).
    The 10% target arrow tracks REPLIES only — that's what algorithm
    throttling watches."""
    s = _stats_pack(days=1)
    def _kind_lines(label, b):
        if b["total_posted"] == 0:
            return f"   {label:<10} 0 posted"
        unmeas = f" · {b['unmeasured']} unmeasured" if b["unmeasured"] else ""
        # Line 1: counts + hit rate. Line 2: absolute engagement.
        return (
            f"   {label:<10} {b['total_posted']} posted · "
            f"{b['engaged']}/{b['measured']} engaged ({_fmt_pct(b['rate'])}){unmeas}\n"
            f"              {b['likes']} ♥  ·  {b['replies']} 💬  ·  {b['rts']} 🔁"
        )
    def _block(label, w):
        rep = w["by_kind"]["reply"]
        orig = w["by_kind"]["original"]
        qt = w["by_kind"]["quote"]
        return (
            f"*{label}*\n"
            f"{_kind_lines('replies:',   rep)}\n"
            f"{_kind_lines('originals:', orig)}\n"
            f"{_kind_lines('quotes:',    qt)}"
        )
    reply_today = s["today"]["by_kind"]["reply"]
    reply_rate = reply_today["rate"]
    if reply_today["measured"] == 0:
        arrow = "⏳"
    else:
        arrow = "✅" if reply_rate >= TARGET_RATE else "⚠️"
    return (
        f"{arrow} *Engagement* (target {_fmt_pct(TARGET_RATE)} reply hit rate)\n"
        f"_Latest sweep per post. Hit rate finalizes after 24h._\n\n"
        f"{_block('Today', s['today'])}\n\n"
        f"{_block('3-day', s['rolling'])}\n\n"
        f"{_block('7-day', s['week'])}"
    )


def _cmd_why() -> str:
    return _explain(_stats_pack(days=1), mode="why")


def _cmd_top() -> str:
    s = _stats_pack(days=1)
    top = s["top_week"]
    if not top:
        return "No finalized winning replies in the last 7 days yet."
    lines = ["🏆 *Top replies (7d)*", ""]
    for r in top:
        lines.append(_fmt_rec_block("•", r))
        lines.append("")
    return "\n".join(lines)


def _cmd_flops() -> str:
    s = _stats_pack(days=1)
    flops = s["flops_today"]
    if not flops:
        return "No finalized dead replies today (yet)."
    lines = ["💀 *Flops (today)*", ""]
    for r in flops:
        lines.append(_fmt_rec_block("•", r))
        lines.append("")
    return "\n".join(lines)


def _cmd_target(handle: str) -> str:
    h = handle.lstrip("@").strip().lower()
    if not h:
        return "Usage: /target @handle"
    history = _load(ENGAGEMENT_PATH, {})
    recs = [r for r in history.values()
            if (r.get("target", "") or "").lower() == h]
    fin = [r for r in recs if r.get("final_at")]
    eng = [r for r in fin if r.get("engaged")]
    rate = (len(eng) / len(fin)) if fin else 0.0
    out = [f"@{h}: {len(eng)}/{len(fin)} engaged ({_fmt_pct(rate)}) — {len(recs)} total posted"]
    if eng:
        out.append("")
        out.append("Recent wins:")
        for r in sorted(eng, key=lambda r: r.get("posted_at", ""), reverse=True)[:3]:
            out.append(_fmt_rec_block("  •", r))
    if fin and not eng:
        out.append("")
        out.append("All recent replies under this target died. Consider /pause.")
    return "\n".join(out)


def _cmd_gate(level: str, state: dict) -> str:
    lv = level.lower().strip()
    if lv not in ("strict", "normal", "loose"):
        return "Usage: /gate strict|normal|loose"
    state["gate"] = lv
    _save_state(state)
    return f"Gate set to *{lv}*. Scorer will flag accordingly on the next queued reply."


def _cmd_pause(handle: str, state: dict) -> str:
    h = handle.lstrip("@").strip()
    if not h:
        return "Usage: /pause @handle"
    if h not in state["paused_targets"]:
        state["paused_targets"].append(h)
        _save_state(state)
    return f"Paused @{h}. All future replies under this target will be flagged."


def _cmd_resume(handle: str, state: dict) -> str:
    h = handle.lstrip("@").strip()
    if not h:
        return "Usage: /resume @handle"
    state["paused_targets"] = [t for t in state["paused_targets"]
                                if t.lower() != h.lower()]
    _save_state(state)
    return f"Resumed @{h}."


def _cmd_paused(state: dict) -> str:
    p = state["paused_targets"]
    if not p:
        return "No paused targets."
    return "Paused targets:\n" + "\n".join(f"  • @{h}" for h in p)


def _handle_free_text(text: str) -> str:
    """User typed something that isn't a command. Pass it to Haiku alongside
    the stats so they can ask 'why did this one work?' etc."""
    stats = _stats_pack(days=1)
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = f"""You are the user's reply-quality coach for X/Twitter.
The user just asked: "{text}"

Answer with concrete citations from this data. Quote actual reply text and
@handles. Keep it tight (8-16 short lines). End with one concrete next action.
Plain text — Telegram Markdown is fragile.

Data:
{json.dumps({
    "today":   stats["today"],
    "rolling": stats["rolling"],
    "week":    stats["week"],
    "winners_today": stats["top_today"][:3],
    "flops_today":   stats["flops_today"][:3],
    "winners_week":  stats["top_week"][:5],
}, default=str, indent=2)}
"""
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        _log(f"  free-text haiku failed: {e}")
        return f"(Haiku unavailable — try /score, /why, /top, /flops, or /help.)"


def _dispatch(text: str, state: dict) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    t = text.lower()
    if t in ("/help", "/start"):                          return HELP
    if t == "/score":                                     return _cmd_score()
    if t == "/why":                                       return _cmd_why()
    if t == "/top":                                       return _cmd_top()
    if t == "/flops":                                     return _cmd_flops()
    if t == "/paused":                                    return _cmd_paused(state)
    if t.startswith("/target"):                           return _cmd_target(text[7:])
    if t.startswith("/gate"):                             return _cmd_gate(text[5:], state)
    if t.startswith("/pause"):                            return _cmd_pause(text[6:], state)
    if t.startswith("/resume"):                           return _cmd_resume(text[7:], state)
    return _handle_free_text(text)


# ---- scheduled pushes ----------------------------------------------------


def _maybe_push_morning(state: dict):
    today = date.today().isoformat()
    if state.get("last_morning_push") == today:
        return
    now = datetime.now()
    if now.hour != 8:
        return
    msg = _explain(_stats_pack(days=1), mode="morning")
    if _tg.send_text(msg):
        state["last_morning_push"] = today
        _save_state(state)
        _log("pushed morning digest")


def _maybe_push_evening(state: dict):
    today = date.today().isoformat()
    if state.get("last_evening_push") == today:
        return
    now = datetime.now()
    if now.hour != 21:
        return
    msg = _explain(_stats_pack(days=1), mode="evening")
    if _tg.send_text(msg):
        state["last_evening_push"] = today
        _save_state(state)
        _log("pushed evening warning")


def _maybe_push_weekly(state: dict):
    today = date.today().isoformat()
    if state.get("last_weekly_push") == today:
        return
    now = datetime.now()
    # Sunday at 08:30. weekday()==6 for Sunday.
    if now.weekday() != 6 or now.hour != 8 or now.minute < 30:
        return
    msg = _explain(_stats_pack(days=7), mode="weekly")
    if _tg.send_text(msg):
        state["last_weekly_push"] = today
        _save_state(state)
        _log("pushed weekly report")


def _maybe_push_emergency(state: dict):
    """Emergency triggers on REPLY rate only — reply throttling is the
    algorithm risk the user actually wanted protection against. Originals
    and QTs are tracked but don't count toward the emergency floor."""
    last = _parse_ts(state.get("last_emergency_at", ""))
    if last and (datetime.now() - last).total_seconds() < EMERGENCY_COOLDOWN_MIN * 60:
        return
    stats = _stats_pack(days=1)
    t_rep = stats["today"]["by_kind"]["reply"]
    r3_rep = stats["rolling"]["by_kind"]["reply"]
    trigger = False
    if t_rep["finalized"] >= EMERGENCY_MIN_REPLIES and t_rep["rate"] < EMERGENCY_DAILY_RATE:
        trigger = True
    if r3_rep["finalized"] >= EMERGENCY_MIN_REPLIES * 3 and r3_rep["rate"] < EMERGENCY_ROLLING_RATE:
        trigger = True
    if not trigger:
        return
    msg = _explain(stats, mode="emergency")
    if _tg.send_text(msg):
        state["last_emergency_at"] = _ts()
        _save_state(state)
        _log("pushed emergency alert")


# ---- main loop -----------------------------------------------------------


def _poll_updates(state: dict):
    try:
        updates = _tg.get_updates(offset=state.get("tg_offset", 0), timeout=2)
    except Exception as e:
        _log(f"long-poll error: {e}")
        time.sleep(5)
        return
    for u in updates:
        state["tg_offset"] = u["update_id"] + 1
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        chat = (msg.get("chat") or {})
        expected = os.environ.get("COACH_CHAT_ID", "")
        if expected and str(chat.get("id", "")) != expected:
            continue
        text = msg.get("text", "")
        if not text:
            continue
        reply = _dispatch(text, state)
        if reply:
            _tg.send_text(reply)
    if updates:
        _save_state(state)


def _discover_chat():
    """Sub-command: wait for any message to the bot and print its chat_id."""
    os.environ.setdefault("COACH_CHAT_ID", "0")
    print("waiting for an inbound message to your coach bot... (send any text)")
    offset = 0
    while True:
        updates = _tg.get_updates(offset=offset, timeout=25)
        for u in updates:
            offset = u["update_id"] + 1
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("id"):
                print(f"\nchat_id: {chat['id']}")
                print(f"Add to .env:\n  COACH_CHAT_ID={chat['id']}")
                return


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover-chat", action="store_true",
                        help="wait for an inbound message and print its chat_id")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT,  _handle_sigterm)

    if args.discover_chat:
        _discover_chat()
        return

    _log("coach-bot up")
    state = _load_state()
    last_schedule = 0.0
    while not _stop:
        try:
            _poll_updates(state)
        except Exception as e:
            _log(f"poll error: {e}")
            time.sleep(5)
        now = time.time()
        if now - last_schedule >= SCHEDULE_CHECK_INTERVAL_S:
            for fn in (_maybe_push_morning, _maybe_push_evening,
                       _maybe_push_weekly, _maybe_push_emergency):
                try:
                    fn(state)
                except Exception as e:
                    _log(f"schedule error in {fn.__name__}: {e}")
            last_schedule = now
        time.sleep(POLL_INTERVAL_S)
    _log("coach-bot down")


if __name__ == "__main__":
    main()
