"""Agent and detail reads page durable owners before reading their bodies."""
import pytest

from amplifier_web.agent_state import read_state
from amplifier_web.directory_state import _ranked
from amplifier_web.native_catalog import NativeCatalog
from amplifier_web.session_files import amplifier_home
from test_automatic_history import app_factory
from test_native_directory import seed


async def test_agent_state_pages_27000_rows_and_resolves_global_pointer_without_admission(app_factory,tmp_path,monkeypatch):
    app=app_factory()
    catalog=NativeCatalog(app.data_dir/'native-catalog.sqlite3',amplifier_home())
    _, workspace=seed(catalog,tmp_path/'archive')
    app._state['workspaces'].append(workspace)
    app.directory.bind(catalog)
    context=app.state_context()
    assert context['sessions']==[]
    decoded=[]
    from amplifier_web.history_directory import HistoryDirectory
    original=HistoryDirectory.decode
    monkeypatch.setattr(HistoryDirectory,'decode',lambda self,row:(decoded.append(1),original(self,row))[1])
    page=read_state(context,{'path':'/sessions','offset':26980,'limit':5},directory=app.directory)
    assert page['total']==27000 and page['nextOffset']==26985
    assert [row['key'] for row in page['items']]==list(range(26980,26985))
    assert len(decoded)==5
    pointer=page['items'][3]['path']
    title=page['items'][3]['value']['title']
    assert read_state(context,{'path':pointer+'/title'},directory=app.directory)['value']==title
    assert len(decoded)==6
    last = read_state(context, {'path': '/sessions/26999/id'}, directory=app.directory)['value']
    assert read_state(context, {'path': '/sessions/-1/id'}, directory=app.directory)['value'] == last
    for invalid in (-27001, 27000):
        with pytest.raises(ValueError, match='does not exist'):
            read_state(context, {'path': f'/sessions/{invalid}/id'}, directory=app.directory)
    assert not app._state['sessions']
    assert not app.runtime.started and not app.runtime.sent
    with pytest.raises(ValueError,match='changed'):
        read_state(context,{'path':'/sessions','revision':-1},directory=app.directory)
    assert len(decoded)==8


async def test_directory_revision_rejects_native_commit_before_app_publication(app_factory,tmp_path):
    app=app_factory()
    catalog=NativeCatalog(app.data_dir/'native-catalog.sqlite3',amplifier_home())
    project,_=seed(catalog,tmp_path/'archive',3)
    app.directory.bind(catalog)
    context=app.state_context()
    first=read_state(context,{'path':'/sessions','limit':1},directory=app.directory)
    args={'path':first['items'][0]['path']+'/id','revision':context['revision'],
          'directoryRevision':first['directoryRevision']}
    assert read_state(context,args,directory=app.directory)['value']==first['items'][0]['value']['id']
    # Simulate the real interval after native cache commit, before the event
    # loop accepts its result and publishes a new application revision.
    with catalog.connect() as db:
        db.execute('DELETE FROM native_rows WHERE namespace=? AND project=? AND identity=?',
                   (catalog.namespace,project,first['items'][0]['value']['nativeIdentity']))
        db.execute('UPDATE native_revisions SET navigation=navigation+1 WHERE namespace=?',(catalog.namespace,))
    assert app._state['revision']==context['revision']
    with pytest.raises(ValueError,match='directory changed'):
        read_state(context,args,directory=app.directory)
    with pytest.raises(ValueError,match='Include directoryRevision'):
        read_state(context,{'path':'/sessions/0/id','revision':context['revision']},directory=app.directory)
    latest=read_state(context,{'path':'/sessions','limit':1},directory=app.directory)
    assert latest['directoryRevision']!=first['directoryRevision']
    assert latest['items'][0]['value']['id']!=first['items'][0]['value']['id']
    assert not app._state['sessions']
    assert not app.runtime.started and not app.runtime.sent


async def test_dormant_pointer_and_overview_keep_identity_indices_and_exact_user_values(app_factory,monkeypatch):
    app=app_factory()
    ids=[]
    for number in range(9):
        row=app._new_session({'title':f'Saved owner {number}'})
        row['messages']=[{'id':f'message-{number}','role':'assistant','text':'Body '+str(number),'createdAt':1}]
        row['structuredData']={'path':'/sessions/0/user-value','$statePath':'/sessions/0/literal'}
        ids.append(row['id']);app._state['sessions'].append(row)
    app._state['selectedSessionId']=ids[0]
    app._state['runtimeControl']={ids[-1]:{'fixture':{'body':'Dormant control value'}}}
    app._save();await app.close()
    restored=app_factory(home=app.data_dir)
    from amplifier_web import state_records,session_projection
    monkeypatch.setattr(state_records,'load',lambda *_:pytest.fail('targeted read enumerated all durable records'))
    hydrated=[]
    hydrate=session_projection.hydrate
    monkeypatch.setattr(session_projection,'hydrate',lambda home,state,db:(hydrated.extend(row['id'] for row in state['sessions']),hydrate(home,state,db))[1])
    context=restored.state_context()
    assert hydrated==[]
    position,metadata=next(item for item in _ranked(restored.directory) if item[1]['id']==ids[-1])
    pointer='/sessions/'+str(position)
    body=read_state(context,{'path':pointer+'/structuredData'},directory=restored.directory)
    assert {row['key']:row['value'] for row in body['items']}=={'path':'/sessions/0/user-value','$statePath':'/sessions/0/literal'}
    assert hydrated==[ids[-1]]
    assert len(restored._state['sessions'])==1
    runtime=read_state(context,{'path':'/runtimeControl/'+ids[-1]+'/fixture/body'},directory=restored.directory)
    assert runtime['value']=='Dormant control value'
    assert ids[-1] not in restored._state['runtimeControl']
    result=read_state(context,{},session_id=ids[-1],directory=restored.directory)
    assert result['session']['id']==ids[-1] and result['session']['$statePath']==pointer
    assert result['session']['recentMessages'][0]['text']=='Body 8'
    assert len(result['conversations'])==9
    for item in result['conversations']:
        assert read_state(context,{'path':item['$statePath']+'/id'},directory=restored.directory)['value']==item['id']
    assert len(restored._state['sessions'])==1
    assert not restored.runtime.started and not restored.runtime.sent


async def test_runtime_control_pages_include_dormant_owners_and_root_advertises_complete_catalog(app_factory):
    app=app_factory()
    ids=[]
    for number in range(7):
        row=app._new_session({'title':str(number)})
        ids.append(row['id']);app._state['sessions'].append(row)
        app._state.setdefault('runtimeControl',{})[row['id']]={'result':number}
    app._state['selectedSessionId']=ids[0]
    app._save();await app.close()
    restored=app_factory(home=app.data_dir)
    context=restored.state_context()
    seen={};offset=0
    while True:
        page=read_state(context,{'path':'/runtimeControl','offset':offset,'limit':2},directory=restored.directory)
        assert page['total']==7
        for item in page['items']:seen[item['key']]=item['value']['result']
        if page['nextOffset'] is None:break
        offset=page['nextOffset']
    assert seen==dict(zip(ids,range(7)))
    offset=0
    while True:
        root=read_state(context,{'path':'','offset':offset,'limit':50},directory=restored.directory)
        item=next((item for item in root['items'] if item['key']=='sessions'),None)
        if item is not None:break
        assert root['nextOffset'] is not None
        offset=root['nextOffset']
    assert item['value']['size']==7 and item['value']['$statePath']=='/sessions'
    assert len(restored._state['sessions'])==1
