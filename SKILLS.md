# Agent Resume skills

## `codex-resume` vs `long-task-retrospective`

They solve different stages of the same workflow.

| Skill/tool | Use it when | Result |
|---|---|---|
| `agent-resume` / `codex-resume` | Work is still running or waiting on a process, timer, build, test, deployment, or MCP job | Stops wasteful polling and resumes the same Codex session when the job finishes or fails |
| `long-task-retrospective` | The task has already completed or failed and took unusually long | Reconstructs the timeline, identifies preventable delays, and persists at most three evidence-backed improvements |

Do not replace the resume workflow with the retrospective. The intended sequence is:

```text
long job starts
→ agent-resume watches durably
→ same session resumes on completion/failure
→ long-task-retrospective runs once when warranted
```

## Install globally

The same command works on macOS and Linux:

```bash
npx -y -p github:megamen32/agent-resume agent-resume-install-retrospective
```

Default target:

```text
~/.agents/skills/long-task-retrospective/SKILL.md
```

Preview without changing files:

```bash
npx -y -p github:megamen32/agent-resume \
  agent-resume-install-retrospective --dry-run
```

Custom target root:

```bash
npx -y -p github:megamen32/agent-resume \
  agent-resume-install-retrospective --target-root ~/.agents/skills
```

The installer is idempotent. If a different copy already exists, it creates a timestamped backup before replacement.

## Invoke in Codex

```text
$long-task-retrospective
```

Use it after a completed or failed task when at least one condition applies:

- elapsed time exceeded roughly 45 minutes;
- repeated materially identical attempts occurred;
- unchanged polling or agent coordination became excessive;
- tool or token usage was unusually high;
- the task succeeded, but the execution path was clearly inefficient.

The skill intentionally limits itself to one bounded pass, no product-code edits, and at most three durable rules.
