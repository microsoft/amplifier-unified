"""Inactive display retirement keeps records exact and all authority hot."""
import copy
import json

import pytest

from amplifier_web.service import AppService
from amplifier_web.session_projection import view_path


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    yield service
    await service.close()


def add_history(app, identity, *, status='idle', approvals=None):
    base = app._new_session({})
    base.update(id=identity, runtimeSessionId=identity, status=status, approvals=approvals or [],
                messages=[{'id':f'{identity}-{n}','role':'user' if n%2==0 else 'assistant',
                           'text':'exact body '+str(n)+'x'*1000,'createdAt':n} for n in range(80)],
                draft='keep-draft', execution={'nodes':[{'id':'legacy-tool','kind':'tool','output':'only-app-output'}], 'turns':[]},
                configuration={'plan':{'tools':[]},'provenance':{}},
                task={'id':'task','status':'blocked'}, capacity={'revision':2}, workers=[])
    app.state['sessions'].append(base)
    app._publish()
    return app._session(identity)


def test_inactive_view_retires_without_changing_saved_history(app, monkeypatch):
    row = add_history(app, 'inactive')
    before = copy.deepcopy(row)
    path = view_path(app.data_dir, row)
    original = json.loads(path.read_text())
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    assert app.cold_display.is_cold(row)
    assert 'messages' not in dict.keys(row), 'resident record must actually release its message list'
    assert row['draft'] == 'keep-draft' and row['task'] == before['task']
    assert row['capacity'] == before['capacity'] and row['approvals'] == before['approvals']
    app._publish()
    from amplifier_web.cold_display import materialize
    assert materialize(json.loads(path.read_text()), app.db)['messages'] == before['messages'], 'retirement/full save must not truncate exact app-only display'
    restored = app._session('inactive')
    assert restored['messages'] == before['messages']
    assert restored['execution'] == before['execution']


def test_running_and_approval_records_are_not_retired(app, monkeypatch):
    running = add_history(app, 'running', status='working')
    approving = add_history(app, 'approval', approvals=[{'id':'permission','status':'pending'}])
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    assert not app.cold_display.is_cold(running)
    assert not app.cold_display.is_cold(approving)


def test_control_catalog_reloads_without_cooling_task_or_receipt(app, monkeypatch):
    row = add_history(app, 'control')
    catalog = {'provider-a': {'models':[{'id':f'model-{n}','description':'x'*1000} for n in range(80)]}}
    control = {'modelCatalogs':catalog,'configuration.providers':{'providers':[{'id':'provider-a','model':'pinned'}]},
               'task.get':{'task':{'status':'blocked'}},'capacity.read':{'revision':7},
               'provider.select':{'accepted':True,'id':'receipt'}}
    app.state.setdefault('runtimeControl', {})[row['id']] = copy.deepcopy(control)
    app._publish()
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    cold = app.state['runtimeControl']['control']
    assert 'modelCatalogs' not in dict.keys(cold)
    assert cold['task.get'] == control['task.get']
    assert cold['capacity.read'] == control['capacity.read']
    assert cold['provider.select'] == control['provider.select']
    app._session('control')
    assert app.state['runtimeControl']['control']['modelCatalogs'] == catalog


async def test_private_client_subscription_protects_only_its_own_history(app, monkeypatch):
    first, second = add_history(app,'first'), add_history(app,'second')
    app.clients.attach('browser-a')
    with app.clients.bind('browser-a'):
        app.clients.records['browser-a']['selectedSessionId'] = 'first'
        app.clients.draft('first','private-A')
        queue = app.subscribe()
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    assert not app.cold_display.is_cold(first)
    assert app.cold_display.is_cold(second)
    assert app.clients.records['browser-a']['drafts']['first']=='private-A'
    app.unsubscribe(queue)


def test_failed_cold_write_keeps_hot_body(app, monkeypatch):
    row = add_history(app,'disk-fault')
    before = copy.deepcopy(row)
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    from amplifier_web import cold_display
    def failure(*args):raise OSError('injected full disk')
    monkeypatch.setattr(cold_display,'put',failure)
    with pytest.raises(OSError):app.cold_display.retire(force=True)
    assert not app.cold_display.is_cold(row)
    assert row['messages']==before['messages']


