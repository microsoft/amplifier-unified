"""Default behavior additions preserve explicit policy, identity and opt-outs."""
import copy
from types import SimpleNamespace

import pytest
from amplifier_foundation import Bundle

from amplifier_web.builtin_behaviors import (
    DEFAULT_BEHAVIORS, COMPUTER_USE_BEHAVIOR_URI, CONTEXT_INTELLIGENCE_BEHAVIOR_URI,
    ROUTING_BEHAVIOR_URI,
)
from amplifier_web.bundles import SNAPSHOT_VERSION
from amplifier_web.host.components import capability_defaults, compose_bundles
from amplifier_web.host.config import HostConfig
from amplifier_web.host.session import LOOP_SOURCE, compose_configured_bundle


def addition(uri):
    if uri == COMPUTER_USE_BEHAVIOR_URI:
        return Bundle(name='computer-use', tools=[{'module': 'tool-computer-use', 'config': {'max_edge': 1280}}])
    if uri == ROUTING_BEHAVIOR_URI:
        return Bundle(name='routing', hooks=[{'module': 'hooks-routing', 'config': {'default_matrix': 'balanced'}}])
    return Bundle(name='context-intelligence', hooks=[{'module': 'hook-context-intelligence', 'config': {
        'context_intelligence_server_url': '${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL:}',
        'context_intelligence_api_key': '${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY:}',
        'base_path': '${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:}',
    }}], tools=[{'module': 'tool-context-intelligence-transcript'}],
        agents={'context-intelligence:session-navigator': {'description': 'Read retained history'}})


async def composed(tmp_path, monkeypatch, uri, root=None, settings=None):
    async def load(registry, config, reference):
        assert reference == uri
        return addition(uri), reference
    monkeypatch.setattr('amplifier_web.host.session.load_configured_bundle', load)
    values = copy.deepcopy(settings or {})
    values['bundle'] = {'app': [uri]}
    values['web_bundles'] = {'excluded': [item for item in DEFAULT_BEHAVIORS if item != uri]}
    config = HostConfig(tmp_path / 'app', tmp_path, values, tmp_path / 'registry')
    return await compose_configured_bundle(None, root or Bundle(name='chosen'), config)


async def test_new_ci_behavior_is_local_even_with_ambient_server_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL', 'https://ambient.invalid')
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY', 'synthetic-not-a-key')
    result = await composed(tmp_path, monkeypatch, CONTEXT_INTELLIGENCE_BEHAVIOR_URI)
    hook, = [row for row in result.hooks if row['module'] == 'hook-context-intelligence']
    assert hook['config']['destinations'] == {}
    assert hook['config']['context_intelligence_server_url'] == ''
    assert hook['config']['context_intelligence_api_key'] == ''
    from amplifier_web.session_files import capture_dir, project_slug
    assert hook['config']['project_slug'] == project_slug(tmp_path)
    assert hook['config']['base_path'] == str(capture_dir(tmp_path, 'root').parents[3])
    assert 'context-intelligence:session-navigator' in result.agents
    assert any(row['module'] == 'tool-context-intelligence-transcript' for row in result.tools)
    assert result.name == 'chosen'


async def test_explicit_ci_destinations_and_capture_identity_remain_authoritative(tmp_path, monkeypatch):
    chosen = {'destinations': {'chosen': {'url': 'https://explicit.invalid', 'include': ['**']}},
              'project_slug': 'explicit-workspace', 'base_path': '/explicit-captures'}
    root = Bundle(name='chosen', hooks=[{'module': 'hook-context-intelligence',
                                      'instance_id': 'owned-ci', 'source': 'owned-source', 'config': chosen}])
    result = await composed(tmp_path, monkeypatch, CONTEXT_INTELLIGENCE_BEHAVIOR_URI, root)
    hook, = [row for row in result.hooks if row['module'] == 'hook-context-intelligence']
    assert hook['config'] == chosen
    assert hook['instance_id'] == 'owned-ci' and hook['source'] == 'owned-source'
    settings = {'overrides': {'hook-context-intelligence': {'config': chosen}}}
    configured = await composed(tmp_path, monkeypatch, CONTEXT_INTELLIGENCE_BEHAVIOR_URI, settings=settings)
    config = next(row['config'] for row in configured.hooks if row['module'] == 'hook-context-intelligence')
    assert all(config[key] == value for key, value in chosen.items())


