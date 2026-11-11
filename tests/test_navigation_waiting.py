"""Exact saved waiting relationships are presentation, never continuation."""
from copy import deepcopy
import json
import sqlite3

import pytest

from amplifier_web.navigation_waiting import initialize, input_boundary, project, wait_epoch
from amplifier_web.navigation_summary import activity
from test_collaborative_workspaces import app


def task():
    return {'id': 'objective', 'revision': 2, 'continuationEpoch': 2,
            'dependencyWaitEpoch': 1, 'status': 'blocked',
            'blockProvenance': {'version': 1, 'origin': 'agent', 'dependency': True, 'revision': 2}}


def session():
    return {'id': 'manager', 'status': 'idle', 'interruptionRevision': 0,
            'executionRevision': 3, 'collaborationGeneration': {'id': 'generation', 'inputIds': ['input']}}


def receipt(sid='worker', **changes):
    return {'commandAction': 'coordination.send', 'protocol': 2, 'senderSessionId': 'manager',
            'sourceGenerationId': 'generation', 'sourceInputIds': ['input'],
            'target': {'sessionId': sid, 'title': 'Worker ' + sid}, 'delivery': 'applied',
            'subscription': {'notificationVersion': 2, 'supported': True, 'status': 'waiting',
                             'open': True, 'taskId': 'objective', 'taskContinuationEpoch': 1,
                             'interruptionRevision': 0},
            **changes}


@pytest.fixture
def db():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY, receipt TEXT)')
    initialize(db)
    yield db
    db.close()


def put(db, row, identity='request'):
    db.execute('INSERT OR REPLACE INTO commands VALUES(?,?)', (identity, json.dumps(row)))


def test_saved_preblock_wait_and_many_not_arbitrary_child(db):
    before = deepcopy(task())
    assert project(db, session(), before) is None
    put(db, receipt())
    value = project(db, session(), before)
    assert value['targets'] == [{'sessionId': 'worker', 'title': 'Worker worker',
                                'phase': 'pending', 'label': 'Waiting for another chat'}]
    assert activity(session(), task=before, db=db)['kind'] == 'waiting'
    put(db, receipt('second'), 'two')
    assert len(project(db, session(), before)['targets']) == 2
    assert before == task()


@pytest.mark.parametrize('field,value', [
    ('senderSessionId','other'), ('protocol',1), ('delivery','submitting'),
    ('sourceGenerationId',None), ('sourceInputIds',[]),
])
def test_inexact_settled_or_unknown_requests_are_not_live_waits(db, field, value):
    put(db, receipt(**{field:value}))
    assert project(db, session(), task()) is None


@pytest.mark.parametrize('field,value', [
    ('taskId','old-objective'), ('taskContinuationEpoch',0), ('interruptionRevision',1),
    ('status','suppressed'), ('open',False), ('supported',False),
    ('notificationVersion',1),
])
def test_old_or_missing_subscription_never_binds(db, field, value):
    row = receipt()
    row['subscription'][field] = value
    put(db, row)
    assert project(db, session(), task()) is None


@pytest.mark.parametrize('patch', [
    {'interruptionRevision':1},
    {'collaborationGeneration':{'id':'new', 'inputIds':['input']}},
    {'collaborationGeneration':{'id':'generation','inputIds':['input','new-input']}},
    {'status':'stopped'}, {'status':'error'}, {'status':'interrupted'},
])
def test_new_input_generation_or_stop_never_resurrects_targets(db, patch):
    put(db, receipt())
    assert project(db, {**session(), **patch}, task()) is None


def test_attention_and_execution_precedence(db):
    put(db, receipt())
    for patch, expected in [
        ({'status':'working'}, 'working'), ({'approvals':[{'status':'pending'}]}, 'attention'),
        ({'questions':[{'status':'pending','required':True}]}, 'attention'),
        ({'status':'error'}, 'attention'), ({'status':'stopped'}, 'paused')]:
        assert activity({**session(), **patch}, task=task(), db=db)['kind'] == expected
    assert activity(session(), task={**task(),'status':'paused'}, db=db)['kind'] == 'paused'
    assert activity(session(), task={**task(),'blockProvenance':None}, db=db) == {
        'kind':'idle', 'label':'Blocked — details unavailable'}


