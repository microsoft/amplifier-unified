"""Read-scope parity through real shared service routes, without model work."""
import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppService, AppError
from test_service import Runtime


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'native'))
    (tmp_path/'one').mkdir()
    (tmp_path/'two').mkdir()
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    rows=[service._new_session({'title':title,'workspace':str(tmp_path/space)})
          for title,space in [('Owner','one'),('Related','one'),('Other','two')]]
    for row in rows:
        row['messages']=[{'id':'message-'+row['id'],'role':'assistant','text':'Retained violet decision','inputId':'input'}]
    service.state['sessions']=rows
    service.state['selectedSessionId']=rows[0]['id']
    service.state['view']['draft']='Private unfinished work'
    service._publish()
    yield service
    await service.close()


async def action(app,name,args):
    return await app.app_bridge('dispatch',{'action':name,'args':args},app.state['sessions'][0]['id'])


async def test_list_wait_window_and_exact_message_use_one_scope(app):
    owner,related,other=app.state['sessions']
    before=copy.deepcopy(app.state['view'])
    found=(await action(app,'coordination.list',{}))['result']['items']
    assert {r['target']['sessionId'] for r in found}=={owner['id'],related['id']}
    with pytest.raises(AppError,match='scope'):
        await action(app,'coordination.list',{'workspace':other['workspace']})
    found=(await action(app,'coordination.list',{'workspace':other['workspace'],'scope':'all'}))['result']['items']
    assert [r['target']['sessionId'] for r in found]==[other['id']]
    loaded=AsyncMock(wraps=app.history.ensure_loaded)
    app.history.ensure_loaded=loaded
    denied=(await action(app,'coordination.wait',{'targets':[{'sessionId':other['id']}]}))['result']
    assert not denied['targets'] and 'scope' in denied['errors'][0]['error']
    loaded.assert_not_awaited()
    waited=(await action(app,'coordination.wait',{'targets':[{'sessionId':other['id']}],'scope':'all'}))['result']
    assert waited['targets'][0]['results'][0]['text']=='Retained violet decision'
    for exact in (False,True):
        args={'sessionId':other['id'],**({'messageId':other['messages'][0]['id']} if exact else {})}
        with pytest.raises(AppError,match='scope'):
            await action(app,'coordination.read',args)
        assert (await action(app,'coordination.read',{**args,'scope':'all'}))['accepted']
    assert app.state['view']==before and app.state['selectedSessionId']==owner['id']
    assert not app.runtime.sent


async def test_wait_rechecks_scope_after_target_moves(app):
    owner,related,_=app.state['sessions']
    async def move(sid):
        app._session(sid)['workspace']='/moved'
    app.history.ensure_loaded=move
    result=(await action(app,'coordination.wait',{'targets':[{'sessionId':related['id']}]}))['result']
    assert not result['targets'] and 'scope' in result['errors'][0]['error']


@pytest.mark.parametrize('name,key',[('coordination.read','sessionId'),('session.inspect','id')])
async def test_human_explicit_scope_is_honored_without_blocking_exact_ui_selection(app,name,key):
    other=app.state['sessions'][2]
    with pytest.raises(AppError,match='scope'):
        await app.dispatch(name,{key:other['id'],'scope':'workspace'})
    assert (await app.dispatch(name,{key:other['id'],'scope':'all'}))['accepted']
    assert (await app.dispatch(name,{key:other['id']}))['accepted']


@pytest.mark.parametrize('route',['history','recall'])
async def test_search_does_not_return_text_after_source_moves(app,monkeypatch,route):
    owner,related,_=app.state['sessions']
    if route=='history':
        import amplifier_web.history_query as history
        original=history._rows
        def moving(row):
            result=original(row)
            if row['id']==related['id']:
                related['workspace']='/moved-during-read'
            return result
        monkeypatch.setattr(history,'_rows',moving)
        result=await app.app_bridge('history',{'action':'search','query':'violet'},owner['id'])
        assert related['id'] not in {item['id'] for item in result['items']}
    else:
        await app.recall.refresh()
        original=app.recall.store.search
        def moving(*args,**kwargs):
            result=original(*args,**kwargs)
            related['workspace']='/moved-during-read'
            return result
        monkeypatch.setattr(app.recall.store,'search',moving)
        result=(await action(app,'recall.search',{'query':'violet'}))['result']
        assert related['id'] not in {item['sessionId'] for item in result['items']}


@pytest.mark.parametrize('route',['history','coordination','legacy','shared'])
async def test_history_catalog_and_read_alternate_routes_preserve_explicit_all(app,route):
    owner,_,other=app.state['sessions']
    if route=='history':
        async def read(scope):
            return await app.app_bridge('history',{'action':'read','session_id':other['id'],**scope},owner['id'])
    elif route=='coordination':
        async def read(scope):
            return await action(app,'coordination.read',{'sessionId':other['id'],**scope})
    elif route=='shared':
        async def read(scope):
            return await action(app,'history.shared.view',{'id':other['id'],'workspace':other['workspace'],**scope})
    else:
        async def read(scope):
            result=await action(app,'history.list',scope)
            assert (other['id'] in {r['id'] for r in result['result']['items']})==bool(scope)
            return result
        await read({})
        await read({'scope':'all'})
        return
    with pytest.raises((AppError,ValueError),match='scope'):
        await read({})
    assert await read({'scope':'all'})


