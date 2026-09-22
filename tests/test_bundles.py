from pathlib import Path
import pytest
import yaml
from amplifier_web.bundles import BundleManager, remote_source, document_metadata

@pytest.mark.parametrize('uri', ['file:///tmp/bundle','https://user:secret@github.com/a/b','https://github.com/a/b?token=secret','git+https://github.com/a/b#subdirectory=../escape','git+https://github.com/a/b@--upload-pack=x'])
def test_discovery_rejects_unsafe_sources(uri):
    with pytest.raises(ValueError): remote_source(uri)

@pytest.mark.asyncio
async def test_registration_order_disable_remove_persist(tmp_path):
    manager = BundleManager(tmp_path)
    manager.store.update(tmp_path, "global", lambda settings: settings.update(bundle={"app": []}))
    args = {'workspace':str(tmp_path)}
    a = (await manager.perform('bundles.add', {**args,'uri':'foundation:a','name':'A'}))['bundles'][0]
    b = (await manager.perform('bundles.add', {**args,'uri':'foundation:b','name':'B'}))['bundles'][1]
    await manager.perform('bundles.move', {**args,'id':b['id'],'direction':'up'})
    await manager.perform('bundles.toggle', {**args,'id':a['id'],'enabled':False})
    assert manager.store.read(tmp_path)['bundle']['app'] == ['foundation:b']
    rows = (await BundleManager(tmp_path).perform('bundles.list',args))['bundles']
    assert [r['name'] for r in rows] == ['B','A']
    await manager.perform('bundles.remove',{**args,'id':b['id']})
    assert 'foundation:b' in manager.store.read(tmp_path)['web_bundles']['excluded']

def fixture_export(manager):
    return manager.export_document({'config':{'providers':[{'module':'provider-openai','config':{'api_key':'${OPENAI_API_KEY}'}}]}},root_bundle='anchors',name='portable',effective_plan={
        'session':{'orchestrator':{'module':'loop-streaming','source':'git+https://github.com/microsoft/amplifier-module-loop-streaming@abc'}},
        'providers':[{'module':'provider-openai','config':{'api_key':'[REDACTED]','max_tokens':123}}],
        'tools':[{'module':'tool-keep'},{'module':'tool-disabled','enabled':False}],
        'agents':{'worker':{'instruction':'Use @anchors:context/baseline.md','tools':[{'module':'tool-disabled','enabled':False}]}},
    }, resources={'instruction':'Welcome @anchors:context/system.md','context':[
        {'name':'anchors:context/system.md','text':'System instruction.'},
        {'name':'anchors:context/baseline.md','text':'Agent baseline.'}], 'namespaces':[]})

def test_flatten_removes_disabled_and_inlines_context_without_secrets(tmp_path):
    result=fixture_export(BundleManager(tmp_path))
    document=yaml.safe_load(result['content'].split('---',2)[1])
    assert 'includes' not in document
    assert [r['module'] for r in document['tools']] == ['tool-keep']
    assert document['agents']['worker']['tools'] == []
    assert 'Agent baseline.' in document['agents']['worker']['instruction']
    assert 'System instruction.' in result['content']
    assert '@anchors:' not in result['content']
    assert document['providers'][0]['config']=={'api_key':'${OPENAI_API_KEY}','max_tokens':123}

def test_missing_static_resource_blocks_export(tmp_path):
    with pytest.raises(ValueError, match='Missing portable'):
        BundleManager(tmp_path).export_document({},effective_plan={},resources={'instruction':'@x:missing.md'})

@pytest.mark.asyncio
async def test_saved_bundle_registered_and_roundtrips_foundation(tmp_path):
    foundation=pytest.importorskip('amplifier_foundation.registry')
    export=fixture_export(BundleManager(tmp_path))
    path=tmp_path/export['filename']; path.write_text(export['content'])
    loaded=await foundation.load_bundle(str(path))
    plan=loaded.to_mount_plan()
    assert [r['module'] for r in plan['tools']] == ['tool-keep']
    assert plan['agents']['worker']['tools']==[]
    assert 'Agent baseline.' in plan['agents']['worker']['instruction']
    assert 'System instruction.' in loaded.instruction

@pytest.mark.asyncio
async def test_save_registers_markdown_and_does_not_read_unrelated_local_file(tmp_path):
    manager=BundleManager(tmp_path)
    result=await manager.perform('bundle.save',{'name':'new'},effective_config={'tools':[]},root_bundle='/private/unread',resources={'instruction':'Static'})
    assert result['saved']['name']=='new'
    assert Path(result['saved']['uri']).suffix=='.md'
    assert manager.store.read(tmp_path)['bundle']['added']['new']==result['saved']['uri']