def test_projection_bound_is_independent_of_retained_receipts(db):
    put(db, receipt())
    # Same sender's settled historical requests and unrelated pending senders.
    db.executemany('INSERT INTO commands VALUES(?,?)', [
        (str(i),json.dumps(receipt(sourceGenerationId='historical', delivery='rejected',
                                  text='private body'*1000))) for i in range(2000)])
    steps = 0
    def count():
        nonlocal steps
        steps += 1
        return 0
    db.set_progress_handler(count, 1)
    value = project(db, session(), task())
    db.set_progress_handler(None, 0)
    assert steps < 300, steps
    assert 'private body' not in json.dumps(value)
    for i in range(40):
        put(db, receipt(str(i)), 'pending'+str(i))
    value = project(db, session(), task())
    assert len(value['targets']) == 16 and value['hasMore']


def test_target_updates_only_visit_indexed_pending_dependencies(db):
    from amplifier_web.navigation_waiting import affected_sources
    put(db,receipt())
    put(db,receipt('other'), 'unrelated')
    put(db,receipt(delivery='rejected',senderSessionId='past'), 'past')
    assert affected_sources(db,'worker') == {'manager'}
    assert affected_sources(db,'missing') == set()


def test_pending_query_uses_covering_expressions_not_large_receipt_bodies(db):
    put(db, receipt(text='large private request body'*100000))
    statements=[]
    db.set_trace_callback(statements.append)
    assert project(db,session(),task())
    db.set_trace_callback(None)
    query=next(sql for sql in statements if 'FROM navigation_dependency_requests' in sql)
    operations=db.execute('EXPLAIN '+query).fetchall()
    print('SQLITE',sqlite3.sqlite_version,'PLAN',json.dumps(operations))
    # Expression-index reads must not invoke JSON functions over retained text.
    assert not any(row[1] in {'Function','PureFunc'} and 'json_extract' in str(row[5]) for row in operations), operations
    assert all('FROM commands' not in sql for sql in statements)


def test_wait_epoch_uses_typed_contiguous_changes_and_stops_at_active_boundary():
    value = {**task(), 'changes': [
        {'revision':1,'origin':'agent','fields':{'status':'active','objective':'Goal'}},
        {'revision':2,'origin':'agent','fields':{'status':'blocked'}}]}
    assert wait_epoch(value) == 1
    value.update(revision=5,continuationEpoch=5, changes=[
        *value['changes'],
        {'revision':3,'origin':'ui','fields':{'status':'paused'}},
        {'revision':4,'origin':'ui','fields':{'status':'active'}},
        {'revision':5,'origin':'agent','fields':{'status':'blocked'}}])
    assert wait_epoch(value) == 4
    value['changes'] = value['changes'][:-2] + value['changes'][-1:]
    assert wait_epoch(value) is None
    value = {**task(), 'revision':3, 'changes': [
        {'revision':1,'origin':'agent','fields':{'status':'active','objective':'Goal'}},
        {'revision':3,'origin':'agent','fields':{'status':'blocked'}}],
        'corrections':[{'revision':2,'origin':'agent','text':'Retained correction'}]}
    assert wait_epoch(value) == 1
    value['corrections'] = []
    assert wait_epoch(value) is None


async def test_native_projection_retains_preblock_epoch_without_mutating_authority(tmp_path, monkeypatch):
    from test_task_continuity import controls, create
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    control = controls('navigation-wait')
    await create(control)
    await control.perform('task.block', {'commandId':'block','expectedRevision':1,
                          'origin':'agent','reason':'Any prose is irrelevant'})
    before = deepcopy(control.tasks.record())
    result = control.tasks.snapshot()
    assert result['task']['dependencyWaitEpoch'] == 1
    assert 'dependencyWaitEpoch' not in control.tasks.record()
    assert control.tasks.record() == before


