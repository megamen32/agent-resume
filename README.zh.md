# agent-resume

[English](README.md) | [Русский](README.ru.md) | **中文**

agent-resume 是一个 MCP/CLI 工具：等待后台工作完成，然后恢复同一个本地 Codex、OpenCode 或 Claude Code 会话。

![agent-resume 开发工作台](docs/assets/readme-hero.png)

## 快速开始

在本地 checkout 中用一条命令把 MCP 配置写入 Codex 和 OpenCode：

```bash
python3 scripts/install-client-configs.py codex opencode
```

该脚本不需要额外的 Python 依赖，并且可以重复运行；它会写入可用的 `npx -y github:megamen32/agent-resume` launcher。要显式配置 Claude Code：

```bash
python3 scripts/install-client-configs.py claude
```

安装或升级后请重启 MCP 客户端，因为 Python 脚本只在 relay 启动时加载。

## 支持的 agent

- Codex：`codex exec resume <SESSION_ID> "prompt"` 或 `codex exec resume --last "prompt"`；
- OpenCode：`opencode --session <SESSION_ID> --prompt "prompt"` 或 `opencode --continue --prompt "prompt"`；
- Claude Code：支持 fallback，但通常 Claude 可以自行恢复会话。

## 配置 identity

通过客户端配置中的 `AGENT_RESUME_AGENT` 设置一次 agent identity，不要在每次工具调用中传入 `agent=codex|opencode|claude`。

Codex 配置示例：

```toml
[mcp_servers.agent_resume]
command = "npx"
args = ["-y", "github:megamen32/agent-resume"]
env = { AGENT_RESUME_AGENT = "codex" }
enabled = true
```

本地直接运行 MCP server：

```bash
python3 agent_resume.py mcp
```

## 长等待与 resume

主要 MCP 工具：

- `run_and_resume`：运行非交互命令，结束后恢复当前聊天；
- `attach_pid_and_resume`：等待指定 PID 退出；
- `attach_query_and_resume`：按命令子串查找并等待进程；
- `wait_and_resume`：等待固定时长；
- `wait_job_status`：查看后台任务状态。

Codex 的当前 thread id 来自 MCP `_meta.threadId`。OpenCode 和 Claude 必须提供 `cwd` 与五个 ASCII 字符的 marker，例如 `Q7xK2`；`use_last` 已禁用，以免唤醒错误聊天。

默认情况下，工具会读取消息正文来查找 marker。如只希望匹配元数据：

```bash
export AGENT_RESUME_SCAN_MESSAGE_BODIES=0
```

## CLI 与测试

```bash
AGENT_RESUME_AGENT=codex ./agent_resume.py find --cwd "$PWD"
AGENT_RESUME_AGENT=codex ./agent_resume.py resume --cwd "$PWD" --query "deploy" --log-file /tmp/job.log

python3 -m py_compile agent_resume.py scripts/install-client-configs.py
node --check npm/agent-resume-mcp.js
python3 -m pytest -q tests
```

`build_resume_command` 默认只返回将要执行的命令；只有 `execute=true` 才会在后台启动 resume。它不会向已打开的 TUI 注入按键；交互式终端应使用专用 terminal tool。

marker matching、`SESSION_ID` 来源、适配器和完整 MCP 工具说明请参阅[英文 README](README.md)。

## 许可证

MIT
