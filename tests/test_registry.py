import json
from pathlib import Path
import pytest
from amplifier_web.registry import RegistryManager,source_uri
from amplifier_web.host.config import load_config

@pytest.fixture
def manager(tmp_path,monkeypatch):
    home=tmp_path/'home';workspace=tmp_path/'project';workspace.mkdir()
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME',str(tmp_path/'absent-legacy'))
    load_config(workspace,home=home)
    return RegistryManager(home),workspace

@pytest.mark.asyncio
async def test_module_override_persistence_and_disable(manager):
    mgr,workspace=manager
    args={'workspace':str(workspace),'section':'tools','module':'tool-sample','source':'git+https://github.com/example/tool-sample','config':{'token':'secret-value','setting':'x'},'scope':'project'}
    result=await mgr.perform('modules.save',args)
    assert result['modules'][0]['config']['token']=='[REDACTED]'
    assert 'secret-value' not in str(result)
    settings=mgr.store.read(workspace,'project')
    assert settings['config']['tools'][0]['config']['token'].startswith('${AMPLIFIER_')
    await mgr.perform('modules.remove',{'workspace':str(workspace),'section':'tools','id':'tool-sample','scope':'project'})
    assert mgr.store.read(workspace,'project')['overrides']['tool-sample']['enabled'] is False

@pytest.mark.asyncio
async def test_source_scopes_remove_reveals_global(manager,tmp_path):
    mgr,workspace=manager
    local=tmp_path/'module';local.mkdir()
    await mgr.perform('sources.save',{'workspace':str(workspace),'kind':'module','name':'tool-test','source':str(local)})
    await mgr.perform('sources.save',{'workspace':str(workspace),'kind':'module','name':'tool-test','source':'git+https://github.com/example/tool-test','scope':'local'})
    assert load_config(workspace,home=mgr.home).module_sources['tool-test'].startswith('git+')
    await mgr.perform('sources.remove',{'workspace':str(workspace),'kind':'module','name':'tool-test','scope':'local'})
    assert load_config(workspace,home=mgr.home).module_sources['tool-test']==str(local)

@pytest.mark.asyncio
async def test_session_module_override_removal_restores_bundle_default(manager):
    mgr,workspace=manager
    await mgr.perform('modules.save',{'workspace':str(workspace),'section':'context','module':'context-simple','config':{'max_tokens':2000}})
    assert mgr.store.read(workspace)['config']['session']['context']['config']['max_tokens']==2000
    await mgr.perform('modules.remove',{'workspace':str(workspace),'section':'context','id':'context-simple'})
    assert 'context' not in mgr.store.read(workspace)['config']['session']

@pytest.mark.asyncio
async def test_validation_uses_isolated_contract_protocol(manager,tmp_path):
    import sys
    mgr,workspace=manager
    await mgr.perform('modules.save',{'workspace':str(workspace),'section':'tools','module':'tool-fixture','config':{}})
    helper=tmp_path/'validator.py';helper.write_text('import sys,json\na=json.loads(sys.stdin.readline())\nassert "config" not in a\nprint(json.dumps({"type":"module.validation","id":a["id"],"passed":True,"checks":[],"method":"core-contract"}))\n')
    mgr.validation_command=[sys.executable,str(helper)]
    result=await mgr.perform('modules.validate',{'workspace':str(workspace),'section':'tools','id':'tool-fixture'})
    assert result['validation']['passed']

@pytest.mark.parametrize('value',['https://user:secret@github.com/a/b','file:///does/not/exist','git+ssh://example.org/repo'])
def test_invalid_source_rejected(value,tmp_path):
    with pytest.raises(ValueError):source_uri(value,tmp_path)

@pytest.mark.asyncio
async def test_validation_follows_runtime_source_override(manager,tmp_path):
    import sys
    mgr,workspace=manager
    direct=tmp_path/'direct';direct.mkdir()
    override=tmp_path/'override';override.mkdir()
    await mgr.perform('modules.save',{'workspace':str(workspace),'section':'tools','module':'tool-fixture','source':str(direct),'config':{}})
    await mgr.perform('sources.save',{'workspace':str(workspace),'kind':'module','name':'tool-fixture','source':str(override),'scope':'local'})
    selected=tmp_path/'selected.json'
    helper=tmp_path/'validator.py';helper.write_text('import sys,json\nfrom pathlib import Path\na=json.loads(sys.stdin.readline())\nPath('+repr(str(selected))+').write_text(json.dumps(a))\nprint(json.dumps({"type":"module.validation","id":a["id"],"passed":True,"checks":[],"method":"core-contract"}))\n')
    mgr.validation_command=[sys.executable,str(helper)]
    await mgr.perform('modules.validate',{'workspace':str(workspace),'section':'tools','id':'tool-fixture'})
    assert json.loads(selected.read_text())['source']==load_config(workspace,home=mgr.home).module_sources['tool-fixture']

