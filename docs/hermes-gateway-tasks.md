# Задачи в существующей Hermes Gateway-сессии

Этот маршрут предназначен только для явного выбора Hermes. Он дополняет старый
opaque `/v1/agent-resume/wake`, сохраняя его locator/result_ref contract.
Новый endpoint находится в **том же** authenticated loopback ingress:
`POST /v1/agent-resume/task`, existing `AGENT_RESUME_HERMES_TOKEN`.
Новых серверов, credentials, scheduler и изменений Hermes core нет.

Поддерживаемый grant в profile config:

```yaml
plugins:
  entries:
    agent-resume:
      allow_gateway_injection: true
      gateway_task_session_key: 'agent:main:telegram:group:-1004360838692:9'
```

`bind` принимает `{op:"bind",name,cwd}`; `find` — те же поля.
Они сохраняют/читают имя задачи и canonical CWD рядом с **существующими**
session_key и native SID. Новая CLI-сессия или Telegram topic не создаётся.
`session` принимает `{op:"session",session_key}`, `home` — `{op:"home"}`.
`send` принимает `{op:"send",session_key,input_id,message}`;
`receipt` — `{op:"receipt",session_key,input_id}` и только читает квитанцию.
Все операции ограничены exact configured home key. Иные поля отклоняются.

Input identity, hash сообщения, случайный native marker, SID и CWD резервируются
атомарно в owner-only `state/agent-resume/gateway-tasks.sqlite3` до единственного
`ctx.inject_message(content,session_key=...)`. Повтор с тем же input ID читает
reservation/receipt; конфликт сохраняет admission flags оригинала. Привязка
остаётся после перезапуска Herder и plugin. Смена native SID требует явной
reconciliation, не запускает новую задачу автоматически.

`inject_message=True` подтверждает scheduling, **не native admission и не конец
LLM**. Registered `pre_llm_call` наблюдает exact marker и native SID из настоящего
agent prologue, сохраняет actual turn ID/model и применяет поддерживаемый
`register_task_env_overrides(SID,{cwd})` до выполнения business tools. В source
проверен Hermes `agent/turn_context.py::_collect_pre_llm_call_context`.
HTTP ждёт этот ACK не дольше 2 секунд. Receipt:

```json
{"ok":true,"admitted":true,"pending":true,"completed":false,
 "inputId":"caller-input-id","turnId":"actual-native-turn",
 "sessionKey":"agent:main:telegram:group:-1004360838692:9",
 "nativeSessionId":"actual-stored-SID","model":"actual-native-model",
 "state":"admitted"}
```

Без native ACK: `ok:false,admitted:false,admissionUnknown:true,pending:true,
nonRetryable:true,completed:false`, state `reserved`/`scheduled`. Допустим только
`receipt` того же input ID. Unknown никогда не отправляется повторно, даже после
restart. Наличие active writer или unconfirmed input отказывает до native call.
CLI `_cli_ref` запрещён: этот route не вводит сообщения в локальный REPL и не
использует `/resume`. Core strict Gateway routing сохраняет stored origin,
thread_id и exact SID; вторую тему/исполнителя bridge не создаёт.

## Установка — только Root

Запустить установленным Hermes Python **из точного published agent-resume source**:

```sh
~/.hermes/hermes-agent/venv/bin/python scripts/install-hermes-plugin.py \
  --hermes-home /home/roomhacker/.hermes \
  --gateway-session-key agent:main:telegram:group:-1004360838692:9
```

Installer сохраняет source symlink `plugins/agent-resume/__init__.py`, manifest
с `pre_gateway_dispatch` и `pre_llm_call`; явно указанная option включает
supported grant и merge только plugin settings, сохраняя остальные значения
live config и canonical symlink. Без option grant не меняется. Restart/services
и actual topic dialogue canary выполняет Root. Bridge по прежнему получает
live Gateway context при первом genuine incoming event.

На read-only проверке 12.10.2026 stored home SID был
`20260909_220106_70ff6a6b`, а **его** model_override — `glm-5.3-flash`/`zai`.
Default config `MiniMax-M3` нельзя выдавать за actual модель этой lane.
Live `~/.hermes/config.yaml` — отдельный файл, а не symlink на canonical
hermes-config snapshot. При установке нельзя заменять его всем старым snapshot;
installer применяет только согласованные plugin settings. Перед consumer canary
Root повторно проверяет live binding и модель.

Focused checks: реальный `PluginContext.inject_message` и Gateway dispatch из
установленного source, exact topic/SID metadata, authenticated aiohttp handler,
slow LLM после fast native ACK, same-input/restart/query-only, конфликт payload,
active/CLI refusal и scoped installer merge. Telegram/provider в fixtures
изолированы; это source proof, не live Telegram acceptance.
