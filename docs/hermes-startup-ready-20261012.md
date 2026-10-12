# Hermes ingress готов сразу после штатного restart

Confirmed defect: gateway1121670 работал, но18791 ConnectionRefused. Plugin915
поднимал bridge только из pre_gateway_dispatch — до первого сообщения прямой
Home API отсутствовал. User требует startup-ready без Telegram bootstrap.

Source2c2950c, AgentResume plugin0.1.10: configured Home platform registers
официальный connect-time platform handler. Adapter.gateway_runner передаётся
штатным SDK до событий. ctx.spawn_task управляет lifetime ТОГО ЖЕ ingress;
concurrent startup/fallback callbacks используют один task и async lock.
Cancellation закрывает listener; startup failure очищает AppRunner. Старый
pre_gateway_dispatch остаётся compatibility recovery, не вторым transport.
Не добавлялись scheduler/server/secret-store/core patches/новые grants.

Scoped review: все изменения ограничены startup/lifetime/installer version и
focused tests/docs. Auth, immutable binding reservation, CWD/ABA fence,
unknown/query-only, actual-model observation и legacy wake receipt неизменны.
Тест проверяет реальные PluginContext, BasePlatformAdapter factory и HTTP
home/receipt/auth; native injection запрещён fixture assertion. Repeated factory
и fallback используют тот же task; cancellation закрывает порт. Missing runner
не создаёт shadow ingress. Два прежних hook contract tests также прошли.

5 focused integration cases PASS3.438s/max15s, CPU1/high1GiB/max2GiB/tasks128.
Native helper0, same_generation_cleanup=true, peak83320832bytes, swap/OOM0.
Прежние3 HermesConfig scenarios не повторялись, provider/business calls0.
Runner source checks вместе с setup ограничены ordinary180s; broad suite не
выполнялась. Exact native result/payload hashes и source pins в paired JSON.

Root подтвердил sole Hermes owner/window: active_agents0, restartfalse,
steady1121670. Прежний SIGUSR1/exit75 в04:02/04:03 не был текущей maintenance;
sender не атрибутирован и не выдумывается. После reviewed publication установлен
только AgentResume manifest/symlink (без --gateway-session-key, без config rewrite).
Один official guarded gateway restart:1121670→1548737, native CLI exit0.

Реальный read-only consumer: home HTTP200 и query ранее admitted input HTTP200.
SDK log04:47:00MSK подтверждает connect-time wiring agent-resume. Home key
agent:main:telegram:group:-1004360838692:9/SID20260909_220106_70ff6a6b,
CWD/generation/binding сохранены. ВСЕ115 routing rows (Home+114other), оба input
records, live config/env и Notice0.1.1 files сравнивались с pre-change snapshot:
точно неизменны. Новых бизнес-inputs0, old input не replay; receipt pending и
completed=false не переименованы в окончание LLM. Telegram сообщение для
bootstrap не требовалось. Root владеет следующим once business canary;
пользователь спит, phone/call/отправок владельцем этой задачи не было.

Protected auth остаётся existing service EnvironmentFile
~/.config/agent-resume/hermes.env (owner1000/group1000,640), а не legacy Hermes
dotenv. Первые две локальные ошибки consumer setup произошли ДО HTTP: неверный
dotenv ref и слишком строгая assumption600. Исправлен только read-only probe;
permissions/token не менялись, значения не выводились/не копировались.

Private full before/live receipts: existing Herder worktree
.tmp/agent-resume-startup-check; до model business acceptance это только
startup/readiness slice. [Selected proof](hermes-startup-ready-20261012.json).
Official supported source: hermes_cli/plugins.py platform factory/spawn_task,
gateway/platforms/base.py::_wire_plugin_handlers,
gateway/run_adapters.py::_create_adapter. Core checkout использован read-only.
