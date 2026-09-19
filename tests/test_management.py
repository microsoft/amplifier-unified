from pathlib import Path
import json
import pytest
from amplifier_web.service import AppService
from amplifier_web.management import Management
from amplifier_web.host.storage import SessionStore
from amplifier_web.notifications import Notifications
from amplifier_web.preferences import SettingsStore

class Runtime:
    async def stop(self,*args):pass
    async def close(self):pass

@pytest.fixture
async def app(tmp_path):
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    service.management=Management(service)
    await service.dispatch('session.create',{'bundle':'anchors-amp-dev'})
    yield service
    await service.close()

async def test_scoped_file_permissions_persist_without_changing_root_bundle(app,tmp_path):
    await app.management.perform('permissions.save',{'scope':'project','allowed':[str(tmp_path/'output')],'denied':[str(tmp_path/'private')]})
    saved=SettingsStore(app.data_dir).read(tmp_path,'project')
    assert saved['overrides']['tool-filesystem']['config']['denied_write_paths']==[str(tmp_path/'private')]
    assert app.state['sessions'][0]['bundle']=='anchors-amp-dev'

async def test_history_import_uses_real_checkpoint_and_does_not_execute(app):
    store=SessionStore(app.data_dir/'sessions')
    store.save('import-test',[{'role':'user','content':'Earlier question'},{'role':'assistant','content':'Earlier answer'}],{'bundle_name':'anchors','working_dir':app.default_workspace})
    await app.management.perform('history.import',{'id':'import-test'})
    assert app.state['selectedSessionId']=='import-test'
    assert app._session()['status']=='stopped'
    assert app._session()['messages'][-1]['text']=='Earlier answer'

async def test_shared_history_uses_the_isolated_probe_without_starting_a_worker(app):
    class ProbeRuntime(Runtime):
        def __init__(self):self.requests=[]
        async def shared_state_probe(self,request):
            self.requests.append(request)
            return {'items':[{'id':'shared-id','workspace':request['workspace'],'shared':True}]}
        async def start(self,*args):raise AssertionError('A history view must not start a worker')
    app.runtime=ProbeRuntime()
    await app.management.perform('history.shared.list',{'workspace':app.default_workspace})
    assert app.runtime.requests==[{'version':1,'op':'list','workspace':app.default_workspace}]
    assert app.state['sharedHistory']['items'][0]['id']=='shared-id'

async def test_open_shared_root_keeps_identity_workspace_and_authority(app, tmp_path):
    workspace = tmp_path / 'other-workspace'
    workspace.mkdir()
    class ProbeRuntime(Runtime):
        async def shared_state_probe(self, request):
            assert request['op'] == 'open'
            return {'id': 'shared-root', 'workspace': str(workspace),
                    'bundle': 'portable-bundle', 'offset': 12, 'totalMessages': 112,
                    'messages': [{'role': 'user', 'text': 'CLI latest turn'}]}
        async def start(self, *args):
            raise AssertionError('Opening history must not mount a writer')
    app.runtime = ProbeRuntime()
    await app.management.perform('history.shared.open', {
        'id': 'shared-root', 'workspace': str(workspace)})
    selected = app._session()
    assert selected['id'] == 'shared-root'
    assert selected['workspace'] == str(workspace)
    assert selected['bundle'] == 'portable-bundle'
    assert selected['messages'][0]['text'] == 'CLI latest turn'
    assert app.state['settings']['workspace'] == str(workspace)
    assert not (app.data_dir / 'sessions' / 'shared-root' / 'checkpoint.json').exists()
    await app.management.perform('history.shared.open', {
        'id': 'shared-root', 'workspace': str(workspace)})
    assert sum(row['id'] == 'shared-root' for row in app.state['sessions']) == 1

async def test_turn_fork_excludes_later_messages(app):
    session=app._session()
    for role,text in [('user','one'),('assistant','answer one'),('user','two'),('assistant','answer two')]:app._message(session,role,text)
    await app.dispatch('session.fork',{'id':session['id'],'turn':1})
    assert [m['text'] for m in app._session()['messages']]==['one','answer one']

