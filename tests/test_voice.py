from pathlib import Path
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from amplifier_web.voice import ProviderError, VoiceCall, VoiceError, VoiceService, compact_context, should_fallback

class Service:
    def __init__(self):
        self.state = {"selectedSessionId": "main", "sessions": [{"id": "main", "workspace": str(Path.cwd()), "messages": [], "workers": []}], "view": {"mode": "chat"}, "voice": {}}
        self.transcripts, self.calls, self.statuses = [], [], []
        self.result = asyncio.Event()
        self.lock = asyncio.Lock()
    def _publish(self): self.statuses.append(dict(self.state['voice']))
    def get_state(self): return self.state
    async def set_voice_status(self, status): self.statuses.append(status)
    async def record_voice_transcript(self, role, text, **kwargs): self.transcripts.append((role, text, kwargs))
    async def voice_delegate(self, text, command_id, session_id=None):
        self.calls.append((text, command_id, session_id))
        return {"accepted": True, "inputId": command_id}
    async def wait_for_response(self, session_id, input_id=None, timeout=600):
        await self.result.wait()
        return "Verified work result"

class Socket:
    closed = False
    def __init__(self): self.sent, self.stopped = [], asyncio.Event()
    async def send_json(self, event): self.sent.append(event)
    async def close(self): self.closed = True; self.stopped.set()
    def __aiter__(self): return self
    async def __anext__(self): await self.stopped.wait(); raise StopAsyncIteration

@pytest.mark.parametrize("status,code,expected", [(403,"",True),(404,"model_not_found",True),(400,"unsupported_model",True),(400,"invalid_value",False),(401,"invalid_api_key",False),(429,"rate_limit_exceeded",False),(500,"server_error",False)])
def test_fallback_only_model_availability(status,code,expected):
    assert should_fallback(ProviderError(status,code)) is expected

async def test_live_uses_client_delegation_and_separate_endpoint():
    service, socket = Service(), Socket()
    http = SimpleNamespace(ws_connect=AsyncMock(return_value=socket))
    manager = VoiceService(service, api_key="not-a-real-key", http=http)
    manager.request = AsyncMock(return_value=({"session":{"id":"live_1"},"transport":{"sdp":"answer"}},"",{}))
    call = VoiceCall(manager,"main")
    result = await call.create("v=0","live")
    request = manager.request.call_args
    assert request.args == ("POST","/live/sessions")
    assert request.kwargs['json']['session']['delegation'] == {"type":"client"}
    assert request.kwargs['json']['session']['model'] == 'gpt-live-1'
    assert http.ws_connect.call_args.args[0].endswith('/live/sessions/live_1/attach')
    assert result['sdp'] == 'answer' and 'not-a-real-key' not in str(result)
    call.final.set()
    await call.close()

async def test_realtime_uses_multipart_and_call_sideband():
    socket = Socket()
    manager = VoiceService(Service(),api_key='secret',http=SimpleNamespace(ws_connect=AsyncMock(return_value=socket)))
    manager.request = AsyncMock(return_value=({},'answer',{'Location':'/v1/realtime/calls/call_1'}))
    call = VoiceCall(manager,'main')
    result = await call.create('v=0','realtime')
    assert manager.request.call_args.args == ('POST','/realtime/calls')
    assert 'data' in manager.request.call_args.kwargs
    assert manager.http.ws_connect.call_args.args[0].endswith('realtime?call_id=call_1')
    assert result['model'] == 'gpt-realtime-2.1'
    await call.close()
    assert manager.request.call_args.args == ('POST','/realtime/calls/call_1/hangup')

async def test_automatic_fallback_is_honest_and_pins_conversation(monkeypatch):
    manager = VoiceService(Service(),api_key='secret',http=object())
    attempted=[]
    async def create(self,sdp,provider):
        attempted.append(provider)
        if provider=='live': raise ProviderError(403,'model_access_denied')
        self.id='call_1'
        return {'id':self.id,'sdp':'answer','provider':provider,'sessionId':self.session_id}
    monkeypatch.setattr(VoiceCall,'create',create)
    result=await manager.connect('v=0')
    assert attempted==['live','realtime'] and result['sessionId']=='main'
    assert 'unavailable' in result['fallbackReason']
    with pytest.raises(VoiceError,match='already active'): await manager.connect('v=0')