async def test_cold_view_survives_restart_and_missing_resource_is_visible(app, monkeypatch):
    row=add_history(app,'restart')
    expected=copy.deepcopy(row['messages'])
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    app._publish()
    path=view_path(app.data_dir,row)
    home,workspace=app.data_dir,app.default_workspace
    await app.close()
    reopened=AppService(home,workspace=workspace)
    try:
        assert reopened._session('restart')['messages']==expected
        from amplifier_web.cold_display import materialize
        assert materialize(json.loads(path.read_text()), reopened.db)['messages']==expected
    finally:await reopened.close()


def test_hydrate_a_save_b_preserves_committed_resource_root(app, monkeypatch):
    from amplifier_web.resource_files import retained_references, collect
    a,b=add_history(app,'gc-a'),add_history(app,'gc-b')
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    ref=dict.get(a,'_coldFields')['messages']['$resource']
    a['messages']
    app._save(session_ids={'gc-b'})
    assert json.loads(view_path(app.data_dir,a).read_text())['_coldFields']['messages']['$resource']==ref
    assert ref in retained_references(app.db,app._state)
    assert ref not in collect(app.db,app._state)


def test_failed_second_retirement_keeps_previous_cold_references(app, monkeypatch):
    row=add_history(app,'rollback')
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    expected=copy.deepcopy(dict.get(row,'_coldFields'))
    row['configuration']={'plan':{'padding':'x'*20000}}
    def fail(**kwargs):raise OSError('injected save failure')
    with monkeypatch.context() as m:
        m.setattr(app,'_save',fail)
        with pytest.raises(OSError):app.cold_display.retire(force=True)
    assert dict.get(row,'_coldFields')==expected
    assert len(row['messages'])==80


@pytest.mark.parametrize('method',['items','copy','copy_module'])
def test_shallow_mapping_access_preserves_nested_mutation(app,monkeypatch,method):
    row=add_history(app,'mapping-'+method)
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    if method=='items':value=next(v for k,v in row.items() if k=='messages')
    elif method=='copy':value=row.copy()['messages']
    else:value=copy.copy(row)['messages']
    value.append({'id':'late','role':'user','text':'must survive'})
    assert any(m['id']=='late' for m in row['messages'])
    app._publish()
    assert any(m['id']=='late' for m in app._session(row['id'])['messages'])


@pytest.mark.parametrize('nested',[False,True])
def test_issued_mutable_alias_is_not_disconnected_by_retirement(app,monkeypatch,nested):
    row=add_history(app,'alias-'+str(nested))
    alias=row['messages'][0] if nested else row['messages']
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    if nested:alias['text']='nested alias mutation'
    else:alias.append({'id':'late','role':'user','text':'must survive'})
    app._publish()
    current=app._session(row['id'])['messages']
    if nested:assert current[0]['text']=='nested alias mutation'
    else:assert any(m['id']=='late' for m in current)


def test_custom_nested_message_alias_is_not_disconnected(app,monkeypatch):
    class Message(dict):pass
    row=add_history(app,'custom-alias')
    row['messages'][0]=Message(row['messages'][0])
    alias=row['messages'][0]
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    alias['text']='custom mutation'
    assert row['messages'][0]['text']=='custom mutation'


def test_in_place_dict_union_replaces_cold_body(app,monkeypatch):
    from amplifier_web.cold_display import saved,materialize
    row=add_history(app,'union-write')
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    replacement=[{'id':'new','role':'user','text':'replacement'}]
    row |= {'messages':replacement}
    assert materialize(saved(row),app.db)['messages']==replacement


def test_control_root_alias_authority_keeps_identity(app,monkeypatch):
    row=add_history(app,'control-alias')
    alias={'provider.select':{'accepted':True,'id':'old'}}
    app._state.setdefault('runtimeControl',{})[row['id']]=alias
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    alias['provider.select']={'accepted':True,'id':'new'}
    assert app._state['runtimeControl'][row['id']]['provider.select']['id']=='new'


