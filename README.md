# agent-resume

MCP/CLI helper that waits for background work and resumes the same local coding-agent session when the work is done.

`agent-resume` is now the long-wait/control-plane tool. It can run a command, attach to an existing PID/query, or wait a fixed timer, then wake the same CLI coding agent and continue the task. `notify` is only an optional human Telegram ping.

## Supported agents

- Codex CLI: `codex exec resume <SESSION_ID> "prompt"` or `codex exec resume --last "prompt"`
- OpenCode: `opencode --session <SESSION_ID> --prompt "prompt"` or `opencode --continue --prompt "prompt"`
- Claude Code: supported as a fallback, but normally not installed because Claude can resume itself.


## Install into clients automatically

Run the installer to write ready-to-use MCP config entries for Codex and OpenCode:

```bash
npx -y github:megamen32/agent-resume --help
python3 scripts/install-client-configs.py codex opencode
```

The installer sets client identity once in each MCP config:

- Codex: `env = { "AGENT_RESUME_AGENT" = "codex" }` in `~/.codex/config.toml`
- OpenCode: `environment.AGENT_RESUME_AGENT = "opencode"` in `~/.config/opencode/opencode.jsonc`
- Claude Code is not installed by default; Claude can resume itself. Pass `claude` explicitly to the installer only if you want the fallback.

After that, tools can be called without passing `agent`.

> **Restart your MCP client after upgrading.** `agent-resume` is a Python
> script loaded once by the MCP relay at startup. It does not hot-reload.
> After upgrading the package, restart Codex / OpenCode / Claude so the new
> tool definitions and scan logic take effect.

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


## Long wait and automatic resume

`agent-resume` has built-in background waiting now. It does not need `notify` to watch long work.

MCP tools:

- `run_and_resume` — run a non-interactive command, wait for it to exit, then resume the same chat.
- `attach_pid_and_resume` — watch an existing PID and resume when it exits.
- `attach_query_and_resume` — find a process by command substring, watch it, then resume.
- `wait_and_resume` — wait a fixed duration, then resume.
- `wait_job_status` — inspect the background wait job state. `watcher_alive`
  reports whether the detached watcher is still armed; legacy `alive` and
  `watched_pid_alive` describe only a watched command/PID (and are false for a
  timer by design).

Default behavior is `execute_resume=true`: when the watched process/timer finishes, the watcher launches the appropriate resume command in the background. Set `execute_resume=false` only for tests.

For Codex, the current thread id is captured immediately from MCP `_meta.threadId` before the watcher detaches. For OpenCode, pass `cwd + marker` so the watcher can freeze the exact target session before it starts waiting.

Example:

```json
{
  "command": "npm test",
  "cwd": "/repo",
  "marker": "Q7xK2",
  "note": "test suite",
  "hard_timeout": "30m"
}
```

When the command exits, `agent-resume` resumes the same chat with job id, log file, status, and note.

## Resume identity and marker rules

`agent-resume` must not guess “the last session”. It resumes by an explicit current-session identity:

- **Codex**: Codex sends its thread/session id in MCP request `_meta.threadId`; `agent-resume` reads it and does not require a marker.
- **OpenCode**: OpenCode does not send session id in MCP tool arguments or `_meta`; `cwd` and `marker` are required.
- **Claude Code**: Claude Code does not expose a documented session id to MCP tool calls; `cwd` and `marker` are required.

For OpenCode/Claude, the marker is a plain required tool argument, not something `agent-resume` invents:

```text
marker = exactly 5 ASCII alphanumeric chars: [A-Za-z0-9]{5}
example: Q7xK2
```

The model should put the same marker in **any one** of these surfaces when starting the long task, then pass it to `agent-resume` later:

- the session **title** (`opencode run --title "...$MARKER..."`)
- the **cwd** directory name or a project subpath
- the user **prompt** itself (e.g. `"Marker: $MARKER — do the task"`)
- the model's own **assistant text** response — `agent-resume` scans both
  user and assistant text parts in opencode `part.data` rows, codex
  rollout JSONL assistant messages, and claude project transcripts.
  This is the most reliable surface because the marker survives even
  when the user prompt is paraphrased by compaction.

