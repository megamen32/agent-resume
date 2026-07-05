#!/usr/bin/env python3
"""Install agent-resume MCP config into Codex, OpenCode, or Claude Code.

No external dependencies. Designed to be idempotent.
OpenCode JSONC is patched minimally to preserve formatting/comments/secrets.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REPO = "github:megamen32/agent-resume"


def home_path(s: str) -> Path:
    return Path(s).expanduser()


def codex_block() -> str:
    return '''[mcp_servers.agent_resume]
command = "npx"
args = ["-y", "github:megamen32/agent-resume"]
env = { "AGENT_RESUME_AGENT" = "codex" }
enabled = true
'''


def install_codex(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = path.read_text() if path.exists() else ""
    before = s
    s = re.sub(r'(?ms)^\[mcp_servers\.agent_resume\]\n.*?(?=^\[|\Z)', '', s).rstrip() + "\n\n"
    insert_at = s.find('\n[plugins.')
    block = codex_block()
    if insert_at >= 0:
        s = s[:insert_at].rstrip() + "\n\n" + block + "\n" + s[insert_at+1:]
    else:
        s += block
    path.write_text(s)
    return {"client": "codex", "path": str(path), "changed": before != s}


def strip_jsonc(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '/':
            while i < n and text[i] not in '\r\n':
                i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '*':
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def remove_trailing_commas(text: str) -> str:
    return re.sub(r',\s*([}\]])', r'\1', text)


def load_jsonc_text(text: str) -> Any:
    return json.loads(remove_trailing_commas(strip_jsonc(text)))


def load_jsonc(path: Path) -> Any:
    return load_jsonc_text(path.read_text())


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def find_string_key(text: str, key: str, start: int = 0) -> int:
    pattern = '"' + key + '"'
    i = start
    in_str = False
    esc = False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            if text.startswith(pattern, i):
                return i
            in_str = True
        elif c == '/' and i + 1 < len(text) and text[i + 1] == '/':
            j = text.find('\n', i)
            i = len(text) if j < 0 else j
            continue
        elif c == '/' and i + 1 < len(text) and text[i + 1] == '*':
            j = text.find('*/', i + 2)
            i = len(text) if j < 0 else j + 2
            continue
        i += 1
    return -1


def find_matching_brace(text: str, open_pos: int) -> int:
    depth = 0
    in_str = False
    esc = False
    i = open_pos
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c == '/' and i + 1 < len(text) and text[i + 1] == '/':
            j = text.find('\n', i)
            i = len(text) if j < 0 else j
            continue
        elif c == '/' and i + 1 < len(text) and text[i + 1] == '*':
            j = text.find('*/', i + 2)
            i = len(text) if j < 0 else j + 2
            continue
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError('unbalanced braces')


def find_object_for_key(text: str, key: str, container_start: int = 0) -> tuple[int, int] | None:
    k = find_string_key(text, key, container_start)
    if k < 0:
        return None
    colon = text.find(':', k)
    open_pos = text.find('{', colon)
    close_pos = find_matching_brace(text, open_pos)
    end = close_pos + 1
    j = end
    while j < len(text) and text[j].isspace():
        j += 1
    if j < len(text) and text[j] == ',':
        return (k, j + 1)
    i = k
    while i > 0 and text[i - 1].isspace():
        i -= 1
    if i > 0 and text[i - 1] == ',':
        i -= 1
    return (i, end)


def install_opencode(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        '    "agent-resume": {\n'
        '      "type": "local",\n'
        '      "command": ["npx", "-y", "github:megamen32/agent-resume"],\n'
        '      "enabled": true,\n'
        '      "environment": { "AGENT_RESUME_AGENT": "opencode" }\n'
        '    }'
    )
    if not path.exists():
        after = '{\n  "mcp": {\n' + entry + '\n  }\n}\n'
        load_jsonc_text(after)
        path.write_text(after)
        return {"client": "opencode", "path": str(path), "changed": True}

    before = path.read_text()
    text = before
    mcp_key = find_string_key(text, "mcp")
    if mcp_key < 0:
        data = load_jsonc_text(text)
        data.setdefault("mcp", {})["agent-resume"] = {
            "type": "local",
            "command": ["npx", "-y", REPO],
            "enabled": True,
            "environment": {"AGENT_RESUME_AGENT": "opencode"},
        }
        after = dump_json(data)
        path.write_text(after)
        return {"client": "opencode", "path": str(path), "changed": before != after}

    colon = text.find(':', mcp_key)
    mcp_open = text.find('{', colon)
    mcp_close = find_matching_brace(text, mcp_open)
    relative = text[mcp_open:mcp_close + 1]
    existing = find_object_for_key(relative, "agent-resume")
    if existing:
        a, b = existing
        text = text[:mcp_open + a] + text[mcp_open + b:mcp_close + 1] + text[mcp_close + 1:]
        mcp_close = find_matching_brace(text, mcp_open)

    before_close = text[:mcp_close].rstrip()
    if before_close.endswith('{'):
        replacement = before_close + "\n" + entry + "\n"
    else:
        replacement = before_close + ",\n" + entry + "\n"
    after = replacement + text[mcp_close:]
    load_jsonc_text(after)
    path.write_text(after)
    return {"client": "opencode", "path": str(path), "changed": before != after}


def install_claude(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        before = path.read_text()
        data = json.loads(before)
    else:
        before = ""
        data = {}
    data.setdefault("mcpServers", {})["agent-resume"] = {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", REPO],
        "env": {"AGENT_RESUME_AGENT": "claude"},
    }
    after = dump_json(data)
    path.write_text(after)
    return {"client": "claude", "path": str(path), "changed": before != after}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clients", nargs="*", choices=["codex", "opencode", "claude"], default=["codex", "opencode"], help="Defaults to codex+opencode. Claude can resume by itself; pass claude explicitly only if you want the experimental fallback.")
    ap.add_argument("--codex-config", default="~/.codex/config.toml")
    ap.add_argument("--opencode-config", default="~/.config/opencode/opencode.jsonc")
    ap.add_argument("--claude-config", default="~/.claude.json")
    args = ap.parse_args()
    results = []
    for client in args.clients:
        if client == "codex":
            results.append(install_codex(home_path(args.codex_config)))
        elif client == "opencode":
            results.append(install_opencode(home_path(args.opencode_config)))
        elif client == "claude":
            results.append(install_claude(home_path(args.claude_config)))
    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