def test_notification_credentials_are_private_and_not_in_public_state(tmp_path):
    notifications=Notifications(tmp_path)
    public=notifications.save({'enabled':True,'topic':'private-topic','token':'secret-token','server':'https://notify.example'})
    assert 'topic' not in public and 'token' not in public
    assert public['topicConfigured'] and public['tokenConfigured']
    assert notifications.path.stat().st_mode & 0o777==0o600
    with pytest.raises(ValueError):notifications.save({'server':'http://insecure.example'})

async def test_transcript_file_import_keeps_tool_evidence_without_execution(app):
    rows=[{'role':'user','content':'Inspect'}, {'role':'assistant','content':'','tool_calls':[{'id':'call','function':{'name':'bash','arguments':'{}'}}]}, {'role':'tool','tool_call_id':'call','content':'done'}, {'role':'assistant','content':'Finished'}]
    await app.management.perform('history.importFile',{'content':json.dumps({'messages':rows,'metadata':{'bundle_name':'anchors'}}),'format':'json'})
    session=app._session()
    saved=SessionStore.for_app(app.data_dir,session['workspace']).load(session['id'])
    assert saved[0]==rows and session['status']=='stopped'
    assert saved[1]['jobs_replayed'] is False
    assert [m['text'] for m in session['messages']]==['Inspect','Finished']

async def test_import_hides_reminders_only_in_display_and_retains_literal_quotes(app):
    text='<system-reminder>Quoted by the user</system-reminder>'
    rows=[{'role':'user','content':text,'metadata':{'ephemeral':True,'persisted':True}},
          {'role':'user','content':text}, {'role':'assistant','content':'A literal quote'}]
    await app.management.perform('history.importFile',{'content':json.dumps(rows),'format':'json'})
    session=app._session()
    assert [m['text'] for m in session['messages']]==[text,'A literal quote']
    assert [m['nativeIndex'] for m in session['messages']]==[1,2]
    assert SessionStore.for_app(app.data_dir,session['workspace']).load(session['id'])[0]==rows

async def test_provider_status_survives_unrelated_operations_and_does_not_block_quick_checks(app,monkeypatch):
    import asyncio
    from amplifier_web.setup import SetupManager
    entered=asyncio.Event();finish=asyncio.Event()
    async def perform(self,action,args):
        if action=='providers.models':
            entered.set();await finish.wait()
            raise ValueError('Provider check timed out. Please retry.')
        return {'credentialCheck':{'module':args['module'],'available':True}}
    monkeypatch.setattr(SetupManager,'perform',perform)
    models=asyncio.create_task(app.management.command('providers.models',{'id':'openai'},'models'))
    await entered.wait()
    await asyncio.wait_for(app.management.command('providers.credentials',{'module':'provider-openai'},'env'),1)
    assert app.state['setup']['operations']['providers.models:openai']['phase']=='working'
    assert app.state['setup']['operations']['providers.credentials:provider-openai']['phase']=='ready'
    finish.set();await models
    await app.management.command('notifications.get',{},'notifications')
    assert app.state['management']['phase']=='ready'
    assert app.state['setup']['operations']['providers.models:openai']['error']=='Provider check timed out. Please retry.'

async def test_old_provider_results_do_not_replace_a_newer_check(app,monkeypatch):
    import asyncio
    from amplifier_web.setup import SetupManager
    entered=asyncio.Event();finish=asyncio.Event()
    async def perform(self,action,args):
        if args['envVar']=='OLD_KEY':entered.set();await finish.wait()
        return {'credentialCheck':{'module':args['module'],'envVar':args['envVar'],'available':True}}
    monkeypatch.setattr(SetupManager,'perform',perform)
    old=asyncio.create_task(app.management.command('providers.credentials',{'module':'provider-openai','envVar':'OLD_KEY'},'old'))
    await entered.wait()
    await app.management.command('providers.credentials',{'module':'provider-openai','envVar':'NEW_KEY'},'new')
    finish.set();await old
    assert app.state['setup']['credentialCheck']['envVar']=='NEW_KEY'
    assert app.state['setup']['operations']['providers.credentials:provider-openai']['commandId']=='new'

