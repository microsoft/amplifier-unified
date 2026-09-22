import copy
import json
import asyncio
import threading
import sqlite3
import pytest
from amplifier_web.service import AppService
from amplifier_web.diagnostics import DEFAULT,validate_config
from context_intelligence.client import CIClientError
from test_service import Runtime


def destination(identity='personal',**patch):
    return {'id':identity,'name':identity,'url':'http://127.0.0.1:18081','enabled':True,'streams':['updates'],'workspace':identity,**patch}


@pytest.fixture
async def service(tmp_path):
    svc=AppService(tmp_path,Runtime(),workspace=tmp_path)
    yield svc
    await svc.close()


async def configure(service,*dest,**patch):
    cfg={**copy.deepcopy(DEFAULT),'destinations':list(dest),**patch}
    return await service.dispatch('diagnostics.configure',{'config':cfg})


async def test_default_local_capture_never_infers_remote_destination(service,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL','https://must-not-use.invalid')
    collector=service.diagnostics
    collector.record('updates',{'event':'update:test','data':{'phase':'probe','stderr':'secret','argv':['secret']}})
    collector.record('conversation',{'event':'content:user','data':{'text':'private prompt'}})
    await collector.flush()
    records=collector.read()
    assert len(records['items'])==1
    assert records['items'][0]['stream']=='updates'
    assert 'secret' not in json.dumps(records) and 'private prompt' not in json.dumps(records)
    assert collector.config['destinations']==[]
    with collector._db() as db:assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0]==0


async def test_auxiliary_failure_keeps_label_and_safe_category_without_error_payload(service):
    collector=service.diagnostics
    collector.runtime_event('execution.event', {'id':'summary-call','kind':'llm','phase':'error',
        'label':'Context compaction','failure':{'errorType':'ContextLengthError','category':'context_limit',
        'summary':'private prompt must never leak','raw':'secret'}}, {'id':'fixture','workspace':service.default_workspace})
    await collector.flush()
    records=collector.read()['items']
    row=next(row for row in records if row['event']=='llm:error')
    assert row['data']['errorType']=='ContextLengthError'
    assert row['data']['errorCode']=='context_limit'
    assert row['data']['label']=='Context compaction'
    assert 'private prompt' not in json.dumps(records) and 'secret' not in json.dumps(records)


@pytest.mark.parametrize('kind,payload_kind,label',[
    ('execution.event','llm','private prompt content'),
    ('execution.event','worker','private prompt content'),
    ('worker.updated','worker','Context compaction'),
])
async def test_runtime_metadata_omits_arbitrary_labels(service,kind,payload_kind,label):
    collector=service.diagnostics
    collector.runtime_event(kind,{'id':'fixture-label','kind':payload_kind,
        'phase':'completed','label':label}, {'id':'fixture','workspace':service.default_workspace})
    await collector.flush()
    rows=[row for row in collector.read()['items'] if row['data'].get('id')=='fixture-label']
    assert rows
    assert all('label' not in row['data'] for row in rows)
    assert 'private prompt' not in json.dumps(rows)


async def test_independent_stream_routes_and_paths(service):
    await configure(service,destination(),destination('team',streams=['usage'],includePaths=True))
    collector=service.diagnostics
    collector.record('updates',{'event':'update:probe','data':{'attemptId':'a'}})
    collector.record('usage',{'event':'llm:response','data':{'usage':{'inputTokens':10,'raw':'secret'},'model':'example'}})
    await collector.flush()
    with collector._db() as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM deliveries')]
    assert len(rows)==2
    personal=next(json.loads(r['payload']) for r in rows if r['destination']=='personal')
    team=next(json.loads(r['payload']) for r in rows if r['destination']=='team')
    assert personal['data']['stream']=='updates' and 'working_dir' not in personal
    assert team['data']['stream']=='usage' and team['working_dir']==service.default_workspace
    assert 'secret' not in json.dumps(team)


