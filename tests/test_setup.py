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
        return SimpleNamespace(resolve_source=lambda reference:None,module_sources={},settings=value,providers=value.get('config',{}).get('providers',[]))
    monkeypatch.setattr(manager,'config',config)
    async def cached_catalog(workspace):pass
    monkeypatch.setattr(manager,'ensure_routing_catalog',cached_catalog)
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
    keyfile=manager.store.shared_home/'keys.env'
    assert keyfile.stat().st_mode & 0o777 == 0o600
    assert 'secret-test' in keyfile.read_text()
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'first','config':{'model':'edited','api_key':'[REDACTED]'},'scope':'project'})
    assert manager.store.read(tmp_path,'project')['config']['providers'][0]['config']['api_key']=='${AMPLIFIER_FIRST_API_KEY}'

@pytest.mark.asyncio
async def test_nested_image_configuration_roundtrips_without_replacing_chat_instance(manager,tmp_path,monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config={'default_model':'kept-chat-model','reasoning_effort':'high','image_generation':{'enabled':True,'id':'images','model':'chosen-image-model'}}
    result=await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'kept-instance','config':config,'scope':'project'})
    row=next(row for row in result['providers'] if row['id']=='kept-instance')
    assert row['config']==config
    saved=manager.store.read(tmp_path,'project')['config']['providers'][0]
    assert saved['id']=='kept-instance' and saved['config']==config


@pytest.mark.asyncio
async def test_provider_removal_tombstones_inherited_instance(manager,tmp_path):
    args={'workspace':str(tmp_path),'module':'provider-test','id':'one','config':{}}
    await manager.perform('providers.save',args)
    result=await manager.perform('providers.remove',{'workspace':str(tmp_path),'id':'one','scope':'local'})
    assert result['providers'][0]['enabled'] is False
    assert 'one' not in manager.store.read(tmp_path,'global')['configurator']['disabled']['providers']
    assert manager.store.read(tmp_path,'local')['configurator']['disabled']['providers'] == ['one']

@pytest.mark.asyncio
async def test_models_and_test_use_isolated_provider_without_starting_session(manager,tmp_path,monkeypatch):
    import sys
    monkeypatch.setenv('TEAM_CHECK_KEY','secret-fixture-value')
    await manager.perform('providers.save',{'id':'one','module':'provider-openai','config':{'base_url':'https://example.test/v1'},'apiKeyEnv':'TEAM_CHECK_KEY','workspace':str(tmp_path)})
    child=tmp_path/'probe.py'
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']['api_key']=='${TEAM_CHECK_KEY}'; assert r['config']['base_url']=='https://example.test/v1'; print(json.dumps({'info':{},'configSchema':{'fields':[]},'models':[{'id':'discovered'}],'test':{'reachable':True,'modelCount':1}}))")
    manager.probe_command=[sys.executable,str(child)]
    async def runtime(*args):raise AssertionError('Must not start a conversation')
    manager.runtime_operation=runtime
    result=await manager.perform('providers.models',{'id':'one','workspace':str(tmp_path),'sessionId':'s'})
    assert result['models'][0]['id']=='discovered' and result['modelsProviderId']=='one'
    assert 'secret-fixture-value' not in str(result)
    result=await manager.perform('providers.test',{'id':'one','workspace':str(tmp_path),'sessionId':'s'})
    assert result['test']['reachable'] and result['test']['providerId']=='one'
    assert manager.store.read(tmp_path)['config']['providers'][0]['config']['api_key']=='${TEAM_CHECK_KEY}'

@pytest.mark.asyncio
async def test_schema_does_not_need_configured_credentials(manager,tmp_path):
    import sys
    child=tmp_path/'probe.py'
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']=={}; print(json.dumps({'info':{},'configSchema':{'fields':[{'id':'effort','choices':['low','high']}]}}))")
    manager.probe_command=[sys.executable,str(child)]
    result=await manager.perform('providers.schema',{'module':'provider-openai','workspace':str(tmp_path)})
    assert result['providerMetadata']['configSchema']['fields'][0]['choices']==['low','high']


