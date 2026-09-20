"""The shared model bridge retains domain policy and ordinary routing semantics."""
import copy
from types import SimpleNamespace

import pytest
import yaml

from amplifier_web.host import shared_runtime_config as bridge
from amplifier_web.shared_settings import settings_paths


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / 'shared'; workspace = tmp_path / 'actual work.space'
    workspace.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(root))
    paths = settings_paths(workspace, session_id='native-session')
    write(paths['global'], {'config': {'providers': [
        {'id': 'primary', 'module': 'provider-a', 'source': 'source-a',
         'config': {'default_model': 'global', 'api_key': '${TEST_BRIDGE_KEY}', 'priority': 10}},
        {'id': 'secondary', 'module': 'provider-b', 'source': 'source-b', 'config': {'priority': 20}}]},
        'routing': {'matrix': 'team'}, 'voice': {'preferred_model': 'untouched'}})
    write(root / 'routing/team.yaml', {'roles': {'general': {'candidates': [{'provider': 'primary', 'model': 'global'}]}}})
    base = {'config_adapter': bridge.__name__, 'bundle': 'domain', 'providers': [{'module': 'provider-old'}],
            'hooks': [{'module': 'hooks-domain'}], 'tools': [{'module': 'tool-domain'}],
            'session': {'orchestrator': {'module': 'loop-streaming'}},
            'agents': {'reviewer': {'model_role': 'reasoning'}}, 'instructions': 'domain intent'}
    return root, workspace, paths, base


def test_scope_instances_sources_and_domain_boundaries(setup):
    root, workspace, paths, base = setup
    write(paths['project'], {'config': {'providers': [{'id': 'primary', 'module': 'provider-a', 'config': {'default_model': 'project'}}]}})
    write(paths['local'], {'config': {'providers': [{'id': 'secondary', 'module': 'provider-b', 'config': {'priority': 1}}]}})
    write(paths['session'], {'routing': {'overrides': {'reasoning': {'candidates': [{'provider': 'secondary', 'model': 'session-model'}]}}}})
    before = copy.deepcopy(base)
    result = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    assert base == before
    assert [p['instance_id'] for p in result['providers']] == ['secondary', 'primary']
    assert result['providers'][1]['config']['default_model'] == 'project'
    assert result['providers'][1]['config']['api_key'] == '${TEST_BRIDGE_KEY}'
    assert result['module_sources']['provider-b'] == 'source-b'
    route = result['hooks'][-1]
    assert route['config']['default_matrix'] == 'team'
    assert route['config']['overrides']['reasoning']['candidates'][0]['provider'] == 'secondary'
    assert route['config']['custom_routing_dirs'] == [str(workspace / '.amplifier/routing.local'), str(workspace / '.amplifier/routing'), str(root / 'routing')]
    for key in ('bundle', 'tools', 'session', 'agents', 'instructions'):
        assert result[key] == base[key]
    assert result['hooks'][0] == base['hooks'][0]
    assert 'voice' not in result


def test_disabled_instances_and_no_private_provider_fallback(setup):
    _, workspace, paths, base = setup
    write(paths['local'], {'configurator': {'disabled': {'providers': ['primary', 'secondary']}}})
    with pytest.raises(ValueError, match='No shared provider'):
        bridge.resolve_config(base, workspace=workspace, session_id='native-session')


def test_shared_sources_override_stale_private_sources(setup):
    _, workspace, paths, base = setup
    base['module_sources'] = {'provider-a': 'old', 'hooks-routing': 'old-route', 'tool-domain': 'domain-source'}
    write(paths['local'], {'overrides': {'hooks-routing': {'source': 'team-route'}},
                           'sources': {'modules': {'provider-b': 'team-provider'}}})
    result = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    assert result['module_sources'] == {'provider-a': 'source-a', 'provider-b': 'source-b',
                                       'hooks-routing': 'team-route', 'tool-domain': 'domain-source'}


def test_custom_matrix_edits_change_identity_even_with_same_selection(setup):
    root, workspace, _, base = setup
    first = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    write(root / 'routing/team.yaml', {'roles': {'general': {'candidates': [{'provider': 'secondary', 'model': 'edited'}]}}})
    second = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    assert first['hooks'] == second['hooks']
    assert first['config_adapter_state'] != second['config_adapter_state']


@pytest.mark.asyncio
async def test_prepare_replaces_bundle_defaults_but_preserves_child_preferences(setup, monkeypatch):
    _, workspace, _, base = setup
    config = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    prepared = SimpleNamespace(mount_plan={})
    async def prepare(**kwargs):
        assert kwargs['strict'] is True
        return prepared
    calls = []
    async def materialize(bundle, actual):
        calls.append(copy.deepcopy(bundle.providers))
        assert actual is prepared
    monkeypatch.setattr(bridge, 'materialize_bundle_providers', materialize)
    bundle = SimpleNamespace(providers=[{'module': 'provider-old'}], hooks=[{'module': 'hooks-domain'}], prepare=prepare)
    assert await bridge.prepare_bundle(config, bundle) is prepared
    assert calls[0] == config['providers']
    child_rows = [{'module': 'provider-b', 'config': {'default_model': 'routed-child', 'priority': 0}}]
    bundle.providers = child_rows
    await bridge.prepare_bundle(config, bundle, is_child=True)
    assert calls[1] == child_rows
    assert [h['module'] for h in bundle.hooks] == ['hooks-domain', 'hooks-routing']


@pytest.mark.asyncio
async def test_changed_routing_during_startup_refused_before_prepare(setup):
    root, workspace, _, base = setup
    config = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    write(root / 'routing/team.yaml', {'roles': {}})
    with pytest.raises(ValueError, match='changed during'):
        await bridge.prepare_bundle(config, None)


def test_credentials_load_without_persisting_or_overriding_launch_environment(setup, monkeypatch):
    root, workspace, _, base = setup
    (root / 'keys.env').write_text('TEST_BRIDGE_KEY=file-value\n')
    monkeypatch.setenv('TEST_BRIDGE_KEY', 'launch-value')
    result = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    import os
    assert os.environ['TEST_BRIDGE_KEY'] == 'launch-value'
    assert 'launch-value' not in repr(result) and 'file-value' not in repr(result)


def test_environment_model_changes_affect_resume_identity_but_key_rotation_does_not(setup, monkeypatch):
    _, workspace, paths, base = setup
    write(paths['local'], {'config': {'providers': [{'id': 'primary', 'module': 'provider-a', 'config': {'default_model': '${BRIDGE_MODEL}'}}]}})
    monkeypatch.setenv('BRIDGE_MODEL', 'model-one')
    monkeypatch.setenv('TEST_BRIDGE_KEY', 'credential-one')
    first = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    monkeypatch.setenv('TEST_BRIDGE_KEY', 'credential-two')
    rotated = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    assert first['config_adapter_state'] == rotated['config_adapter_state']
    monkeypatch.setenv('BRIDGE_MODEL', 'model-two')
    changed = bridge.resolve_config(base, workspace=workspace, session_id='native-session')
    assert first['config_adapter_state'] != changed['config_adapter_state']
