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
