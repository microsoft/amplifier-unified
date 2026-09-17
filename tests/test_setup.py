import os
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from amplifier_web.setup import SetupManager,validate_matrix
from amplifier_web.host.config import merge

@pytest.fixture
def manager(tmp_path,monkeypatch):
    manager=SetupManager(tmp_path/'home')
    def config(workspace):
        value={}
        for scope in ('global','project','local'):value=merge(value,manager.store.read(workspace,scope))
        return SimpleNamespace(settings=value,providers=value.get('config',{}).get('providers',[]))
    monkeypatch.setattr(manager,'config',config)
    return manager

@pytest.mark.asyncio
async def test_keys_are_private_and_redacted_across_scopes(manager,tmp_path,monkeypatch):
    monkeypatch.delenv('AMPLIFIER_FIRST_API_KEY',raising=False)
    result=await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'first','config':{'model':'example','api_key':'secret-test'},'scope':'project'})
    assert result['providers'][0]['config']['api_key']=='[REDACTED]'
    assert 'secret-test' not in str(result)
    assert result['providers'][0]['credentialsConfigured']
    settings=manager.store.read(tmp_path,'project')
    assert settings['config']['providers'][0]['config']['api_key']=='${AMPLIFIER_FIRST_API_KEY}'
    keyfile=manager.home/'config/keys.env'
    assert keyfile.stat().st_mode & 0o777 == 0o600
    assert 'secret-test' in keyfile.read_text()
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'first','config':{'model':'edited','api_key':'[REDACTED]'},'scope':'project'})
    assert manager.store.read(tmp_path,'project')['config']['providers'][0]['config']['api_key']=='${AMPLIFIER_FIRST_API_KEY}'

@pytest.mark.asyncio
async def test_provider_removal_tombstones_inherited_instance(manager,tmp_path):
    args={'workspace':str(tmp_path),'module':'provider-test','id':'one','config':{}}
    await manager.perform('providers.save',args)
    result=await manager.perform('providers.remove',{'workspace':str(tmp_path),'id':'one','scope':'local'})
    assert result['providers'][0]['enabled'] is False
    assert manager.store.read(tmp_path,'global')['config']['providers'][0]['enabled'] is True
    assert manager.store.read(tmp_path,'local')['overrides']['one']['enabled'] is False

@pytest.mark.asyncio
async def test_models_and_test_use_actual_runtime_provider_contract(manager,tmp_path):
    calls=[]
    async def runtime(op,args):
        calls.append((op,args))
        return {'models':[{'id':'provider-discovered-model'}]} if op.endswith('Models') else {'reachable':True,'modelCount':1}
    manager.runtime_operation=runtime
    result=await manager.perform('providers.models',{'id':'one','workspace':str(tmp_path),'sessionId':'s'})
    assert result['models'][0]['id']=='provider-discovered-model'
    result=await manager.perform('providers.test',{'id':'one','workspace':str(tmp_path),'sessionId':'s'})
    assert result['test']['reachable']
    assert calls[0]==('configuration.providerModels',{'provider':'one','sessionId':'s'})

MATRIX={'description':'Custom policy','roles':{'general':{'description':'General','candidates':[{'provider':'one','model':'gpt-*','config':{'reasoning_effort':'high'}}]},'fast':{'description':'Quick','candidates':[{'provider':'one','model':'small'}]}}}

@pytest.mark.asyncio
async def test_routing_roundtrip_and_dynamic_roles(manager,tmp_path):
    await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'personal','matrix':MATRIX})
    await manager.perform('routing.use',{'workspace':str(tmp_path),'name':'personal'})
    result=await manager.perform('routing.show',{'workspace':str(tmp_path),'name':'personal'})
    assert result['matrix']['roles']==MATRIX['roles']
    listing=await manager.perform('routing.list',{'workspace':str(tmp_path)})
    assert listing['active']=='personal'
    assert listing['roles']==['fast','general']

@pytest.mark.parametrize('value',[{}, {'roles':{'general':{}}}, {'roles':{'general':{'description':'x','candidates':['base']},'fast':{'description':'x','candidates':[]}}}])
def test_matrix_validation_rejects_invalid_community_contract(value):
    with pytest.raises(ValueError):validate_matrix(value)

@pytest.mark.asyncio
async def test_login_progress_and_cancel_preserve_owned_path(manager,tmp_path):
    import asyncio,sys
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai-chatgpt','id':'chatgpt','config':{}})
    script=tmp_path/'auth-fixture.py'
    script.write_text('import sys,json,time\nr=json.loads(sys.stdin.readline())\nassert "/config/openai-chatgpt-chatgpt-oauth.json" in r["tokenFile"]\nprint(json.dumps({"status":"waiting","instruction":"Open https://auth.openai.com/codex/device and enter TEST-CODE"}),flush=True)\ntime.sleep(30)\n')
    manager.auth_command=[sys.executable,str(script)]
    updates=[]
    async def progress(value):updates.append(value)
    manager.progress=progress
    initial=await manager.perform('providers.login',{'id':'chatgpt','workspace':str(tmp_path)})
    assert initial['login']['status']=='starting'
    for _ in range(100):
        if any(row['login'].get('url') for row in updates):break
        await asyncio.sleep(.01)
    assert updates[-1]['login']['url']=='https://auth.openai.com/codex/device'
    cancelled=await manager.perform('providers.loginCancel',{'id':'chatgpt','workspace':str(tmp_path)})
    assert cancelled['login']['status']=='cancelled'
    assert manager.logins['chatgpt']['process'].returncode is not None
    await manager.close()

@pytest.mark.asyncio
async def test_local_routing_shadows_project_without_mutating_it(manager,tmp_path):
    import copy
    await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'custom','scope':'project','matrix':MATRIX})
    local=copy.deepcopy(MATRIX);local['description']='Local only'
    await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'custom','scope':'local','matrix':local})
    result=await manager.perform('routing.show',{'workspace':str(tmp_path),'name':'custom'})
    assert result['matrix']['description']=='Local only'
    assert yaml.safe_load((tmp_path/'.amplifier-unified/routing/custom.yaml').read_text())['description']=='Custom policy'
