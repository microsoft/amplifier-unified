import copy
import json

import pytest

from amplifier_web.service import AppError
from amplifier_web.observation_presentation import candidate, permitted, request
from test_observations import fixture, create, record


def client(app, sid):
    app.clients.attach('original')
    with app.clients.bind('original'):
        queue = app.subscribe(sid)
        app._message(app._session(sid), 'user', 'Watch and bring up the exact accepted local result', 'chat', inputId='human-input', inputOrigin='ui')
        source = app._session(sid)['messages'][-1]['id']
        app.clients.save()
    return queue, source


def result(data, args, url='http://localhost:8765/result'):
    record(data/'record.json', 'actionable', 'accepted-1')
    value = json.loads((data/'record.json').read_text())
    value['presentation'] = {'kind':'browser','url':url,'title':'Exact recorded result', 'target':args['target'], 'evidence':value['evidence'][0]}
    (data/'record.json').write_text(json.dumps(value))
    return value


async def arm(app, sid, args, policy=None):
    queue, source = client(app, sid)
    args = {**args, 'sourceMessageId':source, 'presentationRequest':policy or {'kind':'browser','urlPolicy':'loopback-with-explicit-port'}}
    with app.clients.bind('original'):
        watch = await create(app, args)
    return watch, queue, args


async def test_exact_candidate_rechecked_and_opened_once_in_original_client(tmp_path, monkeypatch):
    app,runtime,now,sid,args,data,*_ = await fixture(tmp_path,monkeypatch)
    try:
        watch,_,args = await arm(app,sid,args)
        app.clients.attach('unrelated')
        app.clients.save()
        unrelated = copy.deepcopy(app.clients.records['unrelated'])
        app.clients.records['original']['drafts'][sid] = 'Keep this unsent draft'
        result(data,args)
        original = app.smart_tools.call_tool
        calls=[]
        async def observe(*a,**kw):
            calls.append(kw)
            value=await original(*a,**kw)
            if len(calls)==2: value['structuredContent']['evidence'].append({'uri':'fixture://heartbeat','revision':'new','digest':'b'*64})
            return value
        app.smart_tools.call_tool=observe
        await app.observations.tick(); now[0]+=16; await app.observations.tick()
        assert len(calls)==2 and runtime.observation_input.await_count==1
        out=app.observations.store.rows('outbox')[0]
        assert out['presentationPhase']=='opened' and out['phase']=='accepted'
        canvas=app.clients.records['original']['canvas']
        assert canvas['id']==out['canvasId'] and canvas['url']=='http://localhost:8765/result'
        assert canvas['sessionId']==sid and app.clients.records['original']['drafts'][sid]=='Keep this unsent draft'
        assert app.clients.records['unrelated']==unrelated
        args=runtime.observation_input.call_args.args[1]
        assert args['outcome']['presentationReceipt']=={'status':'opened','canvasId':canvas['id'],'reference':canvas['url']}
        assert app.observations.store.get('watch',watch['id'])['presentationGrant']['policy']['urlPolicy']=='loopback-with-explicit-port'
    finally: await app.close()


@pytest.mark.parametrize('change',['leave-return','disconnect','reconnect','source-selection','wrong-origin','dirty','expiry','cancel'])
async def test_presentation_races_preserve_original_selection_and_offer_reference(tmp_path,monkeypatch,change):
    app,runtime,now,sid,args,data,*_ = await fixture(tmp_path,monkeypatch)
    try:
        watch,queue,args=await arm(app,sid,args)
        result(data,args, 'http://192.168.1.5:8000/' if change=='wrong-origin' else 'http://localhost:8765/result')
        record_before=copy.deepcopy(app.clients.records['original'])
        if change=='leave-return':
            app.clients.records['original']['selectedSessionId']=None; app.clients.dirty.add('original'); app.clients.save()
            app.clients.records['original']['selectedSessionId']=sid; app.clients.dirty.add('original'); app.clients.save()
        elif change in {'disconnect','reconnect'}:
            app.unsubscribe(queue)
            if change=='reconnect':
                with app.clients.bind('original'): app.subscribe(sid)
        elif change=='dirty':
            row=app.clients.records['original']; row['canvas']['open']=True
            row['canvasViews']={'secondary':None,'preferences':{'primary:'+row['canvas']['id']:{'dirty':True}}}
            # Preserve a same-selection dirty change, independently of selection revision.
            watch=app.observations.store.get('watch',watch['id'])
            watch['presentationGrant']['selectionRevision']=app.clients.selection_revision('original')
            app.observations.store.put('watch',watch)
        original=app.smart_tools.call_tool
        calls=[]
        async def observe(*a,**kw):
            calls.append(1); value=await original(*a,**kw)
            if len(calls)==2:
                if change=='source-selection':
                    value['structuredContent']['presentation']['evidence']['revision']='reselected'
                elif change=='expiry': now[0]+=200
                elif change=='cancel':
                    current=app.observations.store.get('watch',watch['id'])
                    await app.dispatch('observation.cancel',{'sessionId':sid,'id':watch['id'],'expectedRevision':current['revision'],'requestId':'cancel'})
            return value
        app.smart_tools.call_tool=observe
        await app.observations.tick()
        out=app.observations.store.rows('outbox')[0]
        assert out.get('presentationPhase')=='skipped'
        assert app.clients.records['original']['canvas']['id']==record_before['canvas']['id']
        if runtime.observation_input.await_count:
            assert runtime.observation_input.call_args.args[1]['outcome']['presentationReceipt']['status']=='skipped'
    finally: await app.close()


