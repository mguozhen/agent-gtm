---
name: multica-hunter-x-analytics
description: Collect and report Hunter's X analytics telemetry for Multica/GTM Swarm. Use when handling collect_daily_telemetry jobs, exporting X analytics for @GuoHunter95258, validating swarm.telemetry.v1 batches, or pushing Hunter X artifacts and observations to GTM Swarm.
---

# Multica Hunter X Analytics

Use this skill for Hunter's X analytics agent: collect X analytics, normalize post metrics, and return GTM Swarm telemetry from Multica `run_only` jobs.

Reference account:

- X handle: `@GuoHunter95258`
- Local root: `/Users/siliconno3/x_agent`
- Account config: `/Users/siliconno3/x_agent/accounts/GuoHunter95258/engage_config.json`
- Chrome debug port: `10000`
- Swarm schema: `swarm.telemetry.v1`

## Source References

- Read [agent-json-contract.md](references/agent-json-contract.md) before generating or validating Swarm payloads.
- Read [gtm-swarm-cli.md](references/gtm-swarm-cli.md) when you need CLI setup, validation, push, or node-worker examples.

## Daily Telemetry Collection

1. Identify the job.
   - For Multica `collect_daily_telemetry`, use the task payload's `workspace`, `agent_key`, `platform`, `report_type`, `day`, `from`, `to`, `job_id`, `daily_run_id`, and `required_metrics`.
   - Default `agent_key` to `x-growth-agent` only when the job does not specify one.
   - Default `node_id` to `multica-agent-runtime` for Multica job results.

2. Collect Hunter X analytics.
   - Prefer the existing local exporter: run from `/Users/siliconno3/x_agent`.
   - Use `scripts/analytics_export_test.py` or the current analytics feedback script if the repo has evolved.
   - Export or inspect only Hunter-owned posts/replies/quote posts inside the requested window.
   - Join X analytics rows back to local posting metadata when possible; note weak attribution in the summary instead of pretending the match is exact.

3. Build telemetry.
   - Return a `batch` object with `schema_version`, `workspace`, `agent_key`, `node_id`, `sent_at`, `artifacts`, and `observations`.
   - Use artifacts for durable X objects that were created or discovered.
   - Use observations for metrics collected at a point in time.
   - Use X post IDs as `external_id`; do not invent IDs.
   - Put numeric values in `metrics`; put raw strings, row metadata, attribution confidence, and source file details in `payload`.

4. Complete the job.
   - On success, return `status: "completed"`, a short summary, and the telemetry `batch`.
   - On source failure, return `status: "failed"`, a short summary, and a stable `error` such as `source_unavailable`, `auth_required`, `no_export`, or `validation_failed`.
   - Keep Multica output decision-oriented. Do not paste long raw CSV rows unless the user asks.

## Required Metrics

When available, collect:

- `views`
- `likes`
- `replies`
- `reposts`
- `bookmarks`
- `profile_visits`
- `follows`
- `engagements`
- `engagement_rate`

Only include a metric when it is present and numeric. If X labels change, preserve the raw label in `payload.raw_metrics` and map only confident numeric fields.

## Hunter Analytics Judgement

Evaluate analytics by candidate-set entry, not likes alone.

Strong signals:

- Profile visits from replies or quote posts
- New follows from replies or quote posts
- OP responses
- Replies/reposts from builders, operators, AI-agent, GTM, automation, or workflow-execution accounts
- Repeated performance in the same target/account pool
- Top replies crossing 500-1,000 impressions

Weak signals:

- Generic AI-news impressions without profile visits or follows
- Broad-audience likes outside Hunter's intended identity
- Overcrowded threads with no OP response
- One-off spikes from accounts that do not fit the target pool

Recommendations must name the evidence and the system change. Use these buckets:

- `responsive_hub_scout`: target discovery and target scoring changes
- `candidate_set_entry_score`: reply opportunity scoring changes
- `bandit_allocation`: volume shifts across topic or target pools
- `negative_signal_guard`: filters for low-quality or risky replies
- `reply_taste_model`: prompt/style changes based on winners and losers

## Validation

Before pushing or returning a batch:

- Confirm `schema_version` is `swarm.telemetry.v1`.
- Confirm every timestamp is ISO 8601 with timezone, preferably UTC `Z`.
- Confirm `platform` and `artifact_type` are lowercase.
- Confirm every observation has `platform`, `artifact_type`, `external_id`, `observed_at`, and numeric `metrics`.
- Confirm every artifact has a real `external_id` and URL when known.
- Validate with `gtm-swarm push batch` or the local Node validator when the CLI is available.

## Push

If the task requires pushing telemetry, use the GTM Swarm CLI with credentials from the environment:

```bash
export GTM_SWARM_SERVER="https://gtm.shulex.com"
export GTM_SWARM_TOKEN="<workspace swarm_token>"
gtm-swarm push batch ./result.json
```

Do not print tokens. If credentials are missing, return `auth_required` and state which environment variable is missing.
