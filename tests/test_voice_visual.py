import asyncio
import base64
import copy
import json
import time
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, Message, ToolSpec

from amplifier_web.service import AppError, AppService
from amplifier_web.surface_delivery import SurfaceDelivery
from amplifier_web.voice_visual_delivery import VoiceVisualDelivery

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'

@pytest.fixture
async def visual(tmp_path):
    app = AppService(tmp_path/'app', workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    app.clients.attach('browser-one')
    call = SimpleNamespace(id='call-one', session_id=sid, client_id='browser-one', closed=False, closing=False)
    app.voice_service = SimpleNamespace(call=call)
    await app.set_voice_status({'id':call.id, 'sessionId':sid, 'status':'connected'})
    yield app, {'sessionId':sid, 'callId':call.id}
    app.voice_service = None
    await app.close()

async def grant(app, target):
    with app.clients.bind('browser-one'):
        return await app.voice_visual.grant_source({**target, 'source':{'kind':'window','label':'Synthetic fixture'}})

async def begin(app, target, *, agent=False, identity='capture-one'):
    if agent:
        task = asyncio.create_task(app.app_bridge('dispatch', {'action':'voice.visual.capture','args':target,'id':identity}, target['sessionId']))
    else:
        task = asyncio.create_task(app.dispatch('voice.visual.capture', target, command_id=identity))
    for _ in range(100):
        if app.voice_visual.pending or task.done(): break
        await asyncio.sleep(.001)
    return task

async def complete(app, target, **extra):
    pending = app.voice_visual.pending
    with app.clients.bind('browser-one'):
        return await app.voice_visual.complete({**target,'requestId':pending['id'],'grantId':pending['grantId'],
                    'capturedAt':time.time(),'image':PNG,**extra})

async def test_grant_only_call_owner_and_cross_session_agent_guard(visual):
    app, target = visual
    with pytest.raises(AppError, match='browser owning'):
        await app.voice_visual.grant_source({**target,'source':{'kind':'window','label':'Wrong'}})
    app.clients.attach('other')
    with app.clients.bind('other'), pytest.raises(AppError, match='browser owning'):
        await app.voice_visual.grant_source({**target,'source':{'kind':'window','label':'Wrong'}})
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch',{'action':'voice.visual.capture','args':target},'other-session')
    assert not (await app.dispatch('voice.visual.status', target))['result']['available']

async def test_capture_is_saved_bounded_evidence_without_navigation_draft_or_work(visual):
    app, target = visual
    await grant(app,target)
    app.state['view']['draft']='Unsent thought'
    selected=app.state['selectedSessionId']; messages=len(app._session(selected)['messages'])
    task=await begin(app,target)
    await complete(app,target)
    result=await task; capture=result['result']
    assert result['accepted'] and capture['nativeForeground'] is False
    assert capture['source']['reportedBy']=='browser' and capture['width']==1
    assert PNG not in json.dumps(result)
    assert app.voice_visual.read(selected,capture['id'])['_image']==PNG
    assert app.state['selectedSessionId']==selected and app.state['view']['draft']=='Unsent thought'
    assert len(app._session(selected)['messages'])==messages+1
    assert app._session(selected)['messages'][-1]['activityOnly'] is True
    assert await app.dispatch('voice.visual.capture',target,command_id='capture-one')==result
    assert app.voice_visual.pending is None

async def test_ending_call_invalidates_inflight_and_late_callback(visual):
    app,target=visual
    await grant(app,target); task=await begin(app,target)
    pending=copy.copy(app.voice_visual.pending)
    await app.set_voice_status({'status':'ending'})
    with pytest.raises(AppError,match='permission ended'): await task
    with app.clients.bind('browser-one'), pytest.raises(AppError,match='connected voice call'):
        await app.voice_visual.complete({**target,'requestId':pending['id'],'grantId':pending['grantId'],'image':PNG,'capturedAt':time.time()})
    assert not app._session(target['sessionId'])['messages']

@pytest.mark.parametrize('payload',[{'image':'Safety stop: user present'}, {'image':base64.b64encode(b'{"error":"halted"}').decode()}, {'capturedAt':0}, {'grantId':'replaced'}])
async def test_error_stale_and_nonimage_inputs_never_saved(visual,payload):
    app,target=visual
    await grant(app,target);task=await begin(app,target)
    with pytest.raises(AppError): await complete(app,target,**payload)
    app.voice_visual.revoke()
    with pytest.raises(AppError): await task
    assert not app._session(target['sessionId'])['messages']

async def test_error_delivery_does_not_invent_image_and_request_not_replayed(visual):
    app,target=visual
    await grant(app,target);task=await begin(app,target)
    await complete(app,target,error='Screen permission denied')
    with pytest.raises(AppError,match='permission denied'): await task
    with pytest.raises(AppError,match='Nothing was replayed'):
        await app.dispatch('voice.visual.capture',target,command_id='capture-one')

@pytest.mark.parametrize('serialization',['direct','model_dump','observed_loop_envelope','kernel_hooks'])
async def test_agent_receipt_delivers_typed_pixels_only_while_exact_and_fresh(visual,serialization):
    from amplifier_core import ToolResult, HookRegistry, HookResult
    app,target=visual
    await grant(app,target);task=await begin(app,target,agent=True);await complete(app,target);receipt=await task
    async def bridge(operation,args): return await app.app_bridge(operation,args,target['sessionId'])
    receipt['result']['capturedAt']=1789888171.4219217
    receipt['result']['receivedAt']=1789888171.4219217
    delivery=VoiceVisualDelivery(SurfaceDelivery(bridge),bridge);receipt=delivery.remember(receipt)
    assert not {'capturedAt','receivedAt','attachment'}.intersection(receipt['result'])
    result=ToolResult(success=True,output=receipt)
    content=(result.get_serialized_output() if serialization=='direct' else
             json.dumps(result.model_dump(exclude={'success'} if serialization=='observed_loop_envelope' else set())))
    if serialization=='kernel_hooks':
        hooks=HookRegistry()
        async def unchanged(event,data):return HookResult()
        hooks.register('tool:post',unchanged,name='pass-through')
        emitted=await hooks.emit('tool:post',{'tool_name':'app_control','result':result.model_dump()})
        content=json.dumps(emitted.data['result'])
    request=ChatRequest(messages=[Message(role='tool',name='app_control',tool_call_id='one',content=content)],tools=[ToolSpec(name='app_control',parameters={})])
    provider=SimpleNamespace(get_info=lambda:SimpleNamespace(capabilities=['vision']))
    prepared=await delivery.prepare(request,provider)
    assert prepared.messages[-1].content[-1].type=='image'
    assert prepared.messages[-1].content[-1].source['data']==PNG
    assert len(request.messages)==1
    for key in ['id','sessionId','callId','grantId','sha256','width']:
        changed=copy.deepcopy(receipt);changed['result'][key]='changed'
        forged=request.model_copy(update={'messages':[Message(role='tool',name='app_control',tool_call_id='one',content=json.dumps(changed))]})
        assert len((await delivery.prepare(forged,provider)).messages)==1
    denied=ToolResult(success=False,output=receipt,error={'message':'denied'})
    for envelope in [denied.model_dump(),denied.model_dump(exclude={'success'})]:
        failure=request.model_copy(update={'messages':[Message(role='tool',name='app_control',tool_call_id='one',content=json.dumps(envelope))]})
        assert len((await delivery.prepare(failure,provider)).messages)==1
    without=await delivery.prepare(request.model_copy(update={'messages':[]}),provider)
    assert not without.messages
    no_vision=await delivery.prepare(request,SimpleNamespace(get_info=lambda:SimpleNamespace(capabilities=[])))
    assert all(b.type!='image' for b in no_vision.messages[-1].content)
    await app.set_voice_status({'status':'ended'})
    stale=await delivery.prepare(request,provider)
    assert all(b.type!='image' for b in stale.messages[-1].content)
    assert 'unavailable' in stale.messages[-1].content[0].text

async def test_next_voice_input_consumes_explicit_capture_once_and_cross_session_read_denied(visual):
    app,target=visual
    await grant(app,target);task=await begin(app,target);await complete(app,target);capture=(await task)['result']
    with pytest.raises(AppError,match='another conversation'):
        app.voice_visual.read('other',capture['id'])
    app.voice_visual.bind_input(target['sessionId'],'spoken-request')
    assert app._session(target['sessionId'])['messages'][-1]['inputId']=='spoken-request'
    app.voice_visual.bind_input(target['sessionId'],'second-request')
    assert app._session(target['sessionId'])['messages'][-1]['inputId']=='spoken-request'

async def test_restart_does_not_restore_consent_or_replay_pending_request(visual):
    app,target=visual
    await grant(app,target)
    app.db.execute('INSERT INTO commands VALUES (?,?,?)',('interrupted',json.dumps(['voice.visual.capture',target['sessionId'],target['callId']]),json.dumps({'accepted':False,'status':'pending'})))
    app._save()
    await app.close()
    reopened=AppService(app.data_dir,workspace=app.default_workspace)
    try:
        assert reopened.voice_visual.grant is None
        with pytest.raises(AppError,match='Nothing was replayed'):
            await reopened.dispatch('voice.visual.capture',target,command_id='interrupted')
        assert reopened.voice_visual.pending is None
    finally: await reopened.close()

async def test_png_magic_header_without_real_pixels_is_rejected(visual):
    app,target=visual
    await grant(app,target);task=await begin(app,target)
    fake=base64.b64encode(base64.b64decode(PNG)[:33]+b'Safety halt, not pixels').decode()
    with pytest.raises(AppError,match='fresh PNG'): await complete(app,target,image=fake)
    app.voice_visual.revoke()
    with pytest.raises(AppError): await task

async def test_source_replacement_invalidates_pending_capture(visual):
    app,target=visual
    first=await grant(app,target);task=await begin(app,target)
    await grant(app,target)
    with pytest.raises(AppError,match='permission ended'): await task
    assert app.voice_visual.grant['id']!=first['id']

async def test_agent_capture_does_not_resend_to_later_voice_input(visual):
    app,target=visual
    await grant(app,target);task=await begin(app,target,agent=True);await complete(app,target);await task
    app.voice_visual.bind_input(target['sessionId'],'unrelated-next-request')
    assert 'inputId' not in app._session(target['sessionId'])['messages'][-1]

async def test_budget_cached_pixels_revalidated_before_provider_transport(visual):
    from amplifier_web.surface_delivery import SurfaceProvider
    app,target=visual
    await grant(app,target);task=await begin(app,target,agent=True);await complete(app,target);receipt=await task
    async def bridge(operation,args):return await app.app_bridge(operation,args,target['sessionId'])
    delivery=VoiceVisualDelivery(SurfaceDelivery(bridge),bridge);receipt=delivery.remember(receipt)
    request=ChatRequest(messages=[Message(role='tool',name='app_control',tool_call_id='one',content=json.dumps(receipt))],tools=[ToolSpec(name='app_control',parameters={})])
    class Provider:
        def get_info(self):return SimpleNamespace(capabilities=['vision'])
        async def complete(self,request):return request
        async def request_budget(self,request):return request
    provider=SurfaceProvider(Provider(),delivery)
    planned=await provider.request_budget(request)
    assert planned.messages[-1].content[-1].type=='image'
    await app.set_voice_status({'status':'ending'})
    sent=await provider.complete(request)
    assert isinstance(sent.messages[-1].content,str) and 'No image was delivered' in sent.messages[-1].content