def test_directory_and_workspace_share_current_waiting_facts(tmp_path):
    from test_history_directory_navigation import directory_fixture
    from amplifier_web.history_directory_query import workspace_counts
    state, directory = directory_fixture(tmp_path, count=5)
    db = directory.db
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY, receipt TEXT)')
    initialize(db)
    owner = state['sessions'][1]
    owner.update(session(), workspace='/projects/alpha', workspaceId='a', sessionKind='root')
    state['runtimeControl'] = {'manager': {'task.get': {'task': task()}}}
    put(db, receipt(workspace='/projects/alpha'))
    directory.sync([owner], state)
    assert db.execute("SELECT activity_kind FROM history_local_rows WHERE id='manager'").fetchone()[0] == 'waiting'
    counts = workspace_counts(directory, state)
    assert counts['/projects/alpha']['activityCounts']['waiting'] == 1
    before = directory.navigation_revision
    put(db, receipt('other',workspace='/projects/alpha'), 'second')
    directory.sync([owner], state)
    assert directory.navigation_revision > before, 'A second wait must invalidate retained context'
    before = directory.navigation_revision
    db.execute("DELETE FROM commands WHERE id='second'")
    put(db, receipt(workspace='/projects/alpha',response={'status':'staged','outcome':'success'}))
    directory.sync([owner], state)
    assert directory.navigation_revision > before
    assert db.execute("SELECT activity_label FROM history_local_rows WHERE id='manager'").fetchone()[0] == 'Dependency result reported'


@pytest.mark.parametrize('created', [False, True])
async def test_actual_producer_send_and_created_initial_brief(app, created):
    from test_collaborative_workspaces import agent_action, send, settled
    source, peer = app.state['sessions']
    active = {'id':'objective','revision':1,'status':'active','continuationEpoch':1}
    source['task'] = active
    app.state.setdefault('runtimeControl', {})[source['id']] = {'task.get': {'task':active}}
    if created:
        folder = app.data_dir/'sessions'/source['id']
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'effective-configuration.json').write_text(json.dumps({
            'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},
            'providers':[], 'tools':[], 'hooks':[]}))
        result = await agent_action(app, source, 'coordination.create',
            {'title':'Created worker','text':'Check the bounded fixture'}, 'create-wait')
        await settled(app, 'create-wait', 'created')
        identity = result['initialInputId']
        peer_id = result['sessionId']
    else:
        await send(app, 'ordinary-wait', returnNotification=True)
        identity, peer_id = 'ordinary-wait', peer['id']
    actual = await settled(app, identity, 'accepted')
    assert actual['commandAction'] == 'coordination.send'
    assert actual['subscription']['open'] is True
    if created:
        assert actual['creationOperationId'] == 'create-wait'
        assert app.collaboration.receipt('create-wait')['subscription']['status'] == 'linked'
    source.update(status='idle', task=task())
    app.state['runtimeControl'][source['id']]['task.get']['task'] = source['task']
    before = deepcopy(actual)
    waiting = project(app.db, source, source['task'])
    assert [row['sessionId'] for row in waiting['targets']] == [peer_id]
    assert app.collaboration.receipt(identity) == before
    assert source['task']['status'] == 'blocked'


def test_pause_new_task_and_settings_preparation_are_not_dependency_waits(db):
    put(db, receipt())
    for changed in [
        {**task(), 'id':'replacement'},
        {**task(), 'status':'paused'},
        {**task(), 'revision':5, 'continuationEpoch':5, 'dependencyWaitEpoch':4},
    ]:
        assert project(db, session(), changed) is None
    assert activity({**session(),'configurationBusy':True},task=task(),db=db)['kind'] == 'working'


