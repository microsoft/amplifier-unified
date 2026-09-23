"""New settings capabilities share the user/agent dispatch path and state."""
import asyncio
import pytest
from amplifier_web.service import AppService
from amplifier_web.management import Management
from amplifier_web.setup import SetupManager
from amplifier_web.smart_tools import SmartToolsManager
from amplifier_web.smart_canvas import SmartCanvas

async def settle(app):
    for _ in range(100):
        tasks=list(app.tasks)
        if not tasks:return
        await asyncio.gather(*tasks)
    pytest.fail('App actions did not settle')

async def test_agent_can_install_batch_and_reorder_saved_settings(tmp_path,monkeypatch):
    async def probe(self,action,args,workspace):
        return {'models':[],'modelsProviderId':args.get('id')}
    monkeypatch.setattr(SetupManager,'probe',probe)
    app=AppService(tmp_path,workspace=tmp_path);app.management=Management(app);app.smart_tools=SmartToolsManager(app);app.smart_canvas=SmartCanvas(app)
    async def install(args):return {'id':args['repository'].rsplit('/',1)[-1]}
    monkeypatch.setattr(app.smart_tools,'install',install)
    try:
        await app.dispatch('session.create',{});sid=app.state['selectedSessionId']
        for name in ['one','two']:
            await app.dispatch('providers.save',{'id':name,'module':'provider-test','config':{}})
            await settle(app)
        await app.app_bridge('dispatch',{'action':'providers.reorder','args':{'ids':['two','one'],'expectedIds':['one','two']}},sid)
        await settle(app)
        assert [row['id'] for row in app.state['setup']['providers']]==['two','one']
        app.state['smartTools']['catalog']=[{'id':'tool','name':'Tool','repository':'https://example.com/tool'}]
        await app.app_bridge('dispatch',{'action':'smartTools.installBatch','id':'agent-batch','args':{'ids':['tool']}},sid)
        await settle(app)
        receipt=app.smart_tools.operation('agent-batch')
        assert receipt['origin']=='agent' and receipt['items'][0]['status']=='completed'
        observed=await app.app_bridge('get_state',{'path':'/smartTools/operations'},sid)
        assert 'agent-batch' in str(observed)
    finally:await app.close()

async def test_guided_provider_finish_has_correlated_user_and_agent_receipts(tmp_path,monkeypatch):
    async def probe(self,action,args,workspace):
        return {'models':[{'id':'fixture-model'}],'modelsProviderId':args.get('id')}
    monkeypatch.setattr(SetupManager,'probe',probe)
    app=AppService(tmp_path,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{});sid=app.state['selectedSessionId']
        saved=await app.dispatch('providers.save',{'id':'guided','module':'provider-test','config':{'opaque':'keep'},'apiKey':'private-guided-value'})
        await settle(app)
        assert saved['operationId']
        assert app.state['setup']['operations']['providers.save:guided']['commandId']==saved['operationId']
        receipt=await app.app_bridge('dispatch',{'action':'providers.finishSetup','id':'agent-guided','args':{'id':'guided','model':'fixture-model','initializeRouting':True}},sid)
        await settle(app)
        assert receipt['operationId']=='agent-guided'
        assert app.state['setup']['operations']['providers.finishSetup:guided']['phase']=='ready'
        row=next(p for p in app.state['setup']['providers'] if p['id']=='guided')
        assert row['config']['opaque']=='keep' and row['config']['default_model']=='fixture-model'
        assert 'private-guided-value' not in str(app.browser_state())
    finally:await app.close()

async def test_voice_and_notification_refinements_use_shared_actions(tmp_path,monkeypatch):
    from amplifier_web.service import AppError
    from amplifier_web.preferences import SettingsStore
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'shared'))
    app=AppService(tmp_path/'app',workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('settings.update',{'patch':{'preferredVoice':'gpt-live-1','voiceName':'willow'}})
        assert app.state['settings']['voiceName']=='willow'
        with pytest.raises(AppError,match='available'):
            await app.dispatch('settings.update',{'patch':{'preferredVoice':'gpt-realtime-2.1','voiceName':'willow'}})
        await app.dispatch('settings.update',{'patch':{'preferredVoice':'gpt-realtime-2.1','voiceInterruptions':False}})
        assert app.state['settings']['voiceName']=='marin'
        assert app.state['settings']['voiceInterruptions'] is False
        saved=SettingsStore(app.data_dir).read(tmp_path,'global')['voice']
        assert saved['voice']=='marin' and saved['interruptions'] is False
        receipt=await app.dispatch('notifications.save',{'patch':{'desktop':True,'enabled':False}},command_id='notify-refinement')
        assert receipt['operationId']=='notify-refinement'
        await settle(app)
        assert app.state['actionStatus']['notifications.save']['phase']=='ready'
        await app.dispatch('view.update',{'patch':{'settingsRootVisit':'root-visit','aiConnectionEditor':{'step':'list'}}})
        assert app.state['view']['settingsRootVisit']=='root-visit'
    finally:
        await app.close()
