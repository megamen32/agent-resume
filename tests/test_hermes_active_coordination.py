"""Focused integration: installed public native redirect + real plugin input ledger.
Expected 1s/max10s. No provider/model/Gateway/Telegram requests.
"""
import asyncio
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_hermes_tasks import setup, request, KEY, SID
from test_hermes_gateway import load_module
sys.path.insert(0,str(Path.home()/'.hermes/hermes-agent'))
from agent.interrupt_control import InterruptControlMixin

class NativeAgent(InterruptControlMixin):
    def __init__(self):
        self.session_id=SID;self._current_turn_id='native-turn'
        self._model_request_active=threading.Event();self._model_request_active.set()
        self._pending_redirect_lock=threading.RLock();self._pending_redirect=None
        self._interrupt_requested=False;self._executing_tools=False
        self._execution_thread_id=None;self._turn_user_intervened=False

def environment(tmp_path):
    mod=load_module();gateway,ctx,entry=setup(mod,tmp_path)
    mod._task_coordination_held=lambda *a:False
    agent=NativeAgent();state=SimpleNamespace(turn=SimpleNamespace(agent=agent,event=SimpleNamespace(_gateway_active_turn_token="gateway-token")),persistent=SimpleNamespace(approvals=None))
    gateway._peek_session_state=lambda key:state if key==KEY else None
    ctx.inject_message=lambda *a,**k:(_ for _ in ()).throw(AssertionError('idle/start injection forbidden'))
    async def bind():
        return (await request(mod,gateway,op='bind',name='task',cwd=str(tmp_path)))['session']['meta']['taskBinding']
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token'}):binding=asyncio.run(bind())
    entry.active_turn_token='gateway-token'
    mod._bridge=SimpleNamespace(gateway=gateway)
    body=dict(op='steer',session_key=KEY,input_id='once',message='exact active guidance',expected_binding=binding,
              expected_turn_token='gateway-token',expected_native_turn_id='native-turn',expected_native_session_id=SID)
    return mod,gateway,agent,entry,body

def test_real_native_redirect_and_receipt_only_repetition(tmp_path):
    mod,gateway,agent,entry,body=environment(tmp_path)
    async def run():
        home=await request(mod,gateway,op='session',session_key=KEY)
        assert home['session']['meta']['nativeActiveTurnId']=='native-turn'
        r=await request(mod,gateway,**body);assert r['admitted'] and r['turnId']=='native-turn'
        assert agent._pending_redirect=='exact active guidance'
        agent._model_request_active.clear();entry.active_turn_token=None
        assert (await request(mod,gateway,**body))['admitted']
        assert agent._pending_redirect=='exact active guidance'
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token'}):asyncio.run(run())

def test_native_completion_during_last_redirect_call_never_queues(tmp_path):
    mod,gateway,agent,entry,body=environment(tmp_path)
    original=agent.redirect
    def final_race(text):agent._model_request_active.clear();return original(text)
    agent.redirect=final_race
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token'}):
        receipt=asyncio.run(request(mod,gateway,**body));assert receipt['notSubmitted'] and not receipt.get('admitted')
        assert agent._pending_redirect is None and not agent._interrupt_requested
        assert asyncio.run(request(mod,gateway,**body))['nonRetryable']

def test_changed_turn_is_refused_and_tool_phase_uses_supported_transform(tmp_path):
    mod,gateway,agent,entry,body=environment(tmp_path)
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token'}):
        entry.active_turn_token='new-token'
        assert asyncio.run(request(mod,gateway,**body))['notSubmitted']
        entry.active_turn_token='gateway-token'
        agent._executing_tools=True
        receipt=asyncio.run(request(mod,gateway,**body));assert receipt['admitted'] and receipt['pending']
        assert mod._on_task_transform_tool_result(session_id=SID,turn_id='foreign',tool_call_id='tool',result='original') is None
        text=mod._on_task_transform_tool_result(session_id=SID,turn_id='native-turn',tool_call_id='tool',result='original')
        assert text.startswith('original') and 'exact active guidance' in text
        assert mod._on_task_transform_tool_result(session_id=SID,turn_id='native-turn',tool_call_id='tool',result='original') is None
    assert agent._pending_redirect is None

def test_supported_transform_is_consumed_by_the_installed_native_result_path(tmp_path):
    mod,gateway,agent,entry,body=environment(tmp_path);agent._executing_tools=True
    from agent.inline_tool_executors import apply_transform_tool_result
    import hermes_cli.lifecycle
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token'}):
        assert asyncio.run(request(mod,gateway,**body))['admitted']
        with patch.object(hermes_cli.lifecycle,'has_hook',lambda name:name=='transform_tool_result'),patch.object(hermes_cli.lifecycle,'invoke_hook',lambda name,**kw:[mod._on_task_transform_tool_result(**kw)]):
            output=apply_transform_tool_result(agent,function_name='terminal',function_args={},result='original effect result',effective_task_id='task',tool_call_id='real-tool')
            assert output.startswith('original effect result') and 'exact active guidance' in output
            assert apply_transform_tool_result(agent,function_name='terminal',function_args={},result='unchanged',effective_task_id='task',tool_call_id='next-tool')=='unchanged'

def test_tool_context_drops_stop_and_new_native_turn(tmp_path):
    mod,gateway,agent,entry,body=environment(tmp_path);agent._executing_tools=True
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token'}):
        assert asyncio.run(request(mod,gateway,**body))['admitted']
        agent._interrupt_requested=True
        assert mod._on_task_transform_tool_result(session_id=SID,turn_id='native-turn',tool_call_id='tool',result='old') is None
        agent._interrupt_requested=False;agent._current_turn_id='next-turn'
        assert mod._on_task_transform_tool_result(session_id=SID,turn_id='next-turn',tool_call_id='tool',result='new') is None
        assert agent._pending_redirect is None

def test_context_guard_rechecks_question_and_owner_after_ledger_lock(tmp_path):
    mod,gateway,agent,entry,body=environment(tmp_path);agent._executing_tools=True
    state=gateway._peek_session_state(KEY)
    with patch.dict(os.environ,{'AGENT_RESUME_HERMES_TOKEN':'test-token'}):
        assert asyncio.run(request(mod,gateway,**body))['admitted']
        def late_question(*a):state.persistent.approvals={'waiting':True};return False
        mod._task_coordination_held=late_question
        assert mod._on_task_transform_tool_result(session_id=SID,turn_id='native-turn',tool_call_id='tool',result='original') is None
        state.persistent.approvals=None;mod._task_coordination_held=lambda *a:True
        assert mod._on_task_transform_tool_result(session_id=SID,turn_id='native-turn',tool_call_id='tool',result='original') is None
        assert agent._pending_redirect is None