def test_current_task_projection_is_content_free_and_bounded():
    row = {**task(), 'changes': [{'revision':i,'origin':'agent','fields':{}} for i in range(1,100)],
           'revision':99}
    assert wait_epoch(row) is None
    # No history walk when there is no canonical agent block.
    class NoHistory(list):
        def __iter__(self):
            raise AssertionError('walked unrelated task history')
    assert wait_epoch({'status':'paused','changes':NoHistory()}) is None


async def test_configuration_process_exit_retires_old_wait_without_new_input(app):
    from test_collaborative_workspaces import send, settled
    source = app.state['sessions'][0]
    active = {'id':'objective','revision':1,'status':'active','continuationEpoch':1}
    source['task'] = active
    app.state.setdefault('runtimeControl', {})[source['id']] = {'task.get': {'task':active}}
    await send(app, 'config-wait', returnNotification=True)
    await settled(app,'config-wait','accepted')
    source.update(status='idle',task=task())
    app.state['runtimeControl'][source['id']]['task.get']['task'] = source['task']
    assert project(app.db, source, source['task'])
    before_task = deepcopy(source['task'])
    await app.on_runtime_event('runtime.ended',{'sessionId':source['id'],'reason':'configuration'})
    source.update(status='idle',configurationBusy=False)
    assert project(app.db, source, source['task']) is None
    assert source['task'] == before_task


def test_cold_task_read_never_parses_retained_runtime_body(tmp_path):
    from test_history_directory_navigation import directory_fixture
    from amplifier_web.navigation_waiting import save_task, saved_task
    _, directory = directory_fixture(tmp_path, count=1)
    db = directory.db
    save_task(db,'manager',task())
    db.execute("INSERT OR REPLACE INTO state_records VALUES('runtime','manager',?)",
               (json.dumps({'history':['x'*2_000_000], 'task.get':{'task':task()}}),))
    queries=[]
    db.set_trace_callback(queries.append)
    assert saved_task(db,'manager') == task()
    db.set_trace_callback(None)
    assert len(queries)==1 and 'state_records' not in queries[0]
    assert input_boundary({'_navigationInput':['old','input'],
                           '_navigationEndedGeneration':'old'}) is None


async def test_actual_corrected_task_send_then_block_retains_exact_wait(app, monkeypatch):
    from test_task_continuity import controls, create
    from test_collaborative_workspaces import send, settled
    from amplifier_web.task_continuity import observe_task
    source=app.state['sessions'][0]
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    control=controls(source['id'])
    await create(control)
    await control.perform('task.update', {'commandId':'correction','expectedRevision':1,
                          'origin':'agent','correction':'Keep the same objective'})
    observe_task(app,source,control.tasks.canonical_snapshot())
    await send(app,'corrected-wait',returnNotification=True)
    await settled(app,'corrected-wait','accepted')
    await control.perform('task.block', {'commandId':'block-corrected','expectedRevision':2,
                          'origin':'agent','reason':'Pending exact result'})
    original=deepcopy(control.tasks.record())
    observe_task(app,source,control.tasks.canonical_snapshot())
    source['status']='idle'
    from amplifier_web.navigation_waiting import task_for
    assert source['_navigationTask']['dependencyWaitEpoch']==1
    assert 'dependencyWaitEpoch' not in source['task']
    assert project(app.db,source,task_for(app.state, source))['targets'][0]['sessionId']==app.state['sessions'][1]['id']
    assert control.tasks.record()==original


def test_waiting_and_paused_filter_shapes_remain_bounded():
    from amplifier_web.chat_navigation import view_patch
    for kind in ('waiting','paused'):
        view_patch({'navStatusFilter':kind,'workHomeFilter':kind,
                    'navStatusPages':{kind:1},'workHomePages':{kind:1},
                    'navStatusCollapsed':[kind]})


