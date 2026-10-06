import os
from pathlib import Path
import subprocess
import unittest
import pytest

from amplifier_web.runtime_controls import public_config, restore_redactions, validate_plan


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['unique', 'ambiguous', 'different-model', 'different-family', 'existing'])
async def test_saved_selection_requires_exact_connection_id(tmp_path, monkeypatch, case):
    import json
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    class Provider:
        def __init__(self, family='openai', model='saved-model'):
            self.family, self.model = family, model
        async def get_info(self):
            return {'id':self.family, 'defaults':{'model':self.model}}
    providers = {'terra':Provider(), 'other':Provider(model='another-model')}
    if case == 'ambiguous': providers['duplicate'] = Provider()
    if case == 'different-model': providers['terra'] = Provider(model='changed-model')
    if case == 'different-family': providers['terra'] = Provider(family='openai-chatgpt')
    if case == 'existing': providers['openai'] = Provider(model='different-default')
    loop = SimpleNamespace(root_provider=None)
    class Coordinator:
        config = {'providers':[]}
        session_state = {}
        def get(self, name): return {'providers':providers, 'orchestrator':loop}.get(name)
        def get_capability(self, name): return None
        def register_capability(self, *args): pass
    controls = RuntimeControls(SimpleNamespace(session_id='restored', coordinator=Coordinator()),
                               SimpleNamespace(generation=None, queued_inputs=0))
    selection = {'instance':'openai', 'model':'saved-model', 'effort':'high'}
    controls.state_path().parent.mkdir(parents=True)
    controls.state_path().write_text(json.dumps({'selection':selection}))
    if case == 'existing':
        await controls.restore()
        expected = selection
        assert controls.selection == expected
        assert loop.root_provider.original is providers[expected['instance']]
        assert loop.root_provider.selection == expected
        assert json.loads(controls.state_path().read_text())['selection'] == expected
        # Restart keeps the exact connection even when its default model differs.
        await controls.restore()
        assert controls.selection == expected
    else:
        with pytest.raises(ValueError, match='available provider instance'):
            await controls.restore()
        assert loop.root_provider is None
        assert json.loads(controls.state_path().read_text())['selection'] == selection
    await controls.close()


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
@pytest.mark.parametrize("bound", [False, True])
async def test_effective_snapshot_sanitizes_only_bound_children_and_restores_exact_selection(
        tmp_path, monkeypatch, bound):
    import copy
    import json
    from types import SimpleNamespace
    from amplifier_web.provider_environment import HOST_CREDENTIAL, credential_bindings
    from amplifier_web.runtime_controls import RuntimeControls

    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    plan = {
        "session": {"orchestrator": {"module": "loop-live"},
                    "context": {"module": "context-simple"}},
        "providers": [
            {"module": "provider-fixture", "instance_id": "alternate", "source": "alternate-source",
             "config": {"api_key": "synthetic-alternate-key", "model": "alternate-model",
                        "reasoning_effort": "low", "priority": 20}},
            {"module": "provider-fixture", "instance_id": "fable", "source": "fable-source",
             "config": {"api_key": "synthetic-fable-key", "model": "default-model",
                        "reasoning_effort": "high", "priority": 1}},
        ],
        "agents": {"worker": {
            "providers": [{"module": "provider-fixture", "instance_id": "fable", "source": "worker-source",
                           "config": {"api_key": "synthetic-worker-key", "model": "worker-model"}}],
            "agents": {"deep": {
                "providers": [{"module": "provider-fixture", "instance_id": "fable",
                               "config": {"api_key": "${EXACT_NESTED_KEY}", "model": "deep-model"}}],
            }},
        }},
        "tools": [], "hooks": [],
    }
    original = copy.deepcopy(plan)

    class Info(SimpleNamespace):
        def model_copy(self, *, update):
            return Info(**{**vars(self), **update})

    providers = {
        row["instance_id"]: SimpleNamespace(
            priority=row["config"]["priority"],
            get_info=lambda row=row: Info(id="fixture", defaults={
                "model": row["config"]["model"], "reasoning_effort": row["config"]["reasoning_effort"]},
                config_fields=[]))
        for row in plan["providers"]
    }
    loop = SimpleNamespace(root_provider=None)
    loop._select_provider = lambda mounted: loop.root_provider or min(
        mounted.values(), key=lambda provider: provider.priority)
    capabilities = {"web.provider_credentials_bound": bound}
    coordinator = SimpleNamespace(
        config=plan, session_state={},
        get=lambda name: {"providers": providers, "orchestrator": loop}.get(name),
        get_capability=capabilities.get,
        register_capability=lambda name, value: capabilities.update({name: value}))
    session = SimpleNamespace(session_id="inherited-child", coordinator=coordinator, config=plan)
    runtime = SimpleNamespace(generation=None, queued_inputs=0)
    selection = {"instance": "fable", "model": "exact-pinned-model", "effort": "xhigh"}
    controls = RuntimeControls(session, runtime)
    controls.state_path().parent.mkdir(parents=True)
    controls.state_path().write_text(json.dumps({"selection": selection}))
    try:
        await controls.restore()
        controls.persist()
        effective_path = controls.state_path().with_name("effective-configuration.json")
        effective = json.loads(effective_path.read_text())
        assert effective == (credential_bindings(plan) if bound else plan)
        assert coordinator.config == original
        if bound:
            assert "synthetic-" not in effective_path.read_text()
            assert effective["providers"][1]["config"]["api_key"] == HOST_CREDENTIAL
            assert effective["agents"]["worker"]["providers"][0]["config"]["api_key"] == HOST_CREDENTIAL
        assert effective["agents"]["worker"]["agents"]["deep"]["providers"][0]["config"]["api_key"] == "${EXACT_NESTED_KEY}"
        assert json.loads(controls.state_path().read_text())["selection"] == selection
        current = await controls.perform("configuration.providers")
        assert current["selection"] == current["effective"] == selection
        assert current["pinned"]
        assert loop.root_provider.original is providers["fable"]
        assert [row["config"]["priority"] for row in effective["providers"]] == [20, 1]
        # A same-family account with an identical model is never a restart substitute.
        providers["alternate"].get_info = providers["fable"].get_info
        loop.root_provider = None
        await controls.restore()
        assert loop.root_provider.original is providers["fable"]
        assert controls.selection == selection
        assert providers["fable"].get_info().defaults["model"] == "default-model"
    finally:
        await controls.close()

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
