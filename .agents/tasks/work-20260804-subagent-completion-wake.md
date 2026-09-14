# Parent does not resume when subagents complete

Role: Explorer

## Symptom

`wait_and_resume` can wait on the parent Codex process/PIT while that parent
remains alive. Its subagents may all finish, but the parent does not resume to
integrate their results because no completion event targets that specific parent
task.

## Smallest evidence

Current run: Agent Resume watcher is attached to the parent Codex session;
subagents have completed independently while the parent PIT remains alive.
Observed worker/reviewer completions need a direct parent wake, not parent-PID
exit.

## Hypothesis, not yet confirmed

The missing owner is a subagent-completion hook/event that correlates one child
completion to its parent Codex session and resumes that parent exactly once.

## Blocker

Wait for the remaining child and observe whether the parent auto-resumes. Then
trace the multi-agent completion transport and Agent Resume ownership before
selecting a hook/API design.

## Assignment

Read only the Agent Resume source and directly relevant local multi-agent/
Codex integration seams. Trace how a child completion is emitted, whether it
contains parent session/thread correlation, and where Agent Resume could
register an exactly-once parent wake. Do not edit, test, restart, deploy, or
inspect unrelated projects. Append exact evidence and the smallest fix scope to
this task file; return only TL;DR to L.

Allowed path: `/home/roomhacker/agents-projects/agent-resume`.

## Explorer evidence

- Agent Resume owns only timer/PID watchers and resumes a frozen Codex
  `_meta.threadId`; it has no child-completion input or parent-child lifecycle
  store.
- Agent Herder's resume transport forwards an already selected target but does
  not own Codex parent/child topology.
- The local Codex DB has `thread_spawn_edges(parent_thread_id, child_thread_id,
  status)`, but no completion payload, wake target, or exactly-once claim.
- Therefore this is not an Agent Resume source fix: the correct owner is the
  Codex multi-agent completion producer/coordinator, which must correlate a
  completed child to its parent and call Agent Resume/Herder once.

## Status

Complete — source owner boundary established; follow-up recorded separately.