`agent-resume` adds **+100 score** at most once across all of these surfaces per session, so there is no benefit to placing the marker in multiple places. The MCP server records `called_at_ms` itself; the model does not need to know the time.

Opencode specifics:

- Compaction summaries (`part.type='compaction'`) and system-injected
  text parts (`synthetic=1`, e.g. skill triggers and JSON-format
  prompts) are **not** matched — they are filtered out before the
  `instr()` substring scan.
- `session_message` (the projection table opencode is migrating
  toward) is scanned in parallel with the legacy `part`+`message`
  JOIN so marker matching keeps working through the migration.
- If the opencode DB lacks all body-bearing tables, `agent-resume`
  writes a one-time warning to stderr and falls back to metadata-only
  matching.

Example for OpenCode/Claude-style clients:

```bash
MARKER=Q7xK2
opencode run --title "agent-resume-$MARKER" "Do the task. Marker: $MARKER"
AGENT_RESUME_AGENT=opencode ./agent_resume.py resume --cwd "$PWD" --marker "$MARKER" --job-id job-123 --log-file /tmp/job.log
```

For custom/local OpenCode builds, set `OPENCODE_DISABLE_CHANNEL_DB=true` if you want all sessions in the standard database:

```bash
export OPENCODE_DISABLE_CHANNEL_DB=true
# writes to ~/.local/share/opencode/opencode.db instead of opencode-<channel>.db
```

`use_last` is disabled because it can wake the wrong chat.

### Privacy opt-out: skip body scanning

By default `agent-resume` reads message bodies to find the marker.
If you prefer that it only match against metadata (session title /
directory / path), disable body scanning:

```bash
export AGENT_RESUME_SCAN_MESSAGE_BODIES=0
```

This applies to all three agents — opencode `part.data`, codex rollout
JSONL, and claude project transcripts. The env var is read once at
MCP server startup; restart your MCP client after changing it.


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
- `run_and_resume` — run command, wait, then resume.
- `attach_pid_and_resume` — watch PID, then resume.
- `attach_query_and_resume` — find process by query, watch, then resume.
- `wait_and_resume` — timer wait, then resume.
- `wait_job_status` — inspect wait job state.
- `register_agent` — let a client record its agent identity and optional session id.

## How it finds sessions

- Codex: reads `~/.codex/session_index.jsonl`.
- OpenCode: reads SQLite session tables from `~/.local/share/opencode/*.db`.
- Claude Code: scans `~/.claude/projects/<encoded-cwd>/*.jsonl` and falls back to all project transcript dirs.

## Current limitation

`agent-resume` resumes CLI sessions non-interactively by launching the client resume command. It does not inject keystrokes into an already-open TUI. For true interactive prompts/TUIs, use a terminal-specific tool manually.

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
/home/roomhacker/agents-projects/apps/forks/opencode
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

## Tests

Basic syntax/package checks are cheap:

```bash
python3 -m py_compile agent_resume.py scripts/install-client-configs.py scripts/test-codex-paid-smoke.py
node --check npm/agent-resume-mcp.js
npm pack --dry-run
```

The real Codex MCP `_meta.threadId` smoke test is **paid**, so it is skipped by default:

```bash
npm run test:codex-paid
# SKIP: paid Codex smoke test disabled. Set AGENT_RESUME_RUN_PAID_CODEX=1 to run.
```

Run it explicitly when needed:

```bash
AGENT_RESUME_RUN_PAID_CODEX=1 AGENT_RESUME_CODEX_MODEL=gpt-5.4-mini npm run test:codex-paid
```

It asserts that Codex calls `agent_resume.build_resume_command` without `cwd` and without `marker`, that `session_id_source == "mcp_meta"`, `marker == null`, `used_last == false`, and that the full argv array begins with `codex exec resume --model <persisted-model> <thread-id>`.
