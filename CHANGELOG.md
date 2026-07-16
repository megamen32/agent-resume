# Changelog

## [0.1.8] - 2026-07-16

Release tag: `v0.1.8`

### Changes

- Merge the main MCP line with the parallel development line.
- Preserve the intermediate delayed-resume and AgentHerder fixes.
- Match OpenCode resume markers across user and assistant text parts.
- Fix the missing `uuid` import used by `job_paths()`.
- Include the delayed Codex resume skill and `wait_job_status` regression test.
- Keep local OpenCode fork references aligned with the canonical workspace path.

### Validation environment

- OS: Linux x86_64
- Python: 3.10.12
- Node.js: 22.22.3
- npm: 10.9.8
- pytest: 8.4.2
- Git: 2.34.1

### Validation commands

- `python3 -m py_compile agent_resume.py scripts/install-client-configs.py scripts/test-codex-paid-smoke.py tests/test_wait_job_status.py` — passed
- `node --check npm/agent-resume-mcp.js` — passed
- `python3 -m pytest -q tests` — `1 passed`
- `npm pack --dry-run` — passed; package `agent-resume-mcp@0.1.8`
- `git diff --check` — passed
