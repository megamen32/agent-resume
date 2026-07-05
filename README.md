# agent-resume

MCP/CLI helper that finds and resumes local coding-agent sessions after a background job finishes.

It is meant to be paired with long-running job watchers such as `notify-mcp`: instead of only notifying a human, a watcher can ask `agent-resume` to wake the same CLI coding agent and continue the task.

## Supported agents

- Codex CLI: `codex exec resume <SESSION_ID> "prompt"` or `codex exec resume --last "prompt"`
- OpenCode: `opencode --session <SESSION_ID> --prompt "prompt"` or `opencode --continue --prompt "prompt"`
- Claude Code: `claude --print --resume <SESSION_ID> "prompt"` or `claude --print --continue "prompt"`

## Safety

`build_resume_command` is dry-run by default. It returns the command it would run. Set `execute=true` only when you really want to start the resumed agent in the background.

## CLI

```bash
./agent_resume.py find --agent codex --cwd "$PWD"
./agent_resume.py find --agent opencode --cwd "$PWD"
./agent_resume.py find --agent claude --cwd "$PWD"

./agent_resume.py resume --agent codex --cwd "$PWD" --query "deploy" --log-file /tmp/job.log
./agent_resume.py resume --agent codex --cwd "$PWD" --session-id 019f... --prompt "Job finished; inspect log and continue"
```

## MCP

```bash
python3 /path/to/agent_resume.py mcp
```

Tools:

- `find_sessions` — list likely sessions for `agent=codex|opencode|claude`.
- `build_resume_command` — choose a session and build or execute the resume command.
- `register_agent` — let a client record its agent identity and optional session id.

## How it finds sessions

- Codex: reads `~/.codex/session_index.jsonl`.
- OpenCode: reads SQLite session tables from `~/.local/share/opencode/*.db`.
- Claude Code: scans `~/.claude/projects/<encoded-cwd>/*.jsonl` and falls back to all project transcript dirs.

## Current limitation

This first version resumes CLI sessions non-interactively. It does not inject text into an already-open terminal UI. For interactive/TUI reattachment, pair this with `pty-mcp` or tmux in a later version.
