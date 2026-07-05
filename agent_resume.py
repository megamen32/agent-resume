#!/usr/bin/env python3
"""agent-resume: find and resume local CLI coding-agent sessions.

MVP supports:
- Codex CLI: codex exec resume <session|--last> "prompt"
- OpenCode: opencode --session <id> --prompt "prompt"
- Claude Code: claude --print --resume <session|--continue> "prompt"

The MCP server is NDJSON stdio for simple MCP relays.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

HOME = Path.home()
STATE_DIR = Path(os.environ.get("AGENT_RESUME_STATE_DIR", HOME / ".local/state/agent-resume"))
SERVER_NAME = "agent-resume"
SERVER_VERSION = "0.1.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_agent(agent: str) -> str:
    a = (agent or "").strip().lower().replace("_", "-")
    aliases = {
        "codex-cli": "codex",
        "openai-codex": "codex",
        "opencode-ai": "opencode",
        "open-code": "opencode",
        "claude-code": "claude",
        "anthropic-claude": "claude",
    }
    a = aliases.get(a, a)
    if a not in {"codex", "opencode", "claude"}:
        raise ValueError("agent must be one of: codex, opencode, claude")
    return a


def safe_cwd(cwd: Optional[str]) -> Path:
    p = Path(cwd or os.getcwd()).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"cwd not found or not a directory: {p}")
    return p


@dataclass
class SessionCandidate:
    agent: str
    session_id: str
    cwd: Optional[str] = None
    title: Optional[str] = None
    updated: Optional[float] = None
    source: Optional[str] = None
    score: float = 0.0
    extra: Optional[Dict[str, Any]] = None


def codex_sessions(cwd: Optional[Path] = None, limit: int = 20, query: Optional[str] = None) -> List[SessionCandidate]:
    path = HOME / ".codex/session_index.jsonl"
    out: List[SessionCandidate] = []
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        sid = item.get("id")
        if not sid:
            continue
        title = item.get("thread_name") or ""
        updated = item.get("updated_at")
        score = 0.0
        if query and query.lower() in title.lower():
            score += 10
        try:
            ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = 0.0
        out.append(SessionCandidate("codex", sid, str(cwd) if cwd else None, title, ts, str(path), score, {"updated_at": updated}))
    out.sort(key=lambda x: (x.score, x.updated or 0), reverse=True)
    return out[:limit]


def opencode_sessions(cwd: Optional[Path] = None, limit: int = 20, query: Optional[str] = None) -> List[SessionCandidate]:
    dbs = [
        HOME / ".local/share/opencode/opencode.db",
    ]
    # include per-branch/test DBs, newest first
    share = HOME / ".local/share/opencode"
    if share.exists():
        dbs.extend(sorted(share.glob("opencode-*.db"), key=lambda p: p.stat().st_mtime, reverse=True))
    out: List[SessionCandidate] = []
    cwd_s = str(cwd) if cwd else None
    for db in dbs:
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "select id,title,directory,path,agent,model,time_updated,time_created,parent_id from session where time_archived is null order by time_updated desc limit 200"
            ).fetchall()
        except Exception:
            continue
        finally:
            try:
                con.close()
            except Exception:
                pass
        for r in rows:
            directory = r["directory"] or None
            pathval = r["path"] or None
            title = r["title"] or ""
            score = 0.0
            if cwd_s and directory:
                try:
                    d = str(Path(directory).expanduser().resolve())
                    if d == cwd_s:
                        score += 20
                    elif cwd_s.startswith(d.rstrip("/") + "/") or d.startswith(cwd_s.rstrip("/") + "/"):
                        score += 8
                except Exception:
                    pass
            if cwd_s and pathval and pathval.strip("/") in cwd_s.strip("/"):
                score += 5
            if query and query.lower() in title.lower():
                score += 10
            # prefer root sessions by default
            if not r["parent_id"]:
                score += 1
            out.append(
                SessionCandidate(
                    "opencode",
                    r["id"],
                    directory,
                    title,
                    (r["time_updated"] or 0) / 1000.0,
                    str(db),
                    score,
                    {"path": pathval, "agent": r["agent"], "model": r["model"], "parent_id": r["parent_id"]},
                )
            )
    out.sort(key=lambda x: (x.score, x.updated or 0), reverse=True)
    return out[:limit]


def claude_project_dir(cwd: Path) -> Path:
    # Claude project dirs use path with slashes converted to hyphen, e.g. /home/x/foo -> -home-x-foo
    return HOME / ".claude/projects" / str(cwd).replace("/", "-")


def claude_sessions(cwd: Optional[Path] = None, limit: int = 20, query: Optional[str] = None) -> List[SessionCandidate]:
    roots: List[Path] = []
    if cwd:
        roots.append(claude_project_dir(cwd))
    all_root = HOME / ".claude/projects"
    if all_root.exists():
        roots.extend([p for p in all_root.iterdir() if p.is_dir() and p not in roots])
    out: List[SessionCandidate] = []
    for root in roots:
        for f in root.glob("*.jsonl"):
            sid = f.stem
            title = None
            try:
                # Read first/last few lines without loading huge files where possible.
                lines = f.read_text(errors="replace").splitlines()
                for line in lines[:20]:
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    msg = item.get("message") or {}
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        title = content.strip().splitlines()[0][:120]
                        break
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                                title = str(part["text"]).strip().splitlines()[0][:120]
                                break
                        if title:
                            break
            except Exception:
                pass
            score = 0.0
            if cwd and root == claude_project_dir(cwd):
                score += 20
            if query and title and query.lower() in title.lower():
                score += 10
            out.append(SessionCandidate("claude", sid, str(cwd) if cwd else None, title, f.stat().st_mtime, str(f), score))
    out.sort(key=lambda x: (x.score, x.updated or 0), reverse=True)
    return out[:limit]


def find_sessions(agent: str, cwd: Optional[str] = None, query: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    a = normalize_agent(agent)
    p = safe_cwd(cwd) if cwd else None
    if a == "codex":
        rows = codex_sessions(p, limit, query)
    elif a == "opencode":
        rows = opencode_sessions(p, limit, query)
    else:
        rows = claude_sessions(p, limit, query)
    return [asdict(x) for x in rows]


def default_prompt(job_id: Optional[str], log_file: Optional[str], status: Optional[Dict[str, Any]], note: Optional[str]) -> str:
    rc = None
    state = None
    if status:
        rc = status.get("returncode")
        state = status.get("state")
    parts = ["Background job finished. Resume the previous task and continue from the result."]
    if note:
        parts.append(f"Note: {note}")
    if job_id:
        parts.append(f"Job id: {job_id}")
    if state:
        parts.append(f"State: {state}")
    if rc is not None:
        parts.append(f"Return code: {rc}")
    if log_file:
        parts.append(f"Log file: {log_file}")
        parts.append("Inspect the log/status as needed; do not repeat work already completed.")
    return "\n".join(parts)


def build_resume_command(agent: str, session_id: Optional[str], cwd: Optional[str], prompt: str, use_last: bool = False) -> List[str]:
    a = normalize_agent(agent)
    if a == "codex":
        if session_id:
            return ["codex", "exec", "resume", session_id, prompt]
        if use_last:
            return ["codex", "exec", "resume", "--last", prompt]
        raise ValueError("codex resume requires session_id or use_last=true")
    if a == "opencode":
        if session_id:
            return ["opencode", "--session", session_id, "--prompt", prompt]
        if use_last:
            return ["opencode", "--continue", "--prompt", prompt]
        raise ValueError("opencode resume requires session_id or use_last=true")
    if a == "claude":
        if session_id:
            return ["claude", "--print", "--resume", session_id, prompt]
        if use_last:
            return ["claude", "--print", "--continue", prompt]
        raise ValueError("claude resume requires session_id or use_last=true")
    raise AssertionError(a)


def resume_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    agent = normalize_agent(str(args.get("agent") or ""))
    cwd = str(safe_cwd(args.get("cwd")))
    session_id = args.get("session_id") or args.get("session")
    query = args.get("query") or args.get("title_query")
    candidates: List[Dict[str, Any]] = []
    if not session_id and not bool(args.get("use_last", False)):
        candidates = find_sessions(agent, cwd, query=query, limit=5)
        if candidates:
            session_id = candidates[0]["session_id"]
    prompt = args.get("prompt") or default_prompt(args.get("job_id"), args.get("log_file"), args.get("status"), args.get("note"))
    use_last = bool(args.get("use_last", False)) or not session_id
    cmd = build_resume_command(agent, str(session_id) if session_id else None, cwd, str(prompt), use_last=use_last)
    result: Dict[str, Any] = {
        "ok": True,
        "agent": agent,
        "cwd": cwd,
        "session_id": session_id,
        "used_last": use_last,
        "candidates": candidates,
        "command": cmd,
        "shell_command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
    }
    if args.get("execute"):
        log_dir = STATE_DIR / "runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{agent}"
        log = log_dir / f"{run_id}.log"
        # detached, because this is intended to wake an agent without blocking the watcher.
        shell = f"setsid {' '.join(shlex.quote(x) for x in cmd)} > {shlex.quote(str(log))} 2>&1 < /dev/null & echo $!"
        p = subprocess.run(["/bin/bash", "-lc", shell], cwd=cwd, text=True, capture_output=True, timeout=10)
        result.update({"executed": True, "launch_returncode": p.returncode, "launch_stdout": p.stdout, "launch_stderr": p.stderr, "log_file": str(log)})
        try:
            result["pid"] = int(p.stdout.strip().splitlines()[-1])
        except Exception:
            result["pid"] = None
        result["ok"] = p.returncode == 0
    return result


def register(args: Dict[str, Any]) -> Dict[str, Any]:
    agent = normalize_agent(str(args.get("agent") or ""))
    cwd = str(safe_cwd(args.get("cwd")))
    session_id = args.get("session_id") or args.get("session")
    label = args.get("label") or args.get("name") or "default"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"agent": agent, "cwd": cwd, "session_id": session_id, "label": label, "updated_at": now_iso(), "metadata": args.get("metadata") or {}}
    path = STATE_DIR / "registry.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "record": rec, "registry": str(path)}


TOOLS = {
    "find_sessions": {
        "description": "Find likely local CLI coding-agent sessions for agent=codex|opencode|claude. Use cwd/title_query to pick the right chat before resuming.",
        "inputSchema": {"type":"object","properties":{"agent":{"type":"string","enum":["codex","opencode","claude"]},"cwd":{"type":["string","null"],"description":"Project directory to scope session search."},"query":{"type":["string","null"],"description":"Optional title/text query."},"limit":{"type":["integer","null"],"default":20}},"required":["agent"],"additionalProperties":False},
        "handler": lambda a: {"sessions": find_sessions(str(a.get("agent")), a.get("cwd"), a.get("query"), int(a.get("limit") or 20))},
    },
    "build_resume_command": {
        "description": "Build the command that would resume an agent session. Dry-run by default; returns shell_command. For Codex uses codex exec resume, for OpenCode opencode --session/--continue, for Claude claude --print --resume/--continue.",
        "inputSchema": {"type":"object","properties":{"agent":{"type":"string","enum":["codex","opencode","claude"]},"cwd":{"type":["string","null"]},"session_id":{"type":["string","null"]},"use_last":{"type":"boolean","default":False},"prompt":{"type":["string","null"]},"job_id":{"type":["string","null"]},"log_file":{"type":["string","null"]},"note":{"type":["string","null"]},"query":{"type":["string","null"]},"execute":{"type":"boolean","default":False,"description":"If true, start the resume command detached. Default false for safety."}},"required":["agent"],"additionalProperties":True},
        "handler": resume_agent,
    },
    "register_agent": {
        "description": "Record that the current AI client identifies as agent=codex|opencode|claude with optional session_id/cwd. This helps later wake/resume logic.",
        "inputSchema": {"type":"object","properties":{"agent":{"type":"string","enum":["codex","opencode","claude"]},"cwd":{"type":["string","null"]},"session_id":{"type":["string","null"]},"label":{"type":["string","null"]},"metadata":{"type":["object","null"]}},"required":["agent"],"additionalProperties":True},
        "handler": register,
    },
}


def reply(req_id: Any, result: Any = None, error: Exception | None = None) -> None:
    if req_id is None:
        return
    if error:
        payload = {"jsonrpc":"2.0","id":req_id,"error":{"code":-32000,"message":str(error),"data":error.__class__.__name__}}
    else:
        payload = {"jsonrpc":"2.0","id":req_id,"result":result}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def handle(req: Dict[str, Any]) -> None:
    method = req.get("method")
    req_id = req.get("id")
    try:
        if method == "initialize":
            reply(req_id, {"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":SERVER_NAME,"version":SERVER_VERSION}})
        elif method == "tools/list":
            reply(req_id, {"tools":[{"name":n,"description":t["description"],"inputSchema":t["inputSchema"]} for n,t in TOOLS.items()]})
        elif method == "tools/call":
            params=req.get("params") or {}; name=params.get("name"); args=params.get("arguments") or {}
            if name not in TOOLS: raise ValueError(f"unknown tool: {name}")
            result=TOOLS[name]["handler"](args)
            reply(req_id, {"content":[{"type":"text","text":json.dumps(result, ensure_ascii=False, indent=2)}],"structuredContent":result})
        elif method and method.startswith("notifications/"):
            return
        else:
            reply(req_id, {})
    except Exception as e:
        print(f"agent-resume error: {e}", file=sys.stderr, flush=True)
        reply(req_id, error=e)


def mcp_main() -> None:
    for line in sys.stdin:
        line=line.strip()
        if not line: continue
        try: handle(json.loads(line))
        except Exception as e: print(f"bad request: {e}", file=sys.stderr, flush=True)


def cli_main() -> None:
    ap=argparse.ArgumentParser(description="Find and resume local CLI coding-agent sessions")
    sub=ap.add_subparsers(dest="cmd", required=True)
    f=sub.add_parser("find")
    f.add_argument("--agent", required=True, choices=["codex","opencode","claude"]); f.add_argument("--cwd"); f.add_argument("--query"); f.add_argument("--limit", type=int, default=20)
    r=sub.add_parser("resume")
    r.add_argument("--agent", required=True, choices=["codex","opencode","claude"]); r.add_argument("--cwd"); r.add_argument("--session-id"); r.add_argument("--use-last", action="store_true"); r.add_argument("--prompt"); r.add_argument("--job-id"); r.add_argument("--log-file"); r.add_argument("--note"); r.add_argument("--query"); r.add_argument("--execute", action="store_true")
    sub.add_parser("mcp")
    args=ap.parse_args()
    if args.cmd == "mcp": mcp_main(); return
    if args.cmd == "find": print(json.dumps({"sessions": find_sessions(args.agent, args.cwd, args.query, args.limit)}, ensure_ascii=False, indent=2)); return
    if args.cmd == "resume": print(json.dumps(resume_agent(vars(args)), ensure_ascii=False, indent=2)); return

if __name__ == "__main__":
    cli_main()