def test_derived_index_preserves_original_receipts_and_three_value_writers(tmp_path):
    path=tmp_path/'existing.sqlite3'
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    original=json.dumps(receipt(),sort_keys=True)
    db.execute('INSERT INTO commands VALUES(?,?,?)',('old','fingerprint',original))
    initialize(db)
    initialize(db)
    db.execute('INSERT INTO commands VALUES(?,?,?)',('new','next',original))
    db.commit();db.close()
    db=sqlite3.connect(path)
    assert db.execute('SELECT id,fingerprint,receipt FROM commands ORDER BY id').fetchall()==[
        ('new','next',original),('old','fingerprint',original)]
    assert project(db,session(),task())['targets'][0]['sessionId']=='worker'
    db.execute('DELETE FROM commands WHERE id=?',('new',))
    db.execute('UPDATE commands SET receipt=? WHERE id=?',
               (json.dumps(receipt(response={'status':'staged','outcome':'success'})),'old'))
    assert project(db,session(),task())['phase'] == 'reported'
    db.rollback()
    assert project(db,session(),task()), 'Derived state must roll back with the canonical receipt'
    db.execute('DELETE FROM commands')
    assert not db.execute('SELECT 1 FROM navigation_dependency_requests').fetchone()
    db.close()


def test_first_population_is_atomic_and_reopen_does_not_reparse_receipts(tmp_path):
    db=sqlite3.connect(tmp_path/'failed-init.sqlite3')
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    original=json.dumps(receipt())
    db.execute('INSERT INTO commands VALUES(?,?,?)',('before','proof',original))
    db.commit()
    def deny_trigger(action,*args):
        return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_CREATE_TRIGGER else sqlite3.SQLITE_OK
    db.set_authorizer(deny_trigger)
    with pytest.raises(sqlite3.DatabaseError):
        initialize(db)
    db.set_authorizer(None)
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='navigation_dependency_requests'").fetchone()
    assert db.execute('SELECT receipt FROM commands').fetchone()[0]==original
    initialize(db)
    assert project(db,session(),task())
    statements=[]
    db.set_trace_callback(statements.append)
    initialize(db)
    db.set_trace_callback(None)
    assert len(statements)==2 and all('sqlite_master' in sql for sql in statements)
    db.close()


@pytest.mark.parametrize('status,qualified,phase', [
    ('staged',False,'reported'), ('sealed',True,'returned'),
])
def test_success_report_keeps_clock_without_claiming_completion(db, status, qualified, phase):
    row = receipt(response={'status':status,'kind':'result','outcome':'success',
                            'qualified':qualified,'outcomeRevision':1})
    # Suppression retires notification authority, not this task's dependency.
    row['subscription'].update(status='suppressed',open=False)
    put(db,row)
    before = db.execute('SELECT receipt FROM commands').fetchall()
    value = activity(session(),task=task(),db=db)
    assert value['kind'] == 'waiting' and value['waiting']['phase'] == phase
    assert 'accepted' not in value['label'].lower()
    assert db.execute('SELECT receipt FROM commands').fetchall() == before


@pytest.mark.parametrize('patch,phase,kind', [
    ({'delivery':'unknown'},'unknown','attention'),
    ({'delivery':'not_sent'},'failed','attention'),
    ({'response':{'status':'sealed','outcome':'failed'}},'failed','attention'),
    ({'response':{'status':'sealed','outcome':'declined'}},'declined','attention'),
    ({'response':{'status':'sealed','outcome':'cancelled'}},'cancelled','attention'),
    ({'response':{'status':'unknown','outcome':'success'}},'unknown','attention'),
    ({'response':{'status':'staged','outcome':'deferred'}},'deferred','waiting'),
    ({'response':{'status':'staged','outcome':'question'}},'decision','attention'),
    ({'response':{'status':'staged','outcome':'conflict'}},'decision','attention'),
    ({'terminalOutcome':{'status':'observed','outcome':'unknown'}},'unknown','attention'),
])
def test_result_dispositions_do_not_hide_failures_or_decisions(db,patch,phase,kind):
    put(db,receipt(**patch))
    value=activity(session(),task=task(),db=db)
    assert value['kind']==kind and value['waiting']['phase']==phase
    if phase=='decision':
        assert value['attentionType']=='decision'


