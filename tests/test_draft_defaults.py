import types
from pathlib import Path
import pytest
from amplifier_web.draft_defaults_probe import query

@pytest.mark.asyncio
async def test_composed_defaults_use_provider_priority_without_session_or_inference(tmp_path,monkeypatch):
    from amplifier_web.host import config,session
    from amplifier_web import provider_environment as env
    workspace=tmp_path/'not-created'
    conf=types.SimpleNamespace(workspace=tmp_path)
    monkeypatch.setattr(config,'load_config',lambda *a,**k:conf)
    async def root(c,bundle,**kwargs):
        assert bundle=='exact-saved';assert c.workspace==workspace
        return None,types.SimpleNamespace(providers=[
            {'module':'provider-test','id':'slow','config':{'priority':100,'model':'slow-model'}},
            {'module':'provider-test','instance_id':'fast','config':{'priority':1,'model':'saved-model','reasoning_effort':'high'}}]),None
    monkeypatch.setattr(session,'load_root_bundle',root)
    closed=[]
    class Provider:
        def __init__(self,config):self.config=config;self.priority=config.get('priority',100)
        def get_info(self):return {'id':'test','display_name':'Test','defaults':{'model':self.config.get('model')},'api_key':'private'}
        def get_config_schema(self):return {'fields':[{'id':'reasoning_effort','choices':['high'],'field_type':'choice','required':False},{'id':'api_key','field_type':'secret','required':False,'default':'private'}]}
        def list_models(self):raise AssertionError('No network model discovery')
        async def close(self):closed.append(self)
    monkeypatch.setattr(env,'provider_class',lambda module:Provider)
    result=await query({'home':str(tmp_path),'workspace':str(workspace),'bundle':'exact-saved'})
    assert result['effective']=={'instance':'fast','model':'saved-model','effort':'high'}
    assert len(closed)==4
    assert 'private' not in str(result)
    assert not workspace.exists()
    assert list(tmp_path.iterdir())==[]

@pytest.mark.asyncio
async def test_default_action_is_read_only_and_keyed_to_requested_draft(tmp_path,monkeypatch):
    from amplifier_web.server import create_app
    from amplifier_web import draft_defaults
    workspace=tmp_path/'workspace';workspace.mkdir()
    async def resolve(home,path,bundle,app_bundle):
        return {'bundle':bundle or 'work','providers':[],'effective':{'instance':'one','model':'actual'}}
    monkeypatch.setattr(draft_defaults,'resolve_defaults',resolve)
    app=await create_app(tmp_path/'app',workspace=str(workspace),voice=False,background_updates=False)
    service=app['service'];before=[s['id'] for s in service.state['sessions']]
    future=workspace/'future'
    try:
        await service.management.command('configuration.defaults',{'workspace':str(future),'bundle':''})
        import json
        key=json.dumps([str(future),''],separators=(',',':'))
        assert service.state['draftDefaults'][key]['effective']['model']=='actual'
        assert [s['id'] for s in service.state['sessions']]==before
        assert not future.exists()
    finally:
        await service.runtime.close()
        for task in list(service.tasks):task.cancel()