async def test_auth_failure_never_silently_changes_model(monkeypatch):
    manager=VoiceService(Service(),api_key='secret',http=object())
    create=AsyncMock(side_effect=ProviderError(401,'invalid_api_key'))
    monkeypatch.setattr(VoiceCall,'create',create)
    with pytest.raises(ProviderError): await manager.connect('v=0')
    assert create.call_count==1

async def test_no_key_and_no_session_fail_before_network():
    manager=VoiceService(Service(),api_key='',http=object())
    with pytest.raises(VoiceError,match='OPENAI_API_KEY'): await manager.connect('v=0')
    manager.api_key='secret';manager.service.state['selectedSessionId']=None
    with pytest.raises(VoiceError,match='conversation'): await manager.connect('v=0')

async def test_transcripts_persist_without_execution_and_are_deduplicated():
    service=Service();call=VoiceCall(VoiceService(service),'main');call.id='live_1'
    event={'type':'session.input_transcript.delta','delta':'Please check','end_ms':100,'event_id':'first'}
    await call.handle(event);await call.handle(event)
    await call.handle({**event,'event_id':'second','delta':' the code.','end_ms':200})
    assert len(service.transcripts)==2 and service.calls==[]
    assert service.transcripts[1][2]['append'] is True
    assert call.user_text=='Please check the code.'

async def test_end_call_keeps_already_delegated_work_running():
    service,socket=Service(),Socket();call=VoiceCall(VoiceService(service),'main')
    call.id,call.socket='live_1',socket
    await call.record('user','Review my code','u1')
    call.background(call.delegate_live('d1'));await asyncio.sleep(0)
    assert service.calls[0][2]=='main'
    task=next(iter(call.tasks));call.final.set()
    result=await call.close()
    assert result['workContinues'] is True and not task.cancelled() and not task.done()
    service.result.set();await task
    assert not any(e['type']=='session.commentary.append' for e in socket.sent)

async def test_delegation_waits_for_matching_result_and_does_not_repeat():
    service,socket=Service(),Socket();call=VoiceCall(VoiceService(service),'main');call.id,call.socket='live_1',socket
    await call.record('user','Review my code','u1')
    task=asyncio.create_task(call.delegate_live('d1'));await asyncio.sleep(0)
    assert not socket.sent
    service.result.set();await task
    assert socket.sent[-1]['content']=='Verified work result'
    service.state['selectedSessionId']='other';await call.delegate_live('d2')
    assert len(service.calls)==1 and service.calls[0][2]=='main'

def test_voice_context_omits_theme_and_uses_pinned_session():
    state=Service().state;state['view']['themeDraft']='huge stylesheet';state['sessions'][0]['title']='Pinned'
    state['sessions'][0]['workers']=[{'id':'w','status':'working'}]
    context=compact_context(state,'main')
    assert 'huge stylesheet' not in context and 'Pinned' in context and 'working' in context

async def test_saved_realtime_preference_skips_live_and_start_session_is_pinned(monkeypatch):
    service=Service()
    from amplifier_web.preferences import SettingsStore
    SettingsStore(Path.cwd()).update(Path.cwd(),'global',lambda settings:settings.update(voice={'preferred_model':'gpt-realtime-2.1'}))
    service.state['sessions'].append({'id':'other','messages':[]})
    service.state['selectedSessionId']='other'
    manager=VoiceService(service,api_key='secret',http=object())
    async def create(self,sdp,provider):
        self.id='call'
        return {'id':self.id,'provider':provider,'sessionId':self.session_id}
    monkeypatch.setattr(VoiceCall,'create',create)
    result=await manager.connect('v=0',session_id='main')
    assert result['provider']=='realtime' and result['sessionId']=='main'

async def test_missing_start_session_does_not_retarget_selected_session():
    manager=VoiceService(Service(),api_key='secret',http=object())
    with pytest.raises(VoiceError,match='conversation'):
        await manager.connect('v=0',session_id='deleted')