def test_later_generation_needs_explicit_owned_reference_not_dependency_flag(db):
    row=receipt(workspace='/project',response={'status':'sealed','qualified':True,'outcome':'success'})
    put(db,row)
    later={**session(),'workspace':'/project','collaborationGeneration':{'id':'later','inputIds':['fresh']}}
    assert activity(later,task=task(),db=db)=={'kind':'idle','label':'Blocked — details unavailable'}
    owned={**task(),'operationIds':['request']}
    assert activity(later,task=owned,db=db)['waiting']['phase']=='returned'
    for change in ({'workspace':'/other'},{'interruptionRevision':1},{'status':'stopped'}):
        assert activity({**later,**change},task=owned,db=db)['kind']!='waiting'
    for change in ({'id':'replacement'},{'status':'paused'},{'dependencyWaitEpoch':4,'continuationEpoch':5}):
        assert activity(later,task={**owned,**change},db=db)['kind']!='waiting'
    put(db,{**row,'senderSessionId':'other-owner'})
    assert activity(later,task=owned,db=db)['kind']=='idle'


def test_backpressure_many_and_precedence(db):
    row=receipt()
    row['subscription']['status']='backpressured'
    put(db,row)
    assert project(db,session(),task())['phase']=='backpressured'
    put(db,receipt('second',response={'status':'sealed','outcome':'success','qualified':True}),'second')
    assert project(db,session(),task())['phase']=='mixed'
    put(db,receipt('third',response={'status':'sealed','outcome':'failed'}),'third')
    value=activity(session(),task=task(),db=db)
    assert value['kind']=='attention' and len(value['waiting']['targets'])==3
    assert activity({**session(),'questions':[{'required':True,'status':'pending'}]},
                    task=task(),db=db)['label']=='Answer requested'
    assert activity({**session(),'error':'new failure'},task=task(),db=db)['label']=='Needs attention'
    db.execute("DELETE FROM commands WHERE id='third'")
    assert activity({**session(),'error':'old reviewed'},task=task(),error_reviewed=True,db=db)['kind']=='waiting'
    assert activity({**session(),'status':'interrupted'},task=task(),db=db)['label']=='Outcome unknown'


def test_derived_upgrade_rebuilds_old_pending_table_atomically(tmp_path):
    db=sqlite3.connect(tmp_path/'upgrade.sqlite3')
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,receipt TEXT)')
    db.execute('CREATE TABLE navigation_pending_requests(id TEXT PRIMARY KEY)')
    db.execute("CREATE TRIGGER navigation_wait_insert AFTER INSERT ON commands BEGIN INSERT INTO navigation_pending_requests VALUES(new.id); END")
    row=json.dumps(receipt(response={'status':'sealed','qualified':True,'outcome':'success'}))
    db.execute('INSERT INTO commands VALUES(?,?)',('old',row))
    db.commit()
    db.set_authorizer(lambda action,*args:sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_CREATE_TRIGGER else sqlite3.SQLITE_OK)
    with pytest.raises(sqlite3.DatabaseError):
        initialize(db)
    db.set_authorizer(None)
    assert db.execute("SELECT id FROM navigation_pending_requests").fetchall()==[('old',)]
    initialize(db)
    assert project(db,session(),task())['phase']=='returned'
    assert db.execute('SELECT receipt FROM commands').fetchall()==[(row,)]
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='navigation_pending_requests'").fetchone()
    db.close()


def test_lifecycle_publication_index_excludes_growing_settled_history(db):
    from amplifier_web.navigation_waiting import affected_sources
    put(db,receipt())
    db.executemany('INSERT INTO commands VALUES(?,?)', [
        (f'history-{i}',json.dumps(receipt(response={'status':'sealed','outcome':'success'})))
        for i in range(2000)])
    queries=[]
    db.set_trace_callback(queries.append)
    assert affected_sources(db,'worker')=={'manager'}
    db.set_trace_callback(None)
    query=queries[-1]
    plan=db.execute('EXPLAIN QUERY PLAN '+query).fetchall()
    assert any('navigation_dependency_target' in str(row) for row in plan)


