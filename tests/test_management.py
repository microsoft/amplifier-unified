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
    saved=SessionStore(app.data_dir/'sessions').load(session['id'])
    assert saved[0]==rows and session['status']=='stopped'
    assert saved[1]['jobs_replayed'] is False
    assert [m['text'] for m in session['messages']]==['Inspect','Finished']

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
