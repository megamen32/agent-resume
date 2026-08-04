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
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.request import Request, urlopen

_OPAQUE_REF = re.compile(r"^(?:urn:agent-herder:)?[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CHAT_TYPES = {"dm", "group", "channel", "thread"}
_TOKEN_ENV = "AGENT_RESUME_HERMES_TOKEN"
_HOST_ENV = "AGENT_RESUME_HERMES_HOST"
_PORT_ENV = "AGENT_RESUME_HERMES_PORT"
_PATH = "/v1/agent-resume/wake"
_bridge: "Bridge | None" = None


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
    global _bridge
    if _bridge is not None:
        return _bridge
    token = os.environ.get(_TOKEN_ENV, "")
    if not token:
        return None
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
        app = web.Application(client_max_size=4096)
        app.router.add_post(_PATH, wake)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=os.environ.get(_HOST_ENV, "127.0.0.1"), port=port)
        await site.start()
        _bridge = Bridge(runner=runner, site=site)
        return _bridge
    except Exception:
        return None


def _on_pre_gateway_dispatch(*, gateway: Any, **_: Any) -> None:
    """The first genuine Gateway event supplies the only live runner context."""
    if _bridge is None:
        asyncio.create_task(start_bridge(gateway), name="agent-resume-hermes-bridge")


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
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    # Normal Gateway startup already has a live runner and an event loop. Start
    # immediately when that seam is available; pre_gateway_dispatch remains a
    # retry path for deferred/plugin-only startup.
    try:
        from gateway.run import _gateway_runner_ref
        gateway = _gateway_runner_ref()
        if gateway is not None:
            asyncio.get_running_loop().create_task(start_bridge(gateway), name="agent-resume-hermes-bridge")
    except (ModuleNotFoundError, RuntimeError):
        pass
