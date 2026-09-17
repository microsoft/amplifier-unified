import os
from pathlib import Path
import subprocess
import unittest
import pytest

from amplifier_web.runtime_controls import public_config, restore_redactions, validate_plan


class ControlsTests(unittest.TestCase):
    def test_redacted_secrets_restore_by_instance_after_reordering(self):
        old={'providers':[{'module':'provider-test','id':'a','config':{'api_key':'private-a','max_tokens':400}},
                          {'module':'provider-test','id':'b','config':{'api_key':'private-b','max_tokens':500}}]}
        public=public_config(old)
        self.assertNotIn('private',str(public))
        self.assertEqual(public['providers'][0]['config']['max_tokens'],400)
        public['providers'].reverse()
        restored=restore_redactions(public,old)
        self.assertEqual(restored['providers'][0]['config']['api_key'],'private-b')

    def test_invalid_mount_changes_fail_before_persistence(self):
        plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},
              'providers':[{'module':'provider-test'}]}
        validate_plan(plan)
        plan['providers'][0]['enabled']=False
        with self.assertRaisesRegex(ValueError,'provider'):
            validate_plan(plan)


def test_real_core_controls_and_tool_approval():
    python=os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON to test public Core/Foundation session controls')
    script=Path(__file__).parent/'fixtures/standalone_controls_probe.py'
    completed=subprocess.run([python,str(script)],capture_output=True,text=True,timeout=45)
    assert completed.returncode==0,completed.stdout+completed.stderr
    assert '"approval_enforced": true' in completed.stdout

@pytest.mark.asyncio
@pytest.mark.parametrize('module_default',[None,24000])
async def test_legacy_automatic_budget_restores_without_overriding_module_default(tmp_path,monkeypatch,module_default):
    import json
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    loop=SimpleNamespace(max_iterations=10)
    context=SimpleNamespace(max_tokens=module_default)
    class Coordinator:
        config={'session':{'context':{'module':'context-simple'}},'providers':[]}
        session_state={}
        def get(self,name):return {'orchestrator':loop,'context':context}.get(name)
        def get_capability(self,name):return None
        def register_capability(self,*args):pass
    controls=RuntimeControls(SimpleNamespace(session_id='legacy-session',coordinator=Coordinator()),SimpleNamespace(generation=None,queued_inputs=0))
    controls.state_path().parent.mkdir(parents=True)
    controls.state_path().write_text(json.dumps({'budget':{'maxIterations':-1,'contextTokens':None}}))
    await controls.restore()
    assert context.max_tokens==module_default and loop.max_iterations==-1
    assert (await controls.perform('configuration.inspect'))['plan']['session']['context']['module']=='context-simple'
    controls.persist()
    saved=json.loads(controls.state_path().read_text())
    assert saved['budget'].get('contextTokens')==module_default
    if module_default is None:assert 'contextTokens' not in saved['budget']


@pytest.mark.asyncio
async def test_unpin_preserves_output_budget_and_restores_automatic_provider(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    class Copyable(SimpleNamespace):
        def model_copy(self, update):
            return Copyable(**{**vars(self), **update})
    class Provider:
        request = None
        def __init__(self, model): self.model = model
        def get_info(self): return Copyable(defaults={'model':self.model})
        async def complete(self, request, **kwargs): self.request = request
    default, pinned = Provider('default-model'), Provider('custom-default')
    providers = {'default':default, 'other':pinned}
    loop = SimpleNamespace(root_provider=None,max_iterations=10)
    loop._select_provider = lambda mounted: loop.root_provider or next(iter(mounted.values()))
    context = SimpleNamespace(max_tokens=None)
    class Coordinator:
        config={'session':{},'providers':[]}
        session_state={}
        def get(self,name): return {'orchestrator':loop,'context':context,'providers':providers}.get(name)
        def get_capability(self,name): return None
        def register_capability(self,*args): pass
    coordinator = Coordinator()
    controls = RuntimeControls(SimpleNamespace(session_id='selection-test',coordinator=coordinator),SimpleNamespace(generation=None,queued_inputs=0))
    await controls.perform('provider.select',{'instance':'other','model':'pinned-model','effort':'high'})
    await controls.perform('budget.set',{'maxOutputTokens':321})
    assert (await controls.perform('configuration.providers'))['effective']['model']=='pinned-model'
    await controls.perform('provider.reset')
    result = await controls.perform('configuration.providers')
    assert result['selection'] is None and not result['pinned']
    assert result['effective']=={'instance':'default','model':'default-model','effort':None}
    await loop.root_provider.complete(Copyable(model=None,max_output_tokens=None))
    assert default.request.max_output_tokens==321
    assert default.request.model is None  # Provider default, not the former pin.
    saved=json.loads(controls.state_path().read_text())
    assert saved['selection'] is None and saved['budget']['maxOutputTokens']==321
    # Persisted unpin survives a restart; output budget continues to apply.
    loop.root_provider=None
    restored=RuntimeControls(SimpleNamespace(session_id='selection-test',coordinator=coordinator),SimpleNamespace(generation=None,queued_inputs=0))
    await restored.restore()
    assert not (await restored.perform('configuration.providers'))['pinned']
    await loop.root_provider.complete(Copyable(model=None,max_output_tokens=None))
    assert default.request.max_output_tokens==321
    providers.clear()
    assert (await restored.perform('configuration.providers'))['providers']==[]
    await restored.perform('provider.reset')
    assert loop.root_provider is None
    with pytest.raises(ValueError,match='No provider'):
        await restored.perform('budget.set',{'maxOutputTokens':432,'maxIterations':123})
    assert loop.max_iterations == 10
