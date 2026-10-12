"""Focused integration, expected 5s/max15s: SDK connect factory -> same HTTP ingress.
No platform messages, model calls, business inputs, or live profile writes.
"""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

from test_hermes_gateway import load_module
from test_hermes_tasks import setup, KEY, SID


def test_sdk_connect_starts_home_without_inbound_and_unload_closes(tmp_path):
    from aiohttp import ClientSession, ClientConnectorError
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from gateway.platforms.base import BasePlatformAdapter

    async def run():
        mod = load_module()
        gateway, _, _ = setup(mod, tmp_path)
        manager = PluginManager(scope_key=str(tmp_path))
        ctx = PluginContext(PluginManifest(name="agent-resume", source="user"), manager)
        ctx.inject_message = lambda *a, **k: (_ for _ in ()).throw(AssertionError("read-only startup must not inject"))
        mod.register(ctx)
        adapter = SimpleNamespace(platform=SimpleNamespace(value="telegram"), name="fixture",
                                  gateway_runner=gateway, _plugin_handler_native=None, _plugin_handlers_wired=None)
        with patch("hermes_cli.plugins.get_plugin_manager", return_value=manager):
            BasePlatformAdapter._wire_plugin_handlers(adapter)
            BasePlatformAdapter._wire_plugin_handlers(adapter)
        task = mod._bridge_task
        assert task is not None
        try:
            async with asyncio.timeout(2):
                while mod._bridge is None:
                    await asyncio.sleep(.01)
            # No pre_gateway_dispatch callback and no incoming Telegram event preceded this HTTP.
            port = mod._bridge.site._server.sockets[0].getsockname()[1]
            url = f"http://127.0.0.1:{port}/v1/agent-resume/task"
            with mod._task_db() as db:
                db.execute("INSERT INTO binding VALUES (?,?,?,?,?)", (KEY, SID, "fixture repair", str(tmp_path), "original-generation"))
            async with ClientSession() as client:
                async with client.post(url, json={"op": "home"}, headers={"Authorization": "Bearer test-token"}) as response:
                    home = await response.json()
                    assert response.status == 200 and home["ok"]
                    assert home["session"]["meta"]["nativeSessionId"] == SID
                    assert home["session"]["meta"]["taskBinding"]["generation"] == "original-generation"
                async with client.post(url, json={"op": "receipt", "session_key": KEY, "input_id": "not-submitted"}, headers={"Authorization": "Bearer test-token"}) as response:
                    assert await response.json() == {"ok": True, "state": "not_found"}
                async with client.post(url, json={"op": "home"}, headers={"Authorization": "Bearer wrong"}) as response:
                    assert (await response.json())["error"] == "unauthorized"
            assert mod._bridge_task is task
            with mod._task_db() as db:
                assert db.execute("SELECT count(*) FROM inputs").fetchone()[0] == 0
                assert db.execute("SELECT generation FROM binding").fetchone()[0] == "original-generation"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert mod._bridge is None
        async with ClientSession() as client:
            try:
                await client.post(url, json={"op": "home"})
            except ClientConnectorError:
                pass
            else:
                raise AssertionError("unloaded plugin retained a second ingress")

    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path), "AGENT_RESUME_HERMES_TOKEN": "test-token", "AGENT_RESUME_HERMES_HOST": "127.0.0.1", "AGENT_RESUME_HERMES_PORT": "0"}):
        # Production forbids port0; choose an unused explicit loopback port for the disposable fixture.
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            os.environ["AGENT_RESUME_HERMES_PORT"] = str(sock.getsockname()[1])
        asyncio.run(run())


def test_connect_without_runner_never_starts_shadow():
    mod = load_module()
    mod._on_platform_connect(None, SimpleNamespace())
    assert mod._bridge is None and mod._bridge_task is None


def test_simultaneous_startup_and_fallback_use_one_ingress():
    mod = load_module()
    calls = []
    async def start(gateway):
        calls.append(gateway)
        await asyncio.sleep(0)
        return None
    async def run():
        gateway = object()
        with patch.object(mod, "start_bridge", start):
            mod._on_platform_connect(None, SimpleNamespace(gateway_runner=gateway))
            task = mod._bridge_task
            mod._on_pre_gateway_dispatch(gateway=gateway)
            mod._on_platform_connect(None, SimpleNamespace(gateway_runner=gateway))
            assert mod._bridge_task is task
            await task
        assert calls == [gateway]
    asyncio.run(run())
