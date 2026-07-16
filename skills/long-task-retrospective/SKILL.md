---
name: long-task-retrospective
description: Analyze a completed or failed Codex task that ran unusually long, looped, polled excessively, or consumed too many tools/tokens. Reconstruct evidence, identify preventable delays, and persist at most three durable improvements. Do not use while work is still running; use agent-resume for waiting and continuation instead.
---

# Long-task retrospective

Run only after the target task has completed or failed. If work is still running, use `agent-resume` to wait durably and resume the same session; do not perform a retrospective yet.

## Budget

- One retrospective pass.
- At most 15 minutes or 25 tool calls.
- No product-code changes.
- No broad repository scan.
- Stop after the report and minimal durable updates are complete.

## Evidence

Prefer, in order:

1. Current session transcript and timestamps.
2. Tool, MCP, job, command, and sub-agent logs.
3. Git diff/status, tests, releases, and produced artifacts.
4. OpenTelemetry spans when available.
5. Existing `AGENTS.md`, skills, and project documentation.

Do not invent durations or causes. Mark conclusions uncertain when evidence is missing.

## Analysis

1. Reconstruct a compact phase timeline with elapsed time, result, and evidence.
2. Identify the three largest time sinks.
3. Separate unavoidable waiting from preventable delay.
4. Classify major delays, including:
   - missing prerequisite or weak acceptance gate;
   - wrong initial hypothesis;
   - repeated materially identical attempts;
   - unchanged polling or unnecessary coordination;
   - broad exploration, rereading, or context loss;
   - unnecessary or insufficient delegation;
   - over-verification or scope creep;
   - model, API, network, build, test, MCP, or infrastructure latency.
5. For each major delay record evidence, root cause, earliest detection point, faster alternative, and estimated saving.
6. Describe an efficient counterfactual execution path.

## Persistence policy

Inspect existing instructions first. Avoid duplicate or contradictory rules.

Route findings as follows:

- Stable cross-project requirement: `~/.codex/AGENTS.md`.
- Stable project invariant: nearest project `AGENTS.md`.
- Reusable workflow: project or global skill.
- Incident-specific evidence: `docs/agent-retrospectives/YYYY-MM-DD-<slug>.md`.
- Temporary detail: final report only.

Add no more than three durable rules. Each rule must be evidence-backed, specific, measurable, and likely to prevent recurrence. Prefer stop conditions, retry limits, progress definitions, preflight gates, and escalation thresholds over vague advice.

Never persist passwords, tokens, API keys, cookies, private keys, credentials, or raw secret-bearing logs.

## Verification

After edits:

1. Show the exact diff.
2. Check for duplicate or conflicting rules.
3. Check that no secret was persisted.
4. Confirm every persisted rule addresses an observed root cause.
5. Finish; do not resume implementation.

## Final report

Return:

- total elapsed and evidence quality;
- compact timeline;
- top three time sinks;
- root causes;
- unavoidable and preventable time;
- efficient alternative path;
- files updated and why;
- rules deliberately not persisted;
- estimated future saving;
- remaining uncertainty.
