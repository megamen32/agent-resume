# agent-resume

[English](README.md) | **Русский** | [中文](README.zh.md)

MCP/CLI-помощник, который ждёт завершения фоновой работы и возобновляет ту же локальную сессию Codex, OpenCode или Claude Code.

![Рабочее место agent-resume](docs/assets/readme-hero.png)

## Быстрый старт

Из локального checkout одной командой добавьте MCP-конфигурацию в Codex и OpenCode:

```bash
python3 scripts/install-client-configs.py codex opencode
```

Скрипт не требует внешних Python-зависимостей, идемпотентен и записывает готовый launcher `npx -y github:megamen32/agent-resume`. Для Claude Code добавьте `claude` явно:

```bash
python3 scripts/install-client-configs.py claude
```

Перезапустите MCP-клиент после установки или обновления: Python-скрипт загружается relay только при старте.

## Поддерживаемые агенты

- Codex: `codex exec resume <SESSION_ID> "prompt"` или `codex exec resume --last "prompt"`;
- OpenCode: `opencode --session <SESSION_ID> --prompt "prompt"` или `opencode --continue --prompt "prompt"`;
- Claude Code: fallback-поддержка; обычно Claude умеет возобновлять себя самостоятельно.

## Настройка identity

Идентификатор агента задаётся один раз в конфигурации клиента через `AGENT_RESUME_AGENT`. Не передавайте `agent=codex|opencode|claude` в каждом вызове.

Пример для Codex:

```toml
[mcp_servers.agent_resume]
command = "npx"
args = ["-y", "github:megamen32/agent-resume"]
env = { AGENT_RESUME_AGENT = "codex" }
enabled = true
```

Пример локального запуска MCP-сервера:

```bash
python3 agent_resume.py mcp
```

## Долгое ожидание и resume

Основные MCP-инструменты:

- `run_and_resume` — запустить неинтерактивную команду и возобновить чат после завершения;
- `attach_pid_and_resume` — дождаться завершения существующего PID;
- `attach_query_and_resume` — найти процесс по подстроке команды;
- `wait_and_resume` — подождать фиксированное время;
- `wait_job_status` — проверить состояние фоновой задачи.

Для Codex текущий thread id берётся из MCP `_meta.threadId`. Для OpenCode и Claude обязательны `cwd` и маркер из пяти ASCII-символов, например `Q7xK2`; `use_last` отключён, чтобы не разбудить неправильный чат.

По умолчанию скрипт читает тела сообщений для поиска маркера. Для режима только по метаданным задайте:

```bash
export AGENT_RESUME_SCAN_MESSAGE_BODIES=0
```

## CLI и проверка

```bash
AGENT_RESUME_AGENT=codex ./agent_resume.py find --cwd "$PWD"
AGENT_RESUME_AGENT=codex ./agent_resume.py resume --cwd "$PWD" --query "deploy" --log-file /tmp/job.log

python3 -m py_compile agent_resume.py scripts/install-client-configs.py
node --check npm/agent-resume-mcp.js
python3 -m pytest -q tests
```

`build_resume_command` по умолчанию работает в dry-run; `execute=true` действительно запускает resume в фоне. Инструмент не вводит клавиши в уже открытый TUI — для интерактивных терминалов нужен отдельный terminal-specific tool.

Подробные правила marker matching, источники `SESSION_ID`, адаптеры и все MCP-инструменты описаны в [английском README](README.md).

## Лицензия

MIT