async def test_new_desktop_access_is_read_only_and_explicit_opt_in_wins(tmp_path, monkeypatch):
    result = await composed(tmp_path, monkeypatch, COMPUTER_USE_BEHAVIOR_URI)
    assert next(row for row in result.tools if row['module'] == 'tool-computer-use')['config']['read_only'] is True
    root = Bundle(name='chosen', tools=[{'module': 'tool-computer-use', 'instance_id': 'desktop',
        'config': {'read_only': False, 'gate_writes': True, 'target': 'ssh://explicit-host'}}])
    result = await composed(tmp_path, monkeypatch, COMPUTER_USE_BEHAVIOR_URI, root)
    tool, = [row for row in result.tools if row['module'] == 'tool-computer-use']
    assert tool == root.tools[0]
    result = await composed(tmp_path, monkeypatch, COMPUTER_USE_BEHAVIOR_URI,
        settings={'overrides': {'tool-computer-use': {'config': {'read_only': False, 'gate_writes': True}}}})
    assert next(row for row in result.tools if row['module'] == 'tool-computer-use')['config']['read_only'] is False


async def test_explicit_matrix_wins_over_default_and_settings_win_over_root(tmp_path, monkeypatch):
    root = Bundle(name='chosen', hooks=[{'module': 'hooks-routing', 'config': {'default_matrix': 'root-matrix'}}])
    result = await composed(tmp_path, monkeypatch, ROUTING_BEHAVIOR_URI, root)
    hook, = [row for row in result.hooks if row['module'] == 'hooks-routing']
    assert hook['config']['default_matrix'] == 'root-matrix'
    result = await composed(tmp_path, monkeypatch, ROUTING_BEHAVIOR_URI, root,
                            settings={'routing': {'matrix': 'settings-matrix'}})
    hook, = [row for row in result.hooks if row['module'] == 'hooks-routing']
    assert hook['config']['default_matrix'] == 'settings-matrix'


@pytest.mark.parametrize('snapshot', [False, True])
async def test_opt_out_and_snapshot_do_not_restore_behavior_from_old_routing_settings(tmp_path, monkeypatch, snapshot):
    async def unexpected(*args):
        pytest.fail('No behavior may load for this snapshot/opt-out')
    monkeypatch.setattr('amplifier_web.host.session.load_configured_bundle', unexpected)
    config = HostConfig(tmp_path / 'app', tmp_path, {
        'bundle': {'app': []}, 'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)},
        'routing': {'matrix': 'retained-but-disabled'},
    }, tmp_path / 'registry')
    root = Bundle(name='saved', version=SNAPSHOT_VERSION if snapshot else '1.0.0')
    result = await compose_configured_bundle(None, root, config)
    assert not any(row['module'] == 'hooks-routing' for row in result.hooks)
    assert not result.tools
    if snapshot:
        assert not result.hooks


def test_discovery_paths_accumulate_without_replacing_module_policy():
    base = Bundle(name='chosen', tools=[{'module': 'tool-skills', 'config': {'skills': ['existing'], 'visibility': False}}],
                  hooks=[{'module': 'hooks-mode', 'config': {'search_paths': ['existing-modes'], 'gate_policy': 'confirm'}}])
    extra = Bundle(name='extra', tools=[{'module': 'tool-skills', 'config': {'skills': ['new']}}],
                   hooks=[{'module': 'hooks-mode', 'config': {'search_paths': ['new-modes'], 'gate_policy': 'allow'}}])
    original = copy.deepcopy(base.to_mount_plan())
    result = compose_bundles(*capability_defaults(base, extra, {}))
    assert result.tools[0]['config'] == {'skills': ['existing', 'new'], 'visibility': False}
    assert result.hooks[0]['config'] == {'search_paths': ['existing-modes', 'new-modes'], 'gate_policy': 'confirm'}
    assert base.to_mount_plan() == original
    assert compose_bundles(*capability_defaults(result, extra, {})).to_mount_plan() == result.to_mount_plan()


