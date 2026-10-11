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
`send` принимает `{op:"send",session_key,input_id,message,expected_binding}`;
для нового input обязательна `expected_binding:{name,cwd,generation}` из
конкретного bind/find/session receipt. `session` также допускает exact текущий
native SID как read alias и возвращает canonical session key; чужой SID запрещён.
`receipt` — `{op:"receipt",session_key,input_id}` и только читает квитанцию.
Все операции ограничены exact configured home key. Иные поля отклоняются.

Input identity, hash сообщения, случайный native marker, SID и CWD резервируются
атомарно в owner-only `state/agent-resume/gateway-tasks.sqlite3` до единственного
`ctx.inject_message(content,session_key=...)`. Повтор с тем же input ID читает
reservation/receipt; конфликт сохраняет admission flags оригинала. Привязка
остаётся после перезапуска Herder и plugin. Смена native SID требует явной
reconciliation, не запускает новую задачу автоматически.

Named binding имеет persisted generation. Под тем же SQLite write transaction
проверяются name + canonical CWD + generation, и только затем резервируется
новый input. Cached A → bind B → send A и resolve A → bind B → send A возвращают
definite `notSubmitted:true` без native call и без смены terminal CWD. Даже
A → B → A не оживляет старую generation. Повтор уже существующего input читает
исходную receipt до этой проверки: binding change не снимает unknown/admitted
fence. Older input receipts совместимы; legacy binding без generation требует
явного bind, generation не угадывается. Обычный follow-up без named context
сначала получает свежую current binding и передаёт её явно; гонка после lookup
также отказывает до injection.

`inject_message=True` подтверждает scheduling, **не native admission и не конец
LLM**. Registered `pre_llm_call` наблюдает exact marker и native SID из настоящего
agent prologue, сохраняет actual turn ID и modelAtAdmission (модель до возможного fallback) и применяет поддерживаемый
`register_task_env_overrides(SID,{cwd})` до выполнения business tools. В source
проверен Hermes `agent/turn_context.py::_collect_pre_llm_call_context`.
HTTP ждёт этот ACK не дольше 2 секунд. Receipt:

```json
{"ok":true,"admitted":true,"pending":true,"completed":false,
 "inputId":"caller-input-id","turnId":"actual-native-turn",
 "sessionKey":"agent:main:telegram:group:-1004360838692:9",
 "nativeSessionId":"actual-stored-SID","model":null,
 "selectedModel":"glm-5.3-flash","modelAtAdmission":"glm-5.3-flash","effectiveModel":null,
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
с `pre_gateway_dispatch`, `pre_llm_call` и `post_api_request`; явно указанная option включает
supported grant и merge только plugin settings, сохраняя остальные значения
live config и canonical symlink. Без option grant не меняется. Restart/services
и actual topic dialogue canary выполняет Root. Bridge по прежнему получает
live Gateway context при первом genuine incoming event.

На read-only проверке 12.10.2026 stored home SID был
`20260909_220106_70ff6a6b`, а **его** model_override — `glm-5.3-flash`/`zai`.
Выбранная override не доказывает actual execution: настоящий consumer подтвердил
fallback `MiniMax-M3`/`minimax`. Ни default, ни override сами по себе не evidence.
Live `~/.hermes/config.yaml` — отдельный файл, а не symlink на canonical
hermes-config snapshot. При установке нельзя заменять его всем старым snapshot;
installer применяет только согласованные plugin settings. Перед consumer canary
Root повторно проверяет live binding и модель.

Focused checks: реальный `PluginContext.inject_message` и Gateway dispatch из
установленного source, exact topic/SID metadata, authenticated aiohttp handler,
slow LLM после fast native ACK, same-input/restart/query-only, конфликт payload,
active/CLI refusal и scoped installer merge. Telegram/provider в fixtures
изолированы; это source proof, не live Telegram acceptance.

## Selected model и effective model

`home/session/find/bind`: `meta.selectedModel/selectedProvider` — выбранная
настройка; top-level `model` и `meta.effectiveModel` берутся из existing core
sessions.model + model_config.gateway_runtime после native turn. Это явно
`scope:last-session-runtime`, не evidence для произвольного старого input.
Если authoritative runtime отсутствует, `model:null`; selected остаётся отдельно.
Herder сравнивает explicit requested model с selected intent, не запускает
смену провайдера из-за наблюдаемого fallback.

`receipt/send`: native ACK сохраняет `modelAtAdmission`, но actual `model:null`
до успешного provider response. Supported `post_api_request` передаёт effective
agent.model/provider после fallback; exact session_id+turn_id обновляет durable
`model/effectiveModel:{model,provider,source:"native-post-api-request",turnId}`.
Последующие human turns и другой SID не могут переписать этот input. Повтор
остаётся чтением receipt, transport не повторяется. Это observation, не признак
завершения: `pending:true,completed:false` сохраняются после ответа провайдера.
Legacy input без per-turn hook evidence сохраняет `modelAtAdmission`, actual
неизвестен; latest session runtime не приписывается прежнему input задним числом.
DB migration добавляет nullable columns без изменения admitted/unknown fences.
Сырые provider payload/base_url/credentials не сохраняются и не выдаются.
