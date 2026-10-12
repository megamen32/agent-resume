"""Hermes wake transport owned by agent-resume.

The module is installed as a Hermes plugin.  Its loopback endpoint is the
only bridge from the standalone ``agent-resume`` process to the *live*
GatewayRunner.  The endpoint is intentionally not an LLM tool and accepts
only a canonical locator plus an opaque completion reference.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import uuid
import logging
import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.request import Request, urlopen

_OPAQUE_REF = re.compile(r"^(?:urn:agent-herder:)?[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CHAT_TYPES = {"dm", "group", "channel", "thread", "webhook"}
_TOKEN_ENV = "AGENT_RESUME_HERMES_TOKEN"
_HOST_ENV = "AGENT_RESUME_HERMES_HOST"
_PORT_ENV = "AGENT_RESUME_HERMES_PORT"
_PATH = "/v1/agent-resume/wake"
_bridge: "Bridge | None" = None
_bridge_lock: asyncio.Lock | None = None
_bridge_task: asyncio.Task | None = None
_ctx: Any = None
_TASK_ACK_SECONDS = 2.0
logger = logging.getLogger(__name__)


def _task_settings() -> dict[str, Any]:
    from hermes_cli.plugins import _plugin_home_scope
    from hermes_cli.config import load_config_readonly
    with _plugin_home_scope(_ctx._manager.home_path):
        config = load_config_readonly() or {}
    entry = config.get("plugins", {}).get("entries", {}).get("agent-resume", {})
    return {"session_key": entry.get("gateway_task_session_key"), "model": config.get("model", {}).get("default")}


@contextmanager
def _task_db():
    home = Path(_ctx._manager.home_path) / "state" / "agent-resume"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home / "gateway-tasks.sqlite3"
    # Create owner-only before SQLite opens it. No prompts or credentials are stored.
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS binding (session_key TEXT PRIMARY KEY, session_id TEXT, name TEXT, cwd TEXT, generation TEXT)")
    if "generation" not in {column[1] for column in db.execute("PRAGMA table_info(binding)")}:
        db.execute("ALTER TABLE binding ADD COLUMN generation TEXT")
    db.execute("CREATE TABLE IF NOT EXISTS inputs (input_id TEXT PRIMARY KEY, session_key TEXT, session_id TEXT, cwd TEXT, digest TEXT, marker TEXT UNIQUE, state TEXT, turn_id TEXT, model TEXT)")
    columns = {column[1] for column in db.execute("PRAGMA table_info(inputs)")}
    for name in ("selected_model", "effective_model", "effective_provider"):
        if name not in columns:
            db.execute(f"ALTER TABLE inputs ADD COLUMN {name} TEXT")
    try:
        with db:
            yield db
    finally:
        db.close()


def _task_failed(error: str) -> dict[str, Any]:
    return {"ok": False, "notSubmitted": True, "error": error}


def _task_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
    admitted = row["state"] == "admitted"
    return {"ok": admitted, "admitted": admitted, "pending": True, "completed": False, "inputId": row["input_id"],
            "turnId": row["turn_id"], "model": row["effective_model"],
            "selectedModel": row["selected_model"], "modelAtAdmission": row["model"],
            "effectiveModel": ({"model": row["effective_model"], "provider": row["effective_provider"], "source": "native-post-api-request", "turnId": row["turn_id"]} if row["effective_model"] else None),
            "nativeSessionId": row["session_id"],
            "sessionKey": row["session_key"], "state": row["state"],
            **({} if admitted else {"admissionUnknown": True, "pending": True, "nonRetryable": True,
                                   "error": "Native turn admission is not yet observed; query this input ID only"})}


def _task_existing(row: Mapping[str, Any], key: str, digest: str) -> dict[str, Any]:
    receipt = _task_receipt(row)
    if row["session_key"] != key or row["digest"] != digest:
        # A conflicting payload does not undo the original input's native admission.
        receipt.update(ok=False, nonRetryable=True, error="Input ID conflicts with its immutable payload")
    return receipt


async def _session_effective_model(gateway: Any, session_id: str) -> dict[str, Any] | None:
    """Read core's persisted last runtime, never interpret its selected override as execution.

    This is session-scoped evidence, not evidence about an arbitrary older input/turn.
    """
    try:
        db = getattr(gateway, "_session_db", None)
        if db is None:
            return None
        row = await asyncio.wait_for(db.get_session(session_id), timeout=.3)
        if not row:
            return None
        config = json.loads(row.get("model_config") or "{}")
        runtime = config.get("gateway_runtime")
        if not isinstance(runtime, dict) or not row.get("model"):
            return None
        return {"model": row["model"], "provider": runtime.get("provider"),
                "fallbackActive": runtime.get("fallback_active") is True, "source": "gateway-runtime", "scope": "last-session-runtime"}
    except Exception:
        return None


def _task_session(row: Mapping[str, Any], entry: Any, settings: Mapping[str, Any], effective: Any = None) -> dict[str, Any]:
    return {"id": row["session_key"], "harness": "hermes", "title": row["name"], "cwd": row["cwd"],
            "status": "running" if getattr(entry, "active_turn_token", None) else "idle",
            "lastActivity": str(getattr(entry, "updated_at", "")), "needsPermission": False,
            "model": effective.get("model") if effective else None,
            "meta": {"transport": "hermes-gateway-task", "sessionKey": row["session_key"],
                     "selectedModel": (getattr(entry, "model_override", None) or {}).get("model") or settings.get("model"),
                     "selectedProvider": (getattr(entry, "model_override", None) or {}).get("provider"),
                     "effectiveModel": effective,
                     **({"taskBinding": {"name": row["name"], "cwd": row["cwd"], "generation": row["generation"]}}
                        if "generation" in row.keys() and row["generation"] else {}),
                     "nativeSessionId": row["session_id"], "sessionId": row["session_id"],
                     "origin": entry.origin.to_dict() if hasattr(entry.origin, "to_dict") else {
                         "platform": entry.origin.platform.value, "chat_id": entry.origin.chat_id,
                         "thread_id": entry.origin.thread_id}, "cwdSource": "task-terminal-override-on-admission"}}


async def handle_task_request(body: Mapping[str, Any], *, authorization: str | None, gateway: Any) -> dict[str, Any]:
    """Authenticated task intake on the existing bridge; ctx is the only native writer.

    Reservation precedes scheduling. A crash/timeout is query-only, never permission
    to inject again. Native admission is observed by the supported agent prologue hook.
    """
    expected = os.environ.get(_TOKEN_ENV, "")
    if not expected or not hmac.compare_digest(str(authorization or ""), f"Bearer {expected}"):
        return _task_failed("unauthorized")
    try:
        if _ctx is None or _ctx._manager._cli_ref is not None:
            return _task_failed("Gateway plugin context unavailable; CLI injection is forbidden")
        settings = _task_settings()
        key = settings.get("session_key")
        if not key or not isinstance(body, Mapping):
            return _task_failed("Explicit gateway task session key is not configured")
        op = body.get("op")
        fields = {"home": {"op"}, "bind": {"op", "name", "cwd"}, "find": {"op", "name", "cwd"}, "session": {"op", "session_key"},
                  "send": {"op", "session_key", "input_id", "message"}, "receipt": {"op", "session_key", "input_id"}}
        allowed = fields.get(op, set())
        if op == "send" and "expected_binding" in body:
            allowed = allowed | {"expected_binding"}
        if op not in fields or set(body) != allowed:
            return _task_failed("Invalid task operation fields")
        if op in {"send", "receipt"} and body["session_key"] != key:
            return _task_failed("Target is not the configured home session")
        with _task_db() as db:
            if op == "receipt":
                row = db.execute("SELECT * FROM inputs WHERE input_id=? AND session_key=?", (body["input_id"], key)).fetchone()
                return _task_receipt(row) if row else {"ok": True, "state": "not_found"}
            if op == "send":
                ident, message = body["input_id"], body["message"]
                if not isinstance(ident, str) or not 1 <= len(ident) <= 256 or any(ord(c)<32 for c in ident):
                    return _task_failed("A bounded stable input ID is required")
                if not isinstance(message, str) or not message.strip() or len(message.encode()) > 32768:
                    return _task_failed("A bounded non-empty task message is required")
                digest = hashlib.sha256(message.encode()).hexdigest()
                row = db.execute("SELECT * FROM inputs WHERE input_id=?", (ident,)).fetchone()
                if row:
                    return _task_existing(row, key, digest)
            entry = await gateway.async_session_store.lookup_by_session_key(key)
            if entry is None or entry.origin is None or not gateway._plugin_injection_accepting():
                return _task_failed("Live stored home session unavailable")
            if op == "session" and body["session_key"] not in {key, entry.session_id}:
                return _task_failed("Target is not the configured home session")
            effective = await _session_effective_model(gateway, entry.session_id) if op != "send" else None
            db.execute("BEGIN IMMEDIATE")
            if op == "send":
                prior = db.execute("SELECT * FROM inputs WHERE input_id=?", (ident,)).fetchone()
                if prior:
                    return _task_existing(prior, key, digest)
            binding = db.execute("SELECT * FROM binding WHERE session_key=?", (key,)).fetchone()
            if binding and binding["session_id"] != entry.session_id:
                return _task_failed("Home native session binding changed; explicit reconciliation required")
            if op in {"bind", "find"}:
                name, cwd = body["name"], body["cwd"]
                if not isinstance(name, str) or not name.strip() or len(name)>512 or any(ord(c)<32 for c in name):
                    return _task_failed("Invalid task name")
                if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
                    return _task_failed("Existing absolute task cwd is required")
                cwd = str(Path(cwd).resolve())
                if op == "find" and (not binding or binding["name"] != name or binding["cwd"] != cwd):
                    return {"ok": True, "session": None}
                if op == "bind":
                    if binding and binding["name"] == name and binding["cwd"] == cwd and binding["generation"]:
                        return {"ok": True, "session": _task_session(binding, entry, settings, effective)}
                    if getattr(entry, "active_turn_token", None) or gateway._is_session_running(key):
                        return _task_failed("Home session has an active writer")
                    if db.execute("SELECT 1 FROM inputs WHERE session_key=? AND state IN ('reserved','scheduled')", (key,)).fetchone():
                        return _task_failed("Home session has an unconfirmed input; query only")
                    db.execute("INSERT OR REPLACE INTO binding VALUES (?,?,?,?,?)", (key, entry.session_id, name, cwd, uuid.uuid4().hex))
                    binding = db.execute("SELECT * FROM binding WHERE session_key=?", (key,)).fetchone()
                return {"ok": True, "session": _task_session(binding, entry, settings, effective)}
            if op in {"session", "home"}:
                row = binding or {"session_key": key, "session_id": entry.session_id, "name": key, "cwd": settings.get("cwd") or str(Path.cwd())}
                return {"ok": True, "session": _task_session(row, entry, settings, effective)}
            if not binding:
                return _task_failed("Bind the task to the existing home session first")
            expected_binding = body.get("expected_binding")
            if (not isinstance(expected_binding, Mapping) or set(expected_binding) != {"name", "cwd", "generation"}
                or not binding["generation"]
                or dict(expected_binding) != {"name": binding["name"], "cwd": binding["cwd"], "generation": binding["generation"]}):
                return _task_failed("Named task binding changed before delivery; resolve the intended task explicitly")
            if getattr(entry, "active_turn_token", None) or gateway._is_session_running(key):
                return _task_failed("Home session has an active writer")
            if db.execute("SELECT 1 FROM inputs WHERE session_key=? AND state IN ('reserved','scheduled')", (key,)).fetchone():
                return _task_failed("Home session has an unconfirmed input; query only")
            marker = f"[Agent Herder task {uuid.uuid4().hex}]"
            selected = (getattr(entry, "model_override", None) or {}).get("model") or settings.get("model")
            db.execute("INSERT INTO inputs (input_id,session_key,session_id,cwd,digest,marker,state,turn_id,model,selected_model) VALUES (?,?,?,?,?,?,'reserved',NULL,NULL,?)", (ident,key,entry.session_id,binding["cwd"],digest,marker,selected))
        # Native call must occur only AFTER the durable reservation commits.
        try:
            scheduled = _ctx.inject_message(f"{marker}\nРабочая папка задачи: {binding['cwd']}\n{message}", session_key=key)
        except Exception:
            scheduled = None  # unknown; immutable reservation is the no-replay fence
        with _task_db() as db:
            if scheduled is False:
                db.execute("DELETE FROM inputs WHERE input_id=? AND state='reserved'", (ident,))
                return _task_failed("Supported gateway injection refused before scheduling")
            if scheduled is True:
                db.execute("UPDATE inputs SET state='scheduled' WHERE input_id=? AND state='reserved'", (ident,))
        until = time.monotonic() + _TASK_ACK_SECONDS
        while True:
            with _task_db() as db:
                row = db.execute("SELECT * FROM inputs WHERE input_id=?", (ident,)).fetchone()
            if row["state"] == "admitted" or time.monotonic() >= until:
                return _task_receipt(row)
            await asyncio.sleep(.025)
    except Exception:
        # This branch can include disk/receipt failure AFTER injection: never assert no submission.
        return {"ok": False, "admissionUnknown": True, "nonRetryable": True, "error": "Task receipt unavailable; query the same input ID only"}


def _on_task_pre_llm_call(*, session_id: str, turn_id: str, user_message: Any, model: str, **_: Any) -> None:
    """Observe actual native prologue, without changing the model or starting a turn."""
    if _ctx is None or not isinstance(user_message, str):
        return
    match = re.search(r"\[Agent Herder task [0-9a-f]{32}\]", user_message)
    if not match:
        return
    with _task_db() as db:
        row = db.execute("SELECT * FROM inputs WHERE marker=? AND session_id=? AND state IN ('reserved','scheduled')", (match.group(),session_id)).fetchone()
        if not row:
            return
        from tools.terminal_tool import register_task_env_overrides
        register_task_env_overrides(session_id, {"cwd": row["cwd"]})
        db.execute("UPDATE inputs SET state='admitted',turn_id=?,model=? WHERE input_id=?", (turn_id,model,row["input_id"]))


def _on_task_post_api_request(*, session_id: str, turn_id: str, model: str, provider: str, **_: Any) -> None:
    """Observe the effective successful provider call for exactly this admitted native turn."""
    if _ctx is None or not session_id or not turn_id or not model:
        return
    with _task_db() as db:
        db.execute("UPDATE inputs SET effective_model=?,effective_provider=? WHERE session_id=? AND turn_id=? AND state='admitted'", (model,provider,session_id,turn_id))


def _receipt_id() -> str:
    return f"hermes-wake-{uuid.uuid4().hex}"


def _string(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"locator.{field} is required")
        return None
    result = str(value).strip()
    if not result and required:
        raise ValueError(f"locator.{field} is required")
    return result or None


def _target(locator: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the immutable exact-session locator, without any discovery."""
    if not isinstance(locator, Mapping):
        raise ValueError("locator is required")
    if str(locator.get("schema") or "") != "hermes.locator.v2":
        raise ValueError("unsupported Hermes locator schema")
    platform = _string(locator.get("platform"), "platform", required=True)
    if not _PLATFORM_RE.fullmatch(platform or ""):
        raise ValueError("locator.platform is unsupported")
    chat_type = _string(locator.get("chat_type"), "chat_type", required=True)
    if chat_type not in _CHAT_TYPES:
        raise ValueError("locator.chat_type is unsupported")
    target: dict[str, Any] = {
        "schema": "hermes.locator.v2",
        "session_key": _string(locator.get("session_key"), "session_key", required=True),
        "platform": platform,
        "chat_id": _string(locator.get("chat_id"), "chat_id", required=True),
        "chat_type": chat_type,
    }
    for field in ("thread_id", "user_id", "user_id_alt", "scope_id", "prospective_thread_id", "profile"):
        value = _string(locator.get(field), field)
        if value is not None:
            target[field] = value
    return target