async def test_locations_browse_files_and_folders_without_reading_contents(app,tmp_path):
    folder=tmp_path/'workspace';folder.mkdir();(folder/'sub').mkdir();(folder/'file.json').write_text('private-content');(folder/'.hidden').write_text('private')
    await app.management.perform('locations.list',{'controlId':'fixture','path':str(folder)})
    listing=app.state['locationListing']
    assert [row['name'] for row in listing['entries']]==['sub','file.json']
    assert 'private-content' not in str(listing)
    await app.management.perform('locations.list',{'controlId':'fixture','path':str(folder),'directoriesOnly':True})
    assert [row['name'] for row in app.state['locationListing']['entries']]==['sub']

async def test_running_mount_plan_changes_queue_then_remount_same_conversation(app):
    import asyncio,copy
    plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},'providers':[{'module':'provider-fixture'}],'tools':[{'module':'tool-fixture','enabled':False,'config':{'limit':2}}]}
    calls=[]
    class LiveRuntime:
        async def start(self,session,emit):calls.append(('start',session['id']))
        async def stop(self,sid):calls.append(('stop',sid))
        async def close(self):pass
        async def control(self,sid,op,args):
            calls.append((op,sid))
            if op=='configuration.apply':assert args['config']==plan;return {'requiresRestart':True}
            return {'plan':copy.deepcopy(plan)}
    app.runtime=LiveRuntime();session=app._session();session['status']='working'
    app._message(session,'user','Preserve this conversation')
    await app.management.perform('configuration.apply',{'id':session['id'],'config':plan,'whenIdle':True})
    assert session['pendingConfiguration']['phase']=='queued' and not calls
    session['status']='idle'
    task=app.management.pending_config_tasks[session['id']]
    await asyncio.wait_for(task,2)
    assert session['pendingConfiguration']['phase']=='ready'
    assert session['configuration']['plan']==plan
    assert session['messages'][0]['text']=='Preserve this conversation'
    assert all(sid==session['id'] for _,sid in calls)
    assert ('stop',session['id']) in calls

async def test_queued_mount_changes_can_be_cancelled(app):
    plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},'providers':[{'module':'provider-fixture'}]}
    session=app._session();session['status']='working'
    await app.management.perform('configuration.apply',{'id':session['id'],'config':plan,'whenIdle':True})
    await app.management.perform('configuration.cancel',{'id':session['id']})
    assert not app.management.queued_path(session['id']).exists()
    assert 'pendingConfiguration' not in session


async def test_native_export_uses_backup_without_repair_or_execution(app):
    from unittest.mock import AsyncMock
    from amplifier_web.session_files import project_slug
    session = app._session()
    store = SessionStore.for_app(app.data_dir, session['workspace'])
    rows = [{'role': 'user', 'content': 'fixture export'}, {'role': 'assistant', 'content': 'answer'}]
    store.save(session['id'], rows, {'bundle': 'anchors'})
    directory = store.directory(session['id'])
    (directory / 'transcript.jsonl').rename(directory / 'transcript.jsonl.backup')
    session.update(nativeProject=project_slug(session['workspace']), nativeIdentity=session['id'])
    before = (directory / 'transcript.jsonl.backup').read_bytes()
    app.management.download = AsyncMock()
    await app.management.perform('history.export', {'id': session['id'], 'format': 'json'})
    exported = json.loads(app.management.download.call_args.args[1])
    assert exported['messages'] == rows
    assert not (directory / 'transcript.jsonl').exists()
    assert (directory / 'transcript.jsonl.backup').read_bytes() == before
    assert any(row['code'] == 'recovered_backup' for row in app.state['historyDiagnostics'])
