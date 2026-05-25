"""Predict whether a queued reply will get engagement.

Pure heuristic — no LLM call — so it can run inline in producers
(engage_daemon, buildlog_drafts, quote_scout) without adding latency
to the queue → Telegram card path.

The prediction is folded into the entry as:
    predicted_engagement: "low" | "medium" | "high"
    prediction_confidence: 0..3   (how many signals agreed)
    prediction_reasons: ["..."]   (human-readable, shown on the card)
    scored_at: timestamp

History lives in state/reply_engagement.json (written by the scorer
daemon after re-fetching posted replies at 1h / 6h / 24h). When history
is empty (first run) the scorer falls back to pattern heuristics only.
"""
import json
import os
import re
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
ENGAGEMENT_PATH = os.path.join(ROOT_DIR, "state", "reply_engagement.json")
COACH_STATE_PATH = os.path.join(ROOT_DIR, "state", "coach_state.json")

# Openers that historically dilute reply value across the operator base.
# Lowercased, stripped. Anchored at the start of the reply.
DEAD_OPENERS = (
    "great point", "great post", "love this", "love it",
    "exactly", "this", "this!", "this is", "this is huge",
    "spot on", "agreed", "100%", "absolutely",
    "couldn't agree more", "well said", "facts",
    "so true", "amazing", "based", "interesting",
    "thanks for sharing", "appreciate this",
)


def _load_engagement_history() -> dict:
    try:
        with open(ENGAGEMENT_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _load_coach_state() -> dict:
    """Coach state holds user-set gate level + paused-target list.
    Lazy-defaulted so the scorer works before the coach bot ever runs."""
    try:
        with open(COACH_STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"gate": "normal", "paused_targets": []}


def _target_hit_rate(history, target):
    """Returns (engaged_count, total_count) of finalized replies under @target."""
    eng = 0
    tot = 0
    target_l = target.lower()
    for rec in history.values():
        if rec.get("target", "").lower() != target_l:
            continue
        if not rec.get("final_at"):
            continue
        tot += 1
        if rec.get("engaged"):
            eng += 1
    return eng, tot


def _opener_hit_rate(history, opener):
    """Hit rate for replies starting with `opener` (lowercased first 25 chars)."""
    eng = 0
    tot = 0
    op = opener.lower().strip()[:25]
    if not op:
        return 0, 0
    for rec in history.values():
        if not rec.get("final_at"):
            continue
        rt = (rec.get("reply_text") or "").lower().strip()[:25]
        if not rt or rt[:len(op)] != op:
            continue
        tot += 1
        if rec.get("engaged"):
            eng += 1
    return eng, tot


def _starts_with_dead_opener(reply_text: str) -> str:
    """If the reply starts with a known dead opener, return that opener.
    Otherwise empty string."""
    s = reply_text.lower().strip()
    # Strip leading punctuation/quotes that don't change perception
    s = s.lstrip("\"'`* ")
    for op in DEAD_OPENERS:
        if s.startswith(op):
            return op
    return ""


def score_entry(entry: dict) -> dict:
    """Score a queued entry. Mutates `entry` in place AND returns it.

    Signals (each can lower or raise confidence):
      - target paused by coach → low (hard)
      - target lifetime hit rate < 5% over >=5 replies → low
      - reply starts with a dead opener (great point, exactly, ...) → low
      - parent post >24h old → low (stale post)
      - parent post has > 200 existing replies → low (you're invisible)
      - reply length < 40 or > 240 chars → low
      - target hit rate >= 15% over >=3 replies → high
      - opener pattern matched a >=20% hit-rate opener with >=3 samples → high

    Output fields on entry:
      predicted_engagement: low|medium|high
      prediction_confidence: number of signals (0..3+)
      prediction_reasons: list[str] (rendered on card)
      scored_at: timestamp
    """
    reasons_low = []
    reasons_high = []

    target = entry.get("target", "")
    reply_text = entry.get("reply_text", "") or ""
    age_min = entry.get("post_age_min", 0) or 0
    existing_replies = entry.get("post_replies", 0) or 0

    history = _load_engagement_history()
    coach = _load_coach_state()

    # Hard rule: paused target.
    if target and target.lower() in [t.lower() for t in coach.get("paused_targets", [])]:
        entry["predicted_engagement"] = "low"
        entry["prediction_confidence"] = 99
        entry["prediction_reasons"] = [f"@{target} is on your pause list"]
        entry["scored_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return entry

    # Target hit rate (only counts if we have enough samples).
    if target:
        eng, tot = _target_hit_rate(history, target)
        if tot >= 5 and eng / tot < 0.05:
            reasons_low.append(f"@{target} hit rate {eng}/{tot} lifetime")
        elif tot >= 3 and eng / tot >= 0.15:
            reasons_high.append(f"@{target} hit rate {eng}/{tot} lifetime")

    # Dead opener pattern.
    dead = _starts_with_dead_opener(reply_text)
    if dead:
        op_eng, op_tot = _opener_hit_rate(history, dead)
        if op_tot >= 3 and op_eng / op_tot < 0.10:
            reasons_low.append(f'opens with "{dead}" — {op_eng}/{op_tot} hit rate')
        elif op_tot == 0:
            # No history yet, but pattern is on the dead list — flag softly.
            reasons_low.append(f'opens with "{dead}" — known low-engagement opener')

    # Post staleness.
    if age_min >= 24 * 60:
        reasons_low.append(f"post is {age_min // 60}h old (low audience left)")
    elif age_min >= 6 * 60:
        reasons_low.append(f"post is {age_min // 60}h old (window closing)")

    # Too many existing replies = you're buried.
    if existing_replies >= 200:
        reasons_low.append(f"{existing_replies} existing replies (you'll be invisible)")
    elif existing_replies >= 100:
        reasons_low.append(f"{existing_replies} existing replies (visibility risk)")

    # Reply length sanity.
    rlen = len(reply_text)
    if rlen < 40:
        reasons_low.append(f"reply only {rlen} chars (too short to add value)")
    elif rlen > 240:
        reasons_low.append(f"reply {rlen} chars (longer replies underperform)")

    # Decide label.
    gate = coach.get("gate", "normal")
    low_threshold = {"strict": 1, "normal": 2, "loose": 3}.get(gate, 2)

    if len(reasons_low) >= low_threshold and not reasons_high:
        label = "low"
    elif len(reasons_high) >= 1 and len(reasons_low) == 0:
        label = "high"
    else:
        label = "medium"

    entry["predicted_engagement"] = label
    entry["prediction_confidence"] = len(reasons_low) + len(reasons_high)
    entry["prediction_reasons"] = (reasons_low + reasons_high) if (reasons_low or reasons_high) else []
    entry["scored_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return entry


def warning_line(entry: dict) -> str:
    """Render a single Markdown line for the Telegram card. Empty string if
    the prediction is high or there's nothing to warn about."""
    label = entry.get("predicted_engagement")
    reasons = entry.get("prediction_reasons", [])
    if label == "low" and reasons:
        return "⚠️ *predicted low:* " + " · ".join(reasons[:3])
    if label == "high" and reasons:
        return "✨ *predicted strong:* " + " · ".join(reasons[:2])
    return ""
