"""Generate post text or reply text using Claude via Anthropic SDK."""
import os
import sys
import time
import anthropic

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
ACCOUNTS_DIR = os.path.join(ROOT_DIR, "accounts")
GLOBAL_PLAYBOOK = os.path.join(ROOT_DIR, "playbook.md")

sys.path.insert(0, SCRIPTS_DIR)
import env; env.load()

_client = None
_cache = {}


def _client_get() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _load(path: str) -> str:
    if path not in _cache:
        try:
            _cache[path] = open(path).read()
        except FileNotFoundError:
            _cache[path] = ""
    return _cache[path]


def _load_account(handle: str) -> str:
    """Returns playbook for the given account handle."""
    base = os.path.join(ACCOUNTS_DIR, handle)
    return _load(os.path.join(base, "playbook.md"))


def _call_claude(messages: list, max_tokens: int) -> str:
    """Call Claude with automatic retry on rate limit."""
    for attempt in range(3):
        try:
            msg = _client_get().messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=max_tokens,
                messages=messages,
            )
            return msg.content[0].text.strip()
        except Exception as e:
            if "rate_limit" in str(e).lower() and attempt < 2:
                wait = 20 * (attempt + 1)
                print(f"  [generate] rate limit, waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    return ""


def extract_topic_from_screenshot(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """Use Claude vision to read an X screenshot and extract the topic.
    Returns {topic, author_handle, post_excerpt, confidence} or {topic: ""}
    if no usable signal. Caller feeds `topic` into topic_research.

    Sonnet (not Haiku) because OCR accuracy on stylized X screenshots matters
    more than the few cents of cost difference."""
    import base64
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    prompt = """This is a screenshot from X (Twitter). Extract the post's topic.

Return STRICT JSON with these fields (no markdown fences, no preamble):
{
  "topic":         "the most specific searchable keyword/phrase (2-5 words). e.g. 'Hermes 4 model', 'Anthropic Skills', 'Mike Krieger harness'. NOT a generic word like 'AI'.",
  "author_handle": "the @ handle of the post's author, without the @ symbol",
  "post_excerpt":  "the first 200 chars of the post body",
  "confidence":    "high | medium | low — how sure are you this is one specific X post on a specific topic?"
}

If the image isn't an X post or you can't read it confidently, return:
{"topic": "", "author_handle": "", "post_excerpt": "", "confidence": "low"}"""

    msg = _client_get().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    raw = msg.content[0].text.strip()
    # Strip code fences if the model added them
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()
    try:
        import json as _json
        d = _json.loads(raw)
        return {
            "topic":         (d.get("topic") or "").strip()[:120],
            "author_handle": (d.get("author_handle") or "").strip().lstrip("@")[:60],
            "post_excerpt":  (d.get("post_excerpt") or "").strip()[:300],
            "confidence":    (d.get("confidence") or "low").strip().lower(),
        }
    except Exception:
        return {"topic": "", "author_handle": "", "post_excerpt": "",
                "confidence": "low"}


def generate_post(handle: str, topic: str = "") -> str:
    """
    Generate an original tweet for the account.
    topic: optional hint (content area, event, angle). If empty, the AI picks freely.
    """
    playbook = _load_account(handle)
    topic_line = f"Topic or angle to write about: {topic}" if topic else "Pick the most compelling topic to post about today based on the playbook."

    prompt = f"""You are operating an X (Twitter) account. Here is the playbook:

{playbook}

Write one original tweet. Follow all rules strictly.
{topic_line}

Reply with ONLY the tweet text. No quotes, no explanations, no hashtags unless the soul explicitly allows them."""

    return _call_claude([{"role": "user", "content": prompt}], max_tokens=300)


def generate_reply(handle: str, tweet_text: str, tweet_author: str = "", context: str = "") -> str:
    """
    Generate a reply to a specific tweet.
    tweet_text: the original tweet content.
    tweet_author: handle of the tweet author (for context).
    context: extra situation hint (e.g. 'competitor complaint', 'trending topic').
    """
    playbook = _load_account(handle)
    author_line = f"Tweet author: @{tweet_author}" if tweet_author else ""
    context_line = f"Situation context: {context}" if context else ""

    prompt = f"""You are operating an X (Twitter) account. Here is the playbook:

{playbook}

Write a reply to the following tweet. Follow all rules strictly.

YOUR SINGLE JOB: write something that makes people stop scrolling.
Use one of these opening shapes:
- A number they haven't heard: "X% of [thing] actually [surprising result]."
- A contrarian opener: "Everyone focuses on [obvious]. The real problem is [actual]."
- A curiosity gap: "[Thing everyone ignores] is where [big outcome] usually hides."
- A one-liner that lands hard — say the whole thing in one sentence if you can.

No hedging, no "it depends", no long explanations. Short and punchy wins.
Never say "Great post!" or agree generically. Max 1-2 sentences.

IMPORTANT: You must always write a reply. Never refuse, never explain why you can't reply. If the tweet topic is adjacent, find the angle that connects to Amazon reviews, customer feedback, ecommerce operations, or product-market fit.

{author_line}
{context_line}

Tweet to reply to:
{tweet_text}

Reply with ONLY the reply text. No quotes, no explanations."""

    return _call_claude([{"role": "user", "content": prompt}], max_tokens=200)


def generate_engaged_reply(target_handle: str, target_post_text: str,
                           library_path: str, archetypes: dict,
                           hunter_handle: str = "GuoHunter95258",
                           examples_per_prompt: int = 8,
                           max_reply_chars: int = 240) -> str:
    """
    Few-shot reply generation for Hunter's engage loop.

    target_handle:     the account whose post we're replying under (e.g. "cwolferesearch")
    target_post_text:  the post text we're replying to
    library_path:      path to state/winning_replies.json (harvested winners)
    archetypes:        {archetype_name: [target_handle, ...]} from engage_config.json
    hunter_handle:     used to load Hunter's playbook for voice grounding (optional)
    examples_per_prompt: how many winning-reply examples to inject as few-shot
    max_reply_chars:   model is asked to stay under this length

    Sampling priority: same target → same archetype → any. Dedupes by reply_url.
    Returns generated reply text (no quotes/prefixes).
    """
    import json as _json
    import random as _random
    _ROOT_DIR = os.path.dirname(SCRIPTS_DIR)

    try:
        with open(library_path) as f:
            library = _json.load(f)
    except (FileNotFoundError, ValueError):
        library = []

    target_l = target_handle.lower()
    same_target = [w for w in library if w["target_handle"].lower() == target_l]

    archetype_of = ""
    for name, members in archetypes.items():
        if any(m.lower() == target_l for m in members):
            archetype_of = name
            break
    same_arch = []
    if archetype_of:
        peers = {m.lower() for m in archetypes[archetype_of] if m.lower() != target_l}
        same_arch = [w for w in library if w["target_handle"].lower() in peers]

    other = [w for w in library
             if w["target_handle"].lower() != target_l
             and w["target_handle"].lower() not in {m.lower() for ms in archetypes.values() for m in ms if archetype_of and m in archetypes[archetype_of]}]

    # Shuffle within each band so the model doesn't see the same first 8 each call
    _random.shuffle(same_target); _random.shuffle(same_arch); _random.shuffle(other)
    pool = same_target + same_arch + other

    chosen: list = []
    seen_urls: set = set()
    for w in pool:
        u = w.get("reply_url", "")
        if u and u in seen_urls:
            continue
        seen_urls.add(u)
        chosen.append(w)
        if len(chosen) >= examples_per_prompt:
            break

    if not chosen:
        return _fallback_reply(target_post_text, max_reply_chars)

    examples_block = "\n\n".join(
        f"OP (@{w['target_handle']}): {w['target_post_text'][:280]}\n"
        f"WINNING REPLY (@{w['reply_author']}, {w['reply_likes']} likes, "
        f"reason: {','.join(w['reasons'])}): {w['reply_text'][:400]}"
        for w in chosen
    )

    # Negative few-shot: most recent rejected replies (with the reason
    # Hunter typed in Telegram). Also includes "rejected_after_post" —
    # replies that posted but Hunter immediately flagged as a mistake
    # (e.g. "too generic"). Their pattern should be avoided going forward
    # even though the tweet stays live on X.
    negative_block = ""
    try:
        with open(os.path.join(_ROOT_DIR, "state", "reply_queue.json")) as f:
            queue = _json.load(f)
        rejected = [
            q for q in queue
            if q.get("status") in ("rejected", "rejected_after_post")
               and q.get("rejection_reason")
        ]
        rejected.sort(key=lambda q: q.get("rejected_at", ""), reverse=True)
        if rejected[:3]:
            negative_block = "\n\nAVOID — Hunter rejected these:\n\n" + "\n\n".join(
                f"OP (@{r['target']}): {r['target_text'][:200]}\n"
                f"REJECTED (reason: {r['rejection_reason']}): {r['reply_text'][:300]}"
                for r in rejected[:3]
            )
    except Exception:
        pass

    hunter_playbook = _load(os.path.join(ACCOUNTS_DIR, hunter_handle, "playbook.md"))
    # Skip empty/placeholder playbooks — they add noise and the few-shot is enough.
    has_real_playbook = hunter_playbook and "[Fill in content]" not in hunter_playbook and len(hunter_playbook.strip()) > 40
    playbook_block = f"Your voice (you are operating @{hunter_handle}):\n{hunter_playbook}\n\n" if has_real_playbook else ""

    # Tone diversity — pick a tone per call to prevent the
    # contrarian/reframe pattern from over-dominating (the few-shot examples
    # are heavily contrarian because Hunter previously approved that style).
    # Distribution favors curious/share-back/builder-experience — Hunter
    # explicitly asked for less negative tone (2026-05-17).
    tone = _random.choices(
        ["curious", "share-back", "builder-experience", "helpful-addition", "contrarian"],
        weights=[28, 22, 22, 18, 10],
        k=1,
    )[0]
    tone_brief = {
        "curious":            ("Open with a CURIOUS question that invites OP to share more — "
                               "ask about something specific they observed, decided, or learned. "
                               "Affirm the experience first, then ask. Do NOT challenge their premise."),
        "share-back":         ("SHARE something concrete from your own experience that connects "
                               "to OP's post — a number, a thing you tried, a tradeoff you hit. "
                               "Don't ask a question; just contribute a peer datapoint."),
        "builder-experience": ("Reply as someone CURRENTLY BUILDING in this space. Mention what "
                               "you're shipping, a specific bug or surprise, or a constraint you're "
                               "wrestling with — that connects to OP's observation."),
        "helpful-addition":   ("ADD something useful OP probably hasn't considered — a tool, a "
                               "reference, a related angle, an edge case. No challenge; pure addition."),
        "contrarian":         ("Push back with a SPECIFIC reason — a counterexample, a hidden "
                               "constraint, a different data point. Earn the disagreement. Don't "
                               "just reframe with 'the real issue is X' — name what's actually wrong."),
    }[tone]

    prompt = f"""{playbook_block}You are writing a reply on X to a post about AI/tech. Below are reply
patterns that landed well in this niche — match the substance and brevity,
but DO NOT copy the all-lowercase styling. Use natural sentence
capitalization (capitalize the first word, "I", proper nouns).

{examples_block}{negative_block}

POST by @{target_handle}:
{target_post_text[:600]}

TONE for THIS reply: {tone}
{tone_brief}

Write ONE reply. HARD RULES on the reply:
- Engage directly with the substance — react to the claim, idea, fact, or
  observation. Never ask for clarification, meta-comment, or refuse.
- Use NATURAL CAPITALIZATION (sentence case). Do NOT write everything in
  lowercase — the few-shot examples above use lowercase but that style is
  retired. Capitalize first word of each sentence, "I", proper nouns.
- Max {max_reply_chars} characters. No quotes, emojis, hashtags, @-mentions.
- Honor the TONE assigned above — if curious, ask; if share-back, contribute;
  if contrarian, push back with a specific reason. Do NOT default to the
  "X doesn't fix Y, the real issue is Z" reframe shape — that's overused.

OUTPUT FORMAT — return strict JSON with exactly these three string fields:
{{
  "op_summary":  "what the OP is saying, in 1 short sentence (≤120 chars)",
  "reply_angle": "what your reply does and why (≤120 chars, e.g. 'questions whether dense training data is the real bottleneck')",
  "reply":       "the actual reply text (≤{max_reply_chars} chars)"
}}
Return ONLY the JSON object, no markdown fences, no preamble."""

    raw = _call_claude([{"role": "user", "content": prompt}], max_tokens=600)
    return _parse_reply_json(raw, max_reply_chars)


def _parse_reply_json(raw: str, max_reply_chars: int) -> dict:
    """Parse the JSON the generator returns. Falls back to treating `raw` as
    the reply text if JSON parsing fails — so an off-format response still
    produces a usable reply (just without summary/angle)."""
    import json as _json
    s = raw.strip()
    # Strip code fences if the model added them despite instructions
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        d = _json.loads(s)
        reply = (d.get("reply") or "").strip().strip('"').strip()
        if len(reply) > max_reply_chars:
            reply = reply[:max_reply_chars].rsplit(" ", 1)[0] + "…"
        return {
            "reply":       reply,
            "op_summary":  (d.get("op_summary")  or "").strip()[:200],
            "reply_angle": (d.get("reply_angle") or "").strip()[:200],
        }
    except Exception:
        # Fallback — treat raw output as the reply itself
        reply = raw.strip().strip('"').strip()
        if len(reply) > max_reply_chars:
            reply = reply[:max_reply_chars].rsplit(" ", 1)[0] + "…"
        return {"reply": reply, "op_summary": "", "reply_angle": ""}


def generate_quote_tweet(target_handle: str, target_post_text: str,
                         handle: str = "GuoHunter95258",
                         max_chars: int = 280,
                         previous_reject_reason: str = "") -> dict:
    """Generate a quote-tweet comment in the operator's voice. The QT must add
    a contrarian, builder-grounded, or analogy-driven angle — never empty
    hype. Returns {reply, op_summary, reply_angle}."""
    playbook = _load_account(handle)
    has_real_playbook = (playbook and "[Fill in content]" not in playbook
                         and len(playbook.strip()) > 40)
    playbook_block = (f"Your voice (you are operating @{handle}):\n{playbook}\n\n"
                      if has_real_playbook else "")
    reject_block = (f"\nAVOID — previously rejected for this reason: "
                    f"{previous_reject_reason}\n"
                    if previous_reject_reason else "")

    prompt = f"""{playbook_block}You are writing a QUOTE TWEET on X. You are quoting the post below to
your own audience — they will see your comment first, then the quoted tweet.

HARD RULES on the QT:
- Add a real angle: a contrarian take with a reason, a concrete data point or
  experience, a cross-domain analogy, or a builder reframe ("this changes how
  I'm thinking about X").
- NEVER write empty hype like "this is huge", "must-read", or "wow".
- NEVER agree generically. NEVER thank or praise the author.
- Max {max_chars} characters. No quotes, no hashtags, no emojis, no @-mentions.
{reject_block}
POST by @{target_handle}:
{target_post_text[:600]}

OUTPUT FORMAT — return strict JSON with exactly these three string fields:
{{
  "op_summary":  "what the OP is saying, in 1 short sentence (≤120 chars)",
  "reply_angle": "what your QT does and why (≤120 chars)",
  "reply":       "the actual QT comment (≤{max_chars} chars)"
}}
Return ONLY the JSON object, no markdown fences, no preamble."""
    raw = _call_claude([{"role": "user", "content": prompt}], max_tokens=600)
    return _parse_reply_json(raw, max_chars)


NEWS_MODE_PROMPTS = {
    "quote_take": {
        "framing": (
            "You are writing a QUOTE TWEET on a piece of AI news. Your audience will "
            "see your comment FIRST, then the quoted news post."
        ),
        "rules": (
            "- Add a real angle: a contrarian take with a reason, a concrete data point "
            "  from your building experience, a cross-domain analogy, or a builder "
            "  reframe (\"this changes how I'm thinking about X\").\n"
            "- NEVER write empty hype (\"this is huge\", \"must-read\", \"wow\").\n"
            "- NEVER agree generically or thank/praise the author."
        ),
        "angle_hint": "what your QT does and why",
    },
    "original_reframe": {
        "framing": (
            "You are writing an ORIGINAL TWEET that reframes a piece of AI news for your "
            "audience. Do NOT quote-tweet, do NOT @-mention the source. Treat the news as "
            "context — your job is to extract the part that actually matters and say it "
            "in your own voice."
        ),
        "rules": (
            "- Lead with the most concrete, non-obvious takeaway. NOT \"X just announced Y\".\n"
            "- Allowed shapes: the one detail most people will miss / what this means for "
            "  builders / a small prediction with a reason / a sharp comparison to a past event.\n"
            "- NEVER summarize the news verbatim. NEVER write a recap.\n"
            "- NEVER include URLs or @-mentions."
        ),
        "angle_hint": "the takeaway your post lands on",
    },
    "counter_take": {
        "framing": (
            "You are writing a CONTRARIAN COUNTER-TAKE on a piece of AI news. The default "
            "framing in everyone's timeline will be hype or excitement — your job is to push "
            "back with a specific, defensible reason. This can be a QT or original; pick the "
            "form that lands harder. Default to QT unless the news is well-known enough that "
            "your audience already saw it."
        ),
        "rules": (
            "- Lead with what's overrated, missing, or wrong in the dominant framing.\n"
            "- Back it with a reason: a constraint people are ignoring, a prior precedent "
            "  that didn't pan out, a specific failure mode, a counter-example you've seen.\n"
            "- NEVER be edgy for its own sake — the take must be defensible if challenged.\n"
            "- NEVER attack the author personally. Attack the IDEA or the FRAMING."
        ),
        "angle_hint": "what the consensus take is and why you disagree",
    },
}


def generate_news_take(mode: str,
                       source_handle: str,
                       source_post_text: str,
                       handle: str = "GuoHunter95258",
                       max_chars: int = 280,
                       previous_reject_reason: str = "") -> dict:
    """Generate an AI-news reaction in one of three modes:
      quote_take       — QT with Hunter's angle (entry kind='quote')
      original_reframe — standalone post extracting the takeaway (kind='original')
      counter_take     — contrarian pushback against the dominant framing
                         (model picks QT or original; we coerce kind from the prompt)

    Returns {reply, op_summary, reply_angle}. Caller maps `mode` to entry kind
    (quote_take → 'quote', original_reframe → 'original', counter_take → 'quote')."""
    cfg = NEWS_MODE_PROMPTS.get(mode)
    if cfg is None:
        raise ValueError(f"unknown news mode: {mode}")

    playbook = _load_account(handle)
    has_real_playbook = (playbook and "[Fill in content]" not in playbook
                         and len(playbook.strip()) > 40)
    playbook_block = (f"Your voice (you are operating @{handle}):\n{playbook}\n\n"
                      if has_real_playbook else "")
    reject_block = (f"\nAVOID — previously rejected for this reason: "
                    f"{previous_reject_reason}\n"
                    if previous_reject_reason else "")

    prompt = f"""{playbook_block}{cfg['framing']}

HARD RULES:
{cfg['rules']}
- Max {max_chars} characters. No quotes around the post, no hashtags, no
  emojis, no @-mentions, no URLs.
{reject_block}
NEWS POST by @{source_handle}:
{source_post_text[:800]}

OUTPUT FORMAT — return strict JSON with exactly these three string fields:
{{
  "op_summary":  "what the news post is saying, in 1 short sentence (≤120 chars)",
  "reply_angle": "{cfg['angle_hint']} (≤120 chars)",
  "reply":       "the actual post text (≤{max_chars} chars)"
}}
Return ONLY the JSON object, no markdown fences, no preamble."""

    raw = _call_claude([{"role": "user", "content": prompt}], max_tokens=600)
    return _parse_reply_json(raw, max_chars)


def classify_intent(input_content: str, instruction: str) -> dict:
    """Decide whether the operator wants research (web search → plain answer)
    or a draft (X scrape → TG card with buttons). Used by the repost-bot
    routing layer. Returns {mode: 'research'|'draft', reasoning: str}.
    """
    import json as _json
    prompt = f"""You are a router for an X-content bot. Given an input topic and the operator's instruction, classify the intent.

INPUT TOPIC: {input_content}
OPERATOR INSTRUCTION: {instruction}

Two modes:
- "research": the operator wants information/synthesis/an answer. They want to LEARN something. They do NOT want a draft post yet. Examples:
    "find recent CEO interviews and summarize"
    "what's the consensus on X"
    "tell me about Y's competitors"
    "explain how Z works"
    "search for what people are saying about W"
- "draft": the operator wants a post/QT/reaction drafted in their voice, ready to post on X. Examples:
    "give me a sarcastic take"
    "5 builder reframes"
    "QT this with a contrarian angle"
    "summarize it in my voice"  (this is a draft, written as their voice)
    "make a post about this"

Output STRICT JSON only, no markdown fences, no preamble:
{{"mode": "research" | "draft", "reasoning": "≤120 chars why"}}"""

    raw = _call_claude([{"role": "user", "content": prompt}], max_tokens=150).strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        d = _json.loads(raw)
        mode = d.get("mode", "").strip().lower()
        if mode not in ("research", "draft"):
            mode = "draft"
        return {"mode": mode, "reasoning": d.get("reasoning", "")[:200]}
    except Exception:
        return {"mode": "draft", "reasoning": "classifier_parse_fail"}


def research_with_web_search(query: str, context: str = "",
                             max_uses: int = 5) -> str:
    """Use Claude's web_search tool to answer a research question.
    Returns a synthesized plain-text answer suitable for sending to Telegram.
    """
    sys_prompt = (
        "You are a research assistant for an X content operator. "
        "Use the web_search tool to find recent, credible sources. "
        "Synthesize findings into a tight answer: lead with the headline insight, "
        "then 3-6 bullet points with specific facts/quotes/numbers, then a "
        "1-line 'why it matters'. Cite key sources inline as plain URLs. "
        "Keep total under ~1500 chars so it fits in one Telegram message."
    )
    user = f"Topic / context: {context}\n\nQuestion: {query}" if context else query
    try:
        msg = _client_get().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            system=sys_prompt,
            tools=[{"type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": max_uses}],
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        return f"⚠️ research call failed: {e}"
    parts = []
    for block in msg.content:
        # Anthropic's SDK returns blocks of type 'text', 'server_tool_use',
        # 'web_search_tool_result'. We only surface 'text' to the operator.
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
    out = "\n\n".join(p.strip() for p in parts if p).strip()
    return out or "_(web search returned no text)_"


def generate_custom_take(source_handle: str, source_post_text: str,
                         instruction: str,
                         handle: str = "GuoHunter95258",
                         max_chars: int = 280) -> dict:
    """Generate a take on a specific source tweet, following a user-supplied
    instruction verbatim (rather than the canned QT/reframe/counter modes).

    Used by the repost-bot URL+instruction flow: operator sends a tweet URL
    plus a free-text directive like "give me a sarcastic take" or "turn this
    into a builder reframe with a specific number". The instruction takes
    priority over mode rotation — caller usually invokes this 3-5 times to
    produce variants the operator can pick from.

    Returns {reply, op_summary, reply_angle}."""
    playbook = _load_account(handle)
    has_real_playbook = (playbook and "[Fill in content]" not in playbook
                         and len(playbook.strip()) > 40)
    playbook_block = (f"Your voice (you are operating @{handle}):\n{playbook}\n\n"
                      if has_real_playbook else "")

    prompt = f"""{playbook_block}You are writing a post on X that reacts to the source tweet below.
Follow the OPERATOR INSTRUCTION exactly — it is the single most important
constraint and overrides any default style preference.

OPERATOR INSTRUCTION:
{instruction.strip()}

HARD RULES that always apply:
- Max {max_chars} characters. No hashtags. No emojis. No @-mentions. No URLs.
- Output is a standalone post (could be a QT or original — whichever the
  instruction implies). Do NOT include the source tweet's URL in your output.
- Never refuse, never meta-comment ("here's my take:"), never explain what
  you're about to do. Just produce the post itself.

SOURCE TWEET by @{source_handle}:
{source_post_text[:800]}

OUTPUT FORMAT — return strict JSON with these three string fields:
{{
  "op_summary":  "what the source tweet is saying, 1 short sentence (≤120 chars)",
  "reply_angle": "how your post fulfills the operator's instruction (≤120 chars)",
  "reply":       "the actual post text (≤{max_chars} chars)"
}}
Return ONLY the JSON object, no markdown fences, no preamble."""

    raw = _call_claude([{"role": "user", "content": prompt}], max_tokens=600)
    return _parse_reply_json(raw, max_chars)


def generate_buildlog_post(handle: str = "GuoHunter95258",
                           context: str = "",
                           max_chars: int = 280,
                           previous_reject_reason: str = "") -> dict:
    """Generate an original 'building in public' post for the operator. The
    context is a free-form summary of what was built/learned recently — diff
    summary, founder sentence, or git commit list. Returns {reply, op_summary,
    reply_angle}."""
    playbook = _load_account(handle)
    has_real_playbook = (playbook and "[Fill in content]" not in playbook
                         and len(playbook.strip()) > 40)
    playbook_block = (f"Your voice (you are operating @{handle}):\n{playbook}\n\n"
                      if has_real_playbook else "")
    reject_block = (f"\nAVOID — previously rejected for this reason: "
                    f"{previous_reject_reason}\n"
                    if previous_reject_reason else "")

    prompt = f"""{playbook_block}You are writing ONE original tweet for a builder/founder audience on X
(researchers, engineers, founders — same crowd as @abacaj, @antirez,
@kalomaze read). The post must prove the author was at a keyboard today —
specific, concrete, unfakeable.

HARD RULES:
- Lead with a concrete fact, number, broken thing, or surprise from the
  context below. NOT a generic insight.
- Allowed shapes: "today I shipped X. Y broke." / "spent 3h debugging Z. the
  fix was W." / "what I was wrong about: ..." / contrarian take with a reason /
  small builder war story.
- BANNED: empty hype, generic AI/agent platitudes, "the future is ...",
  "the bottleneck isn't X it's Y" cliches, lists of bullet points, threads.
- Max {max_chars} characters. No quotes around the post, no hashtags, no
  emojis, no @-mentions.
{reject_block}
CONTEXT (use this — don't make stuff up beyond what's here):
{context[:2000]}

OUTPUT FORMAT — return strict JSON with exactly these three string fields:
{{
  "op_summary":  "1-line summary of what the post is about (≤120 chars)",
  "reply_angle": "what shape this post takes (≤120 chars, e.g. 'shipped + surprise')",
  "reply":       "the actual tweet text (≤{max_chars} chars)"
}}
Return ONLY the JSON object, no markdown fences, no preamble."""
    raw = _call_claude([{"role": "user", "content": prompt}], max_tokens=600)
    return _parse_reply_json(raw, max_chars)


def _fallback_reply(post_text: str, max_chars: int) -> str:
    """Used when the library is empty. Generic but-not-terrible reply."""
    prompt = f"""You are a frontier-AI founder replying to this post on X.
Write a single reply (≤ {max_chars} chars). Casual lowercase, no "Great post!",
no emojis, no hashtags. Sharp question, contrarian take, concrete data, or
cross-domain analogy. Reply text only.

POST: {post_text[:600]}"""
    return _call_claude([{"role": "user", "content": prompt}], max_tokens=400).strip().strip('"').strip()


def generate_amplify_comment(handle: str, hunter_text: str, mode: str = "reply", hunter_author: str = "") -> str:
    """
    Generate an in-network amplification comment.
    handle:       VOC_ai / SolveaCX (the account doing the amplifying)
    hunter_text:  the tweet content from the partner/hunter account
    mode:         "quote" (own audience sees it as a quote-tweet) or "reply" (under the original)
    hunter_author handle of the source tweet's author (for context only — do not @-mention).
    """
    playbook = _load_account(handle)

    if mode == "quote":
        framing = (
            "You are quote-tweeting this to YOUR followers. "
            "Add a take, hook, or insight that makes your audience want to read the quoted tweet. "
            "Do NOT @-mention the original author. Do NOT just say 'great point'. "
            "1-2 sentences max. No hashtags unless the playbook explicitly allows them."
        )
    else:
        framing = (
            "You are replying directly under this tweet so the author and their audience see your comment. "
            "Add genuine value: a specific number, an example, or a sharp angle. "
            "Sound like a real person — no 'Great post!', no generic agreement. "
            "1-2 sentences max. No hashtags unless the playbook explicitly allows them."
        )

    prompt = f"""You are operating an X (Twitter) account. Here is the playbook:

{playbook}

{framing}

IMPORTANT: You must always write a comment. Never refuse, never explain why you can't. If the tweet is adjacent, bridge it to Amazon reviews, customer feedback, or ecommerce operations.

Source tweet to {mode} on:
{hunter_text}

Reply with ONLY the comment text. No quotes, no explanations."""

    return _call_claude([{"role": "user", "content": prompt}], max_tokens=200)
