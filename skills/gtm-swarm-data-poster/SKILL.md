---
name: gtm-swarm-data-poster
description: Post agent telemetry data to GTM Swarm. Use when an agent needs to build, validate, or push swarm.telemetry.v1 batches, artifacts, or observations from X or another platform.
---

# GTM Swarm Data Poster

Use this skill only for posting structured telemetry to GTM Swarm.

Read [agent-json-contract.md](references/agent-json-contract.md) before generating payloads. Read [gtm-swarm-cli.md](references/gtm-swarm-cli.md) only when you need CLI commands.

## Contract

- Use `schema_version: "swarm.telemetry.v1"`.
- Use ISO 8601 timestamps with timezone, preferably UTC `Z`.
- `workspace` is the GTM workspace slug.
- `agent_key` is the stable machine-readable agent name.
- `node_id` is the stable machine or runtime name.
- `platform` and `artifact_type` are lowercase.
- `external_id` must be the real source platform ID.
- Metric values must be numbers.
- Put raw strings, nested metadata, and attribution details in `payload`.

## Payload Types

Use an artifact when the agent created or discovered a durable object:

```json
{
  "platform": "x",
  "artifact_type": "post",
  "external_id": "1794312345678900000",
  "url": "https://x.com/acme/status/1794312345678900000",
  "body": "We shipped today.",
  "created_at": "2026-05-25T08:10:00Z",
  "payload": {}
}
```

Use an observation when the agent collected metrics for an artifact:

```json
{
  "platform": "x",
  "artifact_type": "post",
  "external_id": "1794312345678900000",
  "observed_at": "2026-05-25T09:25:00Z",
  "metrics": {
    "views": 1834,
    "replies": 12
  }
}
```

Return or push a batch:

```json
{
  "schema_version": "swarm.telemetry.v1",
  "workspace": "flatkey",
  "agent_key": "x-growth-agent",
  "node_id": "mac-mini-01",
  "sent_at": "2026-05-25T09:30:00Z",
  "artifacts": [],
  "observations": []
}
```

## Daily Telemetry Jobs

For `collect_daily_telemetry`, use the job payload values for `workspace`, `agent_key`, `platform`, `day`, `from`, `to`, `job_id`, `daily_run_id`, and `required_metrics`.

Successful result:

```json
{
  "status": "completed",
  "summary": "Collected 24 X observations for 2026-05-26.",
  "batch": {
    "schema_version": "swarm.telemetry.v1",
    "workspace": "flatkey",
    "agent_key": "x-growth-agent",
    "node_id": "multica-agent-runtime",
    "sent_at": "2026-05-27T00:20:00Z",
    "artifacts": [],
    "observations": []
  }
}
```

Failure result:

```json
{
  "status": "failed",
  "summary": "X analytics page was unavailable.",
  "error": "source_unavailable"
}
```

## Push

Use credentials from the environment. Never print tokens.

```bash
export GTM_SWARM_SERVER="https://gtm.shulex.com"
export GTM_SWARM_TOKEN="..."
gtm-swarm push batch ./result.json
```