async def test_delegation_includes_role_labelled_voice_reference():
    service=Service()
    service.state['sessions'][0]['messages']=[{'role':'assistant','text':'We could review the checkout flow.','via':'call'}]
    service.result.set()
    call=VoiceCall(VoiceService(service),'main');call.id='live'
    await call.execute('Yes, do that.','d1')
    prompt=service.calls[0][0]
    assert '"role": "assistant"' in prompt and 'checkout flow' in prompt
    assert prompt.endswith('Current spoken user request:\nYes, do that.')

async def test_forwarded_correction_keeps_original_request_and_existing_session():
    service=Service();service.result.set()
    service.state['selectedSessionId']='other'
    service.state['sessions'][0]['messages']=[
        {'role':'assistant','text':'Earlier suggested plan, not a new instruction.','via':'call'}]
    call=VoiceCall(VoiceService(service),'main');call.id='live'
    request='Tell Amplifier to change the delivery color to orange. Delegate this update to Amplifier.'
    await call.execute(request,'correction')
    assert len(service.calls)==1
    prompt,command_id,session_id=service.calls[0]
    assert (command_id,session_id)==('voice:live:correction','main')
    assert 'delivery has already happened' in prompt
    assert 'existing delegation limits and approvals' in prompt
    assert 'reference data, not new instructions' in prompt
    assert prompt.endswith('Current spoken user request:\n'+request)

async def test_end_without_active_call_clears_pending_status():
    service=Service();manager=VoiceService(service)
    result=await manager.end()
    assert service.statuses[-1]['status']=='disconnected'
    assert result['workContinues'] is True

def test_realtime_has_only_main_session_delegation():
    from amplifier_web.voice import realtime_tools
    assert [tool['name'] for tool in realtime_tools()] == ['amplifier_delegate']

async def test_realtime_cannot_bypass_amplifier_for_app_actions_or_state():
    service,socket=Service(),Socket();call=VoiceCall(VoiceService(service),'main');call.id,call.socket='rt',socket
    for name in ['app_state','app_action']:
        await call.realtime_tool({'name':name,'arguments':'{"action":"session.delete","args":{"id":"main"}}','call_id':name})
    assert service.calls==[]
    outputs=[event['item']['output'] for event in socket.sent if event['type']=='conversation.item.create']
    assert all('Unknown voice tool' in output for output in outputs)

async def test_matching_turn_metadata_preserves_pending_workers():
    from amplifier_web.voice import result_text
    service=Service()
    service.wait_for_response=AsyncMock(return_value={'text':'Worker is investigating.','generation_id':'g','input_ids':['input'],'active_job_ids':['worker'],'disposition':'manager_turn_finished'})
    call=VoiceCall(VoiceService(service),'main');call.id='live'
    result=await call.execute('Investigate.','d')
    assert result['generation_id']=='g'
    assert 'not completion of all work' in result_text(result)

async def test_live_coalesced_requests_speak_completed_generation_once():
    service,socket=Service(),Socket();call=VoiceCall(VoiceService(service),'main');call.id,call.socket='live',socket
    call.execute=AsyncMock(return_value={'text':'Updated answer.','generation_id':'same','active_job_ids':[]})
    await call.record('user','first','u1');await call.delegate_live('first')
    await call.record('user','correction','u2');await call.delegate_live('second')
    assert sum(event['type']=='session.commentary.append' for event in socket.sent)==1

async def test_background_generation_updates_are_not_replayed_or_treated_as_tasks():
    service,socket=Service(),Socket()
    service.state['sessions'][0]['generations']=[{'generation_id':'old','event':'generation.finished','text':'Old'}]
    call=VoiceCall(VoiceService(service),'main');call.id,call.socket='live',socket
    service.state['sessions'][0]['generations'] += [
        {'generation_id':'working','event':'generation.started'},
        {'generation_id':'new','event':'generation.finished','text':'Result ready.','input_ids':['worker-observation'],'active_job_ids':[]},
        {'generation_id':'voice','event':'generation.finished','text':'Direct answer.','input_ids':['voice:live:d1']},
    ]
    await call.announce_generations(service.state);await call.announce_generations(service.state)
    spoken=[event['content'] for event in socket.sent if event['type']=='session.commentary.append']
    assert spoken==['Amplifier manager update. Result ready.']
    assert service.calls==[]