def test_routing_export_embeds_effective_roles_without_private_directories(tmp_path):
    roles={'general':{'description':'custom','candidates':[{'provider':'own','model':'custom'}]},'fast':{'description':'quick','candidates':[{'provider':'own','model':'small'}]}}
    result=BundleManager(tmp_path).export_document({},effective_plan={'hooks':[{'module':'hooks-routing','config':{'default_matrix':'private','custom_routing_dirs':['/private/matrices']}}]},resources={'instruction':'Instructions','routingMatrix':{'roles':roles,'baseRoles':['general','fast','coding']}})
    doc=yaml.safe_load(result['content'].split('---',2)[1]);config=doc['hooks'][0]['config']
    assert config['default_matrix']=='balanced'
    assert config['overrides']['general']==roles['general']
    assert config['overrides']['coding']['candidates']==[]
    assert '/private' not in result['content']

@pytest.mark.asyncio
async def test_new_session_saved_snapshot_resists_host_recomposition_and_source_overrides(tmp_path,monkeypatch):
    """Actual Foundation save/load + the same host composition used at startup."""
    foundation=pytest.importorskip('amplifier_foundation.registry')
    from types import SimpleNamespace
    from amplifier_web.host.session import compose_configured_bundle,is_snapshot,module_source
    from amplifier_web.bundles import SNAPSHOT_VERSION
    monkeypatch.setenv('SNAPSHOT_TEST_API_KEY','credential-for-this-host')
    source='git+https://github.com/example/tool-filesystem@'+'a'*40
    export=BundleManager(tmp_path).export_document({},root_bundle='anchors',effective_plan={
        'providers':[{'module':'provider-test','id':'saved-provider','config':{'api_key':'${SNAPSHOT_TEST_API_KEY}','model':'saved-model'}}],
        'tools':[{'module':'tool-filesystem','source':source,'config':{'setting':'saved'}},{'module':'tool-disabled','enabled':False}],
        'agents':{'worker':{'instruction':'Keep literal ${PROMPT_EXAMPLE}.','tools':[{'module':'tool-disabled','enabled':False}]}},
        'hooks':[],
    },resources={'instruction':'Saved static instructions.'})
    target=tmp_path/export['filename'];target.write_text(export['content'])
    behavior=tmp_path/'behavior.yaml';behavior.write_text('bundle: {name: addon}\ntools:\n  - module: tool-disabled\n  - module: tool-extra\n')
    registry=foundation.BundleRegistry(home=tmp_path/'registry')
    settings={'bundle':{'app':[str(behavior)]},'routing':{'matrix':'host-matrix'},
        'config':{'tools':[{'module':'tool-disabled'}],'providers':[{'module':'provider-test','id':'saved-provider','config':{'model':'host-model'}}]},
        'overrides':{'tool-filesystem':{'source':'git+https://github.com/example/replacement','config':{'setting':'host-setting','allowed_write_paths':['/allowed'],'denied_write_paths':['/denied']}}}}
    config=SimpleNamespace(settings=settings,app_bundles=[str(behavior)],providers=settings['config']['providers'],module_sources={'tool-filesystem':'git+https://github.com/example/replacement'},workspace=tmp_path,home=tmp_path,registry_home=tmp_path/'registry',config_home=None,resolve_source=lambda _:None)
    loaded=await registry.load(str(target))
    assert loaded.version==SNAPSHOT_VERSION and is_snapshot(loaded)
    loaded=await compose_configured_bundle(registry,loaded,config)
    plan=loaded.to_mount_plan()
    assert [row['module'] for row in plan['tools']]==['tool-filesystem']
    assert plan['agents']['worker']['tools']==[]
    assert plan['agents']['worker']['instruction']=='Keep literal ${PROMPT_EXAMPLE}.'
    # Provider references remain portable through composition; the schema-aware
    # mount stage resolves them once the provider source has been prepared.
    assert plan['providers'][0]['config']=={'api_key':'${SNAPSHOT_TEST_API_KEY}','model':'saved-model'}
    from amplifier_web.provider_environment import materialize_bundle_providers
    async def schema_loader(module):
        assert module=='provider-test'
        return {'fields':[{'id':'api_key','field_type':'secret','required':True}]}
    await materialize_bundle_providers(loaded,SimpleNamespace(mount_plan=plan),schema_loader=schema_loader)
    assert plan['providers'][0]['config']=={'api_key':'credential-for-this-host','model':'saved-model'}
    assert plan['providers'][0]['instance_id']=='saved-provider'
    assert plan.get('hooks',[])==[]
    assert plan['tools'][0]['config']=={'setting':'saved','allowed_write_paths':[str(tmp_path), '/allowed'],'denied_write_paths':['/denied']}
    assert module_source(config,True,'tool-filesystem',plan['tools'][0]['source'])==source
    assert module_source(config,False,'tool-filesystem',source)==config.module_sources['tool-filesystem']
    # An ordinary root still composes the host's configured behavior normally.
    ordinary=tmp_path/'ordinary.yaml';ordinary.write_text('bundle: {name: ordinary}\ntools: []\n')
    normal=await registry.load(str(ordinary))
    no_routing=SimpleNamespace(**{**vars(config),'settings':{'bundle':{'app':[str(behavior)]}},'providers':[]})
    normal=await compose_configured_bundle(registry,normal,no_routing)
    assert {row['module'] for row in normal.tools}=={'tool-disabled','tool-extra'}