def _result_ref(value: Any) -> str:
    result = str(value or "").strip()
    if not _OPAQUE_REF.fullmatch(result):
        raise ValueError("result_ref must be an opaque UUID reference")
    return result


def _source(locator: Mapping[str, Any]) -> Any:
    from gateway.config import Platform
    from gateway.session import SessionSource
    return SessionSource(
        platform=Platform(str(locator["platform"])), chat_id=str(locator["chat_id"]),
        chat_type=str(locator["chat_type"]), thread_id=locator.get("thread_id") or None,
        user_id=locator.get("user_id") or None, user_id_alt=locator.get("user_id_alt") or None,
        scope_id=locator.get("scope_id") or None,
        prospective_thread_id=locator.get("prospective_thread_id") or None,
        profile=locator.get("profile") or None,
    )


async def deliver_wake(gateway: Any, locator: Mapping[str, Any], result_ref: Any) -> dict[str, Any]:
    """Wake exactly the already-bound Hermes session, or fail closed."""
    receipt_id = _receipt_id()
    target = None
    reference = None
    try:
        target, reference = _target(locator), _result_ref(result_ref)
        source = _source(target)
        actual_key = gateway._session_key_for_source(source)
        if actual_key != target["session_key"]:
            raise RuntimeError("canonical Hermes locator no longer resolves to its bound session")
        from gateway.config import Platform
        from gateway.wake import deliver_wake as gateway_deliver_wake
        adapter = gateway.adapters.get(Platform(target["platform"]))
        if adapter is None:
            raise RuntimeError("Hermes platform adapter is unavailable")
        await gateway_deliver_wake(adapter, text=f"Agent-resume completion available: {reference}", source=source)
        return {"receipt_id": receipt_id, "receipt_ref": receipt_id, "status": "accepted", "target": target, "result_ref": reference}
    except Exception as exc:
        return {"receipt_id": receipt_id, "receipt_ref": receipt_id, "status": "failed", "target": target, "result_ref": reference, "reason": "failed", "error": str(exc)[:300]}