@pytest.mark.asyncio
async def test_schema_uses_only_requested_instances_endpoint_and_no_saved_secrets(manager,tmp_path):
    import sys
    for identity in ('one', 'two'):
        await manager.perform('providers.save', {'id': identity, 'module': 'provider-vllm',
            'config': {'base_url': f'https://{identity}.test/v1/', 'custom_secret': 'private-value'},
            'apiKeyEnv': 'UNSET_SCHEMA_KEY', 'workspace': str(tmp_path)})
    child=tmp_path/'probe.py'
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']=={'base_url':'https://two.test/v1/'}; print(json.dumps({'info':{},'configSchema':{'fields':[]}}))")
    manager.probe_command=[sys.executable,str(child)]
    result=await manager.perform('providers.schema', {'module': 'provider-vllm', 'id': 'two', 'workspace': str(tmp_path)})
    assert result['providerMetadata']['configSchema']=={'fields':[]}
    # Requesting another module cannot borrow the saved account's endpoint.
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']=={}; print(json.dumps({'info':{},'configSchema':{'fields':[]}}))")
    await manager.perform('providers.schema', {'module': 'provider-openai', 'id': 'two', 'workspace': str(tmp_path)})

@pytest.mark.asyncio
async def test_provider_probe_errors_are_visible(manager,tmp_path):
    import sys
    child=tmp_path/'probe.py';child.write_text("import json; print(json.dumps({'error':'Provider setup failed (AuthenticationError). Check the saved credentials and endpoint, then retry.'}))")
    manager.probe_command=[sys.executable,str(child)]
    with pytest.raises(ValueError,match='AuthenticationError'):
        await manager.perform('providers.schema',{'module':'provider-openai','workspace':str(tmp_path)})


@pytest.mark.parametrize('exit_code,output', [(1, ''), (0, 'not-json')])
async def test_provider_probe_missing_result_does_not_claim_installation_is_running(manager,tmp_path,exit_code,output):
    import sys
    child=tmp_path/'failed-probe.py'
    child.write_text(f'import sys; print({output!r}, end=""); print("credential-must-not-leak", file=sys.stderr); sys.exit({exit_code})')
    manager.probe_command=[sys.executable,str(child)]
    with pytest.raises(ValueError) as failure:
        await manager.perform('providers.schema',{'module':'provider-openai','workspace':str(tmp_path)})
    message=str(failure.value)
    assert f'exit code {exit_code}' in message
    assert 'update the app' in message
    assert 'dependencies finish installing' not in message
    assert 'credential-must-not-leak' not in message


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
    assert yaml.safe_load((tmp_path/'.amplifier/routing/custom.yaml').read_text())['description']=='Custom policy'