async def test_route_changes_cancel_pending_and_do_not_replay_history(service):
    await configure(service,destination())
    collector=service.diagnostics
    collector.record('updates',{'event':'update:one','data':{}})
    await collector.flush()
    await configure(service,destination(url='https://other.example.com'))
    with collector._db() as db:
        row=db.execute('SELECT * FROM deliveries').fetchone()
        assert row['status']=='cancelled' and row['payload']=='{}'
    await service.dispatch('diagnostics.retry',{'id':'personal'})
    with collector._db() as db:assert db.execute("SELECT count(*) FROM deliveries WHERE status='pending'").fetchone()[0]==0
    collector.record('updates',{'event':'update:two','data':{}})
    await collector.flush()
    with collector._db() as db:assert db.execute("SELECT count(*) FROM deliveries WHERE status='pending'").fetchone()[0]==1


async def test_capture_disable_revokes_queue_and_restart_retains_local_records(service):
    await configure(service,destination())
    collector=service.diagnostics
    collector.record('updates',{'event':'update:one','data':{}})
    await collector.flush()
    await configure(service,destination(),enabled=False)
    collector.record('updates',{'event':'update:two','data':{}})
    await collector.flush()
    assert len(collector.read()['items'])==1
    with collector._db() as db:assert db.execute('SELECT status FROM deliveries').fetchone()[0]=='cancelled'
    from amplifier_web.diagnostics import Diagnostics
    restarted=Diagnostics(service)
    assert restarted.config['enabled'] is False
    assert len(restarted.read()['items'])==1
    await restarted.close()


async def test_retry_retains_exact_idempotent_payload_and_failure_classification(service,monkeypatch):
    await configure(service,destination())
    collector=service.diagnostics
    collector.record('updates',{'event':'update:test','data':{'phase':'staging'}})
    await collector.flush()
    sent=[]
    class Client:
        async def ingest(self,payload):
            sent.append(copy.deepcopy(payload))
            if len(sent)==1:raise CIClientError('secret details',error_type='timeout',url='secret')
            return {'status':'queued'}
    monkeypatch.setattr(collector,'_client',lambda _:Client())
    dest=collector.config['destinations'][0]
    await collector.deliver(dest)
    with collector._db() as db:
        row=db.execute('SELECT * FROM deliveries').fetchone()
        assert row['status']=='pending' and 'secret' not in row['error']
        db.execute('UPDATE deliveries SET next_at=0')
    await collector.deliver(dest)
    assert sent[0]==sent[1]
    with collector._db() as db:
        row=db.execute('SELECT * FROM deliveries').fetchone()
        assert row['status']=='accepted' and row['payload']=='{}' and row['error'] is None


async def test_bad_auth_requires_explicit_retry_and_never_blocks_other_destination(service,monkeypatch):
    await configure(service,destination(),destination('team'))
    collector=service.diagnostics
    collector.record('updates',{'event':'update:test','data':{}})
    await collector.flush()
    class Client:
        def __init__(self,identity):self.identity=identity
        async def ingest(self,payload):
            if self.identity=='personal':raise CIClientError('private error',error_type='http_status',url='',status_code=401)
            return {'status':'duplicate'}
    monkeypatch.setattr(collector,'_client',lambda d:Client(d['id']))
    for dest in collector.config['destinations']:await collector.deliver(dest)
    with collector._db() as db:
        assert dict(db.execute('SELECT destination,status FROM deliveries'))=={'personal':'failed','team':'duplicate'}
    await service.dispatch('diagnostics.retry',{'id':'personal'})
    with collector._db() as db:assert db.execute("SELECT status FROM deliveries WHERE destination='personal'").fetchone()[0]=='pending'