async def test_realtime_completion_waits_until_model_and_playback_are_idle():
    service,socket=Service(),Socket();call=VoiceCall(VoiceService(service),'main');call.id,call.socket='rt',socket
    call.provider='realtime';call.realtime_pending_response=True;call.realtime_responding=True;call.realtime_playing=True
    await call.handle({'type':'response.done'})
    assert socket.sent==[]
    await call.handle({'type':'output_audio_buffer.stopped'})
    assert socket.sent[0]['type']=='response.create'
    assert socket.sent[0]['response']['tool_choice']=='none'

async def test_followup_enters_main_session_while_earlier_response_is_running():
    service,socket=Service(),Socket();call=VoiceCall(VoiceService(service),'main');call.id,call.socket='live',socket
    await call.record('user','first','u1');first=asyncio.create_task(call.delegate_live('d1'));await asyncio.sleep(0)
    await call.record('user','change direction','u2');second=asyncio.create_task(call.delegate_live('d2'));await asyncio.sleep(0)
    assert len(service.calls)==2 and service.calls[0][1]!=service.calls[1][1]
    assert all(row[2]=='main' for row in service.calls)
    service.result.set();await asyncio.gather(first,second)

async def test_live_delegation_waits_for_late_transcript_instead_of_losing_request():
    service,socket=Service(),Socket();service.result.set()
    call=VoiceCall(VoiceService(service),'main');call.id,call.socket='live',socket
    task=asyncio.create_task(call.delegate_live('early-event'));await asyncio.sleep(0)
    assert service.calls==[]
    await call.record('user','Check the active work.','u1')
    await task
    assert len(service.calls)==1 and 'Check the active work.' in service.calls[0][0]

async def test_interleaved_live_transcripts_keep_role_and_delivery_order():
    service=Service();call=VoiceCall(VoiceService(service),'main');call.id='live'
    await call.record('user','Please ',delta=True,at=100)
    await call.record('assistant','Okay',delta=True,at=110)
    await call.record('user','check it.',delta=True,at=120)
    assert [row[0] for row in service.transcripts]==['user','assistant','user']
    assert service.transcripts[0][2]['item_id']==service.transcripts[2][2]['item_id']
    assert service.transcripts[1][2]['item_id']!=service.transcripts[0][2]['item_id']
    assert call.user_text=='Please check it.'

async def test_observer_catches_generation_finished_during_call_handshake():
    service,socket=Service(),Socket();queue=asyncio.Queue()
    service.subscribe=lambda:queue;service.unsubscribe=lambda value:None
    call=VoiceCall(VoiceService(service),'main');call.id,call.socket='live',socket
    service.state['sessions'][0]['generations']=[{'generation_id':'during-connect','event':'generation.finished','text':'Finished while connecting.','input_ids':['worker-report']}]
    observer=asyncio.create_task(call.observe());await asyncio.sleep(0)
    assert any('Finished while connecting.' in event.get('content','') for event in socket.sent)
    observer.cancel();await asyncio.gather(observer,return_exceptions=True)

async def test_coalesced_realtime_results_return_all_tool_outputs_but_speak_once():
    import json
    service,socket=Service(),Socket();call=VoiceCall(VoiceService(service),'main');call.id,call.socket='rt',socket
    call.execute=AsyncMock(return_value={'generation_id':'same','text':'One final answer.','active_job_ids':[]})
    event={'name':'amplifier_delegate','arguments':'{"text":"request"}','call_id':'first'}
    await call.realtime_tool(event)
    await call.realtime_tool({**event,'call_id':'second'})
    await call.handle({'type':'response.done'})
    assert sum(row['type']=='response.create' for row in socket.sent)==1
    outputs=[row['item'] for row in socket.sent if row['type']=='conversation.item.create']
    assert len(outputs)==2 and json.loads(outputs[1]['output'])['status']=='already_reported'