@pytest.mark.asyncio
async def test_default_env_is_detected_and_saved_as_reference_without_copying_secret(manager,tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','existing-private-key')
    status=await manager.perform('providers.credentials',{'module':'provider-openai','workspace':str(tmp_path)})
    assert status['credentialCheck']['defaultEnvVar']=='OPENAI_API_KEY'
    assert status['credentialCheck']['available']
    result=await manager.perform('providers.save',{'module':'provider-openai','config':{},'workspace':str(tmp_path)})
    row=result['providers'][0]
    assert row['credentialsConfigured'] and row['credential']['envVar']=='OPENAI_API_KEY'
    assert manager.store.read(tmp_path)['config']['providers'][0]['config']['api_key']=='${OPENAI_API_KEY}'
    assert not (manager.store.shared_home/'keys.env').exists()
    assert 'existing-private-key' not in str(status)+str(result)

@pytest.mark.asyncio
async def test_custom_env_overrides_saved_key_and_missing_values_are_honest(manager,tmp_path,monkeypatch):
    monkeypatch.setenv('TEAM_MODEL_KEY','private-team-key')
    monkeypatch.delenv('MISSING_MODEL_KEY',raising=False)
    args={'module':'provider-anthropic','id':'team','config':{},'workspace':str(tmp_path)}
    await manager.perform('providers.save',{**args,'apiKey':'old-private-key'})
    result=await manager.perform('providers.save',{**args,'apiKeyEnv':'TEAM_MODEL_KEY'})
    assert result['providers'][0]['credential']['available']
    assert manager.store.read(tmp_path)['config']['providers'][0]['config']['api_key']=='${TEAM_MODEL_KEY}'
    from amplifier_web.host.config import expand_environment
    assert expand_environment(manager.store.read(tmp_path)['config']['providers'][0]['config'])['api_key']=='private-team-key'
    result=await manager.perform('providers.save',{**args,'apiKeyEnv':'MISSING_MODEL_KEY'})
    assert not result['providers'][0]['credentialsConfigured']
    assert 'private-team-key' not in str(result)
    with pytest.raises(ValueError):await manager.perform('providers.save',{**args,'apiKeyEnv':'BAD-NAME'})
    with pytest.raises(ValueError):await manager.perform('providers.save',{**args,'apiKeyEnv':'TEAM_MODEL_KEY','apiKey':'ambiguous'})

@pytest.mark.asyncio
async def test_implicit_environment_and_provider_fallback_are_reported_without_values(manager,tmp_path,monkeypatch):
    monkeypatch.delenv('GOOGLE_API_KEY',raising=False)
    monkeypatch.setenv('GEMINI_API_KEY','private-google-key')
    manager.store.update(tmp_path,'global',lambda s:s.update(config={'providers':[{'module':'provider-gemini','config':{}}]}))
    row=manager.provider_rows(tmp_path)[0]
    assert row['credentialsConfigured'] and row['credential']['envVar']=='GEMINI_API_KEY'
    assert row['credential']['defaultEnvVar']=='GOOGLE_API_KEY'
    assert 'private-google-key' not in str(row)

@pytest.mark.asyncio
async def test_probe_references_implicit_environment_credential_without_serializing_its_value(manager,tmp_path,monkeypatch):
    import sys
    monkeypatch.setenv('OPENAI_API_KEY','private-openai-key')
    manager.store.update(tmp_path,'global',lambda s:s.update(config={'providers':[{'id':'one','module':'provider-openai','config':{}}]}))
    child=tmp_path/'probe.py'
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']['api_key']=='${OPENAI_API_KEY}'; print(json.dumps({'info':{},'configSchema':{'fields':[]},'models':[{'id':'discovered'}]}))")
    manager.probe_command=[sys.executable,str(child)]
    result=await manager.perform('providers.models',{'id':'one','workspace':str(tmp_path)})
    assert result['models']==[{'id':'discovered'}]


def test_explicit_copilot_environment_choice_wins_over_ambient_default(monkeypatch):
    from amplifier_web.host import session
    monkeypatch.setattr(session,'_copilot_credential',None)
    monkeypatch.setenv('COPILOT_AGENT_TOKEN','ambient')
    session.apply_provider_environment({'providers':[{'module':'provider-github-copilot','config':{'github_token':'selected'}}]})
    assert os.environ['COPILOT_AGENT_TOKEN']=='selected'
    with pytest.raises(ValueError,match='separate conversations'):
        session.apply_provider_environment({'providers':[{'module':'provider-github-copilot','config':{'github_token':'different'}}]})

def test_copilot_credentials_include_nested_agent_providers(monkeypatch):
    from amplifier_web.host import session
    monkeypatch.setattr(session,'_copilot_credential',None)
    with pytest.raises(ValueError,match='separate conversations'):
        session.apply_provider_environment({
            'providers':[{'module':'provider-github-copilot','config':{'github_token':'root'}}],
            'agents':{'worker':{'providers':[{'module':'provider-github-copilot','config':{'github_token':'nested'}}]}},
        })


@pytest.mark.asyncio
async def test_shared_key_file_is_detected_without_returning_value(tmp_path,monkeypatch):
    name='UNIFIED_TEST_SAVED_KEY'
    monkeypatch.delenv(name,raising=False)
    home=tmp_path/'home';(home/'config').mkdir(parents=True)
    shared=Path(os.environ['AMPLIFIER_HOME']);shared.mkdir()
    (shared/'keys.env').write_text(name+'=private-file-value\n')
    manager=SetupManager(home)
    result=await manager.perform('providers.credentials',{'module':'provider-openai','envVar':name,'workspace':str(tmp_path)})
    assert result['credentialCheck']['available'] and 'private-file-value' not in str(result)
    monkeypatch.delenv(name)

@pytest.mark.asyncio
async def test_provider_order_persists_without_copying_inherited_credentials(tmp_path):
    from amplifier_web.host.config import write_private
    home=tmp_path/'home'
    write_private(Path(os.environ['AMPLIFIER_HOME'])/'settings.yaml',yaml.safe_dump({'config':{'providers':[{'id':name,'module':'provider-openai','config':{'api_key':'${PRIVATE_KEY}'}} for name in ['one','two','three']]}}))
    manager=SetupManager(home)
    result=await manager.perform('providers.move',{'workspace':str(tmp_path),'scope':'project','id':'one','beforeId':None})
    assert [row['id'] for row in result['providers']]==['two','three','one']
    saved=manager.store.read(tmp_path,'project')
    assert 'provider_order' not in saved
    assert [row['config'] for row in saved['config']['providers']]==[{'priority':1},{'priority':2},{'priority':3}]
    assert 'PRIVATE_KEY' not in str(saved)
    with pytest.raises(ValueError):await manager.perform('providers.move',{'workspace':str(tmp_path),'id':'missing'})

@pytest.mark.asyncio
async def test_active_routing_can_be_loaded_edited_saved_and_used(manager,tmp_path):
    await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'custom','matrix':MATRIX,'activate':True})
    active=await manager.perform('routing.use',{'workspace':str(tmp_path),'name':'custom'})
    assert active['active']=='custom' and active['matrix']['name']=='custom'
    edited=active['matrix'];edited['roles']['general']['candidates'][0]['model']='edited-model'
    result=await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'custom','matrix':edited,'activate':True})
    assert result['matrix']['roles']['general']['candidates'][0]['model']=='edited-model'
    assert manager.store.read(tmp_path)['routing']['matrix']=='custom'


