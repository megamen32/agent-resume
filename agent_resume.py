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
import re
import uuid
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
CONFIG_PATH = Path(os.environ.get("AGENT_RESUME_CONFIG", HOME / ".config/agent-resume/config.json"))
SERVER_NAME = "agent-resume"
SERVER_VERSION = "0.1.3"


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


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def default_agent() -> Optional[str]:
    for key in ("AGENT_RESUME_AGENT", "MCP_AGENT", "AI_AGENT", "AGENT"):
        val = os.environ.get(key)
        if val:
            return normalize_agent(val)
    cfg = load_config()
    val = cfg.get("agent") or cfg.get("default_agent")
    if val:
        return normalize_agent(str(val))
    return None


def resolve_agent(agent: Optional[str]) -> str:
    if agent:
        return normalize_agent(agent)
    resolved = default_agent()
    if resolved:
        return resolved
    raise ValueError(
        "agent is not configured. Set AGENT_RESUME_AGENT=codex|opencode|claude in this MCP server config, "
        "or pass agent explicitly, or write ~/.config/agent-resume/config.json with {\"agent\":\"codex\"}."
    )


RUN_ID_RE = re.compile(r"^[0-9]{13}$")


def validate_run_id(run_id: Any, *, required: bool = False) -> Optional[str]:
    """Validate correlation id used to avoid fuzzy session matching.

    Accepted forms:
    - 13-digit epoch milliseconds, e.g. 1783217856841
    - UUID4, canonical or parseable by uuid.UUID
    """
    if run_id is None or str(run_id).strip() == "":
        if required:
            raise ValueError("run_id is required unless session_id is explicit or use_last=true. Pass 13-digit epoch milliseconds or a UUID4.")
        return None
    value = str(run_id).strip()
    if RUN_ID_RE.fullmatch(value):
        return value
    try:
        u = uuid.UUID(value)
    except Exception:
        raise ValueError("run_id must be 13-digit epoch milliseconds or UUID4")
    if u.version != 4:
        raise ValueError("run_id UUID must be version 4")
    return str(u)


def text_has(needle: Optional[str], *values: Any) -> bool:
    if not needle:
        return False
    n = str(needle).lower()
    return n in "\n".join(str(v or "") for v in values).lower()


def match_score(query: Optional[str], run_id: Optional[str], *values: Any) -> float:
    score = 0.0
    if run_id and text_has(run_id, *values):
        score += 100.0
    if query and text_has(query, *values):
        score += 10.0
    return score


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