async def test_role_resolution_only_fills_unspecified_preferences():
    from amplifier_web.host.model_selection import child_preferences, pinned_delegate_input, ProviderSelectionError
    calls = []
    async def resolve(role):
        calls.append(role)
        return [{'provider': 'routed', 'model': 'default'}] if role == 'coding' else []
    coordinator = SimpleNamespace(config={'agents': {'pinned': {'model_role': 'coding',
        'provider_preferences': [{'provider': 'chosen', 'model': 'pin'}]}}},
        get_capability=lambda name: SimpleNamespace(resolve=resolve))
    agent = coordinator.config['agents']['pinned']
    assert await child_preferences(coordinator, agent, []) == agent['provider_preferences']
    explicit = [{'provider': 'caller', 'model': 'exact'}]
    assert await child_preferences(coordinator, agent, explicit) == explicit
    assert calls == []
    assert await child_preferences(coordinator, {'providers': [{'module': 'chosen'}], 'model_role': 'coding'}, []) == []
    assert await child_preferences(coordinator, {'model_role': 'coding'}, []) == [{'provider': 'routed', 'model': 'default'}]
    with pytest.raises(ProviderSelectionError, match='no available candidates'):
        await child_preferences(coordinator, {'model_role': 'unknown'}, [])
    supplied = {'agent': 'pinned', 'model_role': 'other'}
    assert pinned_delegate_input(coordinator, supplied)['provider_preferences'] == agent['provider_preferences']
    assert 'provider_preferences' not in supplied
    assert pinned_delegate_input(coordinator, {**supplied, 'provider_preferences': explicit})['provider_preferences'] == explicit


async def test_routing_policy_is_registered_before_initialization():
    from amplifier_web.host.model_selection import prepare_default_routing
    capabilities = {}
    await prepare_default_routing(SimpleNamespace(coordinator=SimpleNamespace(register_capability=capabilities.__setitem__)))
    assert capabilities == {'routing.defer_agent_resolution': True, 'web.default_routing_policy': True}


@pytest.mark.parametrize('spawn', [False, True])
def test_child_overlay_cannot_elevate_read_only_desktop(spawn):
    from amplifier_web.host.children import child_plan
    tool = {'module': 'tool-computer-use', 'config': {'read_only': True, 'gate_writes': True}}
    parent = {'tools': [tool]}
    if spawn:
        parent['spawn'] = {'tools': []}
    result = child_plan(parent, {'tools': [{'module': 'tool-computer-use', 'instance_id': 'alias',
                                          'config': {'read_only': False, 'gate_writes': False}}]})
    for row in result['tools']:
        assert row['config']['read_only'] is True
        assert row['config']['gate_writes'] is True
    assert parent['tools'] == [tool]


async def test_delegate_adapter_preserves_pin_without_overriding_feature_disable():
    from amplifier_web.host.children import PersistentDelegate
    calls = []
    class Original:
        provider_selection_enabled = True
        async def execute(self, supplied):
            calls.append(supplied)
            return supplied
    pin = [{'provider': 'chosen', 'model': 'exact'}]
    coordinator = SimpleNamespace(config={'agents': {'pinned': {'provider_preferences': pin}}},
                                  get_capability=lambda name: name == 'web.default_routing_policy')
    original = Original()
    tool = PersistentDelegate(original, SimpleNamespace(reserved=0), coordinator)
    await tool.execute({'agent': 'pinned', 'model_role': 'coding'})
    assert calls[-1]['provider_preferences'] == pin
    original.provider_selection_enabled = False
    await tool.execute({'agent': 'pinned', 'model_role': 'coding'})
    assert 'provider_preferences' not in calls[-1]


@pytest.mark.parametrize('origin', ['tools', 'spawn'])
def test_desktop_ceiling_survives_a_tool_less_intermediate_child(origin):
    from amplifier_web.host.children import child_plan
    tool = {'module': 'tool-computer-use', 'config': {'read_only': True}}
    root = {'tools': [tool]} if origin == 'tools' else {'spawn': {'tools': [tool]}}
    middle = child_plan(root, {}, tool_inheritance={'exclude_tools': ['tool-computer-use']})
    assert middle['tools'] == []
    grandchild = child_plan(middle, {'tools': [{'module': 'tool-computer-use',
        'instance_id': 'another', 'config': {'read_only': False}}], '_unified_desktop_policy': {}})
    assert grandchild['tools'][0]['config']['read_only'] is True


