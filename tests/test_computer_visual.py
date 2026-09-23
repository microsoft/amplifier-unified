"""Real shared action/capture engine; only pixels and native host are synthetic."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, Message
from amplifier_web.service import AppError, AppService
from amplifier_web.surface_delivery import SurfaceDelivery
from amplifier_web.voice_visual_delivery import VoiceVisualDelivery

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'

@pytest.fixture
async def computer(tmp_path):
    app = AppService(tmp_path/'app', workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    app.clients.attach('browser-one')
    app.clients.attach('browser-two')
    for identity in ('browser-one', 'browser-two'):
        with app.clients.bind(identity): app.subscribe()
    yield app, sid
    for queue in list(app.queues): app.unsubscribe(queue)
    await app.close()

async def grant(app, sid, client='browser-one'):
    with app.clients.bind(client):
        visual = app.computer_visual.for_client(sid)
        row = await visual.grant_source({'sessionId':sid, 'source':{'kind':'window','label':'Synthetic'}})
        return visual, row

async def begin(app, sid, client='browser-one', identity='capture', origin='agent'):
    args = {'sessionId':sid, 'clientId':client}
    if origin == 'agent':
        task = asyncio.create_task(app.app_bridge('dispatch', {'action':'computer.visual.capture','args':args,'id':identity}, sid))
    else:
        with app.clients.bind(client):
            task = asyncio.create_task(app.dispatch('computer.visual.capture', args, command_id=identity))
    visual = app.computer_visual.clients[client]
    for _ in range(100):
        if visual.pending or task.done(): break
        await asyncio.sleep(.001)
    return task

async def complete(app, visual, **patch):
    with app.clients.bind(visual.client_id):
        return await visual.complete({'sessionId':visual.session_id,'requestId':visual.pending['id'],
          'grantId':visual.pending['grantId'],'image':PNG,'capturedAt':time.time(),**patch})

async def test_shared_navigation_targets_calling_chat_without_consent_or_work(computer):
    app, sid = computer
    args = {'patch':{'runtimeDraft':{'section':'screen-source'}},'clientId':'browser-one'}
    await app.app_bridge('dispatch', {'action':'view.update','args':args}, sid)
    view = app.clients.records['browser-one']['view']
    assert view['panel'] == 'runtime' and view['runtimeDraft']['tab'] == 'computer'
    assert view['runtimeDraft']['revealRevision'] == 1
    assert not app.computer_visual.clients and app.voice_visual.grant is None
    assert app.clients.records['browser-two']['view'].get('panel') != 'runtime'
    with app.clients.bind('browser-one'):
        await app.dispatch('view.update', args)
        assert app.state['view']['runtimeDraft']['revealRevision'] == 2
        await app.dispatch('view.update', {'patch':{'runtimeDraft':{'tab':'tools'}}})
        assert app.state['view']['runtimeDraft']['section'] is None
        with pytest.raises(AppError, match='known Chat controls section'):
            await app.dispatch('view.update', {'patch':{'runtimeDraft':{'section':'body > button'}}})
    assert not app._session(sid)['messages']

async def test_agent_and_browser_cannot_borrow_wrong_client_or_chat(computer):
    app, sid = computer
    visual, row = await grant(app, sid)
    with pytest.raises(AppError, match='Multiple connected clients'):
        await app.app_bridge('dispatch', {'action':'computer.visual.capture'}, sid)
    with app.clients.bind('browser-two'), pytest.raises(AppError, match='cannot be borrowed'):
        await app.dispatch('computer.visual.capture', {'sessionId':sid,'clientId':'browser-one'})
    with app.clients.bind('browser-two'), pytest.raises(AppError, match='Only the browser'):
        await visual.grant_source({'sessionId':sid,'source':{'kind':'window','label':'Wrong'}})
    with app.clients.bind('browser-two'):
        await app.dispatch('session.create', {})
    for action, args in [('view.update',{'patch':{'runtimeDraft':{'section':'capture'}},'clientId':'browser-two'}),
                         ('computer.visual.capture', {'sessionId':sid,'clientId':'browser-two'})]:
        with pytest.raises(AppError, match='client displaying'):
            await app.app_bridge('dispatch', {'action':action,'args':args}, sid)
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action':'computer.visual.capture','args':{'sessionId':sid}}, 'foreign')
    assert visual.grant == row and visual.pending is None

async def test_nonvoice_capture_ui_and_agent_share_receipts_delivery_and_revoke(computer):
    app, sid = computer
    visual, row = await grant(app, sid)
    task = await begin(app, sid)
    await complete(app, visual)
    receipt = await task
    capture = receipt['result']
    assert capture['callId'] is None and capture['width'] == 1
    assert not app.voice_visual.grant and app.voice_service is None
    assert app._session(sid)['messages'][-1]['activityOnly']
    assert app._session(sid)['messages'][-1]['via'] == 'chat'
    assert app.computer_visual.read(sid, capture['id'])['_image'] == PNG
    # Actual delivery wrapper consumes a matching direct tool receipt and
    # revalidates the separate computer grant before transport.
    async def bridge(operation, args): return await app.app_bridge(operation,args,sid)
    delivery = VoiceVisualDelivery(SurfaceDelivery(bridge),bridge)
    stable = delivery.remember(receipt)
    delivery.surfaces.image_capabilities.supports = lambda *args: asyncio.sleep(0, result=True)
    request = ChatRequest(messages=[Message(role='tool',name='app_control',tool_call_id='tool',content=json.dumps(stable))])
    result = await delivery.prepare(request, object())
    assert any(getattr(block,'type',None)=='image' for block in result.messages[-1].content)
    with app.clients.bind('browser-one'):
        await app.dispatch('computer.visual.revoke', {'sessionId':sid})
    result = await delivery.revalidate(result)
    assert 'No image was delivered' in result.messages[-1].content
    assert visual.grant is None

@pytest.mark.parametrize('end', ['switch','disconnect','delete'])
async def test_scope_end_invalidates_inflight_and_late_callbacks_even_after_return(computer, end):
    app, sid = computer
    visual, row = await grant(app, sid)
    task = await begin(app, sid)
    pending = copy.copy(visual.pending)
    with app.clients.bind('browser-one'):
        if end == 'switch':
            await app.dispatch('session.create', {})
            await app.dispatch('session.select', {'id':sid})
        elif end == 'disconnect':
            for queue in list(app.queues):
                if app.queue_clients.get(queue) == 'browser-one': app.unsubscribe(queue)
        else:
            app._state['sessions'] = [s for s in app._state['sessions'] if s['id'] != sid]
            app.clients.reconcile('browser-one')
    with pytest.raises(AppError, match='permission ended'):
        await task
    with app.clients.bind('browser-one'), pytest.raises(AppError, match='changed chats or disconnected'):
        await visual.complete({'sessionId':sid,'requestId':pending['id'],'grantId':row['id'],'image':PNG,'capturedAt':time.time()})
    assert not visual.receipts

async def test_ui_snapshot_only_binds_next_input_and_client_resume_never_copies_consent(computer):
    app, sid = computer
    visual, row = await grant(app, sid)
    task = await begin(app, sid, origin='ui')
    await complete(app, visual); await task
    app.clients.attach('resumed', resume='browser-one')
    with app.clients.bind('resumed'):
        assert not app.browser_state()['computerVisual']['available']
        app.computer_visual.bind_input(sid,'wrong-client')
    assert 'inputId' not in app._session(sid)['messages'][-1]
    with app.clients.bind('browser-one'):
        app.computer_visual.bind_input(sid,'next-text')
    assert app._session(sid)['messages'][-1]['inputId'] == 'next-text'
    # Ending voice has no power to create or extend nonvoice permission.
    await app.set_voice_status({'status':'ended'})
    assert visual.grant == row
    app.computer_visual.reconcile('browser-one',disconnect=True)
    assert not app.computer_visual.project('browser-one')['available']

async def test_native_grant_inflight_cannot_survive_switch_away_and_back(computer):
    app, sid = computer
    entered, finish = asyncio.Event(), asyncio.Event()
    async def native(operation):
        entered.set(); await finish.wait()
        return {'available':True}
    with app.clients.bind('browser-one'):
        visual = app.computer_visual.for_client(sid)
        visual.native = SimpleNamespace(run=native)
        from amplifier_web.host_identity import local_host_identity
        task = asyncio.create_task(visual.grant_source({'sessionId':sid,'source':{'kind':'native-foreground','hostId':local_host_identity()['id'],'hostInstanceId':app.instance_id}}))
        await entered.wait()
        await app.dispatch('session.create', {})
        await app.dispatch('session.select', {'id':sid})
        finish.set()
        with pytest.raises(AppError, match='changed chats or disconnected'): await task
    assert visual.grant is None

async def test_same_source_and_inflight_capture_survive_voice_start_and_end(computer):
    app, sid = computer
    visual, row = await grant(app, sid)
    task = await begin(app, sid)
    call = SimpleNamespace(id='call',session_id=sid,client_id='browser-one',closed=False,closing=False)
    app.voice_service = SimpleNamespace(call=call)
    try:
        await app.set_voice_status({'id':'call','sessionId':sid,'status':'connected'})
        assert visual.grant == row and not visual.detached
        with app.clients.bind('browser-one'):
            assert app.computer_visual.for_client(sid) is visual
            assert app.browser_state()['computerVisual']['available']
        await complete(app, visual)
        receipt = await task
        await app.set_voice_status({'status':'ended'})
        assert visual.grant == row
        assert app.computer_visual.read(sid, receipt['result']['id'])['_image'] == PNG
        assert not app.voice_visual.grant
        with app.clients.bind('browser-one'):
            await app.dispatch('computer.visual.revoke', {'sessionId':sid})
        assert visual.grant is None
    finally:
        app.voice_service = None

async def test_source_can_be_chosen_during_voice_and_remains_after_call(computer):
    app, sid = computer
    call = SimpleNamespace(id='call',session_id=sid,client_id='browser-one',closed=False,closing=False)
    app.voice_service = SimpleNamespace(call=call)
    try:
        await app.set_voice_status({'id':'call','sessionId':sid,'status':'connected'})
        visual, row = await grant(app, sid)
        task = await begin(app, sid, origin='ui')
        await complete(app, visual); await task
        with app.clients.bind('browser-two'):
            app.computer_visual.bind_input(sid, 'wrong-voice-browser')
        assert 'inputId' not in app._session(sid)['messages'][-1]
        with app.clients.bind('browser-one'):
            # Same binding used by the ordinary voice_delegate path.
            app.computer_visual.bind_input(sid, 'spoken-input')
        assert app._session(sid)['messages'][-1]['inputId'] == 'spoken-input'
        await app.set_voice_status({'status':'ended'})
        assert visual.grant == row
        visual.grant['expiresAt'] = time.time()-1
        assert not app.computer_visual.project('browser-one')['available']
    finally:
        app.voice_service = None
