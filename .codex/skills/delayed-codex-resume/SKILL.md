---
name: delayed-codex-resume
description: Schedule and verify a safe, one-shot delayed continuation of the same Codex session using AgentResume. Use when a task must pause for a known interval and then continue exactly once, especially for a small timer smoke test. Do not use for recurring maintenance, production orchestration, reboot recovery, or background autonomous work.
---

# Delayed Codex Resume

Use AgentResume for a one-time callback into the current Codex session. The
session itself schedules the wait, then its resumed instruction performs the
requested follow-up. This avoids inventing a scheduler, but it is intentionally
not a durable job system.

## Preconditions

- The request authorizes a one-shot callback and its cost.
- The dedicated session has the `agent_resume` MCP tool configured; otherwise it
  cannot schedule its own continuation.
- Choose the model when starting the session. `AgentResume` resumes that same
  session and cannot switch its model later.
- Probe a requested model with a tiny `codex exec --ephemeral -m <model>` call
  before relying on it. If unavailable, report the exact error; never silently
  substitute a different model.

Read [the transport contract](references/agent-resume-contract.md) before
changing this workflow or using it in an unfamiliar environment.

## One-shot workflow

1. Start a dedicated, non-ephemeral Codex session with the chosen model. Keep
   its task narrow and prohibit unrelated tools or edits.
2. In that session, call `agent_resume.wait_and_resume` with:

   ```json
   {
     "wait_seconds": 60,
     "note": "One-shot callback: reply exactly НАДО.",
     "execute_resume": true
   }
   ```

   The initial prompt must say that the continuation, after the callback, must
   reply with the requested exact text.
3. Record the returned job id. Do not create a cron job, systemd timer, or
   retry loop for this test.
4. Observe for at most the requested delay plus two minutes, then verify all of
   the following:

   - `agent_resume.wait_job_status` reports `state: "finished"`;
   - its `meta.json` records `resume_result.executed: true`, a zero
     `launch_returncode`, and `resume_result.log_file`;
   - `resume_result.log_file` contains the expected response. If a resume PID
     is recorded, it is no longer alive before declaring the test complete.
5. Report elapsed time, selected model, job id, and the exact evidence. A zero
   launch return code merely means the resumed Codex process started; it does
   not prove the response was produced. If the
   callback fails, preserve the log and diagnose before attempting another one.

## Safety limits

- A reboot, user logout, or host failure can lose the detached watcher. This is
  not a reboot-safe queue.
- Repeating the scheduling call creates another independent resume. Avoid
  duplicate calls and do not add automatic retries.
- Never attach this mechanism to production deploy, service restart, database
  migration, or user-visible traffic without a separately designed durable
  controller and an explicit approval.
- Check for an existing active job before scheduling a replacement.

## AgentHerder boundary

Use AgentHerder only as a best-effort observer of existing work. Its current
Codex adapter does not provide a reliable delayed resume: it launches a new
`codex --full-auto` process and reads legacy session files rather than invoking
`codex exec resume` for the original session. It must not be treated as this
skill's scheduler or recovery mechanism. A 2026-07-14 local smoke test found
active Codex processes but `listSessions()` returned zero because the adapter
scans `~/.codex/sessions/*.json`, while the installed Codex uses
`session_index.jsonl` and JSONL rollout data.

## Minimal model probe

Run this from a trusted repository, never from `/home` without `-C`:

```sh
codex exec --ephemeral --color never -m gpt-5.4-nano -C /path/to/repo \
  'Reply with exactly MODEL_OK. Do not use tools or edit files.'
```

Only start the delayed test when this returns `MODEL_OK`. Capture an unsupported
model error verbatim and ask whether to use a named alternative.