async def test_exact_retry_cannot_upgrade_presentation_and_agent_cannot_choose_client(tmp_path,monkeypatch):
    app,_,_,sid,args,*_=await fixture(tmp_path,monkeypatch)
    try:
        _,source=client(app,sid)
        config={**args,'sourceMessageId':source,'presentationRequest':{'kind':'browser','urlOrigins':['https://exact.example']}}
        with app.clients.bind('original'):
            with pytest.raises(AppError,match='trusted input'):
                await app.dispatch('observation.preview',config,origin='agent')
        envelope={'action':'observation.preview','args':config,'_inputClients':['original'], '_inputBindings':[{'inputId':'human-input','clientId':'original'}]}
        preview=(await app.app_bridge('dispatch',envelope,sid))['result']
        payload={**config,'previewHash':preview['previewHash'],'requestId':'exact'}
        envelope.update(action='observation.create',args=payload)
        first=await app.app_bridge('dispatch',envelope,sid)
        assert first['result']['watch']['presentationGrant']['clientId']=='original'
        envelope['args']={**payload,'presentationRequest':{'kind':'browser','urlOrigins':['https://different.example']}}
        with pytest.raises(AppError,match='different intent'):
            await app.app_bridge('dispatch',envelope,sid)
    finally: await app.close()


async def test_unknown_after_canvas_claim_never_reopens(tmp_path,monkeypatch):
    app,runtime,now,sid,args,data,*_=await fixture(tmp_path,monkeypatch)
    try:
        await arm(app,sid,args); result(data,args)
        # Canvas persistence/publish can fail after the durable claim; no retry.
        monkeypatch.setattr(app,'_publish',lambda: (_ for _ in ()).throw(RuntimeError('crash after claim')))
        await app.observations.tick()
        out=app.observations.store.rows('outbox')[0]
        assert out['presentationPhase']=='unknown'
        before=app.clients.records['original']['canvas']['id']
        now[0]+=100; await app.observations.tick()
        assert app.clients.records['original']['canvas']['id']==before
        assert runtime.observation_input.await_count==1
        assert runtime.observation_input.call_args.args[1]['outcome']['presentationReceipt']['status']=='unknown'
    finally: await app.close()


def test_presentation_url_policy_and_null_candidate():
    assert request(None) is None and candidate(None,{}) is None
    policy=request({'kind':'browser','urlPolicy':'loopback-with-explicit-port'})
    for url in ['http://localhost:123/a','https://127.0.0.1:4/','http://[::1]:500/a']:
        assert permitted({'url':url},policy)
    for url in ['http://localhost/a','https://192.168.1.5:44/','https://localhost.example:88/']:
        assert not permitted({'url':url},policy)
    for value in [{'kind':'browser','urlOrigins':['https://*.example']},{'kind':'browser','urlOrigins':['http://user@localhost:3']},{'kind':'browser','urlOrigins':['https://example/path']}]:
        with pytest.raises(ValueError): request(value)


async def test_connection_uuid_and_deep_selection_snapshot_prevent_reviving_grants(tmp_path,monkeypatch):
    from amplifier_web.observation_presentation import check_grant
    app,_,_,sid,args,*_=await fixture(tmp_path,monkeypatch)
    try:
        watch,queue,_=await arm(app,sid,args)
        original=app.queue_tokens[queue]
        # Simulate exact Python queue-object reuse; subscription identity still changes.
        app.unsubscribe(queue)
        import uuid
        app.queues.add(queue); app.queue_clients[queue]='original'; app.queue_sessions[queue]=sid; app.queue_tokens[queue]=uuid.uuid4().hex
        assert app.queue_tokens[queue]!=original
        with pytest.raises(ValueError,match='connection or selection'): check_grant(app.observations,watch)
        app.clients.records['original']['canvasViews']={'secondary':{'id':'first'}}
        first=app.clients.selection_revision('original')
        app.clients.records['original']['canvasViews']['secondary']['id']='second'
        assert app.clients.selection_revision('original')==first+1
    finally: await app.close()
