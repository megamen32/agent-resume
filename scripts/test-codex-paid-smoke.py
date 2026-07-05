#!/usr/bin/env python3
"""Paid Codex smoke test for agent-resume MCP _meta handling.

Skipped by default because it spends paid model tokens. Enable with:

  AGENT_RESUME_RUN_PAID_CODEX=1 scripts/test-codex-paid-smoke.py

Optional env:
  AGENT_RESUME_CODEX_MODEL=gpt-5.4-mini
  AGENT_RESUME_CODEX_TIMEOUT=240
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
DEFAULT_MODEL = "gpt-5.4-mini"


def fail(message: str, *, stdout: str = "", stderr: str = "") -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    if stdout:
        print("\n== codex stdout tail ==", file=sys.stderr)
        print("\n".join(stdout.splitlines()[-80:]), file=sys.stderr)
    if stderr:
        print("\n== codex stderr ==", file=sys.stderr)
        print(stderr[-8000:], file=sys.stderr)
    raise SystemExit(1)


def json_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def extract_structured_result(item: dict[str, Any]) -> dict[str, Any] | None:
    result = item.get("result") or {}
    if not isinstance(result, dict):
        return None
    # Codex JSON stream uses structured_content; MCP canonical payload uses structuredContent.
    for key in ("structuredContent", "structured_content"):
        value = result.get(key)
        if isinstance(value, dict):
            return value
    # Some versions wrap MCP result under result.Ok.
    ok = result.get("Ok")
    if isinstance(ok, dict):
        for key in ("structuredContent", "structured_content"):
            value = ok.get(key)
            if isinstance(value, dict):
                return value
    return None


def main() -> int:
    if os.environ.get("AGENT_RESUME_RUN_PAID_CODEX", "").lower() not in TRUE_VALUES:
        print("SKIP: paid Codex smoke test disabled. Set AGENT_RESUME_RUN_PAID_CODEX=1 to run.")
        return 0

    codex = shutil.which("codex")
    if not codex:
        fail("codex binary not found in PATH")

    model = os.environ.get("AGENT_RESUME_CODEX_MODEL", DEFAULT_MODEL)
    timeout_s = int(os.environ.get("AGENT_RESUME_CODEX_TIMEOUT", "240"))
    tmp = Path(tempfile.mkdtemp(prefix="agent-resume-codex-paid-"))
    last_message = tmp / "last.txt"

    prompt = (
        "Paid smoke test for the MCP server named agent_resume. "
        "Call the agent_resume build_resume_command tool exactly once with arguments "
        "{\"job_id\":\"codex-meta-paid-smoke\","
        "\"log_file\":\"/tmp/codex-meta-paid-smoke.log\","
        "\"note\":\"codex paid MCP _meta smoke\"}. "
        "Do not pass cwd. Do not pass marker. "
        "After the tool result, reply only with compact JSON containing: "
        "ok, session_id, session_id_source, marker, used_last, command."
    )

    cmd = [
        codex,
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-C",
        str(tmp),
        "-m",
        model,
        "-o",
        str(last_message),
        prompt,
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_s)
    if proc.returncode != 0:
        fail(f"codex exited with rc={proc.returncode}", stdout=proc.stdout, stderr=proc.stderr)

    rows = json_lines(proc.stdout)
    thread_id = None
    for row in rows:
        if row.get("type") == "thread.started":
            thread_id = row.get("thread_id")
            break
    if not isinstance(thread_id, str) or not thread_id:
        fail("thread.started/thread_id not found", stdout=proc.stdout, stderr=proc.stderr)

    mcp_calls: list[dict[str, Any]] = []
    for row in rows:
        item = row.get("item")
        if isinstance(item, dict) and item.get("type") == "mcp_tool_call":
            if item.get("server") == "agent_resume" and item.get("tool") == "build_resume_command":
                mcp_calls.append(item)

    completed = [x for x in mcp_calls if x.get("status") == "completed"]
    if len(completed) != 1:
        fail(f"expected exactly one completed agent_resume/build_resume_command call, got {len(completed)}", stdout=proc.stdout, stderr=proc.stderr)

    call = completed[0]
    args = call.get("arguments") or {}
    if "cwd" in args or "marker" in args:
        fail(f"Codex smoke call must not pass cwd/marker, got arguments={args!r}", stdout=proc.stdout, stderr=proc.stderr)

    structured = extract_structured_result(call)
    if not structured:
        fail("MCP structured result not found", stdout=proc.stdout, stderr=proc.stderr)

    command = structured.get("command")
    checks = {
        "ok": structured.get("ok") is True,
        "session_id_matches_thread": structured.get("session_id") == thread_id,
        "session_id_source_mcp_meta": structured.get("session_id_source") == "mcp_meta",
        "marker_is_null": structured.get("marker") is None,
        "used_last_false": structured.get("used_last") is False,
        "command_is_full_argv": isinstance(command, list) and len(command) >= 5,
        "command_prefix": isinstance(command, list) and command[:4] == ["codex", "exec", "resume", thread_id],
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        fail(f"failed checks: {failed}; structured={json.dumps(structured, ensure_ascii=False)}", stdout=proc.stdout, stderr=proc.stderr)

    summary = {
        "ok": True,
        "model": model,
        "thread_id": thread_id,
        "session_id_source": structured.get("session_id_source"),
        "marker": structured.get("marker"),
        "used_last": structured.get("used_last"),
        "command_prefix": command[:4],
        "command_len": len(command),
        "tmp": str(tmp),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