async def test_complete_provider_order_is_atomic_and_rejects_stale_list(tmp_path):
    manager=SetupManager(tmp_path)
    args={'workspace':str(tmp_path)}
    for name in ('one','two','three'):
        await manager.perform('providers.save',{**args,'id':name,'module':'provider-test','config':{'custom':name}})
    result=await manager.perform('providers.reorder',{**args,'ids':['three','one','two'],'expectedIds':['one','two','three']})
    assert [row['id'] for row in result['providers']]==['three','one','two']
    assert result['providers'][0]['config']['custom']=='three'
    before=manager.store.read(tmp_path)
    with pytest.raises(ValueError,match='changed'):
        await manager.perform('providers.reorder',{**args,'ids':['two','one','three'],'expectedIds':['one','two','three']})
    assert manager.store.read(tmp_path)==before
    with pytest.raises(ValueError):
        await manager.perform('providers.reorder',{**args,'ids':['three','three','two'],'expectedIds':['three','one','two']})


async def test_model_discovery_for_a_future_workspace_does_not_create_it(manager,tmp_path):
    import sys
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'one','config':{}})
    child=tmp_path/'draft-probe.py'
    child.write_text("import os,json; print(json.dumps({'info':{},'configSchema':{'fields':[]},'models':[{'id':os.getcwd()}]}))")
    manager.probe_command=[sys.executable,str(child)]
    future=tmp_path/'not-created'/'project'
    result=await manager.perform('providers.models',{'workspace':str(future),'id':'one'})
    assert result['models']==[{'id':str(tmp_path)}]
    assert not future.parent.exists()

