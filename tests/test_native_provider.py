"""Host identity, private durability, and shared action acceptance."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.native_provider import NativeProviderHost, install_native
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.runtime import normalize_event


class Coordinator:
    session_id='native-test'
    session_state={}
    config={'providers':[]}
    def __init__(self, provider, *, enabled=True):
        self.providers={'configured':provider}
        self.loop=SimpleNamespace(config={'native_provider':enabled},root_provider=None,jobs={})
        self.loop._select_provider=lambda providers:self.loop.root_provider or providers['configured']
        self.messages=[{'role':'user','content':'original'}]
        self.context=SimpleNamespace(get_messages=self.get_messages)
        self.caps={'live.runtime':SimpleNamespace(generation={'id':'generation-1'})}
    async def get_messages(self):return copy.deepcopy(self.messages)
    def get(self,key):return {'providers':self.providers,'orchestrator':self.loop,'context':self.context}.get(key)
    def get_capability(self,key):return self.caps.get(key)
    def register_capability(self,key,value):self.caps[key]=value
    async def mount(self,section,value,name):self.providers[name]=value


def fake():
    return SimpleNamespace(get_info=lambda:SimpleNamespace(defaults={'model':'fixture'}))


@pytest.mark.asyncio
async def test_disabled_default_does_not_load_or_replace_provider(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    c=Coordinator(fake(),enabled=False)
    original=c.providers['configured']
    await install_native(c.loop,c,c.providers)
    status=c.get_capability('web.native_provider').status()
    assert not status['supported'] and status['steering']=='request_boundary'
    assert c.providers['configured'] is original and not list(tmp_path.rglob('*.json'))


@pytest.mark.asyncio
async def test_supported_instance_wrap_is_opt_in_and_preserves_root_selection(tmp_path,monkeypatch):
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_web.host.session import SelectedProvider
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    provider=OpenAIProvider(api_key='fixture',config={'default_model':'gpt-6-astra'})
    c=Coordinator(provider)
    selection={'instance':'configured','model':'gpt-6-astra','effort':'high'}
    c.loop.root_provider=SelectedProvider(provider,copy.deepcopy(selection))
    await install_native(c.loop,c,c.providers)
    host=c.get_capability('web.native_provider')
    assert host.status()['supported']
    from amplifier_module_loop_live.scope import LIVE_OWNER
    token=LIVE_OWNER.set(SimpleNamespace(coordinator=object()))
    try:assert host.provider.owner_getter() is None
    finally:LIVE_OWNER.reset(token)
    owned=SimpleNamespace(coordinator=c)
    token=LIVE_OWNER.set(owned)
    try:assert host.provider.owner_getter() is owned
    finally:LIVE_OWNER.reset(token)
    assert c.loop.root_provider.original is host.provider
    assert c.loop.root_provider.selection==selection
    assert host.provider._api_key=='fixture' and provider._client is None
    # An explicit model/effort change must not use the previous identity's state.
    c.loop.root_provider.selection['effort']='low'
    assert host.status()['steering']=='request_boundary'
    with pytest.raises(ValueError):await host.compact()


@pytest.mark.asyncio
@pytest.mark.parametrize('model,endpoint',[('gpt-5.6-terra',None),('gpt-6-astra','https://proxy.example/v1')])
async def test_unsupported_selected_model_or_endpoint_falls_back_without_switch(tmp_path,monkeypatch,model,endpoint):
    from amplifier_module_provider_openai import OpenAIProvider
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    p=OpenAIProvider(api_key='fixture',config={'default_model':model,**({'base_url':endpoint} if endpoint else {})})
    c=Coordinator(p)
    await install_native(c.loop,c,c.providers)
    assert c.providers['configured'] is p and p._client is None
    assert c.get_capability('web.native_provider').status()['steering']=='request_boundary'


@pytest.mark.asyncio
async def test_durable_receipts_reconcile_restart_unknown_without_replay(tmp_path):
    c=Coordinator(fake())
    host=NativeProviderHost(c,c.loop,tmp_path)
    host.identity=host.selected_identity()
    await host.lifecycle('native.submitting',{'input':'private text excluded'})
    await host.lifecycle('steering.accepted',{'input_id':'input-2','steer_id':'s'})
    saved=host.path.read_text()
    assert 'private text' not in saved
    restored=NativeProviderHost(c,c.loop,tmp_path)
    assert restored.status()['receipt']['outcome']=='unknown'
    assert restored.state['generationId']=='generation-1'
    assert restored.state['receipts'][-1]['input_id']=='input-2'
    assert c.messages==[{'role':'user','content':'original'}]
    # A recorded final receipt remains final on restart.
    restored.identity=restored.selected_identity()
    await restored.lifecycle('native.completed',{'response_id':'r','pending_steering':False})
    assert NativeProviderHost(c,c.loop,tmp_path).state['outcome']=='completed'


@pytest.mark.asyncio
async def test_shared_control_compacts_only_idle_and_never_returns_private_opaque(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    c=Coordinator(fake())
    host=NativeProviderHost(c,c.loop,tmp_path)
    host.identity=host.selected_identity()
    host.provider=SimpleNamespace(native_status=lambda:{'supported':True,'compactAvailable':True,'checkpoint':'created'},
        native_compact=AsyncMock(return_value={'checkpoint':'created'}),native_export_checkpoint=lambda:{'output':'opaque-secret'})
    c.caps['live.checkpoint']=AsyncMock()
    runtime=SimpleNamespace(generation=None,queued_inputs=0)
    controls=RuntimeControls(SimpleNamespace(session_id=c.session_id,coordinator=c),runtime)
    controls.checkpoint=AsyncMock()
    result=await controls.perform('native.compact')
    assert 'opaque-secret' not in json.dumps(result)
    assert json.loads(host.checkpoint_path.read_text())['record']['output']=='opaque-secret'
    assert host.checkpoint_path.stat().st_mode & 0o777 == 0o600
    c.caps['live.checkpoint'].assert_awaited_once()
    runtime.generation={'id':'active'}
    with pytest.raises(ValueError,match='Wait'):await controls.perform('native.compact')
    host.provider.native_compact.assert_awaited_once()
    assert (await controls.perform('native.status'))['supported']
    await controls.close()


def test_public_steering_event_reports_identity_not_content():
    event=normalize_event({'type':'steering.applied','input_id':'i','response_id':'r','steer_id':'s',
                           'encrypted_content':'secret','input':'private'},'owned')
    assert event==('runtime.steering',{'sessionId':'owned','event':'steering.applied','input_id':'i','response_id':'r','steer_id':'s'})


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['native.status','native.compact'])
async def test_agent_runtime_alias_cannot_target_other_session_or_forge_actor(tmp_path,operation):
    from amplifier_web.service import AppService, AppError
    from amplifier_web.management import Management
    class Runtime:
        async def close(self):pass
        control=AsyncMock()
    runtime=Runtime()
    app=AppService(tmp_path,runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{})
        own=app._session()['id']
        await app.dispatch('session.create',{})
        other=app._session()['id']
        with pytest.raises(AppError,match='calling conversation'):
            await app.app_bridge('dispatch',{'action':'runtime.control','args':{
                'sessionId':other,'operation':operation,'args':{'actor':'user','source':'user'}}},own)
        runtime.control.assert_not_awaited()
        assert app.state['selectedSessionId']==other
    finally:await app.close()
