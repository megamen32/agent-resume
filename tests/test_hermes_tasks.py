"""Focused integration: real supported plugin injection, native ACK vs slow LLM.

Expected 3s, maximum 15s. No Telegram/provider network calls.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_hermes_gateway import load_module

KEY = "agent:main:telegram:group:-1004360838692:9"
SID = "20260909_220106_70ff6a6b"


def setup(mod, tmp_path):
    sys.path.insert(0, os.environ.get("HERMES_SOURCE", str(Path.home()/".hermes/hermes-agent")))
    entry = SimpleNamespace(session_id=SID, model_override={"model": "glm-5.3-flash", "provider": "zai"}, active_turn_token=None,
                            origin=SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id="-1004360838692", thread_id="9"))
    async def lookup(key):
        return entry if key == KEY else None
    gateway = SimpleNamespace(async_session_store=SimpleNamespace(lookup_by_session_key=lookup),
                              _plugin_injection_accepting=lambda: True, _is_session_running=lambda key: False)
    async def get_session(sid):
        assert sid == SID
        return {"model": "glm-5.3-flash", "model_config": json.dumps({"gateway_runtime": {"provider": "zai", "fallback_active": False}})}
    gateway._session_db = SimpleNamespace(get_session=get_session)
    ctx = SimpleNamespace(_manager=SimpleNamespace(home_path=tmp_path, _cli_ref=None))
    mod._ctx = ctx
    mod._task_settings = lambda: {"session_key": KEY, "model": "MiniMax-M3", "cwd": str(tmp_path)}
    return gateway, ctx, entry


def request(mod, gateway, **body):
    return mod.handle_task_request(body, authorization="Bearer test-token", gateway=gateway)


def test_native_ack_is_fast_and_duplicate_survives_restart(tmp_path):
    mod = load_module(); gateway, ctx, entry = setup(mod, tmp_path)
    calls = []
    def inject(content, *, session_key):
        calls.append((content, session_key))
        # The real agent prologue fires this BEFORE the slow model response.
        mod._on_task_pre_llm_call(session_id=SID, turn_id="native-turn", user_message=content, model="glm-5.3-flash")
        return True
    ctx.inject_message = inject
    async def run():
        bound = await request(mod, gateway, op="bind", name="repair", cwd=str(tmp_path))
        assert bound["session"]["id"] == KEY
        assert bound["session"]["meta"]["nativeSessionId"] == SID
        assert bound["session"]["model"] == "glm-5.3-flash"
        assert (await request(mod,gateway,op="session",session_key=SID))["session"]["id"]==KEY
        assert (await request(mod,gateway,op="session",session_key="foreign-SID"))["notSubmitted"]
        body = dict(op="send", session_key=KEY, input_id="input-1", message="Исправь только выбранную причину", expected_binding=bound["session"]["meta"]["taskBinding"])
        receipt = await asyncio.wait_for(request(mod, gateway, **body), 1)
        assert receipt["admitted"] is True and receipt["completed"] is False
        assert receipt["turnId"] == "native-turn"
        assert receipt["model"] is None and receipt["modelAtAdmission"] == "glm-5.3-flash"
        assert receipt["selectedModel"] == "glm-5.3-flash"
        assert await request(mod, gateway, **body) == receipt
        restarted = load_module(); setup(restarted, tmp_path)[1].inject_message = lambda *a, **k: (_ for _ in ()).throw(AssertionError("replay"))
        assert await request(restarted, gateway, **body) == receipt
        found = await request(restarted, gateway, op="find", name="repair", cwd=str(tmp_path))
        assert found["session"]["id"] == KEY
        conflict=await request(mod, gateway, **{**body, "message": "different"})
        assert conflict["nonRetryable"] and conflict["admitted"]
    with patch.dict(os.environ, {"AGENT_RESUME_HERMES_TOKEN": "test-token"}): asyncio.run(run())
    assert len(calls) == 1


def test_scheduling_true_is_unknown_and_never_replayed(tmp_path):
    mod = load_module(); gateway, ctx, entry = setup(mod, tmp_path)
    calls = []
    ctx.inject_message = lambda content, **kw: calls.append(content) or True
    mod._TASK_ACK_SECONDS = .01
    async def run():
        bound = await request(mod, gateway, op="bind", name="repair", cwd=str(tmp_path))
        body = dict(op="send", session_key=KEY, input_id="lost", message="work", expected_binding=bound["session"]["meta"]["taskBinding"])
        receipt = await request(mod, gateway, **body)
        assert receipt["ok"] is False and receipt["admissionUnknown"] and receipt["nonRetryable"]
        assert not receipt["admitted"] and receipt["completed"] is False
        await request(mod, gateway, **body)
        other = await request(mod, gateway, **{**body, "input_id": "second"})
        assert other["notSubmitted"] is True
        mod._on_task_pre_llm_call(session_id=SID, turn_id="observed", user_message=calls[0], model="glm-5.3-flash")
        settled = await request(mod, gateway, op="receipt", session_key=KEY, input_id="lost")
        assert settled["admitted"] and settled["turnId"] == "observed"
    with patch.dict(os.environ, {"AGENT_RESUME_HERMES_TOKEN": "test-token"}): asyncio.run(run())
    assert len(calls) == 1


def test_auth_target_cli_and_active_writer_fail_before_injection(tmp_path):
    mod = load_module(); gateway, ctx, entry = setup(mod, tmp_path)
    ctx.inject_message = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not inject"))
    async def run():
        denied = await mod.handle_task_request({"op": "session"}, authorization="wrong", gateway=gateway)
        assert denied["notSubmitted"] and denied["error"] == "unauthorized"
        bound = await request(mod, gateway, op="bind", name="repair", cwd=str(tmp_path))
        body = dict(op="send", session_key=KEY, input_id="i", message="work", expected_binding=bound["session"]["meta"]["taskBinding"])
        assert (await request(mod, gateway, **{**body, "session_key": "foreign"}))["notSubmitted"]
        entry.active_turn_token = "busy"
        assert (await request(mod, gateway, **body))["notSubmitted"]
        entry.active_turn_token = None; ctx._manager._cli_ref = object()
        assert (await request(mod, gateway, **body))["notSubmitted"]
    with patch.dict(os.environ, {"AGENT_RESUME_HERMES_TOKEN": "test-token"}): asyncio.run(run())


def test_supported_context_and_gateway_dispatch_preserve_topic_and_native_id(tmp_path):
    core = Path(os.environ.get("HERMES_SOURCE", str(Path.home()/".hermes/hermes-agent")))
    sys.path.insert(0, str(core))
    from hermes_cli.plugins import PluginContext
    from gateway.run_plugin_injection import GatewayPluginInjectionMixin
    from gateway.session import SessionSource
    from gateway.config import Platform
    mod = load_module(); gateway, ctx, entry = setup(mod, tmp_path)
    entry.origin = SessionSource(platform=Platform.TELEGRAM, chat_id="-1004360838692", chat_type="group", thread_id="9", user_id="540308572")
    events = []
    class Host(GatewayPluginInjectionMixin):
        _plugin_injection_accepting = lambda self: True
        _plugin_injection_authorized = lambda self, *a, **kw: True
        _plugin_injection_scope = lambda self, source: __import__('contextlib').nullcontext()
        _restored_source = lambda self, e: e.origin
        _delivery_adapter_for = lambda self, source: SimpleNamespace(handle_message=self.handle)
        async def handle(self, event):
            events.append(event)
            mod._on_task_pre_llm_call(session_id=SID, turn_id="real-prologue", user_message=event.text, model="glm-5.3-flash")
            await asyncio.sleep(10)  # LLM remains running after native ACK.
    async def run():
        host=Host(); host.async_session_store=gateway.async_session_store
        tasks=[]
        def schedule(**kw):
            tasks.append(asyncio.create_task(host._dispatch_plugin_message_injection(session_key=kw['session_key'], content=kw['content'], plugin_id=kw['plugin_id'])))
            return True
        ctx.plugin_id='agent-resume'; ctx._gateway_injection_allowed=lambda: True
        ctx._manager.has_tui_message_injector=False;ctx._manager.has_gateway_message_injector=True;ctx._manager.inject_gateway_message=schedule
        ctx.inject_message=PluginContext.inject_message.__get__(ctx)
        bound=await request(mod,gateway,op='bind',name='repair',cwd=str(tmp_path))
        receipt=await asyncio.wait_for(request(mod,gateway,op='send',session_key=KEY,input_id='supported',message='Ремонт',expected_binding=bound['session']['meta']['taskBinding']),1)
        assert receipt['admitted'] and not tasks[0].done()
        event=events[0]
        assert event.source.thread_id == '9' and event.metadata['gateway_session_strict']
        assert event.metadata['gateway_session_id'] == SID and not event.allow_gateway_control
        tasks[0].cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    with patch.dict(os.environ, {"AGENT_RESUME_HERMES_TOKEN": "test-token"}):asyncio.run(run())


def test_existing_authenticated_http_ingress_ack_does_not_wait_for_llm(tmp_path):
    import socket
    import aiohttp
    mod=load_module();gateway,ctx,entry=setup(mod,tmp_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    async def run():
        turns=[]
        async def turn(content):
            await asyncio.sleep(.05)
            mod._on_task_pre_llm_call(session_id=SID,turn_id='http-native',user_message=content,model='glm-5.3-flash')
            await asyncio.sleep(5)
        def inject(content,**kw):
            assert kw['session_key']==KEY
            turns.append(asyncio.create_task(turn(content)));return True
        ctx.inject_message=inject
        bridge=await mod.start_bridge(gateway)
        assert bridge is not None
        url=f'http://127.0.0.1:{port}/v1/agent-resume/task'
        try:
            async with aiohttp.ClientSession(headers={'Authorization':'Bearer test-token'}) as client:
                async with client.post(url,json={'op':'bind','name':'http-repair','cwd':str(tmp_path)}) as r:
                    bound=await r.json();assert bound['session']['id']==KEY
                body={'op':'send','session_key':KEY,'input_id':'http-one','message':'Работа','expected_binding':bound['session']['meta']['taskBinding']}
                async with asyncio.timeout(1):
                    async with client.post(url,json=body) as r: receipt=await r.json()
                assert receipt['admitted'] and receipt['turnId']=='http-native' and not receipt['completed']
                assert not turns[0].done()
                async with client.post(url,json=body) as r: assert await r.json()==receipt
                async with client.post(url,json={'op':'receipt','session_key':KEY,'input_id':'http-one'}) as r:
                    assert (await r.json())['admitted']
            assert len(turns)==1
        finally:
            for t in turns:t.cancel()
            await asyncio.gather(*turns,return_exceptions=True)
            await bridge.runner.cleanup();mod._bridge=None
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token','AGENT_RESUME_HERMES_PORT':str(port)}):asyncio.run(run())


def test_atomic_binding_generation_fences_a_b_cached_a_and_aba(tmp_path):
    mod=load_module();gateway,ctx,entry=setup(mod,tmp_path)
    a=tmp_path/'A';b=tmp_path/'B';a.mkdir();b.mkdir();calls=[]
    def inject(content,**kw):
        calls.append(content)
        mod._on_task_pre_llm_call(session_id=SID,turn_id=str(len(calls)),user_message=content,model='glm-5.3-flash')
        return True
    ctx.inject_message=inject
    async def run():
        bound_a=(await request(mod,gateway,op='bind',name='A',cwd=str(a)))['session']['meta']['taskBinding']
        body={'op':'send','session_key':KEY,'input_id':'A-first','message':'A','expected_binding':bound_a}
        assert (await request(mod,gateway,**body))['admitted']
        bound_b=(await request(mod,gateway,op='bind',name='B',cwd=str(b)))['session']['meta']['taskBinding']
        assert (await request(mod,gateway,**{**body,'input_id':'B-first','message':'B','expected_binding':bound_b}))['admitted']
        from tools.terminal_tool import get_session_cwd
        assert get_session_cwd(SID)==str(b)
        stale=await request(mod,gateway,**{**body,'input_id':'A-distinct'})
        assert stale['notSubmitted'] and not stale.get('admissionUnknown')
        assert get_session_cwd(SID)==str(b) and len(calls)==2
        assert (await request(mod,gateway,op='receipt',session_key=KEY,input_id='A-distinct'))['state']=='not_found'
        current=(await request(mod,gateway,op='session',session_key=KEY))['session']['meta']['taskBinding']
        assert current==bound_b
        assert (await request(mod,gateway,**{**body,'input_id':'B-follow','expected_binding':current}))['admitted']
        assert len(calls)==3 and get_session_cwd(SID)==str(b)
        new_a=(await request(mod,gateway,op='bind',name='A',cwd=str(a)))['session']['meta']['taskBinding']
        assert new_a['generation']!=bound_a['generation']
        assert (await request(mod,gateway,**{**body,'input_id':'ABA-stale'}))['notSubmitted']
        # Original admission is still query-only/replay-safe after binding changes.
        assert (await request(mod,gateway,**body))['admitted'] and len(calls)==3
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token'}):asyncio.run(run())


def test_effective_fallback_is_exact_turn_evidence_not_selected_or_later_session(tmp_path):
    """Real ingress + official post-response producer; expected 1s/max 5s.

    Defect: prologue/selected GLM claimed as actual despite MiniMax fallback.
    Session runtime is last-session evidence; legacy input must stay unknown.
    """
    import socket
    import aiohttp
    from agent.turn_response_intake import _fire_post_api_request_hook
    mod=load_module();gateway,ctx,entry=setup(mod,tmp_path)
    runtime={"model":"MiniMax-M3", "model_config":json.dumps({"gateway_runtime":{"provider":"minimax","fallback_active":True}})}
    async def get_session(sid):
        assert sid==SID
        return runtime
    gateway._session_db.get_session=get_session
    calls=[]
    def inject(content,**kw):
        calls.append(content)
        mod._on_task_pre_llm_call(session_id=SID,turn_id='fallback-turn',user_message=content,model='glm-5.3-flash')
        return True
    ctx.inject_message=inject
    # This official producer reads agent.model/provider AFTER fallback; no response body retained.
    agent=SimpleNamespace(session_id=SID,platform='telegram',model='MiniMax-M3',provider='minimax',base_url='unused',api_mode='chat_completions',
        _api_response_payload_for_hook=lambda *a,**k:{},_usage_summary_for_api_request_hook=lambda *a:{})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    async def run():
        bridge=await mod.start_bridge(gateway)
        try:
            async with aiohttp.ClientSession(headers={'Authorization':'Bearer test-token'}) as client:
                url=f'http://127.0.0.1:{port}/v1/agent-resume/task'
                async def post(body):
                    async with client.post(url,json=body) as response:
                        assert response.status==200
                        return await response.json()
                home=(await post({'op':'home'}))['session']
                assert home['model']=='MiniMax-M3' and home['meta']['selectedModel']=='glm-5.3-flash'
                assert home['meta']['selectedProvider']=='zai' and home['meta']['effectiveModel']['provider']=='minimax'
                bound=(await post({'op':'bind','name':'fallback','cwd':str(tmp_path)}))['session']
                body={'op':'send','session_key':KEY,'input_id':'fallback-one','message':'Read only','expected_binding':bound['meta']['taskBinding']}
                admitted=await post(body)
                assert admitted['admitted'] and admitted['pending'] and not admitted['completed']
                assert admitted['model'] is None and admitted['modelAtAdmission']=='glm-5.3-flash'
                with patch('hermes_cli.lifecycle.has_hook',return_value=True), patch('hermes_cli.lifecycle.invoke_hook',side_effect=lambda name,**kw:mod._on_task_post_api_request(**kw)), patch('agent.conversation_loop._moa_reference_metrics_for_hook',return_value=[]):
                    _fire_post_api_request_hook(agent,SimpleNamespace(model='provider-alias'),SimpleNamespace(content='done',tool_calls=[]),'stop',api_messages=[],api_call_count=1,api_duration=.01,api_start_time=0,api_request_id='test',effective_task_id='task',turn_id='fallback-turn')
                settled=await post({'op':'receipt','session_key':KEY,'input_id':'fallback-one'})
                assert settled['model']=='MiniMax-M3' and settled['selectedModel']=='glm-5.3-flash'
                assert settled['effectiveModel']=={'model':'MiniMax-M3','provider':'minimax','source':'native-post-api-request','turnId':'fallback-turn'}
                assert settled['pending'] and not settled['completed']
                # A later human turn / other native SID cannot overwrite this input's evidence.
                mod._on_task_post_api_request(session_id=SID,turn_id='human-followup',model='other',provider='other')
                mod._on_task_post_api_request(session_id='foreign',turn_id='fallback-turn',model='other',provider='other')
                runtime['model']='later-model'
                assert await post(body)==settled and len(calls)==1
                assert (await post({'op':'home'}))['session']['model']=='later-model'
                restarted=load_module();setup(restarted,tmp_path)
                assert await request(restarted,gateway,op='receipt',session_key=KEY,input_id='fallback-one')==settled
                # Historical receipt has only prologue model: never fill actual from latest home.
                with mod._task_db() as db:
                    db.execute("UPDATE inputs SET effective_model=NULL,effective_provider=NULL,selected_model=NULL WHERE input_id='fallback-one'")
                legacy=await post({'op':'receipt','session_key':KEY,'input_id':'fallback-one'})
                assert legacy['model'] is None and legacy['effectiveModel'] is None
                assert legacy['modelAtAdmission']=='glm-5.3-flash' and legacy['admitted']
                gateway._session_db=None
                missing=(await post({'op':'home'}))['session']
                assert missing['model'] is None and missing['meta']['selectedModel']=='glm-5.3-flash'
                assert len(calls)==1
        finally:
            await bridge.runner.cleanup();mod._bridge=None
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token','AGENT_RESUME_HERMES_PORT':str(port)}):asyncio.run(run())