def test_nested_default_module_does_not_repin_existing_source():
    from amplifier_web.host.components import HostComponents
    source = 'git+https://github.com/example/desktop@explicit#subdirectory=modules/tool'
    root = Bundle(name='chosen', tools=[{'module': 'tool-computer-use', 'source': source}])
    extra = Bundle(name='extra', agents={'operator': {'tools': [
        {'module': 'tool-computer-use', 'source': source.replace('@explicit', '@main')}]}})
    base, behavior = capability_defaults(root, extra, {})
    components = HostComponents()
    components.select_defaults(behavior, base, {})
    result = components.apply(compose_bundles(base, behavior))
    assert result.tools[0]['source'] == source
    assert result.agents['operator']['tools'][0]['source'] == source


def test_declared_agent_provider_is_prioritized_without_changing_its_config():
    from amplifier_web.host.model_selection import prioritize_agent_providers
    plan = {'providers': [
        {'module': 'provider-test', 'id': 'parent', 'config': {'priority': 0, 'default_model': 'a'}},
        {'module': 'provider-test', 'id': 'child', 'config': {'priority': 100, 'default_model': 'b', 'effort': 'high'}},
    ]}
    result = prioritize_agent_providers(plan, [plan['providers'][1]])
    assert result['providers'][1] == plan['providers'][1]
    assert result['providers'][0]['config']['priority'] > result['providers'][1]['config']['priority']
    assert plan['providers'][0]['config']['priority'] == 0


async def test_explicit_legacy_ci_settings_are_not_shadowed_by_new_empty_default(tmp_path, monkeypatch):
    chosen = {'context_intelligence_server_url': 'https://explicit.invalid',
              'context_intelligence_api_key': 'synthetic-explicit-key'}
    result = await composed(tmp_path, monkeypatch, CONTEXT_INTELLIGENCE_BEHAVIOR_URI,
        settings={'overrides': {'hook-context-intelligence': {'config': chosen}}})
    config = next(row['config'] for row in result.hooks if row['module'] == 'hook-context-intelligence')
    assert 'destinations' not in config
    assert all(config[key] == value for key, value in chosen.items())
    root = Bundle(name='chosen', hooks=[{'module': 'hook-context-intelligence', 'config': {'destinations': {}}}])
    result = await composed(tmp_path, monkeypatch, CONTEXT_INTELLIGENCE_BEHAVIOR_URI, root,
        settings={'overrides': {'hook-context-intelligence': {'config': chosen}}})
    config = next(row['config'] for row in result.hooks if row['module'] == 'hook-context-intelligence')
    assert config['destinations'] == {}


async def test_default_ci_hook_keeps_existing_sibling_repository_pin(tmp_path, monkeypatch):
    repository = 'git+https://github.com/microsoft/amplifier-bundle-context-intelligence@'
    tool_source = repository + 'explicit#subdirectory=modules/tool-context-intelligence-transcript'
    hook_source = repository + 'main#subdirectory=modules/hook-context-intelligence'
    root = Bundle(name='chosen', tools=[{'module': 'tool-context-intelligence-transcript',
                                        'source': tool_source}])
    async def load(registry, config, reference):
        behavior = addition(CONTEXT_INTELLIGENCE_BEHAVIOR_URI)
        behavior.hooks[0]['source'] = hook_source
        behavior.tools[0]['source'] = tool_source.replace('@explicit#', '@main#')
        return behavior, reference
    monkeypatch.setattr('amplifier_web.host.session.load_configured_bundle', load)
    config = HostConfig(tmp_path / 'app', tmp_path, {
        'bundle': {'app': [CONTEXT_INTELLIGENCE_BEHAVIOR_URI]},
        'web_bundles': {'excluded': [uri for uri in DEFAULT_BEHAVIORS if uri != CONTEXT_INTELLIGENCE_BEHAVIOR_URI]},
    }, tmp_path / 'registry')
    result = await compose_configured_bundle(None, root, config)
    assert next(row for row in result.tools if row['module'] == 'tool-context-intelligence-transcript')['source'] == tool_source
    assert next(row for row in result.hooks if row['module'] == 'hook-context-intelligence')['source'] == hook_source.replace('@main#', '@explicit#')