async def test_close_blocks_new_delegation_but_keeps_existing_work():
    service,socket=Service(),Socket();call=VoiceCall(VoiceService(service),'main');call.id,call.socket='live',socket
    closing=asyncio.create_task(call.close());await asyncio.sleep(0)
    assert call.closing and not call.closed
    await call.handle({'type':'session.delegation.created','delegation':{'id':'late','target':'client'}})
    assert not call.tasks
    with pytest.raises(VoiceError,match='no new work'):
        await call.execute('late request','late')
    call.final.set();await closing
    assert service.calls==[]

async def test_background_results_stay_pinned_when_user_selects_another_session():
    service,socket=Service(),Socket();call=VoiceCall(VoiceService(service),'main');call.id,call.socket='live',socket
    service.state['selectedSessionId']='other'
    service.state['sessions'].append({'id':'other','generations':[{'generation_id':'foreign','event':'generation.finished','text':'Wrong conversation.'}]})
    service.state['sessions'][0]['generations']=[{'generation_id':'pinned','event':'generation.finished','text':'Pinned answer.'}]
    await call.announce_generations(service.state)
    assert ''.join(row.get('content','') for row in socket.sent)=='Amplifier manager update. Pinned answer.'


def test_empty_completed_turn_never_claims_success_or_silently_disappears():
    from amplifier_web.voice import result_text
    assert 'does not confirm' in result_text({'text':'','generation_id':'g','active_job_ids':[]})

async def test_real_service_bridge_ignores_provisional_text_and_keeps_pinned_session(tmp_path):
    from amplifier_web.service import AppService
    class Runtime:
        def __init__(self): self.started=asyncio.Event();self.release=asyncio.Event();self.sid=None
        async def send(self,session,text,input_id,emit):
            self.sid=session['id']
            await emit('assistant.message',{'sessionId':self.sid,'inputId':input_id,'text':'Starting the tool.'})
            self.started.set();await self.release.wait()
            await emit('runtime.generation',{'sessionId':self.sid,'event':'generation.finished','generation_id':'verified-turn','input_ids':[input_id],'text':'The manager answer.','active_job_ids':['still-running'],'disposition':'manager_turn_finished'})
        async def close(self): pass
    runtime=Runtime();service=AppService(tmp_path,runtime,workspace=tmp_path)
    await service.dispatch('session.create',{});pinned=service.get_state()['selectedSessionId']
    socket=Socket();call=VoiceCall(VoiceService(service),pinned);call.id,call.socket='live',socket
    await service.dispatch('session.create',{})
    await call.record('user','Check my work.','u1')
    delegated=asyncio.create_task(call.delegate_live('d1'));await runtime.started.wait()
    assert socket.sent==[] and runtime.sid==pinned
    runtime.release.set();await delegated
    spoken=''.join(row.get('content','') for row in socket.sent)
    assert 'The manager answer.' in spoken and 'Starting the tool' not in spoken
    assert 'not completion of all work' in spoken
    call.final.set();await call.close();await service.close()

async def test_provider_finalization_releases_call_without_cancelling_work():
    service,socket=Service(),Socket();manager=VoiceService(service)
    call=VoiceCall(manager,'main');manager.call=call;call.id,call.socket='live',socket
    await call.handle({'type':'session.closed','usage':{'duration':3}})
    await asyncio.gather(*list(call.tasks))
    assert call.closed and socket.closed and call.final.is_set()
    assert call.close_result['workContinues'] is True

async def test_graceful_end_delivers_pending_live_result_before_closing():
    service, socket = Service(), Socket()
    call = VoiceCall(VoiceService(service), 'main')
    call.id, call.socket = 'live_1', socket
    await call.record('user', 'End our call', 'u1')
    call.background(call.delegate_live('d1'))
    await asyncio.sleep(0)
    ending = asyncio.create_task(call.prepare_end())
    await asyncio.sleep(0)
    assert call.ending and not call.closing and not socket.closed
    assert not any(event['type'] == 'session.close' for event in socket.sent)
    # New work is rejected, while the already admitted answer can still arrive.
    await call.handle({'type': 'session.delegation.created', 'delegation': {'id': 'd2'}})
    assert 'd2' not in call.delegations
    service.result.set()
    result = await ending
    assert result['closing'] and result['maxDrainMs'] > 0
    assert socket.sent[-1]['type'] == 'session.commentary.append'
    assert not socket.closed
    call.final.set()
    await call.close()
    assert socket.closed and call.end_timeout.cancelled()