async def test_recall_exact_read_carries_search_scope_and_revision(app):
    owner,_,other=app.state['sessions']
    await app.recall.refresh()
    searched=(await action(app,'recall.search',{'query':'violet','scope':'all'}))['result']
    match=next(r for r in searched['items'] if r['sessionId']==other['id'])
    assert match['reference']['scope']=='all'
    args={'sourceSessionId':other['id'],'messageId':match['messageId'],'sourceRevision':match['sourceRevision']}
    with pytest.raises(AppError,match='scope'):
        await action(app,'recall.read',args)
    assert (await action(app,'recall.read',{**args,'scope':'all'}))['result']['scope']=='all'
    other['messages'][0]['text']='Changed original'
    with pytest.raises(AppError,match='source changed'):
        await action(app,'recall.read',{**args,'scope':'all'})


@pytest.mark.parametrize('name,key,extra',[
    ('session.inspect','id',{}),('session.history','id',{}),('session.titlePreview','sessionId',{}),
    ('message.copy','sessionId',{'messageId':'unused'}),('conversation.delivery','sessionId',{'inputId':'input'}),
    ('history.export','sessionId',{}),
])
async def test_generic_agent_peer_history_reads_do_not_bypass_scope(app,name,key,extra):
    other=app.state['sessions'][2]
    with pytest.raises(AppError,match='scope'):
        await action(app,name,{key:other['id'],**extra})
    # All remains available; downstream ownership/transport/file checks remain
    # independent and need not succeed for an unavailable synthetic record.
    try:
        await action(app,name,{key:other['id'],**extra,'scope':'all'})
    except AppError as exc:
        assert 'requested history scope' not in str(exc)


async def test_all_does_not_grant_output_library_or_request_participation(app):
    owner,related,other=app.state['sessions']
    app.collaboration.insert('retained-request','fixture',{'commandAction':'coordination.send','requestId':'retained-request',
        'senderSessionId':related['id'],'target':{'sessionId':other['id']},'workspace':related['workspace']})
    app.db.commit()
    with pytest.raises(AppError,match='request and workspace'):
        await action(app,'coordination.result',{'requestId':'retained-request','scope':'all'})
    for scope in ({},{'scope':'all'}):
        with pytest.raises(AppError,match='Output actions must target'):
            await action(app,'outputs.list',{'sessionId':other['id'],**scope})


async def test_explicit_user_session_client_keeps_registered_history_access(tmp_path,authenticated_client,monkeypatch):
    from amplifier_web.server import create_app
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'native'))
    server=await create_app(tmp_path/'app',workspace=tmp_path,runtime=Runtime(),voice=False,background_updates=False,preload_providers=False)
    client=await authenticated_client(server)
    app=server['service']
    (tmp_path/'other').mkdir()
    owner=app._new_session({'title':'Owner'})
    other=app._new_session({'title':'Other','workspace':str(tmp_path/'other')})
    app.state['sessions']=[owner,other]
    app.state['selectedSessionId']=owner['id']
    app._publish()
    await client.post('/api/clients/attach',json={'clientId':'history-client','kind':'tui'})
    headers={'X-Amplifier-Client':'history-client'}
    target=f"/api/sessions/{other['id']}/commands"
    response=await client.post(target,headers=headers,json={'id':'read-target','action':'session.history','args':{}})
    assert response.status==200,await response.text()
    response=await client.post(target,headers=headers,json={'id':'bad-target','action':'session.history','args':{'id':owner['id'],'scope':'all'}})
    assert response.status==400
    assert not app.runtime.sent


async def test_dormant_scope_facts_do_not_decode_or_admit_owners(app, monkeypatch):
    from amplifier_web.owned_history import require, metadata
    owner, related, other = app.state['sessions']
    app._save()
    app.state['sessions'] = [owner]
    before = [row['id'] for row in app.state['sessions']]
    def forbidden(*args, **kwargs):
        pytest.fail('scope authorization decoded or admitted a dormant owner')
    monkeypatch.setattr(app.directory, 'decode', forbidden)
    monkeypatch.setattr(app, '_admit_session', forbidden)
    assert require(app, owner['id'], related['id']) == {'id':related['id'], 'workspace':related['workspace']}
    with pytest.raises(AppError, match='scope'):
        require(app, owner['id'], other['id'])
    assert require(app, owner['id'], other['id'], 'all')['id'] == other['id']
    assert require(app, related['id'], related['id'], 'task')['id'] == related['id']
    with pytest.raises(AppError, match='scope'):
        require(app, related['id'], owner['id'], 'task')
    with pytest.raises(AppError) as missing:
        metadata(app, 'missing-history')
    assert missing.value.status == 404
    assert [row['id'] for row in app.state['sessions']] == before


async def test_dormant_scope_filter_is_applied_before_history_paging(app, monkeypatch):
    from amplifier_web.history_query import query_history
    owner, related, other = app.state['sessions']
    app._save()
    app.state['sessions'] = [owner]
    monkeypatch.setattr(app, '_admit_session', lambda *a, **kw: pytest.fail('list admitted a dormant owner'))
    result = await query_history(app, {'action':'list','scope':'all','workspace':other['workspace'],'limit':1}, owner['id'])
    assert [row['id'] for row in result['items']] == [other['id']]
    assert result['next_offset'] is None
    with pytest.raises(ValueError, match='scope'):
        await query_history(app, {'action':'list','workspace':other['workspace']}, owner['id'])
    assert [row['id'] for row in app.state['sessions']] == [owner['id']]


async def test_denied_dispatch_does_not_admit_dormant_target(app, monkeypatch):
    owner, _, other = app.state['sessions']
    app._save()
    app.state['sessions'] = [owner]
    monkeypatch.setattr(app, '_admit_session', lambda *a, **kw: pytest.fail('denied request admitted target'))
    with pytest.raises(AppError, match='scope'):
        await app.app_bridge('dispatch', {'action':'session.inspect','args':{'id':other['id']}}, owner['id'])
    assert [row['id'] for row in app.state['sessions']] == [owner['id']]
