---
name: hunter-x-analytics-analysis
description: Analyze Hunter's X analytics and recommend growth-system changes. Use for interpreting @GuoHunter95258 metrics, judging candidate-set entry, and producing target, reply-taste, or algorithm recommendations.
---

# Hunter X Analytics Analysis

Use this skill for analysis only. Do not use it to post telemetry data to GTM Swarm; use `$gtm-swarm-data-poster` for that.

Reference account:

- X handle: `@GuoHunter95258`
- Local root: `/Users/siliconno3/x_agent`
- Account config: `/Users/siliconno3/x_agent/accounts/GuoHunter95258/engage_config.json`
- Existing exporter: `/Users/siliconno3/x_agent/scripts/analytics_export_test.py`

## Judgement Frame

Evaluate Hunter by candidate-set entry, not likes alone. Hunter should become legible as a builder/operator using AI agents for GTM, automation, and workflow execution.

Strong signals:

- Profile visits from replies or quote posts
- New follows from replies or quote posts
- OP responses
- Replies/reposts from people in the target cluster
- Repeated performance from the same target/account pool
- Top replies crossing 500-1,000 impressions

Weak signals:

- Broad AI-news impressions without profile visits or follows
- Likes from generic audiences
- Threads with no OP response
- Overcrowded threads where Hunter gets no graph edge

## Recommendation Buckets

Every recommendation must name evidence and a system change. Use these buckets:

- `responsive_hub_scout`: target discovery and target scoring changes
- `candidate_set_entry_score`: reply opportunity scoring changes
- `bandit_allocation`: volume shifts across topic or target pools
- `negative_signal_guard`: filters for low-quality or risky replies
- `reply_taste_model`: prompt/style changes based on winners and losers

## Output

Keep output short:

- Candidate-set judgement
- Evidence
- Target/account pool changes
- Reply-taste updates
- Algorithm updates
- Metrics to watch next run
