"""Source inheritance through real Foundation preparation, without installs."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from amplifier_foundation import Bundle

from amplifier_web.host.components import HostComponents, declarations
from amplifier_web.host.config import read_config
from amplifier_web.host.session import module_source
from amplifier_web.managed_chats import allocate
from amplifier_web.session_creation import apply, prepare, template
from amplifier_web.shared_settings import read_yaml, settings_paths, update_settings


@pytest.fixture
def sources(tmp_path, monkeypatch):
    paths = {}
    for label in ('A', 'B', 'C', 'D'):
        path = tmp_path / 'module-sources' / label
        path.mkdir(parents=True)
        (path / 'selected-source.txt').write_text(label)
        paths[label] = str(path)
    activated = []

    class ControlledActivator:
        """Control I/O only; Bundle.prepare and BundleModuleResolver are real."""
        bundle_package_paths = []

        def __init__(self, **kwargs):
            assert kwargs['install_deps'] is False

        async def activate_all(self, specs, **kwargs):
            activated.append(copy.deepcopy(specs))
            return {row['module']: Path(row['source']) for row in specs}

        async def activate(self, module, source):
            activated.append([{'module': module, 'source': source}])
            return Path(source)

        def finalize(self):
            pass

    monkeypatch.setattr('amplifier_foundation.modules.activator.ModuleActivator', ControlledActivator)
    return paths, activated


def fixture(tmp_path, paths, *, location='workspace'):
    home = tmp_path / 'app'
    home.mkdir()
    source = {'id': str(uuid.uuid4()), 'bundle': 'fixture'}
    target = {'id': str(uuid.uuid4()), 'bundle': 'fixture'}
    workspace = tmp_path / 'history'
    workspace.mkdir()
    source['workspace'] = target['workspace'] = str(workspace)
    if location == 'runtime-id':
        source['runtimeSessionId'], target['runtimeSessionId'] = 'native-parent', 'native-child'
    elif location == 'worktree':
        execution = tmp_path / 'execution'
        execution.mkdir()
        source['workingDirectory'] = target['workspace'] = str(execution)
        # A misleading execution-session file must not replace history settings.
        update_settings(settings_paths(execution, session_id=source['id'])['session'],
                        lambda s: s.update(sources={'modules': {'tool-fixture': paths['C']}}))
    elif location == 'managed':
        for row in (source, target):
            row['location'] = {'kind': 'managed'}
            row['workspace'] = allocate(home, row['id'], 'test-allocation')

    plan = {
        'session': {
            'orchestrator': {'module': 'loop-fixture', 'source': paths['A'], 'config': {'max_iterations': 23}},
            'context': {'module': 'context-fixture', 'source': paths['A'], 'config': {'max_tokens': 12345}}},
        'providers': [{'module': 'provider-fixture', 'id': 'selected-alias', 'instance_id': 'selected-alias',
                       'source': paths['A'], 'config': {'api_key': '${INHERIT_TEST_KEY}', 'default_model': 'exact-model'}}],
        'tools': [{'module': 'tool-fixture', 'id': 'writer-alias', 'source': paths['A'],
                   'config': {'allowed_write_paths': [source['workspace']], 'exact': {'keep': [1, 2]}}}],
        'hooks': [{'module': 'hooks-fixture', 'source': paths['A'], 'config': {'enabled': True}}],
        'agents': {'helper': {
            'session': {'context': {'module': 'context-fixture', 'source': paths['A']}},
            'providers': [{'module': 'provider-fixture', 'instance_id': 'worker-alias', 'source': paths['A'],
                           'config': {'api_key': '${WORKER_TEST_KEY}', 'default_model': 'worker-model'}}],
            'tools': [{'module': 'tool-agent', 'source': paths['A']}],
            # A deeper reference to an already eager module reuses its B path.
            'agents': {'nested': {'tools': [{'module': 'tool-agent', 'source': paths['A']}]}}}},
        'instruction': 'Preserve the complete effective configuration.',
        'context': {'policy': 'not-a-module-source.md'},
    }
    controls = {
        'selection': {'instance': 'selected-alias', 'model': 'exact-model', 'effort': 'high'},
        'configurator': {'disabled': {'tools': ['disabled-alias'], 'hooks': ['disabled-hook']}},
        'mode': 'careful',
        'budget': {'maxIterations': 23, 'contextTokens': 12345, 'maxOutputTokens': 4321},
        'capacity': {'revision': 9, 'maxChildren': 3, 'maxOutstandingCalls': 4},
        'task': {'objective': 'Never copy this task'}, 'goal': 'Never copy this goal',
        'taskHistory': ['must-not-copy'], 'capacityReceipts': {'must-not-copy': True},
    }
    source['selection'] = copy.deepcopy(controls['selection'])
    directory = home / 'sessions' / (source.get('runtimeSessionId') or source['id'])
    directory.mkdir(parents=True)
    (directory / 'effective-configuration.json').write_text(json.dumps(plan))
    (directory / 'configuration.json').write_text(json.dumps({'tools': [{'module': 'authored-only'}]}))
    (directory / 'control-state.json').write_text(json.dumps(controls))
    service = SimpleNamespace(data_dir=home, state={'sessions': [source]},
                              db=SimpleNamespace(execute=lambda *args: []), _session=lambda sid: source)
    return service, source, target, plan, controls


def session_path(row):
    return settings_paths(row['workspace'], session_id=row.get('runtimeSessionId') or row['id'])['session']


async def prepared(plan, configured):
    bundle = Bundle(name='fixture', **{key: copy.deepcopy(plan[key]) for key in
                    ('session', 'providers', 'tools', 'hooks', 'agents', 'spawn') if key in plan})
    components = HostComponents()  # No host-owned modules in these declarations.
    return await bundle.prepare(install_deps=False, strict=True,
                                source_resolver=lambda module, source: module_source(
                                    configured, False, module, source, components))


def selected(preparation, module, hint):
    # Public resolver result, not the mount-plan spelling or a mocked hash.
    path = preparation.resolver.resolve(module, source_hint=hint).resolve()
    return (path / 'selected-source.txt').read_text()


@pytest.mark.parametrize('location', ['workspace', 'runtime-id', 'worktree', 'managed'])
async def test_actual_b_survives_a_plan_and_c_child_defaults_with_exact_controls(tmp_path, sources, location):
    paths, activations = sources
    service, source, target, plan, state = fixture(tmp_path, paths, location=location)
    names = {row['module'] for row in declarations(plan)}
    global_path = settings_paths(source['workspace'])['global']
    update_settings(global_path, lambda s: s.update(sources={'modules': {name: paths['C'] for name in names}}))
    update_settings(session_path(source), lambda s: s.update(
        sources={'modules': {**{name: paths['B'] for name in names}, 'unused-module': paths['D']},
                 'bundles': {'unrelated': 'do-not-copy'}},
        config={'providers': [{'id': 'selected-alias', 'module': 'provider-fixture',
                               'config': {'api_key': 'synthetic-not-for-child-settings'}}]},
        unrelated={'private': 'do-not-copy'}, routing={'matrix': 'do-not-copy'}))
    parent_config = read_config(source['workspace'], home=service.data_dir,
                                session_id=source.get('runtimeSessionId') or source['id'])
    parent = await prepared(plan, parent_config)
    assert parent.mount_plan['providers'][0]['source'] == paths['A']
    for name in names:
        assert selected(parent, name, paths['A']) == 'B'
    assert {row['source'] for row in activations[-1]} == {paths['B']}

    # The control child would really activate C, so copying A-shaped JSON alone
    # is not sufficient evidence of inheritance.
    default_config = read_config(target['workspace'], home=service.data_dir,
                                 session_id=target.get('runtimeSessionId') or target['id'])
    default = await prepared(plan, default_config)
    assert selected(default, 'provider-fixture', paths['A']) == 'C'

    config, review = template(service, source)
    assert config['plan'] == plan  # Effective wins over the authored-only file.
    assert config['sourceSettings'] == {'sources': {'modules': {name: paths['B'] for name in names}}}
    assert 'synthetic-not-for-child-settings' not in json.dumps(config)
    assert 'sourceSettings' not in review
    apply(service, target, config)
    runtime_id = target.get('runtimeSessionId') or target['id']
    directory = service.data_dir / 'sessions' / runtime_id
    assert json.loads((directory / 'configuration.json').read_text()) == plan
    expected = {key: copy.deepcopy(state[key]) for key in ('selection', 'configurator', 'mode', 'budget', 'capacity')}
    expected['capacity']['revision'] = 0
    assert json.loads((directory / 'control-state.json').read_text()) == expected
    assert target['selection'] == state['selection']
    assert read_yaml(session_path(target)) == config['sourceSettings']
    assert session_path(target).stat().st_mode & 0o777 == 0o600
    assert not (directory / 'inherited-module-sources.json').exists()
    if location == 'runtime-id':
        assert not (service.data_dir / 'sessions' / target['id']).exists()

    # Qualification and normal worker configuration both use this exact
    # read_config(target session) boundary; no extra probe flag/sidecar needed.
    child_config = read_config(target['workspace'], home=service.data_dir, session_id=runtime_id)
    child = await prepared(json.loads((directory / 'configuration.json').read_text()), child_config)
    assert child.mount_plan['providers'][0]['source'] == paths['A']
    for name in names:
        assert selected(child, name, paths['A']) == 'B'
    deep = child.mount_plan['agents']['helper']['agents']['nested']['tools'][0]
    assert selected(child, deep['module'], deep['source']) == 'B'
    assert read_yaml(global_path)['sources']['modules']['provider-fixture'] == paths['C']
    assert template(service, source)[1] == review
    if location != 'managed':  # Managed children intentionally have fresh folders.
        assert template(service, target)[1]['configurationHash'] == review['configurationHash']


@pytest.mark.parametrize('winner', ['modules', 'overrides', 'providers'])
async def test_shared_source_precedence_and_provider_aliases_are_preserved(tmp_path, sources, winner):
    paths, _ = sources
    service, source, target, plan, _ = fixture(tmp_path, paths)
    settings = {'sources': {'modules': {'provider-fixture': paths['B']}},
                'unrelated': {'private': 'do-not-copy'}}
    if winner != 'modules':
        settings['overrides'] = {'provider-fixture': {'source': paths['C'], 'config': {'private': 'do-not-copy'}}}
    if winner == 'providers':
        settings['config'] = {'providers': [{'module': 'provider-fixture', 'id': 'selected-alias',
            'instance_id': 'selected-alias', 'source': paths['D'], 'config': {'api_key': 'synthetic-secret'}}]}
    update_settings(session_path(source), lambda s: s.update(settings))
    config, _ = template(service, source)
    binding = paths[{'modules': 'B', 'overrides': 'C', 'providers': 'D'}[winner]]
    parent_config = read_config(source['workspace'], home=service.data_dir, session_id=source['id'])
    assert parent_config.module_sources['provider-fixture'] == binding
    assert selected(await prepared(plan, parent_config), 'provider-fixture', paths['A']) == Path(binding).name

    # The locked writer must preserve unrelated destination fields as well.
    update_settings(session_path(target), lambda s: s.update(
        voice={'preferred_model': 'keep'}, config={'providers': [{'id': 'selected-alias',
            'module': 'provider-fixture', 'config': {'api_key': '${DESTINATION_KEY}'}}]}))
    apply(service, target, config)
    saved = read_yaml(session_path(target))
    assert saved['voice'] == {'preferred_model': 'keep'}
    assert saved['config']['providers'][0]['config'] == {'api_key': '${DESTINATION_KEY}'}
    assert 'synthetic-secret' not in json.dumps(saved) and 'do-not-copy' not in json.dumps(saved)
    child_config = read_config(target['workspace'], home=service.data_dir, session_id=target['id'])
    assert child_config.module_sources['provider-fixture'] == binding
    assert selected(await prepared(config['plan'], child_config), 'provider-fixture', paths['A']) == Path(binding).name


def test_source_selection_is_reviewed_but_secrets_and_unrelated_settings_are_not(tmp_path, sources):
    paths, _ = sources
    service, source, target, _, _ = fixture(tmp_path, paths)
    update_settings(session_path(source), lambda s: s.update(
        sources={'modules': {'tool-fixture': paths['B']}}, unrelated={'private': 'first'},
        config={'providers': [{'module': 'provider-fixture', 'id': 'selected-alias',
                               'config': {'api_key': 'synthetic-first-secret'}}]}))
    config, review = template(service, source)
    update_settings(session_path(source), lambda s: s.update(
        unrelated={'private': 'changed'}, config={'providers': [{'module': 'provider-fixture',
            'id': 'selected-alias', 'config': {'api_key': 'synthetic-changed-secret'}}]}))
    assert template(service, source)[1] == review
    update_settings(session_path(source), lambda s: s['sources']['modules'].update({'tool-fixture': paths['C']}))
    assert template(service, source)[1]['configurationHash'] != review['configurationHash']
    args = {**target, 'inheritConfiguration': {'sessionId': source['id'],
                                               'configurationHash': review['configurationHash']}}
    with pytest.raises(ValueError, match='reviewed source configuration changed'):
        prepare(service, args, 'ui', None)
    assert not (service.data_dir / 'sessions' / target['id']).exists()
    assert config['sourceSettings']['sources']['modules']['tool-fixture'] == paths['B']


def test_conflicting_higher_priority_destination_source_refused_before_execution_plan(tmp_path, sources):
    paths, activations = sources
    service, source, target, _, _ = fixture(tmp_path, paths)
    update_settings(session_path(source), lambda s: s.update(sources={'modules': {'provider-fixture': paths['B']}}))
    config, _ = template(service, source)
    # A newly added destination provider has higher semantic precedence than a
    # session sources.modules selector. The narrow seam must refuse, not run C.
    update_settings(settings_paths(target['workspace'])['global'], lambda s: s.update(
        config={'providers': [{'module': 'provider-fixture', 'id': 'different-alias', 'source': paths['C']}]}))
    with pytest.raises(ValueError, match='cannot preserve the reviewed module sources'):
        apply(service, target, config)
    assert not (service.data_dir / 'sessions' / target['id'] / 'configuration.json').exists()
    assert not target.get('selection') and not activations


@pytest.mark.parametrize('declaration', ['nested-agent', 'spawn'])
async def test_lazy_only_source_override_refused_at_existing_settings_boundary(tmp_path, sources, declaration):
    paths, activations = sources
    service, source, _, plan, _ = fixture(tmp_path, paths)
    row = {'module': 'tool-lazy-only', 'source': paths['A']}
    if declaration == 'nested-agent':
        plan['agents']['helper']['agents']['nested']['tools'].append(row)
    else:
        plan['spawn'] = {'tools': [row]}
    directory = service.data_dir / 'sessions' / source['id']
    (directory / 'effective-configuration.json').write_text(json.dumps(plan))
    update_settings(session_path(source), lambda s: s.update(sources={'modules': {'tool-lazy-only': paths['B']}}))
    configured = read_config(source['workspace'], home=service.data_dir, session_id=source['id'])
    preparation = await prepared(plan, configured)
    assert configured.module_sources['tool-lazy-only'] == paths['B']
    assert all(spec['module'] != 'tool-lazy-only' for spec in activations[-1])
    # Real lazy resolver activation has no source_resolver callback: settings B
    # cannot replace A here without changing the Foundation/worker interface.
    resolved = await preparation.resolver.async_resolve(row['module'], source_hint=row['source'])
    assert (resolved.resolve() / 'selected-source.txt').read_text() == 'A'
    before = copy.deepcopy(activations)
    with pytest.raises(ValueError, match='Foundation does not retain the source resolver for lazy activation'):
        template(service, source)
    assert activations == before


async def test_old_prepared_source_without_session_selector_needs_no_stamp(tmp_path, sources):
    paths, _ = sources
    service, source, target, plan, _ = fixture(tmp_path, paths)
    config, review = template(service, source)
    assert 'sourceSettings' not in config
    apply(service, target, config)
    assert not session_path(target).exists()
    assert template(service, target)[1] == review
    child_config = read_config(target['workspace'], home=service.data_dir, session_id=target['id'])
    assert selected(await prepared(plan, child_config), 'provider-fixture', paths['A']) == 'A'