async def handle_local_request(body: Mapping[str, Any], *, authorization: str | None, gateway: Any) -> dict[str, Any]:
    expected = os.environ.get(_TOKEN_ENV, "")
    supplied = str(authorization or "")
    if not expected or not hmac.compare_digest(supplied, f"Bearer {expected}"):
        return {"receipt_id": _receipt_id(), "status": "failed", "target": None, "result_ref": None, "reason": "unavailable", "error": "unauthorized"}
    if not isinstance(body, Mapping) or set(body) != {"locator", "result_ref"}:
        return {"receipt_id": _receipt_id(), "status": "failed", "target": None, "result_ref": None, "reason": "invalid", "error": "only locator and opaque result_ref are accepted"}
    return await deliver_wake(gateway, body["locator"], body["result_ref"])


@dataclass
class Bridge:
    runner: Any
    site: Any


async def start_bridge(gateway: Any) -> Bridge | None:
    """Start one loopback-only receiver after the Gateway is live."""
    global _bridge_lock
    if _bridge_lock is None:
        _bridge_lock = asyncio.Lock()
    async with _bridge_lock:
        return await _start_bridge_locked(gateway)


async def _start_bridge_locked(gateway: Any) -> Bridge | None:
    global _bridge
    if _bridge is not None:
        return _bridge
    token = os.environ.get(_TOKEN_ENV, "")
    if not token:
        return None
    runner = None
    try:
        port = int(os.environ.get(_PORT_ENV, "18791"))
        if not 1 <= port <= 65535:
            raise ValueError("port out of range")
        from aiohttp import web
        async def wake(request: Any) -> Any:
            try:
                body = await request.json()
            except Exception:
                body = {}
            receipt = await handle_local_request(body, authorization=request.headers.get("Authorization"), gateway=gateway)
            return web.json_response(receipt, status=200 if receipt["status"] == "accepted" else 400)
        async def task(request: Any) -> Any:
            try:
                body = await request.json()
            except Exception:
                body = {}
            receipt = await handle_task_request(body, authorization=request.headers.get("Authorization"), gateway=gateway)
            return web.json_response(receipt, status=200)
        app = web.Application(client_max_size=65536)
        app.router.add_post(_PATH, wake)
        app.router.add_post("/v1/agent-resume/task", task)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=os.environ.get(_HOST_ENV, "127.0.0.1"), port=port)
        await site.start()
        _bridge = Bridge(runner=runner, site=site)
        return _bridge
    except asyncio.CancelledError:
        if runner is not None:
            await runner.cleanup()
        raise
    except Exception as exc:
        if runner is not None:
            await runner.cleanup()
        logger.warning("agent-resume Hermes bridge did not start: %s", exc)
        return None