async def test_content_opt_in_redaction_and_bounded_agent_read(service,monkeypatch):
    monkeypatch.setenv('TEST_API_KEY','private-test-secret')
    await configure(service,streams=[*DEFAULT['streams'],'conversation'])
    await service.dispatch('session.create',{})
    session=service._session()
    service._message(session,'user','Hello private-test-secret')
    await service.diagnostics.flush()
    result=await service.app_bridge('dispatch',{'action':'diagnostics.records','args':{'stream':'conversation'}},session['id'])
    assert result['result']['items'][0]['data']['prompt']=='Hello [REDACTED]'
    assert 'private-test-secret' not in json.dumps(result['result'])
    exported=await service.dispatch('diagnostics.export')
    assert exported['effects'][0]['mime']=='application/x-ndjson'
    assert len(exported['effects'][0]['content'].splitlines())==1
    from context_intelligence import CaptureLocator,read_native_transcript
    capture=service.data_dir/'capture-proof';capture.mkdir()
    (capture/'events.jsonl').write_text(exported['effects'][0]['content'])
    (capture/'metadata.json').write_text(json.dumps({'format':'context-intelligence','version':'1.0.0','session_id':session['id'],'workspace':session['workspace']}))
    transcript=read_native_transcript(CaptureLocator.from_session_dir(capture))
    assert transcript.messages[0].content=='Hello [REDACTED]'


async def test_child_session_correlates_with_app_session_and_parent(service):
    await service.dispatch('session.create',{})
    session=service._session();session['runtimeSessionId']='runtime-root'
    await service.on_runtime_event('execution.event',{'id':'call','kind':'llm','phase':'completed','sessionId':'child','rootSessionId':session['id'],'parentId':'delegate','usage':{'totalTokens':10}})
    await service.diagnostics.flush()
    row=service.diagnostics.read(stream='usage')['items'][0]
    assert row['session']=='child'
    assert row['data']['parent_id']=='runtime-root' and row['data']['appSessionId']==session['id']


def test_destination_validation_prevents_embedded_credentials_and_unselected_content():
    for patch in [{'url':'https://token:secret@example.com'}, {'url':'https://example.com?api_key=secret'}, {'url':'http://example.com'}, {'streams':['conversation']}]:
        with pytest.raises(ValueError):validate_config({**DEFAULT,'destinations':[destination(**patch)]})


async def test_queue_retention_and_disk_failure_are_visible_and_do_not_break_work(service,monkeypatch):
    collector=service.diagnostics
    for _ in range(2001):collector.record('updates',{'event':'update:test','data':{}})
    assert collector.dropped==1
    monkeypatch.setattr(collector,'_persist',lambda *args:(_ for _ in ()).throw(OSError('disk full private path')))
    await collector.flush()
    assert collector.storage_error and collector.dropped==2001
    await service.dispatch('session.create',{})
    assert service._session()['status']=='idle'


async def test_disabling_after_queue_read_cannot_send_detached_cancelled_payload(service,monkeypatch):
    await configure(service,destination())
    collector=service.diagnostics
    collector.record('updates',{'event':'update:race','data':{}})
    await collector.flush()
    started=threading.Event();release=threading.Event();sent=[]
    original=collector._next
    def paused(dest):
        row=original(dest);started.set();assert release.wait(5);return row
    class Client:
        async def ingest(self,payload):sent.append(payload);return {'status':'queued'}
    monkeypatch.setattr(collector,'_next',paused)
    monkeypatch.setattr(collector,'_client',lambda _:Client())
    task=asyncio.create_task(collector.deliver(copy.deepcopy(collector.config['destinations'][0])))
    try:
        assert await asyncio.to_thread(started.wait,5)
        await configure(service,destination(enabled=False))
        # Re-enabling exactly the original route cannot revive a detached row.
        await configure(service,destination())
    finally:
        release.set()
        await task
    assert sent==[]
    with collector._db() as db:assert db.execute('SELECT status FROM deliveries').fetchone()[0]=='cancelled'


async def test_revocation_cancels_owned_delivery_awaiting_auth_before_http(service,monkeypatch):
    await configure(service,destination())
    collector=service.diagnostics
    collector.record('updates',{'event':'update:auth-race','data':{}})
    await collector.flush()
    auth_started=asyncio.Event();auth_release=asyncio.Event();sent=[]
    class Client:
        async def ingest(self,payload):
            auth_started.set();await auth_release.wait()
            sent.append(payload);return {'status':'queued'}
    monkeypatch.setattr(collector,'_client',lambda _:Client())
    task=asyncio.create_task(collector.deliver(copy.deepcopy(collector.config['destinations'][0])))
    collector.delivery_tasks['personal']=task
    await asyncio.wait_for(auth_started.wait(),5)
    await configure(service,destination(url='https://changed.example.com'))
    auth_release.set()
    assert task.cancelled() and sent==[]
    with collector._db() as db:assert db.execute('SELECT status FROM deliveries').fetchone()[0]=='cancelled'


