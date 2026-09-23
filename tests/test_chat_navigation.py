"""The agent and sidebar share ordering, eligibility, pins and scoped pages."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import chat_navigation
from amplifier_web.agent_state import overview
from amplifier_web.native_history import NativeHistory
from amplifier_web.service import AppService, AppError
from test_automatic_history import app_factory, native_session, native_rows, files_snapshot, finish_actions
from test_service import Runtime


def state_fixture():
    return {'workspaces':[
        {'id':'one','path':'/projects/one/shared','name':'Personal notes','available':True},
        {'id':'two','path':'/projects/two/shared','name':'shared','available':True},
        {'id':'gone','path':'/projects/gone','name':'gone','available':False},
        {'id':'unknown','path':None,'name':'unknown','available':True}],
        'selectedWorkspaceId':'one','selectedSessionId':None,'view':{'navChatScope':'workspace'},
        'pinnedSessionIds':[], 'sessions':[]}


def chat(identity,workspace='one',recent=1,**fields):
    return {'id':identity,'title':identity,'description':'','workspaceId':workspace,
            'status':'idle','sessionKind':'root','recentActivityAt':recent,**fields}


def ids(page):return [row['id'] for row in page['items']]


def test_shared_session_id_search_keeps_app_keys_and_workspace_scope():
    state = state_fixture()
    shared_id = '11111111-1111-4111-8111-111111111111'
    state['sessions'] = [chat('internal-one', runtimeSessionId=shared_id),
                         chat('internal-two', 'two', nativeIdentity=shared_id),
                         chat('22222222-2222-4222-8222-222222222222')]
    state['selectedSessionId'] = 'internal-one'
    state['pinnedSessionIds'] = ['internal-one']
    state['view'].update(navChatScope='all', navFilter=shared_id[:8])
    before = deepcopy(state)
    page = chat_navigation.snapshot(state)
    assert ids(page) == ['internal-one', 'internal-two']
    assert page['items'][0]['runtimeSessionId'] == shared_id
    assert page['items'][0]['pinned'] is True
    assert state == before
    state['view']['navChatScope'] = 'workspace'
    assert ids(chat_navigation.snapshot(state)) == ['internal-one']
    state['view']['navFilter'] = '22222222'
    assert ids(chat_navigation.snapshot(state)) == ['22222222-2222-4222-8222-222222222222']


def test_all_chats_uses_only_available_roots_and_pins_then_actual_recency():
    state=state_fixture();state['view']['navChatScope']='all'
    state['sessions']=[chat('old-pin',recent=2),chat('newest',recent=90),chat('same-first',recent=50),
        chat('same-second','two',50),chat('new-pin','two',3),chat('gone','gone',1000),
        chat('unknown','unknown',1000),chat('child',recent=1000,sessionKind='worker'),
        chat('internal',recent=1000,sessionKind='internal',status='working'),
        chat('fork','two',4,parentId='newest')]
    state['pinnedSessionIds']=['old-pin','new-pin','child','gone']
    state['sessions'][0]['updatedAt']=99999
    page=chat_navigation.snapshot(state)
    assert ids(page)==['old-pin','new-pin','newest','same-first','same-second','fork']
    assert page['scope']=={'mode':'all','workspaceId':None,'filter':'','selectedSessionId':None}
    assert page['items'][0]['workspace']=='/projects/one/shared'
    assert page['items'][0]['pinned'] and not page['items'][2]['pinned']
    assert set(page['items'][0])=={'id','title','description','status','workspace','workspaceId','pinned','recentActivityAt','workspaceName','workspaceLabel','activity','runtimeSessionId','createdAt'}
    state['view']['navChatScope']='workspace'
    assert ids(chat_navigation.snapshot(state))==['old-pin','newest','same-first']


def test_path_only_legacy_chat_remains_visible_and_missing_selection_is_empty():
    state=state_fixture()
    state['sessions']=[{'id':'legacy','title':'Legacy','workspace':'/projects/one/shared','createdAt':42}]
    assert ids(chat_navigation.snapshot(state))==['legacy']
    state['selectedWorkspaceId']='gone'
    page=chat_navigation.snapshot(state)
    assert page['total']==0 and page['scope']['workspaceId'] is None
    state['view']['navChatScope']='all'
    assert ids(chat_navigation.snapshot(state))==['legacy']


@pytest.mark.parametrize('query,expected',[
    ('*/two/*',['beta']),('Personal*',['alpha']),('ALPHA',['alpha']),
    ('special description',['beta']),('bet?',['beta']),('[ab]*',['beta','alpha']),
    ('no match',[]),
])
def test_search_matches_names_ids_descriptions_and_full_paths(query,expected):
    state=state_fixture();state['view'].update(navChatScope='all',navFilter=query)
    state['sessions']=[chat('alpha',recent=1,title='Alpha title'),chat('beta','two',2,description='Special description')]
    assert ids(chat_navigation.snapshot(state))==expected


def test_scoped_paging_is_bounded_and_all_starts_with_pins_and_recent():
    state=state_fixture()
    state['sessions']=[chat(str(i),recent=1000-i) for i in range(250)]
    state['selectedSessionId']='220'
    page=chat_navigation.snapshot(state)
    assert (page['index'],page['start'],page['end'],page['total'],page['pages'])==(2,200,250,250,3)
    state['view']['navChatPage']={**page['scope'],'index':1}
    assert len(chat_navigation.snapshot(state)['items'])==100
    state['view']['navChatScope']='all'
    page=chat_navigation.snapshot(state)
    assert page['index']==0 and ids(page)[0]=='0'
    state['view']['navChatPage']={**page['scope'],'index':2}
    assert chat_navigation.snapshot(state)['index']==2
    state['view']['navFilter']='22?'
    page=chat_navigation.snapshot(state)
    assert page['index']==0 and page['total']==10


async def test_pin_preference_persists_without_native_writes_or_runtime_work(tmp_path,app_factory):
    directory=native_session(tmp_path/'native','root')
    app=app_factory();await app.history.refresh()
    native=native_rows(app)[0]
    before=files_snapshot(directory);recent=native['recentActivityAt']
    app.state['view']['navChatPage']={'index':9}
    await app.dispatch('session.pin',{'id':native['id'],'pinned':True})
    await app.dispatch('session.pin',{'id':native['id'],'pinned':True})
    await app.history.refresh()
    assert app.state['pinnedSessionIds']==[native['id']]
    assert 'navChatPage' not in app.state['view']
    assert app._session(native['id'])['recentActivityAt']==recent
    assert before==files_snapshot(directory)
    assert not app.runtime.started and not app.runtime.sent
    assert not any(event[1]['data'].get('action')=='session.pin' for event in app.observed_diagnostics)
    saved=json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    indexed=next(row for row in saved['sessions'] if row['id']==native['id'])
    assert indexed['$native'] and indexed['recentActivityAt']==recent
    data_dir=app.data_dir;workspace=app.default_workspace
    await app.close()
    restored=app_factory(home=data_dir,workspace=Path(workspace))
    assert restored.state['pinnedSessionIds']==[native['id']]
    assert restored._session(native['id'])['recentActivityAt']==recent
    await restored.dispatch('session.pin',{'id':native['id'],'pinned':False})
    await restored.dispatch('session.pin',{'id':native['id'],'pinned':False})
    assert not restored.state['pinnedSessionIds']
    assert before==files_snapshot(directory)


async def test_pin_rejects_workers_deletion_cleans_preference_and_unpin_is_idempotent(tmp_path,app_factory):
    app=app_factory()
    await app.dispatch('session.create',{'location':{'kind':'managed'}})
    root=app._session()
    worker={**deepcopy(root),'id':'worker','sessionKind':'worker','nativeParentId':root['id']}
    app.state['sessions'].append(worker)
    with pytest.raises(AppError,match='top-level'):
        await app.dispatch('session.pin',{'id':worker['id'],'pinned':True})
    with pytest.raises(AppError):
        await app.dispatch('session.pin',{'id':root['id'],'pinned':'yes'})
    with pytest.raises(AppError):
        await app.dispatch('session.pin',{'id':'','pinned':True})
    await app.dispatch('session.pin',{'id':root['id'],'pinned':True})
    preview=(await app.dispatch('session.deletePreview',{'id':root['id']}))['result']
    await app.dispatch('session.delete',{'id':root['id'],'confirmationToken':preview['confirmationToken']});await finish_actions(app)
    assert root['id'] not in app.state['pinnedSessionIds']
    await app.dispatch('session.pin',{'id':root['id'],'pinned':False})
    assert not app.state['pinnedSessionIds']


@pytest.mark.parametrize('patch',[
    {'navChatScope':'invalid'},{'navChatScope':[]},{'navChatScope':None},
    {'navFilter':['bad']},{'navChatPage':{'index':True}},
    {'navChatPage':{'index':-1,'mode':'all','filter':''}},
])
async def test_invalid_shared_navigation_controls_are_rejected(app_factory,patch):
    app=app_factory()
    with pytest.raises(AppError):await app.dispatch('view.update',{'patch':patch})


async def test_real_conversation_activity_updates_recency_but_navigation_and_lifecycle_do_not(app_factory,monkeypatch):
    app=app_factory();await app.dispatch('session.create',{})
    root=app._session();root['recentActivityAt']=10
    clock=[20];monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:clock[0]))
    await app.dispatch('session.select',{'id':root['id']})
    await app.dispatch('session.rename',{'id':root['id'],'title':'Renamed'})
    await app.dispatch('session.pin',{'id':root['id'],'pinned':True})
    await app.dispatch('view.update',{'patch':{'navChatScope':'all'}})
    for status in ('starting','ready','idle','stopped','interrupted'):
        await app.on_runtime_event('runtime.status',{'sessionId':root['id'],'status':status})
    await app.on_runtime_event('session.naming',{'sessionId':root['id'],'name':'Automatic name'})
    await app.on_runtime_event('execution.event',{'sessionId':root['id'],'id':'name-call','kind':'llm','label':'Session naming','phase':'completed'})
    assert root['recentActivityAt']==10
    await app.dispatch('conversation.send',{'text':'An actual user message'})
    assert root['recentActivityAt']==20
    clock[0]=30
    await app.on_runtime_event('assistant.delta',{'sessionId':root['id'],'text':'Working'})
    assert root['recentActivityAt']==30
    clock[0]=40
    await app.on_runtime_event('runtime.tool',{'sessionId':root['id'],'tool':'read_file','phase':'pre'})
    assert root['recentActivityAt']==40
    clock[0]=50
    await app.on_runtime_event('assistant.message',{'sessionId':root['id'],'text':'Done'})
    assert root['recentActivityAt']==50
    before=root['recentActivityAt'];root['status']='working';app._save()
    data_dir=app.data_dir;workspace=app.default_workspace;await app.close()
    restored=AppService(data_dir,Runtime(),workspace=workspace)
    try:
        assert restored._session()['status']=='interrupted'
        assert restored._session()['recentActivityAt']==before
        agent=overview(restored.get_state(),root['id'])
        assert 'chatNavigation' in agent and agent['session']['pinned']
        assert 'session.pin' in agent['_stateAccess']['chats']
    finally:await restored.close()


async def test_rejected_send_does_not_make_chat_recent(app_factory,monkeypatch):
    from amplifier_web.runtime import SessionInUseError
    app=app_factory();await app.dispatch('session.create',{})
    session=app._session();session['recentActivityAt']=10
    monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:20))
    async def reject(*args,**kwargs):raise SessionInUseError({'pid':123,'host':'fixture'})
    monkeypatch.setattr(app.runtime,'send',reject)
    with pytest.raises(AppError,match='in use'):
        await app.dispatch('conversation.send',{'text':'Not admitted'})
    assert session['recentActivityAt']==10 and not session['messages']


async def test_native_recency_uses_transcript_not_rename_and_refresh_keeps_web_activity(tmp_path,app_factory):
    directory=native_session(tmp_path/'native','root')
    transcript=directory/'transcript.jsonl'
    os.utime(transcript,(100,100))
    app=app_factory();await app.history.refresh()
    row=app._session(native_rows(app)[0]['id']);assert row['recentActivityAt']==100
    metadata=json.loads((directory/'metadata.json').read_text())
    metadata.update(name='Renamed externally',updated_at=9999999999)
    (directory/'metadata.json').write_text(json.dumps(metadata))
    await app.history.refresh()
    assert row['recentActivityAt']==100
    os.utime(transcript,(200,200));await app.history.refresh()
    assert row['recentActivityAt']==200
    row.update(historyManaged=False,recentActivityAt=300)
    await app.history.refresh()
    assert row['recentActivityAt']==300


def test_native_recency_falls_back_only_to_explicit_event_or_creation(tmp_path):
    directory=native_session(tmp_path/'native','root',metadata={'turn_count':1,'last_event_at':200,'created_at':100,'created':100,'started_at':100,'updated_at':99999})
    (directory/'transcript.jsonl').unlink()
    index=NativeHistory()
    assert index.scan()['sessions'][0]['recentActivityAt']==200
    metadata=json.loads((directory/'metadata.json').read_text());metadata.pop('last_event_at')
    (directory/'metadata.json').write_text(json.dumps(metadata))
    assert index.scan()['sessions'][0]['recentActivityAt']==200


async def test_voice_responses_without_transcripts_count_as_activity_but_aggregate_updates_do_not(app_factory,monkeypatch):
    app=app_factory();await app.dispatch('session.create',{})
    session=app._session();session['recentActivityAt']=10
    clock=[20];monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:clock[0]))
    await app.set_voice_status({'status':'connecting'})
    await app.record_voice_usage(session['id'],'call','session','fixture',{'input_tokens':0})
    assert session['recentActivityAt']==10
    await app.record_voice_usage(session['id'],'call','response','fixture',{},phase='running')
    assert session['recentActivityAt']==20
    clock[0]=30
    await app.record_voice_usage(session['id'],'call','response','fixture',{'output_tokens':12})
    assert session['recentActivityAt']==30
    clock[0]=40
    await app.record_voice_usage(session['id'],'call','response','fixture',{'output_tokens':12})
    await app.record_voice_usage(session['id'],'call','session','fixture',{'output_tokens':12})
    await app.set_voice_status({'status':'ended'})
    assert session['recentActivityAt']==30


@pytest.mark.parametrize('sort,expected', [('activity',['b','c','a']), ('created',['c','a','b']), ('name',['a','b','c'])])
def test_sort_choices_keep_pin_order(sort, expected):
    state=state_fixture()
    state['sessions']=[chat('p1',recent=1),chat('p2',recent=999),
        chat('a',recent=10,title='Alpha',createdAt=20),
        chat('b',recent=30,title='beta',createdAt=10),chat('c',recent=20,title='Charlie',createdAt=30)]
    state['pinnedSessionIds']=['p1','p2']
    state['view']['navSort']=sort
    assert ids(chat_navigation.snapshot(state))==['p1','p2',*expected]
    with pytest.raises(ValueError):chat_navigation.view_patch({'navSort':'invalid'})


async def test_progress_keeps_order_and_shell_key_until_ready(app_factory,monkeypatch):
    app=app_factory();await app.dispatch('session.create',{})
    root=app._session();root.update(recentActivityAt=10,navigationActivityAt=10)
    clock=[20];monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:clock[0]))
    await app.dispatch('conversation.send',{'text':'Keep working'})
    key=app.browser_state()['shellDataKey']
    for kind,payload in [('assistant.delta',{'text':'Progress'}),
        ('runtime.tool',{'tool':'read_file','phase':'pre'}),
        ('runtime.tool',{'tool':'read_file','phase':'post'}),
        ('assistant.message',{'text':'Still working'}),
        ('runtime.generation',{'event':'generation.finished','text':'Still working','active_job_ids':['worker']})]:
        clock[0]+=10
        await app.on_runtime_event(kind,{'sessionId':root['id'],**payload})
        assert chat_navigation.navigation_activity(root)==10
        assert app.browser_state()['shellDataKey']==key
    clock[0]=100
    await app.on_runtime_event('runtime.status',{'sessionId':root['id'],'status':'idle'})
    assert chat_navigation.navigation_activity(root)==100
    assert app.browser_state()['shellDataKey']!=key
    clock[0]=200
    await app.on_runtime_event('runtime.status',{'sessionId':root['id'],'status':'idle'})
    assert chat_navigation.navigation_activity(root)==100


@pytest.mark.parametrize('kind,payload',[
    ('runtime.error',{'error':'Provider failed'}),
    ('runtime.status',{'status':'stopped'}),
    ('approval.requested',{'id':'permission','tool':'write_file'}),
])
async def test_attention_boundaries_commit_activity(app_factory,monkeypatch,kind,payload):
    app=app_factory();await app.dispatch('session.create',{})
    root=app._session();root.update(recentActivityAt=10,navigationActivityAt=10)
    clock=[20];monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:clock[0]))
    await app.dispatch('conversation.send',{'text':'Work'})
    clock[0]=30
    await app.on_runtime_event(kind,{'sessionId':root['id'],**payload})
    assert chat_navigation.navigation_activity(root)==30


async def test_history_refresh_does_not_move_running_chat(tmp_path,app_factory):
    directory=native_session(tmp_path/'native','root')
    transcript=directory/'transcript.jsonl';os.utime(transcript,(100,100))
    app=app_factory();await app.history.refresh()
    row=app._session(native_rows(app)[0]['id']);row.update(historyManaged=False,status='working')
    os.utime(transcript,(200,200));await app.history.refresh()
    assert row['recentActivityAt']==200
    assert chat_navigation.navigation_activity(row)==100