@pytest.mark.asyncio
async def test_guided_setup_selects_balanced_once_and_preserves_private_config(manager,tmp_path):
    workspace=str(tmp_path)
    await manager.perform('providers.save',{'workspace':workspace,'id':'first','module':'provider-openai','apiKey':'private-guided-key','config':{'opaque':{'keep':True}}})
    args={'workspace':workspace,'id':'first','model':'chosen-model','initializeRouting':True}
    result=await manager.perform('providers.finishSetup',args)
    assert result['setupCompletion']['routingCreated'] is False
    assert result['setupCompletion']['routingSelected']=='balanced'
    assert result['takesEffect']=='new_sessions'
    active=result['active']
    assert active=='balanced'
    assert not list((manager.store.shared_home/'routing').glob('my-ai-*.yaml'))
    saved=manager.store.read(workspace)['config']['providers'][0]['config']
    assert saved['opaque']=={'keep':True}
    assert saved['api_key']=='${AMPLIFIER_FIRST_API_KEY}'
    assert saved['default_model']=='chosen-model'
    assert 'private-guided-key' not in str(result)
    # A retry or later connection edit cannot reset a user's model rules.
    retry=await manager.perform('providers.finishSetup',{**args,'model':'another-model'})
    assert retry['setupCompletion']['routingCreated'] is False
    assert manager.routing(workspace)['active']==active

@pytest.mark.asyncio
@pytest.mark.parametrize('protection',['custom-balanced','explicit-profile','multiple-providers'])
async def test_guided_setup_never_replaces_existing_routing(manager,tmp_path,protection):
    workspace=str(tmp_path)
    await manager.perform('providers.save',{'workspace':workspace,'id':'one','module':'provider-openai','config':{'default_model':'before'}})
    if protection=='multiple-providers':
        await manager.perform('providers.save',{'workspace':workspace,'id':'two','module':'provider-openai','config':{}})
    else:
        await manager.perform('routing.save',{'workspace':workspace,'name':'balanced' if protection=='custom-balanced' else 'personal','matrix':MATRIX,'activate':protection=='explicit-profile'})
    before=manager.routing(workspace)
    result=await manager.perform('providers.finishSetup',{'workspace':workspace,'id':'one','model':'after','initializeRouting':True})
    assert not result['setupCompletion']['routingCreated']
    assert manager.routing(workspace)==before

@pytest.mark.asyncio
async def test_guided_setup_rejects_blank_or_missing_connection_without_writes(manager,tmp_path):
    for args,message in [({'id':'missing','model':'chosen'},'connection changed'),({'id':'missing','model':'  '},'Choose a model')]:
        with pytest.raises(ValueError,match=message):
            await manager.perform('providers.finishSetup',{'workspace':str(tmp_path),**args})
    assert manager.store.read(str(tmp_path))=={}

