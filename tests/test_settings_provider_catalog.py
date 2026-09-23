"""Provider onboarding uses ecosystem modules and preserves authored routing."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from amplifier_web.setup import SetupManager,KNOWN_PROVIDER_SOURCES

@pytest.mark.asyncio
async def test_builtin_routing_download_is_cached_and_never_copied_to_custom(tmp_path,monkeypatch):
    manager=SetupManager(tmp_path/'app')
    config=SimpleNamespace(registry_home=tmp_path/'registry',resolve_source=lambda source:None)
    monkeypatch.setattr(manager,'config',lambda workspace:config)
    bundle=tmp_path/'registry/cache/amplifier-bundle-routing-matrix-fixture/routing'
    async def download(registry,config,source):
        assert source=='git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main'
        bundle.mkdir(parents=True)
        (bundle/'balanced.yaml').write_text('roles: {}')
    load=AsyncMock(side_effect=download)
    monkeypatch.setattr('amplifier_web.host.session.session_registry',lambda config:object())
    monkeypatch.setattr('amplifier_web.host.session.load_configured_bundle',load)
    await manager.ensure_routing_catalog(tmp_path)
    await manager.ensure_routing_catalog(tmp_path)
    assert load.await_count==1
    assert not (manager.store.shared_home/'routing').exists()

@pytest.mark.asyncio
async def test_catalog_failure_keeps_saved_profiles_available(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'shared'))
    manager=SetupManager(tmp_path/'app')
    matrix={'roles':{role:{'description':role,'candidates':[{'provider':'local','model':'local-model'}]} for role in ('general','fast')}}
    await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'personal','matrix':matrix,'activate':True})
    monkeypatch.setattr(manager,'ensure_routing_catalog',AsyncMock(side_effect=TimeoutError))
    result=await manager.perform('routing.list',{'workspace':str(tmp_path)})
    assert result['active']=='personal'
    assert result['matrices'][0]['name']=='personal'
    assert result['routingCatalogError']

@pytest.mark.asyncio
async def test_compatible_provider_saves_endpoint_without_requiring_or_inventing_key(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'shared'))
    manager=SetupManager(tmp_path/'app')
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-chat-completions','id':'local','config':{'base_url':'http://localhost:1234/v1'}})
    row=manager.config(tmp_path).providers[0]
    assert row['source']==KNOWN_PROVIDER_SOURCES['provider-chat-completions']
    assert row['config']=={'base_url':'http://localhost:1234/v1'}

@pytest.mark.asyncio
async def test_device_login_publishes_code_then_removes_it_after_success(tmp_path,monkeypatch):
    import sys
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'shared'))
    manager=SetupManager(tmp_path/'app')
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai-chatgpt','id':'chatgpt','config':{}})
    script=tmp_path/'auth.py'
    script.write_text('import sys,json\njson.loads(sys.stdin.readline())\nprint(json.dumps({"status":"waiting","instruction":"Enter code: TEST-1234"}),flush=True)\nprint(json.dumps({"status":"completed"}),flush=True)\n')
    manager.auth_command=[sys.executable,str(script)]
    updates=[]
    async def progress(value):updates.append(value['login'])
    manager.progress=progress
    await manager.perform('providers.login',{'workspace':str(tmp_path),'id':'chatgpt'})
    await manager.logins['chatgpt']['task']
    assert any(row.get('deviceCode')=='TEST-1234' for row in updates)
    assert updates[-1]['status']=='completed'
    assert 'deviceCode' not in updates[-1] and not updates[-1]['instructions']