@pytest.mark.parametrize('snapshot', [False, True])
async def test_fallback_ci_hook_keeps_existing_sibling_pin_with_defaults_disabled(tmp_path, monkeypatch, snapshot):
    async def unexpected(*args):
        pytest.fail('No behavior may load for this snapshot/opt-out')
    monkeypatch.setattr('amplifier_web.host.session.load_configured_bundle', unexpected)
    repository = 'git+https://github.com/microsoft/amplifier-bundle-context-intelligence@'
    tool = {'module': 'tool-context-intelligence-transcript', 'instance_id': 'owned-transcript',
            'source': repository + 'explicit#subdirectory=modules/tool-context-intelligence-transcript',
            'config': {'base_path': '/explicit-captures'}}
    root = Bundle(name='chosen', version=SNAPSHOT_VERSION if snapshot else '1.0.0',
                  tools=[copy.deepcopy(tool)], session={'orchestrator': {
                      'module': 'loop-live', 'source': 'explicit-loop', 'config': {'max_iterations': 7}}})
    config = HostConfig(tmp_path / 'app', tmp_path, {
        'bundle': {'app': []}, 'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)},
    }, tmp_path / 'registry')
    result = await compose_configured_bundle(None, root, config)
    assert result.tools == [tool]
    assert result.name == 'chosen'
    assert result.version == (SNAPSHOT_VERSION if snapshot else '1.0.0')
    # Registering the CI pin must not replace the required host loop source.
    assert result.session['orchestrator'] == {
        'module': 'loop-live', 'source': LOOP_SOURCE, 'config': {'max_iterations': 7}}
    if snapshot:
        assert result.hooks == []
    else:
        hook, = result.hooks
        assert hook['module'] == 'hook-context-intelligence'
        assert hook['source'] == repository + 'explicit#subdirectory=modules/hook-context-intelligence'
        assert hook['config']['destinations'] == {}
        assert not result.agents


@pytest.mark.parametrize('scoped_source', [False, True])
async def test_fallback_ci_pin_keeps_later_app_and_scoped_source_precedence(tmp_path, monkeypatch, scoped_source):
    repository = 'git+https://github.com/microsoft/amplifier-bundle-context-intelligence@'
    suffix = '#subdirectory=modules/tool-context-intelligence-transcript'
    root = Bundle(name='chosen', tools=[{'module': 'tool-context-intelligence-transcript',
        'source': repository + 'root-pin' + suffix, 'config': {'base_path': '/explicit-captures'}}])
    calls = []
    async def load(registry, config, reference):
        calls.append(reference)
        return Bundle(name=reference, tools=[{'module': 'tool-context-intelligence-transcript',
            'source': repository + reference + suffix, 'config': {'selection': reference}}]), reference
    monkeypatch.setattr('amplifier_web.host.session.load_configured_bundle', load)
    settings = {
        'bundle': {'app': ['first-overlay', 'last-overlay']},
        'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)},
    }
    if scoped_source:
        settings['overrides'] = {'tool-context-intelligence-transcript': {
            'source': repository + 'scoped-pin' + suffix, 'config': {'selection': 'scoped'}}}
    config = HostConfig(tmp_path / 'app', tmp_path, settings, tmp_path / 'registry')
    result = await compose_configured_bundle(None, root, config)
    revision = 'scoped-pin' if scoped_source else 'last-overlay'
    assert calls == ['first-overlay', 'last-overlay']
    assert result.tools == [{'module': 'tool-context-intelligence-transcript',
        'source': repository + revision + suffix,
        'config': {'base_path': '/explicit-captures', 'selection': 'scoped' if scoped_source else 'last-overlay'}}]
    hook, = result.hooks
    assert hook['source'] == repository + revision + '#subdirectory=modules/hook-context-intelligence'
    assert hook['config']['destinations'] == {}


async def test_existing_root_ambient_placeholders_are_not_explicit_forwarding(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL', 'https://ambient.invalid')
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY', 'ambient-synthetic')
    root = Bundle(name='chosen', hooks=addition(CONTEXT_INTELLIGENCE_BEHAVIOR_URI).hooks)
    result = await composed(tmp_path, monkeypatch, CONTEXT_INTELLIGENCE_BEHAVIOR_URI, root)
    ci = next(row['config'] for row in result.hooks if row['module'] == 'hook-context-intelligence')
    assert ci['destinations'] == {}
    assert ci['context_intelligence_server_url'] == ''
    assert ci['context_intelligence_api_key'] == ''