async def _bridge_lifecycle(gateway: Any) -> None:
    """The SDK owns this task; unload cancels and closes the same ingress."""
    global _bridge
    bridge = None
    try:
        bridge = await start_bridge(gateway)
        if bridge is not None:
            await asyncio.Future()
    finally:
        if bridge is not None:
            await bridge.runner.cleanup()
            if _bridge is bridge:
                _bridge = None


def _schedule_bridge(gateway: Any) -> None:
    global _bridge_task
    if _bridge is not None or (_bridge_task is not None and not _bridge_task.done()):
        return
    spawn = getattr(_ctx, "spawn_task", asyncio.create_task)
    _bridge_task = spawn(_bridge_lifecycle(gateway), name="agent-resume-hermes-bridge")


def _on_platform_connect(_native: Any, adapter: Any) -> None:
    """Supported connect-time factory, before any platform MessageEvent."""
    gateway = getattr(adapter, "gateway_runner", None)
    if gateway is not None:
        _schedule_bridge(gateway)


def _on_pre_gateway_dispatch(*, gateway: Any, **_: Any) -> None:
    """Compatibility recovery shares the same supervised startup and ingress."""
    _schedule_bridge(gateway)


class HermesGatewayAdapter:
    """Standalone agent-resume client for the plugin's authenticated seam."""
    def __init__(self, endpoint: str, token: str, *, opener: Any = urlopen) -> None:
        if not endpoint or not token:
            raise ValueError("Hermes wake endpoint and token are required")
        self.endpoint, self.token, self._opener = endpoint.rstrip("/"), token, opener

    def wake(self, locator: Mapping[str, Any], result_ref: Any) -> dict[str, Any]:
        target, reference = _target(locator), _result_ref(result_ref)
        request = Request(self.endpoint, data=json.dumps({"locator": target, "result_ref": reference}, separators=(",", ":")).encode(), headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}, method="POST")
        with self._opener(request, timeout=8) as response:
            value = json.loads(response.read())
        return value if isinstance(value, dict) else {"status": "failed", "target": target, "result_ref": reference, "reason": "failed"}


def register(ctx: Any) -> None:
    global _ctx
    _ctx = ctx
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", _on_task_pre_llm_call)
    ctx.register_hook("post_api_request", _on_task_post_api_request)
    key = _task_settings().get("session_key")
    if isinstance(key, str):
        parts = key.split(":")
        if len(parts) > 2 and parts[0] == "agent" and _PLATFORM_RE.fullmatch(parts[2]):
            ctx.register_platform_handler(parts[2], _on_platform_connect)
