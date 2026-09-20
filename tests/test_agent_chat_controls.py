"""The agent uses exactly the same rename and provider-selection actions as UI."""
import asyncio
import pytest
from amplifier_web.service import AppService
from amplifier_web.management import Management

class Runtime:
    def __init__(self):self.selection=None
    async def start(self,session,emit):
        await emit('runtime.status',{'sessionId':session['id'],'status':'ready'})
    async def control(self,sid,operation,args):
        if operation=='provider.select':self.selection=args.copy();return {'selection':self.selection}
        if operation=='configuration.providers':return {'providers':[],'selection':self.selection,'effective':self.selection,'pinned':bool(self.selection)}
        return {}
    async def close(self):pass

async def test_agent_can_rename_select_model_and_inspect_visible_draft(tmp_path):
    runtime=Runtime();app=AppService(tmp_path,runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{})
        sid=app._session()['id']
        await app.app_bridge('dispatch',{'action':'session.rename','args':{'id':sid,'title':'Agent named chat'}},sid)
        assert app._session()['title']=='Agent named chat'
        await app.app_bridge('dispatch',{'action':'runtime.control','args':{'sessionId':sid,'operation':'provider.select','args':{'instance':'fixture','model':'agent-model','effort':'high'}}},sid)
        for _ in range(100):
            if app.state.get('actionStatus',{}).get('runtime.control',{}).get('phase') in {'ready','error'}:break
            await asyncio.sleep(.01)
        assert app.state['actionStatus']['runtime.control']['phase']=='ready'
        assert runtime.selection=={'instance':'fixture','model':'agent-model','effort':'high'}
        assert app.state['runtimeControl'][sid]['configuration.providers']['effective']==runtime.selection
        await app.update_device({'clientId':'fixture','controls':[{'label':'New name for Agent named chat','value':'Visible draft'}]})
        observed=await app.app_bridge('get_state',{'path':'/devices/fixture/controls'},sid)
        assert 'Visible draft' in str(observed)
    finally:await app.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['kernels.create','kernels.execute','kernels.interrupt','operations.cancel'])
async def test_private_runtime_alias_cannot_bypass_shared_operation_authority(tmp_path, operation):
    from amplifier_web.service import AppService, AppError
    from unittest.mock import AsyncMock
    app=AppService(tmp_path/'app',workspace=tmp_path,runtime=Runtime())
    try:
        await app.dispatch('session.create',{});first=app._session()['id']
        await app.dispatch('session.create',{});other=app._session()['id']
        app.runtime.control=AsyncMock()
        for target in [first,other]:
            with pytest.raises(AppError,match='shared computation'):
                await app.app_bridge('dispatch',{'action':'runtime.control','args':{'sessionId':target,'operation':operation,'args':{'actor':'ui','language':'python'}}},first)
        app.runtime.control.assert_not_awaited()
    finally:await app.close()