def test_explicit_task_reference_survives_cold_directory_without_runtime_body(tmp_path):
    from test_history_directory_navigation import directory_fixture
    from amplifier_web.navigation_waiting import saved_task, task_for
    state,directory=directory_fixture(tmp_path,count=1)
    db=directory.db
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,receipt TEXT)')
    initialize(db)
    owner=state['sessions'][0]
    owner.update(session(),workspace='/projects/alpha')
    owner['collaborationGeneration']={'id':'later','inputIds':['later-input']}
    current={**task(),'operationIds':['request']}
    state['runtimeControl']={'manager':{'task.get':{'task':current}}}
    put(db,receipt(workspace='/projects/alpha',
                   response={'status':'sealed','outcome':'success','qualified':True}))
    directory.sync([owner],state)
    saved=db.execute("SELECT summary FROM history_local_rows WHERE id='manager'").fetchone()[0]
    cold=json.loads(saved)
    cold_task=saved_task(db,'manager')
    assert cold_task['operationIds']==['request']
    assert project(db,cold,cold_task)['phase']=='returned'
    assert task_for(state,owner)==current


async def test_actual_request_return_keeps_blocked_owner_presentation_only(app):
    from test_collaborative_workspaces import send, settled, agent_action, generation
    source,peer=app.state['sessions']
    source['task']={'id':'objective','revision':1,'status':'active','continuationEpoch':1}
    app.state.setdefault('runtimeControl',{})[source['id']]={'task.get':{'task':source['task']}}
    await send(app,'return-wait',returnNotification=True)
    actual=await settled(app,'return-wait','accepted')
    await generation(app,peer,'recipient-return-wait',['return-wait'])
    source.update(status='idle',task=task())
    app.state['runtimeControl'][source['id']]['task.get']['task']=source['task']
    await agent_action(app,peer,'coordination.reply',{'requestId':'return-wait',
        'kind':'result','outcome':'success','text':'Synthetic fixture result'},'return-report')
    actual=app.collaboration.receipt('return-wait')
    before=deepcopy(actual)
    value=activity(source,task=source['task'],db=app.db)
    assert value['kind']=='waiting' and value['waiting']['phase']=='reported'
    assert app.collaboration.receipt('return-wait')==before
    assert source['task']['status']=='blocked'


@pytest.mark.parametrize('unknown', [False, True])
async def test_cancelled_actual_admission_retains_uncertainty_with_another_wait(app, unknown):
    from test_collaborative_workspaces import send, settled
    from test_request_cancellation import cancel
    source,peer=app.state['sessions']
    source['task']={'id':'objective','revision':1,'status':'active','continuationEpoch':1}
    app.state.setdefault('runtimeControl',{})[source['id']]={'task.get':{'task':source['task']}}
    app.runtime.fail=unknown
    await send(app,'cancel-dependency',returnNotification=True)
    await settled(app,'cancel-dependency','unknown' if unknown else 'accepted')
    await cancel(app,'cancel-dependency','cancel-exact')
    saved=deepcopy(app.collaboration.receipt('cancel-dependency'))
    assert saved['cancellation']['status']=='requested'
    other=deepcopy(saved)
    other.update(requestId='pending-other',delivery='accepted')
    other.pop('cancellation',None);other.pop('terminalOutcome',None)
    other['subscription'].update(status='waiting',open=True)
    app.collaboration.insert('pending-other','fixture-other',other)
    source.update(status='idle',task=task())
    value=activity(source,task=source['task'],db=app.db)
    expected='unknown' if unknown else 'cancellation_requested'
    assert value['kind']=='attention' and value['waiting']['phase']==expected
    # Reopen an isolated copied DB: no live app or authority recovery side effect.
    path=app.data_dir/'cancel-navigation.sqlite3'
    cold=sqlite3.connect(path);app.db.commit();app.db.backup(cold);cold.close()
    cold=sqlite3.connect(path)
    assert project(cold,source,source['task'])['phase']==expected
    assert json.loads(cold.execute('SELECT receipt FROM commands WHERE id=?',
                                  ('cancel-dependency',)).fetchone()[0])==saved
    cold.close()