@pytest.mark.asyncio
async def test_drag_reorder_moves_atomically_and_persists_composition_order(tmp_path):
    manager=BundleManager(tmp_path);args={'workspace':str(tmp_path)}
    manager.store.update(tmp_path, "global", lambda settings: settings.update(bundle={"app": []}))
    for name in ['a','b','c']:
        result=await manager.perform('bundles.add',{**args,'uri':'foundation:'+name,'name':name})
    a,b,c=result['bundles']
    result=await manager.perform('bundles.move',{**args,'id':a['id'],'beforeId':None})
    assert [row['name'] for row in result['bundles']]==['b','c','a']
    assert manager.store.read(tmp_path)['bundle']['app']==['foundation:b','foundation:c','foundation:a']
    result=await manager.perform('bundles.move',{**args,'id':a['id'],'beforeId':b['id']})
    assert [row['name'] for row in result['bundles']]==['a','b','c']


async def test_picker_lists_standalone_registrations_not_namespace_roots(tmp_path,monkeypatch):
    import json
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME',str(tmp_path/'legacy'))
    manager=BundleManager(tmp_path)
    manager.store.update(tmp_path,'global',lambda _: {'bundle':{'added':{'my-root':'foundation:custom','work':'foundation:work','anchors-work':'foundation:anchors-work'},'app':['foundation:behaviors/addon']},'sources':{'bundles':{'override-root':'foundation:override'}}})
    directory=tmp_path/'foundation';directory.mkdir(exist_ok=True)
    (directory/'registry.json').write_text(json.dumps({'bundles':{
        'namespace-root':{'is_root':True},
        'requested-addon':{'is_root':True,'explicitly_requested':True},
        'app-addon':{'is_root':True,'app_bundle':True},
        'behavior-only':{'is_root':False},
        'anchors-amp-dev':{'is_root':False},
    }}))
    result=await manager.perform('bundles.list',{'workspace':str(tmp_path)})
    names={row['name'] for row in result['registeredBundles']}
    assert names == {'anchors','foundation','anchors-amp-dev','my-root','work','anchors-work'}
    assert any(row['role']=='behavior' and row['uri']=='foundation:behaviors/addon' for row in result['bundles'])


async def test_builtin_work_and_catalog_order_use_displayed_names_without_loading(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path/'legacy'))
    manager = BundleManager(tmp_path)
    # Raw identifier order puts Zulu before anchors. Display-label order also
    # differs from identifier order for aliases, and must be shared with agents.
    manager.store.update(tmp_path, 'global', lambda _: {'bundle': {'added': {
        'Zulu': 'file:///unavailable/zulu.md', 'alpha': 'file:///unavailable/alpha.md',
        'anchors-amp-dev': 'file:///unavailable/dev.md',
        'anchors-work': 'file:///unavailable/work.md',
    }}})
    result = await manager.perform('bundles.list', {'workspace': str(tmp_path)})
    assert [row['name'] for row in result['registeredBundles']] == [
        'alpha', 'anchors', 'anchors-work', 'anchors-amp-dev', 'foundation', 'work', 'Zulu']
    assert next(row for row in result['registeredBundles'] if row['name']=='work')['label']=='Work'
    assert not (tmp_path/'foundation/registry.json').exists()


async def test_composition_reorder_preserves_aliases_and_disabled_entries(tmp_path):
    manager=BundleManager(tmp_path);args={'workspace':str(tmp_path)}
    manager.store.update(tmp_path,'global',lambda settings:settings.update(bundle={'app':[]}))
    for name,role in [('A','behavior'),('Alias','standalone'),('B','behavior'),('Disabled','behavior')]:
        result=await manager.perform('bundles.add',{**args,'uri':'foundation:'+name,'name':name,'role':role})
    by_name={row['name']:row['id'] for row in result['bundles']}
    await manager.perform('bundles.toggle',{**args,'id':by_name['Disabled'],'enabled':False})
    await manager.perform('bundles.reorder',{**args,'ids':[by_name['B'],by_name['A']],'expectedIds':[by_name['A'],by_name['B']]})
    saved=manager.store.read(tmp_path)
    assert saved['bundle']['app']==['foundation:B','foundation:A']
    assert saved['bundle']['added']['Alias']=='foundation:Alias'
    with pytest.raises(ValueError,match='changed'):
        await manager.perform('bundles.reorder',{**args,'ids':[by_name['A'],by_name['B']],'expectedIds':[by_name['A'],by_name['B']]})
    assert manager.store.read(tmp_path)==saved
