# agent-resume in the Human Request stack

This document separates what `agent-resume` already does today from its proposed role in the Human Request stack.

## Current behavior

`agent-resume` is a local MCP helper that finds and resumes coding-agent sessions for Codex, OpenCode, and Claude Code.

Today it can:

- build a resume command for the selected client;
- freeze the target session before a delayed resume starts;
- accept a frozen target through `resume_bound_target`, persist a durable
  receipt keyed by `idempotency_key`, and return the same receipt on an exact
  replay without launching a second turn;
- read that receipt later with `query_resume_receipt`;
- run detached background jobs that wait for a process exit or a fixed timer; and
- persist job state under `~/.local/state/agent-resume` so the watcher can later resume the same chat.

For Codex, a successful detached `codex exec resume <SESSION_ID>` launch is
reported as `accepted` with reason `resume_process_started`. This is the
business admission boundary used by Agent Herder. Native JSONL correlation can
be added later as stricter admission hardening; it is not required for durable
duplicate prevention.

The current timer path is `wait_and_resume`. It records a timer job, starts a detached watcher, sleeps for the configured duration, and then resumes the frozen target session. `wait_job_status` reports both the watched process state and watcher state separately; timer jobs have no watched PID, so `alive`/`watched_pid_alive` are false while `watcher_alive` can still be true.

Session selection is client-specific:

- Codex can take the session/thread id from MCP `_meta.threadId`.
- OpenCode and Claude require `cwd` plus a 5-character correlation marker when no explicit session id is provided.
- `use_last` is disabled.

This is a wake/resume transport, not a notification system and not a secret-handling authority.

## Proposed role after Human Request resolution

In the Human Request stack, `agent-resume` should be the final transport that reopens the same coding-agent chat after a correlated HumanRequest has already been resolved.

Proposed flow:

1. HumanRequest is correlated and resolved by the stack’s owning components.
2. The owning component freezes the target agent session reference.
3. `agent-resume` is invoked with that frozen target and a follow-up prompt.
4. The watcher wakes later, resumes the same chat, and leaves the result in job state/logs.

This makes `agent-resume` the “resume delivery” layer, not the decision layer.

## Ownership boundaries

`agent-resume` owns only session wake/resume transport:

- it may wait, watch, and resume;
- it may persist its own job metadata;
- it may reconstruct the resume command for Codex, OpenCode, or Claude.

Notify owns user-facing delivery and attention routing:

- detect that a human response is needed;
- deliver the alert/request;
- track acknowledgement or resolution;
- do not resume coding-agent sessions.

SSS owns secret-safe access and secret resolution:

- manage secrets or secret retrieval;
- keep sensitive values out of `agent-resume` state;
- do not use `agent-resume` as a secret authority.

Agent Herder owns session lineage and orchestration:

- select or correlate the right agent session;
- freeze the session identity before handing off;
- hand the resolved target to `agent-resume` for the actual wake/resume step.

When Agent Herder and this checkout are siblings, configure its service with
absolute paths so its working directory cannot change which script is invoked:

```ini
[Service]
Environment=AGENT_RESUME_PYTHON=/usr/bin/python3
Environment=AGENT_RESUME_SCRIPT=/home/roomhacker/agents-projects/agent-resume/agent_resume.py
```

`AgentResumeClient` treats an `accepted` receipt as resumed. Subsequent calls
with the same `idempotency_key` query or replay the persisted receipt rather
than starting another Codex turn.

## Non-goals

- No Ask Secret authority.
- No Notify delivery logic.
- No secret storage or secret retrieval.
- No session-correlation policy engine.
- No change to README files.

## Summary

Use `agent-resume` only as the final transport that wakes and resumes a preselected agent session after HumanRequest resolution. Keep Notify, SSS, and Agent Herder responsible for their own domains, and do not blur those boundaries inside this component.