def test_confirmed_cancel_withdrawal_does_not_override_another_pending_wait(db):
    row=receipt(delivery='cancelled',cancellation={'status':'withdrawn'},
                terminalOutcome={'status':'observed','outcome':'cancelled'})
    put(db,row)
    assert project(db,session(),task()) is None
    put(db,receipt('second'),'second')
    assert activity(session(),task=task(),db=db)['kind']=='waiting'


@pytest.mark.parametrize('status', ['error','failed'])
def test_status_only_failure_survives_directory_and_reviewed_old_notice(tmp_path,status):
    from test_history_directory_navigation import directory_fixture
    from amplifier_web.history_directory_query import workspace_counts
    state,directory=directory_fixture(tmp_path,count=0)
    owner={**session(),'workspace':'/projects/alpha','workspaceId':'a',
           'status':status,'sessionKind':'root'}
    state['sessions']=[owner]
    state['runtimeControl']={'manager':{'task.get':{'task':task()}}}
    directory.db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,receipt TEXT)')
    initialize(directory.db)
    for with_wait in (False,True):
        if with_wait:put(directory.db,receipt(workspace='/projects/alpha'))
        directory.sync([owner],state)
        counts=workspace_counts(directory,state)
        assert counts['/projects/alpha']['activityCounts']['attention']==1
        assert directory.db.execute("SELECT base_activity_label FROM history_local_rows WHERE id='manager'").fetchone()[0]=='Needs attention'
        # Removing the resident overlay cannot erase the persisted failure.
        cold={**state,'sessions':[],'runtimeControl':{}}
        assert workspace_counts(directory,cold)['/projects/alpha']['activityCounts']['attention']==1


def test_upgrade_refreshes_dormant_old_block_facts_without_runtime_hydration(tmp_path):
    from test_history_directory_navigation import directory_fixture
    from amplifier_web.navigation_waiting import save_task, refresh_directory
    from amplifier_web.history_directory_query import workspace_counts
    state,directory=directory_fixture(tmp_path,count=0);db=directory.db
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,receipt TEXT)');initialize(db)
    for i in range(2):
        owner={**session(),'id':'manager' if i==0 else 'unbound',
               'workspace':'/projects/alpha','workspaceId':'a','sessionKind':'root'}
        owner['task']=task() if i==0 else {'status':'blocked'}
        directory.sync([owner],state)
        db.execute("UPDATE history_local_rows SET base_activity_kind='attention',base_activity_label='Work blocked',activity_kind='attention',activity_label='Work blocked' WHERE id=?",(owner['id'],))
    put(db,receipt(workspace='/projects/alpha',response={'status':'staged','outcome':'success'}))
    db.execute('DELETE FROM navigation_fact_versions')
    before=db.execute("SELECT revision FROM history_observation_revisions WHERE collection='navigation'").fetchone()[0]
    reads=[]
    db.set_trace_callback(reads.append)
    refresh_directory(db)
    db.set_trace_callback(None)
    facts=db.execute("SELECT id,base_activity_kind,base_activity_label FROM history_local_rows ORDER BY id").fetchall()
    assert facts==[('manager','waiting','Dependency result reported'),('unbound','idle','Blocked — details unavailable')]
    assert not any('state_records' in sql or 'FROM commands' in sql for sql in reads)
    assert db.execute("SELECT revision FROM history_observation_revisions WHERE collection='navigation'").fetchone()[0]>before
    counts=workspace_counts(directory,{**state,'sessions':[],'runtimeControl':{}})
    assert counts['/projects/alpha']['activityCounts']['waiting']==1
    assert counts['/projects/alpha']['activityCounts']['attention']==0
    reads=[];db.set_trace_callback(reads.append);refresh_directory(db);db.set_trace_callback(None)
    assert not any('history_local_rows' in sql for sql in reads)