async def test_reconfiguration_cancels_rows_from_an_earlier_persist_snapshot(service,monkeypatch):
    await configure(service,destination())
    collector=service.diagnostics
    collector.record('updates',{'event':'update:persist-race','data':{}})
    started=threading.Event();release=threading.Event();original=collector._persist;first=True
    def paused(items,cfg):
        nonlocal first
        if first:first=False;started.set();assert release.wait(5)
        return original(items,cfg)
    monkeypatch.setattr(collector,'_persist',paused)
    flush=asyncio.create_task(collector.flush())
    assert await asyncio.to_thread(started.wait,5)
    changed=asyncio.create_task(configure(service,destination(),enabled=False))
    await asyncio.sleep(0)
    assert not changed.done()
    release.set()
    await asyncio.gather(flush,changed)
    with collector._db() as db:
        assert db.execute('SELECT count(*) FROM records').fetchone()[0]==1
        assert db.execute('SELECT status FROM deliveries').fetchone()[0]=='cancelled'


async def test_failed_revocation_persistence_is_not_reported_as_saved(service,monkeypatch):
    await configure(service,destination())
    collector=service.diagnostics
    saved=collector.config_path.read_text()
    monkeypatch.setattr(collector,'_persist',lambda *args:(_ for _ in ()).throw(OSError('private disk error')))
    with pytest.raises(Exception,match='new settings were not saved'):
        await configure(service,destination(),enabled=False)
    assert collector.config_path.read_text()==saved
    assert collector.config['enabled'] is True
    assert collector.storage_error


async def test_unreadable_diagnostics_database_preserved_without_blocking_app(tmp_path):
    directory=tmp_path/'diagnostics';directory.mkdir()
    path=directory/'events.sqlite3';original=b'not a sqlite database: retain these bytes'
    path.write_bytes(original)
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    try:
        collector=service.diagnostics
        assert collector.storage_error and not collector.storage_ready
        assert service.state['diagnostics']['local']['storageError']
        await service.dispatch('session.create',{})
        assert service._session()['status']=='idle'
        collector.record('updates',{'event':'update:while-unavailable','data':{}})
        assert collector.dropped==2  # session.create and the explicit record
        assert not collector.pending
        for action,args in [('diagnostics.records',{}),('diagnostics.retry',{'id':'missing'})]:
            with pytest.raises(Exception,match='storage is unavailable'):
                await service.dispatch(action,args)
        result=await service.dispatch('diagnostics.environment',{'name':'UNSET_DIAGNOSTICS_FIXTURE_KEY'})
        assert result['result']['phase']=='error'
        await collector.publish()
        assert service.state['diagnostics']['local']['records'] is None
        assert service.state['diagnostics']['local']['storageError']
    finally:await service.close()
    assert path.read_bytes()==original


async def test_unavailable_capture_directory_does_not_block_startup_or_close(tmp_path):
    path=tmp_path/'diagnostics';path.write_text('preserve this existing file')
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    try:
        assert service.diagnostics.storage_error
        await service.dispatch('session.create',{})
        await service.diagnostics.flush()
        assert service._session()['status']=='idle'
    finally:await service.close()
    assert path.read_text()=='preserve this existing file'


async def test_background_summary_failure_is_visible_and_recovers_next_cycle(service,monkeypatch):
    collector=service.diagnostics;original=collector._read_summary;fail=True
    def summary():
        nonlocal fail
        if fail:fail=False;raise sqlite3.OperationalError('synthetic private disk location')
        return original()
    monkeypatch.setattr(collector,'_read_summary',summary)
    collector.start()
    async with asyncio.timeout(5):
        while not service.state['diagnostics']['local'].get('storageError'):await asyncio.sleep(.01)
        assert not collector.task.done()
        assert 'private disk' not in json.dumps(service.state['diagnostics'])
        while service.state['diagnostics']['local'].get('storageError'):await asyncio.sleep(.01)
    assert collector.storage_ready and not collector.task.done()
    collector.record('updates',{'event':'update:after-recovery','data':{}})
    await collector.flush()
    assert collector.read(stream='updates')['items'][0]['event']=='update:after-recovery'