@pytest.mark.asyncio
async def test_credential_discovery_and_explicit_account_choice(manager,tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','environment-account')
    result=await manager.perform('providers.credentials',{'workspace':str(tmp_path),'module':'provider-anthropic'})
    assert result['credentialCheck']['available'] and 'environment-account' not in str(result)
    args={'workspace':str(tmp_path),'module':'provider-anthropic','config':{}}
    await manager.perform('providers.save',{**args,'id':'env-account','apiKeyEnv':'ANTHROPIC_API_KEY'})
    assert not (manager.store.shared_home/'keys.env').exists()
    await manager.perform('providers.save',{**args,'id':'other-account','apiKey':'different-account'})
    rows=manager.config(tmp_path).providers
    assert rows[0]['config']['api_key']=='${ANTHROPIC_API_KEY}'
    assert rows[1]['config']['api_key']=='${AMPLIFIER_OTHER_ACCOUNT_API_KEY}'
    assert 'different-account' not in str(manager.provider_rows(tmp_path))
    assert os.environ['ANTHROPIC_API_KEY']=='environment-account'

@pytest.mark.asyncio
async def test_github_cli_discovery_is_metadata_only_and_save_is_explicit(manager,tmp_path,monkeypatch):
    monkeypatch.setattr('amplifier_web.setup.github_cli_token',lambda:'cli-account-token')
    args={'workspace':str(tmp_path),'module':'provider-github-copilot'}
    result=await manager.perform('providers.credentials',args)
    assert result['credentialCheck']['githubCliAvailable']
    assert 'cli-account-token' not in str(result)
    assert not (manager.store.shared_home/'keys.env').exists()
    result=await manager.perform('providers.save',{**args,'id':'copilot','config':{},'useGitHubCli':True})
    assert 'cli-account-token' not in str(result)
    assert manager.config(tmp_path).providers[0]['config']['github_token']=='${AMPLIFIER_COPILOT_GITHUB_TOKEN}'
    monkeypatch.setattr('amplifier_web.setup.github_cli_token',lambda:None)
    with pytest.raises(ValueError,match='no longer available'):
        await manager.perform('providers.save',{**args,'id':'missing','config':{},'useGitHubCli':True})

def test_account_status_checks_saved_provider_tokens_without_exposing_them(tmp_path):
    from amplifier_web.setup import account_connected
    path=tmp_path/'oauth.json';config={'token_file_path':str(path)}
    assert not account_connected(config)
    path.write_text('{"access_token":"private","refresh_token":"private-refresh"}')
    assert account_connected(config)
    path.write_text('{"access_token":"expired","expires_at":"2000-01-01T00:00:00+00:00"}')
    assert not account_connected(config)
    path.write_text('[]');assert not account_connected(config)

@pytest.mark.asyncio
async def test_removed_connections_are_excluded_from_preference_order(manager,tmp_path):
    args={'workspace':str(tmp_path),'module':'provider-test','config':{}}
    for identity in ('a','b','c'):await manager.perform('providers.save',{**args,'id':identity})
    await manager.perform('providers.remove',{'workspace':str(tmp_path),'id':'b'})
    result=await manager.perform('providers.reorder',{'workspace':str(tmp_path),'ids':['c','a'],'expectedIds':['a','c']})
    assert not next(row for row in result['providers'] if row['id']=='b')['enabled']


async def test_explicit_test_retries_failed_probe_without_waiting_for_catalog_ttl(manager, tmp_path):
    import sys
    await manager.perform('providers.save', {'id': 'retry', 'module': 'provider-openai', 'config': {}, 'workspace': str(tmp_path)})
    child = tmp_path / 'probe-retry.py'
    child.write_text("import json; print(json.dumps({'error':'First attempt failed'}))")
    manager.probe_command = [sys.executable, str(child)]
    args = {'id': 'retry', 'workspace': str(tmp_path)}
    with pytest.raises(ValueError, match='First attempt'):
        await manager.perform('providers.test', args)
    child.write_text("import json; print(json.dumps({'info':{},'configSchema':{},'test':{'reachable':True,'modelCount':2}}))")
    assert (await manager.perform('providers.test', args))['test']['reachable']


async def test_probe_drains_large_stderr_and_reports_safe_build_evidence(manager, tmp_path):
    import sys
    child = tmp_path / 'probe-build.py'
    child.write_text("import sys; sys.stderr.write('secret-do-not-copy' * 100000); sys.stderr.write('rustc 1.91.1 is not supported; private-dependency requires rustc 1.92.0'); sys.exit(1)")
    manager.probe_command = [sys.executable, str(child)]
    with pytest.raises(ValueError) as failure:
        await manager.perform('providers.schema', {'module': 'provider-openai', 'workspace': str(tmp_path)})
    message = str(failure.value)
    assert 'Rust 1.91.1 is unsupported' in message
    assert 'requires Rust 1.92.0' in message
    assert 'secret-do-not-copy' not in message and 'private-dependency' not in message
    assert len(message) < 500