def test_unreadable_cold_body_refuses_without_empty_replacement(app,monkeypatch):
    from amplifier_web.resource_files import root
    row=add_history(app,'missing-body')
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    ref=dict.get(row,'_coldFields')['messages']['$resource']
    path=root(app.db)/(ref+'.json')
    original=path.read_bytes()
    path.unlink()
    try:
        with pytest.raises(FileNotFoundError):app._session('missing-body')
        assert ref==dict.get(row,'_coldFields')['messages']['$resource']
        assert not dict.__contains__(row,'messages')
    finally:path.write_bytes(original)
    assert len(app._session('missing-body')['messages'])==80


def test_notification_summary_preserves_latest_dates_without_warming_all(app,monkeypatch):
    row=add_history(app,'notification')
    for n,message in enumerate(row['messages']):
        message['via']='text'
        message['createdAt']=1000-n  # canonical order need not be chronological
    del message  # no borrowed mutable message remains for this retirement test
    app._publish()
    expected=copy.deepcopy(app.projections.sessions(app.state).notifications)
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    app.projections.values.clear()
    observed=app.projections.sessions(app.state).notifications
    assert observed==expected
    assert app.cold_display.is_cold(row)


@pytest.mark.parametrize('method',['update','union'])
def test_shallow_patch_merge_cannot_erase_omitted_cold_body(app,monkeypatch,method):
    a,b=add_history(app,'merge-a'),add_history(app,'merge-b')
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    patch=b.copy()
    for key in ('id','workspace','runtimeSessionId','messages'):
        patch.pop(key,None)
    if method=='update':a.update(patch)
    else:a |= patch
    assert len(a['messages'])==80
    assert a['messages'][0]['id']=='merge-a-0'


def test_mapping_clear_popitem_union_and_length_resolve_virtual_fields(app,monkeypatch):
    row=add_history(app,'dict-protocol')
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    merged=row | {'title':'replacement'}
    assert len(merged['messages'])==80 and merged['title']=='replacement'
    assert merged['messages'] is row['messages']
    copy_row=copy.copy(row)
    removed={}
    while copy_row:
        key,value=copy_row.popitem()
        removed[key]=value
    assert len(removed['messages'])==80
    assert len(copy_row)==0
    standalone=copy.copy(row)
    standalone.clear()
    assert not standalone and len(standalone)==0 and standalone.get('messages') is None


def test_valid_json_corruption_is_refused_without_adopting_wrong_history(app,monkeypatch):
    from amplifier_web.resource_files import root
    row=add_history(app,'corrupt-body')
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    ref=dict.get(row,'_coldFields')['messages']['$resource']
    path=root(app.db)/(ref+'.json')
    original=path.read_bytes()
    path.write_text('[{"id":"wrong","text":"changed"}]')
    try:
        with pytest.raises(ValueError,match='display payload changed'):app._session('corrupt-body')
        assert ref==dict.get(row,'_coldFields')['messages']['$resource']
        assert not dict.__contains__(row,'messages')
    finally:path.write_bytes(original)


def test_newly_created_history_is_recent_until_idle_deadline(app,monkeypatch):
    row=app._new_session({})
    row['messages']=[{'id':str(n),'text':'x'*2000} for n in range(20)]
    app.state['sessions'].append(row)
    app._publish()
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    assert row['id'] in app.cold_display.recent
    app.cold_display.last_sweep=0
    app.cold_display.retire()
    assert not app.cold_display.is_cold(row)


def test_cold_right_hand_union_cannot_export_a_destructive_marker(app,monkeypatch):
    from amplifier_web.cold_display import ColdRecord
    a,b=add_history(app,'rhs-a'),add_history(app,'rhs-b')
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    patch=ColdRecord({},app.db) | b
    for key in ('id','workspace','runtimeSessionId','messages'):
        patch.pop(key,None)
    a.update(patch)
    assert len(a['messages'])==80


def test_native_catalog_cold_references_remain_gc_roots_without_full_view(app,monkeypatch):
    from amplifier_web.resource_files import retained_references,collect
    row=add_history(app,'native-cold')
    row.update(historyManaged=True,nativeProject='fixture-project',nativeIdentity='native-cold')
    app._publish()
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    ref=dict.get(row,'_coldFields')['messages']['$resource']
    assert ref in retained_references(app.db,app._state)
    app._publish()
    assert ref in retained_references(app.db,{})
    assert ref not in collect(app.db,{})


