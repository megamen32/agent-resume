# AgentResume transport contract

## What the tool does

`agent_resume.wait_and_resume` freezes the current Codex session target, writes
a job below `~/.local/state/agent-resume/jobs/<job-id>/`, and starts a detached
watcher. When the timer expires, the watcher launches:

```text
codex exec resume --model <frozen-model> <frozen-session-id> <note>
```

The resumed process belongs to the original session. AgentResume freezes the
model persisted for that session at scheduling time and passes it explicitly,
so a later CLI-default change cannot silently select a different model.

## Evidence to inspect

For a completed one-shot job, inspect:

- `meta.json` for the target, requested delay, `state: "finished"`, and
  `resume_result` launch metadata, including the frozen `model`;
- the `resume_result.log_file` path (normally below
  `~/.local/state/agent-resume/runs/`) for the resumed Codex output;
- the initial and resumed Codex outputs for the intended response.

`launch_returncode: 0` proves only that the detached `codex exec resume`
process was started. It does not carry the eventual Codex exit status. Wait for
the expected output in `resume_result.log_file`, and, where available, confirm
the recorded resume PID exited.

Use the `wait_job_status` MCP tool first; read the state files only when status
or diagnosis needs more detail. Do not expose paths or contents that contain
credentials.

## Known non-goals

AgentResume is deliberately not a persistent scheduler. Its detached watcher
does not provide reboot recovery, leader election, overlap protection, or
recurring execution. Those requirements need a separately designed service,
with an idempotent workload and operational ownership.

AgentHerder is also not a substitute for a scheduler. As of the 2026-07-14
adapter fix, it can discover current Codex threads from `session_index.jsonl`
and rollout metadata and resume an identified thread with its persisted model.
It still has no timer, durable queue, reboot recovery, or overlap protection;
use it only for operator initiated observation or follow-up.
