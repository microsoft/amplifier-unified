import copy
import json

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.host.storage import SessionStore


@pytest.fixture
async def app(tmp_path):
    service=AppService(tmp_path/'data',workspace=tmp_path)
    await service.dispatch('session.create',{})
    yield service
    await service.close()


async def test_artifacts_survive_replacement_closed_tabs_reload_and_deleted_files(app,tmp_path):
    original=app._session()
    app._message(original,'user','Make visuals')
    path=tmp_path/'plan.md';path.write_text('# Old plan')
    await app.dispatch('canvas.show',{'kind':'auto','path':str(path)})
    first=copy.deepcopy(app.state['canvas'])
    await app.dispatch('canvas.show',{'kind':'html','title':'Demo','content':'<b>Two</b>'})
    second=copy.deepcopy(app.state['canvas'])
    assert len(app.state['canvasArtifacts'])==2
    assert all('content' not in a and 'surface' not in a for a in app.state['canvasArtifacts'])
    assert app.state['canvasArtifacts'][0]['messageId']==original['messages'][0]['id']
    path.unlink()
    await app.dispatch('canvas.tabClose',{'id':first['id']})
    await app.dispatch('canvas.close',{})
    restored=AppService(app.data_dir,workspace=tmp_path)
    try:
        assert len(restored.state['canvasArtifacts'])==2
        await restored.dispatch('canvas.select',{'id':first['id']})
        assert restored.state['canvas']['content']=='# Old plan'
        await restored.dispatch('canvas.select',{'id':second['id']})
        assert restored.state['canvas']['content']=='<b>Two</b>'
        await restored.dispatch('canvas.tabClose',{'id':second['id']})
        assert restored.state['canvas']['id']==first['id']
        await restored.dispatch('canvas.tabClose',{'id':first['id']})
        assert restored.state['canvas']['placeholder']
        assert not any(r['tabOpen'] for r in restored.state['canvasArtifacts'])
    finally:
        await restored.close()


async def test_chat_scope_background_agent_and_fork_boundary(app):
    source=app._session();app._message(source,'user','first')
    await app.dispatch('canvas.show',{'kind':'text','title':'First','content':'first snapshot'})
    first=app.state['canvas']['id']
    app._message(source,'assistant','done')
    app._message(source,'user','second')
    await app.dispatch('canvas.show',{'kind':'text','title':'Second','content':'future snapshot'})
    app._message(source,'assistant','done')
    await app.dispatch('session.fork',{'id':source['id'],'turn':1})
    fork=app._session()
    inherited=[r for r in app.state['canvasArtifacts'] if r['sessionId']==fork['id']]
    assert len(inherited)==1 and inherited[0]['title']=='First'
    assert app.state['canvas']['placeholder']
    with pytest.raises(AppError,match='another chat'):
        await app.dispatch('canvas.select',{'id':first})
    await app.app_bridge('dispatch',{'action':'canvas.show','args':{'kind':'text','title':'Background','content':'from original'}},source['id'])
    assert app.state['canvas']['placeholder']
    assert app.state['canvasArtifacts'][-1]['sessionId']==source['id']
    await app.dispatch('session.select',{'id':source['id']})
    assert app.state['canvas']['title']=='Background'
    await app.dispatch('canvas.select',{'id':first})
    assert app.state['canvas']['content']=='first snapshot'


async def test_browser_urls_validation_shared_controls_and_external_action(app):
    for url in ['javascript:alert(1)','file:///tmp/x','data:text/html,Hi','http://user:password@localhost:3000','http://localhost:bad','https://example.com/\nattack']:
        with pytest.raises(AppError,match='address'):
            await app.dispatch('canvas.show',{'kind':'browser','url':url})
    await app.dispatch('canvas.show',{'kind':'browser','url':'http://localhost:3000/demo','title':'App'})
    canvas=app.state['canvas']
    await app.dispatch('canvas.view',{'id':canvas['id'],'patch':{'reload':123}})
    result=await app.dispatch('canvas.openExternal',{'id':canvas['id']})
    assert result['effects'][0]['url']=='http://localhost:3000/demo'
    assert canvas['view']['reload']==123
    with pytest.raises(AppError,match='Only HTML'):
        await app.dispatch('canvas.snapshot',{'id':canvas['id'],'document':{'text':'no','controls':[]}})


async def test_old_successful_inline_artifacts_are_recovered_without_replaying_tools(app):
    session=app._session();app._message(session,'user','Create some diagrams');app._message(session,'assistant','Done')
    calls=[];receipts=[]
    for n in range(3):
        calls.append({'id':str(n),'tool':'app_control','arguments':{'operation':'dispatch','args':{'action':'canvas.show','args':{'kind':'markdown','title':f'Visual {n}','content':f'# {n}'}}}})
        receipts.append({'role':'tool','tool_call_id':str(n),'content':json.dumps({'output':{'accepted':n<2}})})
    SessionStore(app.data_dir/'sessions').save(session['id'],[{'role':'user','content':'Create some diagrams'},{'role':'assistant','tool_calls':calls},*receipts],{})
    app.state.pop('canvasLibraryMigration');app._save()
    restored=AppService(app.data_dir,workspace=app.default_workspace)
    try:
        assert restored.state['canvasLibraryMigration']['recovered']==2
        assert len(restored.state['canvasArtifacts'])==2
        assert all(not r['tabOpen'] and r['messageId']==session['messages'][0]['id'] for r in restored.state['canvasArtifacts'])
        await restored.dispatch('canvas.select',{'id':restored.state['canvasArtifacts'][0]['id']})
        assert restored.state['canvas']['content']=='# 0'
        from amplifier_web.canvas_library import recover_legacy
        recover_legacy(restored.state,restored.db,restored.data_dir)
        assert len(restored.state['canvasArtifacts'])==2
    finally:
        await restored.close()


async def test_saved_bodies_are_paged_on_demand_not_repeated_in_overview(app):
    body='artifact content '*30000
    for n in range(3):
        await app.dispatch('canvas.show',{'kind':'text','title':f'Large {n}','content':body})
    overview=await app.app_bridge('get_state',{},app._session()['id'])
    assert len(json.dumps(overview))<20000
    result=await app.app_bridge('get_state',{'path':'/canvasArtifacts/0/body/content','offset':0,'limit':100},app._session()['id'])
    assert result['value']==body[:100] and result['nextOffset']==100
    assert app.db.execute('SELECT count(*) FROM state_resources').fetchone()[0]==1