def test_ordinary_retirement_is_small_batch_and_cold_catalog_is_not_rescanned(app,monkeypatch):
    rows=[add_history(app,'batch-'+str(n)) for n in range(12)]
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    for row in rows:app.cold_display.recent[row['id']]=0
    app.cold_display.last_sweep=0
    retired=app.cold_display.retire()
    assert 0<len(retired)<=app.cold_display.RETIRE_BATCH
    assert not retired.intersection(app.cold_display.recent)
    assert all(app.cold_display.is_cold(row) for row in rows if row['id'] in retired)


def test_busy_probe_front_does_not_starve_a_later_inactive_body(app,monkeypatch):
    busy=[add_history(app,'probe-busy-'+str(n),status='working') for n in range(2)]
    last=add_history(app,'probe-tail')
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    monkeypatch.setattr(app.cold_display,'PROBE_BATCH',2)
    for row in [*busy,last]:app.cold_display.recent[row['id']]=0
    app.cold_display.last_sweep=0
    assert app.cold_display.retire()==set()
    app.cold_display.last_sweep=0
    assert last['id'] in app.cold_display.retire()


def test_cold_configuration_does_not_skip_live_turn_anchor_normalization(app,monkeypatch):
    row=add_history(app,'cold-config-anchor')
    row['configuration']={'plan':{'padding':'x'*20000}}
    app._publish()
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    row['messages']  # only message body needed by this writer
    row['execution']={'nodes':[],'turns':[{'id':'new-turn','inputId':'anchor-input','startedAt':100}]}
    row['messages'].append({'id':'anchor-message','inputId':'anchor-input','role':'user','text':'new','createdAt':99})
    app._publish(session_ids={row['id']})
    assert row['execution']['turns'][0]['anchorMessageId']=='anchor-message'
    assert not dict.__contains__(row,'configuration')


async def test_managed_deletion_plan_owns_cold_payload_blobs(app,monkeypatch):
    from amplifier_web.managed_deletion import plan
    await app.dispatch('session.create',{'location':{'kind':'managed'},'title':'To remove'})
    sid=app.state['selectedSessionId']
    row=app._session(sid)
    row['messages']=[{'id':str(n),'role':'user','text':'private body '+'x'*2000,'createdAt':n} for n in range(20)]
    app._publish()
    await app.dispatch('session.create',{'location':{'kind':'managed'},'title':'Retained'})
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    ref=dict.get(row,'_coldFields')['messages']['$resource']
    deletion=plan(app,sid)
    assert ref in deletion['resourceIds'], 'confirmed deletion must not leave a full cold conversation copy behind'
    from amplifier_web.resource_files import root
    body_path=root(app.db)/(ref+'.json')
    preview=(await app.dispatch('session.deletePreview',{'id':sid}))['result']
    await app.dispatch('session.delete',{'id':sid,'confirmationToken':preview['confirmationToken']})
    assert not body_path.exists()
    assert not app.db.execute('SELECT 1 FROM state_resources WHERE id=?',(ref,)).fetchone()
    assert sid not in {row['id'] for row in app._state['sessions']}


async def test_deleting_one_chat_preserves_a_shared_cold_payload(app,monkeypatch):
    from amplifier_web.managed_deletion import plan
    await app.dispatch('session.create',{'location':{'kind':'managed'},'title':'To remove'})
    first=app._session()
    first['messages']=[{'id':str(n),'role':'user','text':'shared body '+'x'*2000,'createdAt':n} for n in range(20)]
    app._publish()
    await app.dispatch('session.create',{'location':{'kind':'managed'},'title':'Keep'})
    second=app._session()
    second['messages']=copy.deepcopy(first['messages'])
    app._publish()
    app.state['selectedSessionId']=None
    monkeypatch.setattr(app.cold_display,'RECENT_LIMIT',0)
    app.cold_display.retire(force=True)
    ref=dict.get(first,'_coldFields')['messages']['$resource']
    assert ref==dict.get(second,'_coldFields')['messages']['$resource']
    deletion=plan(app,first['id'])
    assert ref not in deletion['resourceIds']
    preview=(await app.dispatch('session.deletePreview',{'id':first['id']}))['result']
    await app.dispatch('session.delete',{'id':first['id'],'confirmationToken':preview['confirmationToken']})
    assert len(app._session(second['id'])['messages'])==20