async def test_graceful_realtime_end_waits_for_result_without_suppressing_response():
    service, socket = Service(), Socket()
    manager = VoiceService(service)
    manager.request = AsyncMock(return_value=({}, '', {}))
    call = VoiceCall(manager, 'main')
    call.id, call.socket, call.provider = 'call_1', socket, 'realtime'
    call.background(call.realtime_tool({'call_id': 'd1', 'name': 'amplifier_delegate', 'arguments': '{"text":"End call"}'}))
    await asyncio.sleep(0)
    ending = asyncio.create_task(call.prepare_end())
    await asyncio.sleep(0)
    service.result.set()
    await ending
    assert any(event['type'] == 'conversation.item.create' for event in socket.sent)
    assert any(event['type'] == 'response.create' for event in socket.sent)
    manager.request.assert_not_called()
    await call.close()


async def test_graceful_end_timeout_keeps_backend_work_running(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.GRACEFUL_END_SECONDS', 0.03)
    service = Service()
    manager = VoiceService(service)
    manager.request = AsyncMock(return_value=({}, '', {}))
    call = VoiceCall(manager, 'main')
    call.id, call.socket, call.provider = 'call_1', Socket(), 'realtime'
    work = asyncio.create_task(service.result.wait())
    call.tasks.add(work)
    await call.prepare_end()
    await call.end_timeout
    assert call.closed and not work.cancelled() and not work.done()
    service.result.set()
    await work


async def test_immediate_end_preempts_prepare_and_rejects_stale_call():
    service = Service()
    manager = VoiceService(service)
    manager.request = AsyncMock(return_value=({}, '', {}))
    call = manager.call = VoiceCall(manager, 'main')
    call.id, call.socket, call.provider = 'call_1', Socket(), 'realtime'
    work = asyncio.create_task(service.result.wait())
    call.tasks.add(work)
    preparing = asyncio.create_task(manager.end('call_1', graceful=True))
    await asyncio.sleep(0)
    await asyncio.wait_for(manager.end('call_1'), 0.2)
    assert call.closed and not work.cancelled()
    with pytest.raises(VoiceError, match='no longer active'):
        await manager.end('previous_call', graceful=True)
    service.result.set()
    await work
    await preparing


async def test_deadline_cleanup_cannot_end_a_replacement_call(monkeypatch):
    service = Service()
    async def status(value):
        service.state['voice'].update(value)
    service.set_voice_status = status
    manager = VoiceService(service, api_key='fixture', http=object())
    manager.request = AsyncMock(return_value=({}, '', {}))
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowSocket(Socket):
        async def close(self):
            entered.set()
            await release.wait()
            await super().close()
    old = manager.call = VoiceCall(manager, 'main')
    old.id, old.provider, old.socket = 'old-call', 'realtime', SlowSocket()
    async def create(self, sdp, provider):
        self.id = 'new-call'
        return {'id': self.id, 'sdp': 'answer', 'provider': provider, 'sessionId': self.session_id}
    monkeypatch.setattr(VoiceCall, 'create', create)
    monkeypatch.setattr('amplifier_web.shared_settings.read_settings', lambda *args, **kwargs: {'voice': {}})
    cleanup = asyncio.create_task(old._end_after_deadline())
    replacement = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert old.closed
        replacement = asyncio.create_task(manager.connect('v=0', 'realtime'))
        await asyncio.sleep(0)
        assert not replacement.done()
        release.set()
        await asyncio.wait_for(asyncio.gather(cleanup, replacement), 1)
        assert manager.call.id == 'new-call'
        assert service.state['voice']['id'] == 'new-call'
        assert service.state['voice']['status'] == 'connecting'
    finally:
        release.set()
        await asyncio.gather(cleanup, *([replacement] if replacement else []), return_exceptions=True)
