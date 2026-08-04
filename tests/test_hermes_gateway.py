from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load_module():
    source = Path(__file__).parents[1] / "hermes_gateway.py"
    spec = importlib.util.spec_from_file_location("agent_resume_hermes_gateway", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    sys.modules.pop(spec.name, None)
    return module


LOCATOR = {
    "schema": "hermes.locator.v2",
    "session_key": "agent:main:telegram:thread:chat-42:topic-7",
    "platform": "telegram",
    "chat_id": "chat-42",
    "chat_type": "thread",
    "thread_id": "topic-7",
    "profile": "default", "user_id": "user-1",
}
REF = "7dca6552-77e2-4699-9871-b86c963f6425"


def test_live_gateway_deliver_wake_uses_canonical_source_and_sanitized_text():
    mod = load_module()
    calls = []

    async def fake_deliver(adapter, *, text, source):
        calls.append((adapter, text, source))

    gateway = SimpleNamespace(adapters={}, _session_key_for_source=lambda source: LOCATOR["session_key"])
    # Enum lookup is supplied by the module's imported gateway symbols in the live process.
    platform = lambda value: value
    gateway_config = types.ModuleType("gateway.config")
    gateway_config.Platform = platform
    gateway_wake = types.ModuleType("gateway.wake")
    gateway_wake.deliver_wake = fake_deliver
    gateway_package = types.ModuleType("gateway")
    gateway_package.__path__ = []
    with patch.object(mod, "_source", lambda locator: locator), patch.dict(
        sys.modules,
        {"gateway": gateway_package, "gateway.config": gateway_config, "gateway.wake": gateway_wake},
    ):
        gateway.adapters = {"telegram": "adapter"}
        receipt = asyncio.run(mod.deliver_wake(gateway, LOCATOR, REF))
    assert receipt["status"] == "accepted"
    assert receipt["target"] == LOCATOR
    assert calls[0][1] == f"Agent-resume completion available: {REF}"
    assert "secret" not in json.dumps(receipt).lower()


def test_local_boundary_requires_bearer_and_rejects_free_form_payload():
    mod = load_module()
    gateway = SimpleNamespace(adapters={})
    with patch.dict(os.environ, {"AGENT_RESUME_HERMES_TOKEN": "local-token"}):
        unauthorized = asyncio.run(mod.handle_local_request({"locator": LOCATOR, "result_ref": REF}, authorization="Bearer wrong", gateway=gateway))
        rejected = asyncio.run(mod.handle_local_request({"locator": LOCATOR, "result_ref": REF, "prompt": "secret"}, authorization="Bearer local-token", gateway=gateway))
    assert unauthorized["status"] == "failed" and unauthorized["error"] == "unauthorized"
    assert rejected["status"] == "failed"
    assert "secret" not in json.dumps(rejected)


def test_register_exposes_gateway_hook_not_an_llm_tool_or_heartbeat():
    mod = load_module()
    calls = []
    ctx = SimpleNamespace(register_hook=lambda *args: calls.append(args))
    mod.register(ctx)
    assert [call[0] for call in calls] == ["pre_gateway_dispatch", "gateway_startup"]
    assert "heartbeat" not in str(calls).lower()


def test_agent_resume_adapter_sends_only_locator_and_opaque_reference():
    mod = load_module()
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({"status":"accepted","receipt_id":"r1","target":LOCATOR,"result_ref":REF}).encode()

    def opener(request, timeout):
        requests.append((request, timeout))
        return Response()

    adapter = mod.HermesGatewayAdapter("http://127.0.0.1:18791/wake", "local-secret", opener=opener)
    receipt = adapter.wake(LOCATOR, REF)
    assert receipt["status"] == "accepted"
    request, timeout = requests[0]
    assert timeout == 8
    assert request.get_header("Authorization") == "Bearer local-secret"
    body = json.loads(request.data)
    assert body == {"locator": LOCATOR, "result_ref": REF}
    assert "local-secret" not in request.data.decode()