@pytest.mark.asyncio
async def test_validation_source_precedence_and_private_inputs(manager,tmp_path,monkeypatch):
    import sys
    from amplifier_web.host.session import module_source
    mgr,workspace=manager
    paths={key:tmp_path/('source-'+key) for key in ('direct','global','project','local','override')}
    for path in paths.values():path.mkdir()
    selected=tmp_path/'selected.json'
    helper=tmp_path/'validator.py'
    helper.write_text('import sys,json\nfrom pathlib import Path\na=json.loads(sys.stdin.readline())\nPath('+repr(str(selected))+').write_text(json.dumps(a))\nprint(json.dumps({"type":"module.validation","id":a["id"],"passed":True,"checks":[]}))\n')
    mgr.validation_command=[sys.executable,str(helper)]
    monkeypatch.setenv('PRIVATE_FIXTURE_KEY','must-not-appear')
    await mgr.perform('modules.save',{'workspace':str(workspace),'section':'tools','module':'tool-fixture','source':str(paths['direct']),'config':{'api_key':'${PRIVATE_FIXTURE_KEY}'}})
    async def check(expected):
        result=await mgr.perform('modules.validate',{'workspace':str(workspace),'section':'tools','id':'tool-fixture'})
        payload=json.loads(selected.read_text())
        config=load_config(workspace,home=mgr.home)
        assert payload['source']==module_source(config,False,'tool-fixture',str(paths['direct']))==str(paths[expected])
        assert set(payload)=={'id','module','section','source','behavioral'}
        assert 'must-not-appear' not in json.dumps(result)+json.dumps(payload)
    await check('direct')
    for scope in ('global','project','local'):
        await mgr.perform('sources.save',{'workspace':str(workspace),'kind':'module','name':'tool-fixture','source':str(paths[scope]),'scope':scope})
        await check(scope)
    for scope,expected in (('local','project'),('project','global')):
        await mgr.perform('sources.remove',{'workspace':str(workspace),'kind':'module','name':'tool-fixture','scope':scope})
        await check(expected)
    mgr.store.update(workspace,'local',lambda settings:settings.setdefault('overrides',{}).update({'tool-fixture':{'source':str(paths['override'])}}))
    await check('override')

@pytest.mark.asyncio
async def test_candidate_validation_never_saves_failure_or_trusts_stale_success(manager,tmp_path):
    import sys
    mgr,workspace=manager
    original=tmp_path/'original';original.mkdir()
    candidate=tmp_path/'candidate';candidate.mkdir()
    args={'workspace':str(workspace),'kind':'module','name':'tool-fixture','section':'tools','source':str(candidate),'scope':'project'}
    await mgr.perform('sources.save',{**args,'source':str(original)})
    settings_path=mgr.store.path(workspace,'project');before=settings_path.read_bytes()
    marker=tmp_path/'valid';marker.touch()
    helper=tmp_path/'validator.py'
    helper.write_text('import sys,json\nfrom pathlib import Path\na=json.loads(sys.stdin.readline())\nprint(json.dumps({"type":"module.validation","id":a["id"],"passed":Path('+repr(str(marker))+').exists(),"checks":[]}))\n')
    mgr.validation_command=[sys.executable,str(helper)]
    checked=await mgr.perform('sources.validate',args)
    assert checked['sourceValidation']['passed'] and not checked['sourceValidation']['saved']
    assert settings_path.read_bytes()==before
    marker.unlink()
    rejected=await mgr.perform('sources.save',{**args,'validate':True})
    assert not rejected['sourceValidation']['passed'] and not rejected['sourceValidation']['saved']
    assert settings_path.read_bytes()==before and 'takesEffect' not in rejected
    marker.touch()
    saved=await mgr.perform('sources.save',{**args,'validate':True})
    assert saved['sourceValidation']['passed'] and saved['sourceValidation']['saved']
    assert mgr.store.read(workspace,'project')['sources']['modules']['tool-fixture']==str(candidate)
    with pytest.raises(ValueError,match='module type'):
        await mgr.perform('sources.save',{**args,'section':'unknown','validate':True})

@pytest.mark.asyncio
async def test_validation_matches_provider_source_policy(manager,tmp_path):
    import sys
    from amplifier_web.host.session import module_source
    mgr,workspace=manager
    direct=tmp_path/'provider';direct.mkdir()
    override=tmp_path/'replacement';override.mkdir()
    await mgr.perform('modules.save',{'workspace':str(workspace),'section':'providers','module':'provider-fixture','id':'custom','source':str(direct)})
    await mgr.perform('sources.save',{'workspace':str(workspace),'kind':'module','name':'provider-fixture','source':str(override)})
    selected=tmp_path/'selected.json'
    helper=tmp_path/'validator.py';helper.write_text('import sys,json\nfrom pathlib import Path\na=json.loads(sys.stdin.readline())\nPath('+repr(str(selected))+').write_text(json.dumps(a))\nprint(json.dumps({"type":"module.validation","id":a["id"],"passed":True,"checks":[]}))\n')
    mgr.validation_command=[sys.executable,str(helper)]
    await mgr.perform('modules.validate',{'workspace':str(workspace),'section':'providers','id':'custom'})
    config=load_config(workspace,home=mgr.home)
    assert json.loads(selected.read_text())['source']==module_source(config,False,'provider-fixture',str(direct))==str(direct)
