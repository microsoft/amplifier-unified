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