def parse_time(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        # Normalize epoch seconds / milliseconds / microseconds / nanoseconds.
        v = float(value)
        if v > 1_000_000_000_000_000_00:  # nanoseconds
            return v / 1_000_000_000.0
        if v > 1_000_000_000_000_00:  # microseconds
            return v / 1_000_000.0
        if v > 10_000_000_000:  # milliseconds
            return v / 1000.0
        return v
    text = str(value).strip()
    if not text:
        return 0.0
    if text.isdigit():
        return parse_time(int(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python 3.10 only accepts microseconds; Codex timestamps can have nanoseconds.
    if "." in text:
        head, tail = text.split(".", 1)
        tz = ""
        for sep in ("+", "-"):
            if sep in tail:
                frac, rest = tail.split(sep, 1)
                tz = sep + rest
                break
        else:
            frac = tail
        text = head + "." + frac[:6].ljust(6, "0") + tz
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


def codex_sessions(cwd: Optional[Path] = None, limit: int = 20, query: Optional[str] = None, run_id: Optional[str] = None) -> List[SessionCandidate]:
    """Find Codex sessions.

    Preferred source is ~/.codex/state_5.sqlite: threads table has id, cwd,
    title/preview, rollout_path, model, git metadata, updated_at_ms.
    Fallback is ~/.codex/session_index.jsonl, which has id/title/time but not cwd.
    """
    out: List[SessionCandidate] = []
    cwd_s = str(cwd) if cwd else None
    db = HOME / ".codex/state_5.sqlite"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "select id,cwd,title,preview,first_user_message,rollout_path,updated_at_ms,updated_at,created_at_ms,model,source,thread_source,agent_nickname,agent_role,git_origin_url,git_branch from threads where archived=0 order by updated_at_ms desc limit 500"
            ).fetchall()
            con.close()
            for r in rows:
                rcwd = r["cwd"] or None
                title = r["title"] or r["preview"] or r["first_user_message"] or ""
                score = 0.0
                if cwd_s and rcwd:
                    try:
                        d = str(Path(rcwd).expanduser().resolve())
                        if d == cwd_s:
                            score += 30
                        elif cwd_s.startswith(d.rstrip("/") + "/") or d.startswith(cwd_s.rstrip("/") + "/"):
                            score += 12
                    except Exception:
                        pass
                score += match_score(query, run_id, title, rcwd, r["rollout_path"], r["git_branch"])
                if not r["agent_nickname"]:
                    score += 1
                out.append(SessionCandidate(
                    "codex", r["id"], rcwd, title[:300], parse_time(r["updated_at_ms"] or r["updated_at"]), str(db), score,
                    {"rollout_path": r["rollout_path"], "model": r["model"], "source": r["source"], "thread_source": r["thread_source"], "agent_nickname": r["agent_nickname"], "agent_role": r["agent_role"], "git_origin_url": r["git_origin_url"], "git_branch": r["git_branch"]}
                ))
        except Exception:
            pass
    if not out:
        path = HOME / ".codex/session_index.jsonl"
        if path.exists():
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
                score += match_score(query, run_id, title, str(cwd) if cwd else None)
                ts = parse_time(updated)
                out.append(SessionCandidate("codex", sid, str(cwd) if cwd else None, title, ts, str(path), score, {"updated_at": updated}))
    out.sort(key=lambda x: (x.score, x.updated or 0), reverse=True)
    return out[:limit]


def opencode_sessions(cwd: Optional[Path] = None, limit: int = 20, query: Optional[str] = None, run_id: Optional[str] = None) -> List[SessionCandidate]:
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
            score += match_score(query, run_id, title, directory, pathval)
            # prefer root sessions by default
            if not r["parent_id"]:
                score += 1
            out.append(
                SessionCandidate(
                    "opencode",
                    r["id"],
                    directory,
                    title,
                    parse_time(r["time_updated"]),
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


def claude_sessions(cwd: Optional[Path] = None, limit: int = 20, query: Optional[str] = None, run_id: Optional[str] = None) -> List[SessionCandidate]:
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
            score += match_score(query, run_id, title, path.name, str(f))
            out.append(SessionCandidate("claude", sid, str(cwd) if cwd else None, title, f.stat().st_mtime, str(f), score))
    out.sort(key=lambda x: (x.score, x.updated or 0), reverse=True)
    return out[:limit]


def find_sessions(agent: Optional[str] = None, cwd: Optional[str] = None, query: Optional[str] = None, limit: int = 20, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    a = resolve_agent(agent)
    rid = validate_run_id(run_id) if run_id else None
    p = safe_cwd(cwd) if cwd else None
    if a == "codex":
        rows = codex_sessions(p, limit, query, rid)
    elif a == "opencode":
        rows = opencode_sessions(p, limit, query, rid)
    else:
        rows = claude_sessions(p, limit, query, rid)
    return [asdict(x) for x in rows]


def default_prompt(job_id: Optional[str], log_file: Optional[str], status: Optional[Dict[str, Any]], note: Optional[str], run_id: Optional[str] = None) -> str:
    rc = None
    state = None
    if status:
        rc = status.get("returncode")
        state = status.get("state")
    parts = ["Background job finished. Resume the previous task and continue from the result."]
    if run_id:
        parts.append(f"Run id: {run_id}")
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


def build_resume_command(agent: Optional[str], session_id: Optional[str], cwd: Optional[str], prompt: str, use_last: bool = False) -> List[str]:
    a = resolve_agent(agent)
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
    agent = resolve_agent(args.get("agent"))
    cwd = str(safe_cwd(args.get("cwd")))
    session_id = args.get("session_id") or args.get("session")
    use_last_requested = bool(args.get("use_last", False))
    run_id = validate_run_id(args.get("run_id"), required=not session_id and not use_last_requested)
    query = args.get("query") or args.get("title_query")
    candidates: List[Dict[str, Any]] = []
    if not session_id and not use_last_requested:
        candidates = find_sessions(agent, cwd, query=query, limit=5, run_id=run_id)
        if candidates and (not run_id or float(candidates[0].get("score") or 0) >= 100.0):
            session_id = candidates[0]["session_id"]
    if not session_id and not use_last_requested:
        raise ValueError(f"no session matched run_id={run_id!r} query={query!r} cwd={cwd!r}; refusing to fall back to --last")
    prompt = args.get("prompt") or default_prompt(args.get("job_id"), args.get("log_file"), args.get("status"), args.get("note"), run_id=run_id)
    use_last = use_last_requested or not session_id
    cmd = build_resume_command(agent, str(session_id) if session_id else None, cwd, str(prompt), use_last=use_last)
    result: Dict[str, Any] = {
        "ok": True,
        "agent": agent,
        "cwd": cwd,
        "session_id": session_id,
        "run_id": run_id,
        "used_last": use_last,
        "candidates": candidates,
        "command": cmd,
        "shell_command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
    }
    if args.get("execute"):
        log_dir = STATE_DIR / "runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        launch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{agent}"
        log = log_dir / f"{launch_id}.log"
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
    agent = resolve_agent(args.get("agent"))
    cwd = str(safe_cwd(args.get("cwd")))
    session_id = args.get("session_id") or args.get("session")
    label = args.get("label") or args.get("name") or "default"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    run_id = validate_run_id(args.get("run_id")) if args.get("run_id") else None
    rec = {"agent": agent, "cwd": cwd, "session_id": session_id, "run_id": run_id, "label": label, "updated_at": now_iso(), "metadata": args.get("metadata") or {}}
    path = STATE_DIR / "registry.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "record": rec, "registry": str(path)}


TOOLS = {
    "get_config": {
        "description": "Show agent-resume effective config: default agent from env/config and state paths. Use this to verify the MCP server identity; normal calls do not need an agent argument when AGENT_RESUME_AGENT is set.",
        "inputSchema": {"type":"object","properties":{},"additionalProperties":False},
        "handler": lambda a: {"default_agent": default_agent(), "config_path": str(CONFIG_PATH), "config": load_config(), "env_agent": os.environ.get("AGENT_RESUME_AGENT"), "state_dir": str(STATE_DIR)},
    },
    "find_sessions": {
        "description": "Find likely local CLI coding-agent sessions. Agent is optional when AGENT_RESUME_AGENT=codex|opencode|claude is set in this MCP server config. Use cwd/query/run_id to pick the right chat before resuming.",
        "inputSchema": {"type":"object","properties":{"agent":{"type":"string","enum":["codex","opencode","claude"]},"cwd":{"type":["string","null"],"description":"Project directory to scope session search."},"query":{"type":["string","null"],"description":"Optional title/text query."},"run_id":{"type":["string","null"],"description":"Correlation marker: 13-digit epoch milliseconds or UUID4. Adds exact-match scoring."},"limit":{"type":["integer","null"],"default":20}},"required":[],"additionalProperties":False},
        "handler": lambda a: {"sessions": find_sessions(a.get("agent"), a.get("cwd"), a.get("query"), int(a.get("limit") or 20), a.get("run_id"))},
    },
    "build_resume_command": {
        "description": "Build the command that would resume an agent session. Agent is optional when configured via AGENT_RESUME_AGENT. Dry-run by default; returns shell_command. To prevent waking the wrong chat, run_id is required unless session_id is explicit or use_last=true. For Codex uses codex exec resume, for OpenCode opencode --session/--continue, for Claude claude --print --resume/--continue.",
        "inputSchema": {"type":"object","properties":{"agent":{"type":"string","enum":["codex","opencode","claude"]},"cwd":{"type":["string","null"]},"session_id":{"type":["string","null"]},"use_last":{"type":"boolean","default":False},"prompt":{"type":["string","null"]},"job_id":{"type":["string","null"]},"log_file":{"type":["string","null"]},"note":{"type":["string","null"]},"query":{"type":["string","null"]},"run_id":{"type":["string","null"],"description":"Required unless session_id is explicit or use_last=true. Must be 13-digit epoch milliseconds or UUID4."},"execute":{"type":"boolean","default":False,"description":"If true, start the resume command detached. Default false for safety."}},"required":[],"additionalProperties":True},
        "handler": resume_agent,
    },
    "register_agent": {
        "description": "Optional manual registration of current client/session. Normally agent identity should be configured once through AGENT_RESUME_AGENT in the MCP config, not passed on every call.",
        "inputSchema": {"type":"object","properties":{"agent":{"type":"string","enum":["codex","opencode","claude"]},"cwd":{"type":["string","null"]},"session_id":{"type":["string","null"]},"label":{"type":["string","null"]},"run_id":{"type":["string","null"],"description":"13-digit epoch milliseconds or UUID4 correlation marker."},"metadata":{"type":["object","null"]}},"required":[],"additionalProperties":True},
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
    f.add_argument("--agent", choices=["codex","opencode","claude"], help="Defaults to AGENT_RESUME_AGENT/config.json"); f.add_argument("--cwd"); f.add_argument("--query"); f.add_argument("--run-id"); f.add_argument("--limit", type=int, default=20)
    r=sub.add_parser("resume")
    r.add_argument("--agent", choices=["codex","opencode","claude"], help="Defaults to AGENT_RESUME_AGENT/config.json"); r.add_argument("--cwd"); r.add_argument("--session-id"); r.add_argument("--use-last", action="store_true"); r.add_argument("--prompt"); r.add_argument("--job-id"); r.add_argument("--log-file"); r.add_argument("--note"); r.add_argument("--query"); r.add_argument("--run-id"); r.add_argument("--execute", action="store_true")
    sub.add_parser("mcp")
    args=ap.parse_args()
    if args.cmd == "mcp": mcp_main(); return
    if args.cmd == "find": print(json.dumps({"sessions": find_sessions(args.agent, args.cwd, args.query, args.limit, args.run_id)}, ensure_ascii=False, indent=2)); return
    if args.cmd == "resume": print(json.dumps(resume_agent(vars(args)), ensure_ascii=False, indent=2)); return

if __name__ == "__main__":
    cli_main()
