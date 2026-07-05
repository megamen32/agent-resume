# agent-resume

MCP/CLI helper that finds and resumes local coding-agent sessions after a background job finishes.

It is meant to be paired with long-running job watchers such as `notify-mcp`: instead of only notifying a human, a watcher can ask `agent-resume` to wake the same CLI coding agent and continue the task.

## Supported agents

- Codex CLI: `codex exec resume <SESSION_ID> "prompt"` or `codex exec resume --last "prompt"`
- OpenCode: `opencode --session <SESSION_ID> --prompt "prompt"` or `opencode --continue --prompt "prompt"`
- Claude Code: `claude --print --resume <SESSION_ID> "prompt"` or `claude --print --continue "prompt"`

## Agent identity

Do **not** make the model pass `agent=codex|opencode|claude` on every tool call. Configure identity once in the MCP client config:

### Codex

```toml
[mcp_servers.agent_resume]
command = "npx"
args = ["-y", "github:megamen32/agent-resume"]
env = { AGENT_RESUME_AGENT = "codex" }
```

### OpenCode

```jsonc
{
  "mcp": {
    "agent-resume": {
      "type": "local",
      "command": ["npx", "-y", "github:megamen32/agent-resume"],
      "enabled": true,
      "environment": { "AGENT_RESUME_AGENT": "opencode" }
    }
  }
}
```

### Claude Code

```json
{
  "mcpServers": {
    "agent-resume": {
      "command": "npx",
      "args": ["-y", "github:megamen32/agent-resume"],
      "env": { "AGENT_RESUME_AGENT": "claude" }
    }
  }
}
```

A local fallback also works:

```json
// ~/.config/agent-resume/config.json
{ "agent": "codex" }
```

## Where SESSION_ID comes from

MCP does not have a universal “current chat id” field. `agent-resume` derives it from each client’s local state:

- **Codex:** `~/.codex/state_5.sqlite`, table `threads`, preferred because it includes `id`, `cwd`, `title`, `rollout_path`, model and git metadata. Fallback: `~/.codex/session_index.jsonl`.
- **OpenCode:** `~/.local/share/opencode/*.db`, table `session`, including `id`, `directory`, `title`, `agent`, `model`, timestamps.
- **Claude Code:** `~/.claude/projects/<encoded-cwd>/*.jsonl`, where file stem is the session id.

The strongest match is an explicit `session_id`; next best is `cwd + query`; fallback is “latest for this configured agent”.

## Safety

`build_resume_command` is dry-run by default. It returns the command it would run. Set `execute=true` only when you really want to start the resumed agent in the background.

## CLI

```bash
AGENT_RESUME_AGENT=codex ./agent_resume.py find --cwd "$PWD"
AGENT_RESUME_AGENT=opencode ./agent_resume.py find --cwd "$PWD"
AGENT_RESUME_AGENT=claude ./agent_resume.py find --cwd "$PWD"

AGENT_RESUME_AGENT=codex ./agent_resume.py resume --cwd "$PWD" --query "deploy" --log-file /tmp/job.log
AGENT_RESUME_AGENT=codex ./agent_resume.py resume --cwd "$PWD" --session-id 019f... --prompt "Job finished; inspect log and continue"
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

## Source findings

### Codex

Codex source was checked from `https://github.com/openai/codex`.

Important files:

- `codex-rs/exec/src/cli.rs`
  - `resume` accepts `SESSION_ID`, `--last`, `--all`, images, and prompt.
  - `SESSION_ID` is documented as conversation/session UUID or thread name; UUID wins.
- `codex-rs/rollout/src/list.rs`
  - `find_thread_path_by_id_str()` locates rollout files by UUID.
  - It first asks the state DB for `rollout_path`, verifies the file belongs to the same thread id, then falls back to scanning rollout filenames.
- `codex-rs/rollout/src/session_index.rs`
  - `session_index.jsonl` is a thread-name index and fallback helper, not the strongest source for current project matching.

Best local SESSION_ID source for Codex is therefore:

```text
~/.codex/state_5.sqlite
  table: threads
  id              -> SESSION_ID
  cwd             -> project match
  rollout_path    -> backing transcript file
  updated_at_ms   -> recency
```

Fallback:

```text
~/.codex/session_index.jsonl
```

### OpenCode

OpenCode source was checked from local fork:

```text
/home/roomhacker/.config/opencode/apps/forks/opencode
```

Important files:

- `packages/opencode/src/cli/cmd/run.ts`
  - `--continue` / `-c` continues the last root session.
  - `--session` / `-s` continues a specific session id.
  - `--fork` forks before continuing.
  - `--prompt` sends a message to the continued session.
- `packages/opencode/src/cli/cmd/session.ts`
  - `opencode session list --format json` exposes `id`, `title`, `updated`, `created`, `projectId`, `directory`.

Best local SESSION_ID source for OpenCode:

```text
~/.local/share/opencode/*.db
  table: session
  id          -> SESSION_ID
  directory   -> project match
  title       -> query match
  time_updated -> recency
```

### Claude Code

Claude Code is a binary here, but local state and CLI help show:

```text
claude --print --resume <SESSION_ID> "prompt"
claude --print --continue "prompt"
```

Best local SESSION_ID source:

```text
~/.claude/projects/<encoded-cwd>/*.jsonl
  filename stem -> SESSION_ID
```