async def test_outbox_storage_failure_is_caught_without_network_or_task_failure(service,monkeypatch):
    await configure(service,destination())
    collector=service.diagnostics
    def broken(dest):raise sqlite3.OperationalError('private disk location')
    monkeypatch.setattr(collector,'_next',broken)
    monkeypatch.setattr(collector,'_client',lambda _:pytest.fail('No network client should be constructed'))
    await collector.deliver(copy.deepcopy(collector.config['destinations'][0]))
    assert collector.storage_error and not collector.storage_ready


@pytest.mark.parametrize('failed',[False,True])
async def test_changed_destination_discards_late_test_success_or_failure(service,monkeypatch,failed):
    await configure(service,destination())
    collector=service.diagnostics;started=asyncio.Event();release=asyncio.Event()
    class Client:
        async def whoami(self):return {'contributor_id':'fixture'}
        async def ingest(self,payload):
            started.set();await release.wait()
            if failed:raise CIClientError('private old server error',error_type='timeout',url='')
            return {'status':'queued'}
    monkeypatch.setattr(collector,'_client',lambda _:Client())
    generation=collector.policy_generation
    task=asyncio.create_task(collector.test('personal',generation))
    await asyncio.wait_for(started.wait(),5)
    await configure(service,destination(url='https://changed.example.com'))
    release.set()
    assert await task is None
    assert collector.results=={}
    # A test queued against the former generation must not resolve a new route.
    monkeypatch.setattr(collector,'_client',lambda _:pytest.fail('Stale test must not construct a network client'))
    assert await collector.test('personal',generation) is None


def test_malformed_runtime_metadata_cannot_break_main_event_handler(service):
    service.diagnostics.runtime_event('execution.event',{'kind':'llm','usage':'not a mapping'}, {'id':'fixture','workspace':service.default_workspace})
    assert service.diagnostics.dropped==1


async def test_native_navigation_does_not_enroll_captures_or_poison_app_diagnostics(service, tmp_path, monkeypatch):
    from amplifier_web import capture_index
    from amplifier_web.session_files import amplifier_home
    await service.dispatch('session.create', {})
    owned=service._session()
    owned['workers']=[{'id':'owned-worker'}, {'id':'legacy:worker'}]
    historical_workspace=str(tmp_path/'unavailable-cli-folder')
    native=[{'id':f'index-{i}','runtimeSessionId':f'root:worker-{i}',
             'nativeIdentity':f'root:worker-{i}','nativeProject':'historical-project',
             'workspace':None if i%2 else historical_workspace,'historyManaged':True,
             'status':'idle','messages':[],'workers':[]} for i in range(5000)]
    service.state['sessions'].extend(native)
    indexed=[]
    def index_shared(db, scopes, config):
        indexed.extend(scopes)
        return []
    monkeypatch.setattr(capture_index,'index_shared',index_shared)
    native_root=amplifier_home()/'projects'/'historical-project'
    assert not native_root.exists()
    collector=service.diagnostics
    collector.record('sessions',{'event':'app:view','data':{'action':'session.select'}},
                     session_id='root:worker-1',workspace=None)
    # An otherwise valid root identity is also observational when index-only.
    native[0]['runtimeSessionId']=native[0]['nativeIdentity']='historical-root'
    collector.record('sessions',{'event':'app:view','data':{'action':'session.select'}},
                     session_id='historical-root',workspace=historical_workspace)
    await collector.flush()
    assert not collector.storage_error
    assert set(indexed)=={(owned['workspace'],owned['id']),(owned['workspace'],'owned-worker')}
    rows=[r for r in collector.read()['items'] if r['event']=='app:view']
    assert len(rows)==2
    assert {r['session'] for r in rows}=={collector.instance_id}
    assert {r['data']['runtimeSessionId'] for r in rows}=={'root:worker-1','historical-root'}
    assert not native_root.exists()
    assert not (tmp_path/'unavailable-cli-folder').exists()
