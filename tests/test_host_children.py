import os
from pathlib import Path
import subprocess
import pytest


def test_model_selection_inheritance_is_opt_in_and_specialists_win():
    from types import SimpleNamespace
    from amplifier_web.host.model_selection import inherited_selection
    choice = {"instance": "provider-instance", "model": "chosen-model", "effort": "high"}
    loop = SimpleNamespace(config={"inherit_effective_model": True}, root_provider=SimpleNamespace(selection=choice))
    parent = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: loop))
    result = inherited_selection(parent, {}, [])
    assert result == choice and result is not choice
    assert inherited_selection(parent, {"model_role": "specialist"}, []) is None
    assert inherited_selection(parent, {"providers": [{"module": "other"}]}, []) is None
    assert inherited_selection(parent, {}, [object()]) is None
    loop.config.clear()
    assert inherited_selection(parent, {}, []) is None
    assert inherited_selection(parent, {}, [], {"effective_selection": choice}) == choice


def test_real_foundation_child_lifecycle_and_delegate_contract():
    python=os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON to a warmed standalone runtime for the real-core probe')
    probe=Path(__file__).parent/'fixtures/standalone_children_probe.py'
    result=subprocess.run([python,str(probe)],text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr+result.stdout
    assert '"delegate_compatible": true' in result.stdout
    assert '"failed_resume_preserved": true' in result.stdout
    assert '"cli_imports": false' in result.stdout


def test_real_community_recipe_completes_its_report_step():
    python = os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python or not os.environ.get('WARM_RECIPES_PATH'):
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON and WARM_RECIPES_PATH for the community recipe probe')
    probe = Path(__file__).parent / 'fixtures/recipe_children_probe.py'
    result = subprocess.run([python, str(probe)], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    assert '"recipe_report_completed": true' in result.stdout
    assert '"approval_preserved": true' in result.stdout
    assert '"subprocess_not_downgraded": true' in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['true', 'nonboolean', 'agent_config', 'inherited_config'])
async def test_rejected_child_options_preserve_retained_history_before_effects(tmp_path, mode):
    import asyncio
    import copy
    from types import SimpleNamespace
    pytest.importorskip('amplifier_module_loop_live')
    pytest.importorskip('amplifier_foundation')
    from amplifier_web.host.children import Children
    from amplifier_web.host.storage import SessionStore

    runtime=SimpleNamespace(inbox=asyncio.Queue())
    children=Children(runtime,SessionStore(tmp_path/'children'),None)
    config={'agents':{'worker':{}}}
    parent=SimpleNamespace(session_id='parent',coordinator=SimpleNamespace(
        config=config, get_capability=lambda name: None))
    children.prepared['parent']=object()
    children.rows={f'completed-{index}':{'status':'completed','reportId':f'report-{index}'} for index in range(256)}
    original=copy.deepcopy(children.rows)
    original_paths=list(tmp_path.rglob('*'))
    args={}
    if mode=='true':args['use_subprocess']=True
    elif mode=='nonboolean':args['use_subprocess']=0
    elif mode=='agent_config':args['agent_configs']={'worker':{'spawn_mode':'subprocess'}}
    else:config['spawn_mode']='subprocess'
    match='use_subprocess must be a boolean' if mode=='nonboolean' else 'Subprocess child sessions are not supported'
    with pytest.raises(ValueError,match=match):
        await children.spawn('worker','must not execute',parent,sub_session_id='rejected-child',**args)
    assert children.rows==original
    assert runtime.inbox.empty()
    assert not children.sessions
    assert list(tmp_path.rglob('*'))==original_paths


# Synthetic provider SDK boundary; real Core/Foundation/Children select and mount.
# A finite loop observes the actual provider selector without model inference.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    'case,connections,requested,expected_checked,expected_error',
    [
        ('default-cross-provider', [('openai', None), ('anthropic', None)],
         'anthropic', 'anthropic', False),
        ('default-same-provider', [('openai', None), ('anthropic', None)],
         'openai', 'openai', False),
        ('exact-alias', [('openai', 'openai'), ('anthropic', 'fable')],
         'fable', 'fable', False),
        ('family-to-alias', [('openai', 'openai'), ('anthropic', 'fable')],
         'anthropic', 'fable', False),
        ('explicit-family-id', [('openai', 'openai'), ('anthropic', 'anthropic')],
         'anthropic', 'anthropic', False),
        ('id-only', [('openai', 'openai'), ('anthropic', 'fable')],
         'fable', 'fable', False),
        ('instance-only', [('openai', 'openai'), ('anthropic', 'fable')],
         'fable', 'fable', False),
        ('two-connections-family', [('openai', 'openai'), ('anthropic', 'anthropic'),
                                    ('anthropic', 'fable')],
         'anthropic', 'anthropic', False),
        ('two-connections-alias', [('openai', 'openai'), ('anthropic', 'anthropic'),
                                   ('anthropic', 'fable')],
         'fable', 'fable', False),
        ('two-aliases-family', [('openai', 'openai'), ('anthropic', 'fable'),
                                ('anthropic', 'secondary')],
         'anthropic', 'fable', False),
        ('missing-selected', [('openai', 'openai'), ('anthropic', 'fable')],
         'fable', 'fable', True),
        ('failed-selected', [('openai', 'openai'), ('anthropic', 'fable')],
         'fable', 'fable', True),
    ],
)
async def test_child_preferences_preserve_native_connection_identity(
        tmp_path, monkeypatch, case, connections, requested, expected_checked, expected_error):
    import copy
    from dataclasses import dataclass
    import importlib.util
    import sys
    from types import SimpleNamespace
    from amplifier_core import AmplifierSession
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children
    from amplifier_web.host.model_selection import ProviderSelectionError, prepare_default_routing
    from amplifier_web.host.session import SelectedProvider, _apply_settings
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.provider_isolation import (
        ProviderIsolation, ProviderUnavailable, UnavailableProvider, require_available,
    )

    # Only external provider loading is replaced. Core owns real mount keys;
    # Foundation owns preference resolution; Unified owns availability + spawn.
    package = tmp_path / ('amplifier_module_provider_issue1581_' + case.replace('-', '_'))
    package.mkdir()
    (package / '__init__.py').write_text('''
from amplifier_core import ProviderInfo
__amplifier_module_type__ = "provider"
class Spy:
    def __init__(self, config):
        self.name = config.get("family", "metadata-only-spy")
        self.config = dict(config)
        self.priority = config.get("priority", 100)
        self.default_model = config.get("default_model", "metadata-only-model")
        self.calls = []
    def get_info(self):
        return ProviderInfo(id=self.name, display_name="Synthetic " + self.name,
                            defaults={"model": self.default_model})
    async def list_models(self):
        return []
    def get_config_schema(self):
        return {"fields": []}
    async def complete(self, request, **kwargs):
        self.calls.append("complete")
        raise AssertionError("No model inference is permitted")
    def parse_tool_calls(self, response):
        return []
async def mount(coordinator, config=None):
    spy = Spy(config)
    await coordinator.mount("providers", spy, name=spy.name)
''', encoding='utf-8')
    paths = {
        name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
        for name in ('loop-live', 'context-simple')
    }
    paths.update({'provider-openai': package, 'provider-anthropic': package})
    rows = []
    for index, (family, instance) in enumerate(connections):
        row = {'module': 'provider-' + family, 'config': {
            'family': family, 'default_model': 'parent-default-' + family, 'priority': index}}
        if instance is not None:
            if case != 'id-only':
                row['instance_id'] = instance
            if case != 'instance-only':
                row['id'] = instance
        rows.append(row)
    plan = {'session': {'orchestrator': {'module': 'loop-live'},
                        'context': {'module': 'context-simple'}}, 'providers': rows}
    executions, creations, checks = [], [], []

    class ObserveOnly(BundleLiveOrchestrator):
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            selected = self._select_provider(providers)
            underlying = getattr(selected, 'original', selected)
            instance, = [key for key, provider in providers.items() if provider is underlying]
            executions.append({'instance': instance, 'model': selected.default_model})
            assert all(not provider.calls for provider in providers.values())
            return 'selector observed; no inference'

    @dataclass
    class ObservedPrepared(PreparedBundle):
        async def create_session(self, **kwargs):
            creations.append(kwargs['session_id'])
            session = await super().create_session(**kwargs)
            assert isinstance(session, AmplifierSession)
            await session.coordinator.mount('orchestrator', ObserveOnly({'inherit_effective_model': True}))
            return session

    bundle = Bundle.from_dict({'bundle': {'name': 'issue1581-spies'}, **plan}, base_path=tmp_path)
    # Use the actual host's settings composition before Core mounts. In
    # particular, legacy id-only rows are normalized here, not by the spy.
    _apply_settings(bundle, SimpleNamespace(settings={}, providers=[], workspace=tmp_path))
    plan = bundle.to_mount_plan()
    prepared = ObservedPrepared(copy.deepcopy(plan), BundleModuleResolver(paths), bundle)
    isolation = ProviderIsolation()

    async def initialize(session):
        await isolation.before_initialize(session)
        await prepare_default_routing(session)

    parent = await prepared.create_session(session_id='openai-parent', before_initialize=initialize)
    # Child materialization reads schema separately from Core's resolver. Keep
    # this external-provider metadata boundary on the SAME no-credential spy,
    # never the installed OpenAI SDK or an uninstalled Anthropic package.
    spy_class = sys.modules[package.name].Spy
    def provider_class(module):
        assert module in {'provider-openai', 'provider-anthropic'}
        return spy_class
    monkeypatch.setattr('amplifier_web.provider_environment.provider_class', provider_class)
    coordinator = parent.coordinator
    mounted = coordinator.get('providers')
    assert set(mounted) == {instance or family for family, instance in connections}
    coordinator.get('orchestrator').root_provider = SelectedProvider(
        mounted['openai'], {'instance': 'openai', 'model': 'gpt-parent', 'effort': 'high'})
    if case == 'missing-selected':
        await coordinator.unmount('providers', name='fable')
        mounted = coordinator.get('providers')
        assert 'fable' not in mounted
    elif case == 'failed-selected':
        failed = next(row for row in plan['providers'] if row.get('instance_id') == 'fable')
        marker = UnavailableProvider(failed, isolation.record(failed, 'provider_mount_failed'))
        await coordinator.mount('providers', marker, name='fable')
        mounted = coordinator.get('providers')
    coordinator.register_capability('session.working_dir', str(tmp_path))
    initial = copy.deepcopy((coordinator.config, prepared.mount_plan, prepared.bundle.to_mount_plan()))
    defaults = {key: copy.deepcopy(provider.config) for key, provider in mounted.items()}

    def checked(instance):
        checks.append(instance)
        return require_available(coordinator, instance)

    coordinator.register_capability('provider.check_available', checked)
    registry = await install_children(parent, prepared, Runtime('openai-parent'),
                                      SessionStore(tmp_path / 'children'), None)
    model = 'gpt-5.5' if requested == 'openai' else 'claude-opus-5-5'
    try:
        if expected_error:
            failure_type = ProviderUnavailable if case == 'failed-selected' else ProviderSelectionError
            with pytest.raises(failure_type) as failure:
                await registry.spawn('self', 'do not infer', parent, sub_session_id='probe-child',
                    provider_preferences=[{'provider': requested, 'model': model}])
            if case == 'failed-selected':
                assert failure.value.failures[0]['instance_id'] == 'fable'
            else:
                assert str(failure.value) == 'The selected provider instance is unavailable; select a connection explicitly.'
            assert creations == ['openai-parent'] and not executions and not registry.rows
        else:
            result = await registry.spawn('self', 'do not infer', parent, sub_session_id='probe-child',
                provider_preferences=[{'provider': requested, 'model': model}])
            assert result['output'] == 'selector observed; no inference'
            assert executions == [{'instance': expected_checked, 'model': model}]
            assert creations == ['openai-parent', 'probe-child']
        assert checks == [expected_checked]
        assert (coordinator.config, prepared.mount_plan, prepared.bundle.to_mount_plan()) == initial
        assert {key: provider.config for key, provider in mounted.items()} == defaults
        assert coordinator.get('orchestrator').root_provider.selection == {
            'instance': 'openai', 'model': 'gpt-parent', 'effort': 'high'}
        assert all(not provider.calls for provider in mounted.values()
                   if not isinstance(provider, UnavailableProvider))
    finally:
        await parent.cleanup()
