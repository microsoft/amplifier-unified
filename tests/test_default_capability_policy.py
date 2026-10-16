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


@pytest.mark.parametrize('intent', [
    'automatic', 'root-balanced', 'root-other', 'app-default', 'app-pin', 'other-app',
    'settings-balanced', 'settings-other', 'settings-overrides', 'module-config',
    'scoped-config', 'scoped-source', 'snapshot',
])
async def test_fallback_origin_is_decided_before_composition(tmp_path, monkeypatch, intent):
    from amplifier_web.host.model_selection import prepare_default_routing, child_preferences, ProviderSelectionError
    settings = {'bundle': {'app': []},
                'web_bundles': {'excluded': [uri for uri in DEFAULT_BEHAVIORS if uri != ROUTING_BEHAVIOR_URI]}}
    root = Bundle(name='chosen')
    if intent.startswith('root-'):
        root.hooks = [{'module': 'hooks-routing', 'config': {
            'default_matrix': 'balanced' if intent == 'root-balanced' else 'private'}}]
    elif intent == 'app-default':
        settings['bundle']['app'] = [ROUTING_BEHAVIOR_URI]
    elif intent == 'app-pin':
        settings['bundle']['app'] = [ROUTING_BEHAVIOR_URI.replace('@main', '@chosen')]
    elif intent == 'other-app':
        settings['bundle']['app'] = ['explicit-routing']
    elif intent.startswith('settings-'):
        settings['routing'] = ({'overrides': {'coding': {'candidates': []}}}
                               if intent == 'settings-overrides' else
                               {'matrix': 'balanced' if intent == 'settings-balanced' else 'private'})
    elif intent == 'module-config':
        settings['config'] = {'hooks': [{'module': 'hooks-routing', 'config': {'default_matrix': 'balanced'}}]}
    elif intent == 'scoped-config':
        settings['overrides'] = {'hooks-routing': {'config': {'default_matrix': 'balanced'}}}
    elif intent == 'scoped-source':
        settings['sources'] = {'modules': {'hooks-routing': 'chosen-source'}}
    elif intent == 'snapshot':
        root = Bundle(name='saved', version=SNAPSHOT_VERSION,
                      hooks=addition(ROUTING_BEHAVIOR_URI).hooks)
    async def load(registry, config, reference):
        return addition(ROUTING_BEHAVIOR_URI), reference
    monkeypatch.setattr('amplifier_web.host.session.load_configured_bundle', load)
    original = copy.deepcopy(settings)
    config = HostConfig(tmp_path / 'app', tmp_path, settings, tmp_path / 'registry')
    result = await compose_configured_bundle(None, root, config)
    assert result._host_automatic_routing is (intent == 'automatic')
    assert settings == original
    h = routing_fixture()
    h.coordinator.config = result.to_mount_plan()
    await prepare_default_routing(h.parent, automatic=result._host_automatic_routing)
    # Same resolver name/content does not make an explicit balanced choice auto.
    if intent == 'automatic':
        assert await child_preferences(h.coordinator, {'model_role': 'coding'}, []) == []
    else:
        with pytest.raises(ProviderSelectionError, match='no available candidates'):
            await child_preferences(h.coordinator, {'model_role': 'coding'}, [])


def routing_fixture(*, selection=True):
    """Policy unit fixture only; no Core mount, provider or model execution."""
    from unittest.mock import AsyncMock
    choice = {'instance': 'private-service', 'model': 'conversation-v1', 'effort': 'high'}
    loop = SimpleNamespace(config={'inherit_effective_model': True},
                           root_provider=SimpleNamespace(selection=choice) if selection else None)
    resolver = SimpleNamespace(name='balanced', matrix_source='bundle', resolve=AsyncMock(return_value=[]))
    capabilities = {'model_role_resolver': resolver}
    mounts = {'orchestrator': loop, 'providers': {'private-service': object()}, 'tools': {}}
    coordinator = SimpleNamespace(session_id='parent', config={'hooks': addition(ROUTING_BEHAVIOR_URI).hooks},
                                  get=mounts.get, get_capability=capabilities.get,
                                  register_capability=capabilities.__setitem__)
    return SimpleNamespace(parent=SimpleNamespace(session_id='parent', coordinator=coordinator),
                           coordinator=coordinator, loop=loop, choice=choice, mounts=mounts,
                           capabilities=capabilities, resolver=resolver)


async def test_unmatched_automatic_route_inherits_exact_conversation_snapshot():
    from amplifier_web.host.model_selection import (
        prepare_default_routing, child_preferences, inherited_selection, child_routing,
    )
    h = routing_fixture()
    await prepare_default_routing(h.parent, automatic=True)
    overlay = {'model_role': 'coding'}
    preferences = await child_preferences(h.coordinator, overlay, [])
    assert preferences == []
    selection = inherited_selection(h.parent, overlay, preferences, default_fallback=True)
    assert selection == h.choice and selection is not h.choice
    assert child_routing(h.parent, overlay, preferences, selection)['selectionSource'] == 'inherited_conversation'
    assert inherited_selection(h.parent, overlay, preferences) is None
    assert h.resolver.resolve.await_args.args == ('coding',)
    routed = [{'provider': 'supported', 'model': 'matrix-model'}]
    h.resolver.resolve.return_value = routed
    assert await child_preferences(h.coordinator, overlay, []) is routed
    assert inherited_selection(h.parent, overlay, routed, default_fallback=True) is None


@pytest.mark.parametrize('change', ['user-balanced', 'unknown-source', 'different-matrix', 'changed-hook',
                                  'capability-overrides', 'overlay-hook', 'spoofed-marker', 'old-save', 'explicit-save'])
async def test_automatic_exception_cannot_widen_explicit_or_unknown_routing(change):
    from amplifier_web.host.model_selection import prepare_default_routing, child_preferences, ProviderSelectionError
    h = routing_fixture()
    await prepare_default_routing(h.parent, automatic=True)
    overlay, saved = {'model_role': 'coding'}, None
    if change in {'user-balanced', 'unknown-source'}:
        h.resolver.matrix_source = 'user' if change == 'user-balanced' else None
    elif change == 'different-matrix':
        h.resolver.name = 'other'
    elif change == 'changed-hook':
        h.coordinator.config['hooks'][0]['config']['overrides'] = {'coding': {}}
    elif change == 'capability-overrides':
        h.capabilities['session.routing'] = {'overrides': {'coding': {}}}
    elif change == 'overlay-hook':
        overlay['hooks'] = addition(ROUTING_BEHAVIOR_URI).hooks
    elif change == 'spoofed-marker':
        marker = h.capabilities.pop('web.default_routing_fallback')
        h.coordinator.config['web.default_routing_fallback'] = marker
        overlay.update(automatic_default_routing=True, _host_automatic_routing=True)
        saved = {'automatic_default_routing': True}
    else:
        saved = {} if change == 'old-save' else {'automatic_default_routing': False}
    with pytest.raises(ProviderSelectionError, match='no available candidates'):
        await child_preferences(h.coordinator, overlay, [], saved=saved)


async def test_default_fallback_does_not_convert_resolver_failures_or_missing_resolver():
    from amplifier_web.host.model_selection import prepare_default_routing, child_preferences, ProviderSelectionError
    h = routing_fixture()
    await prepare_default_routing(h.parent, automatic=True)
    error = RuntimeError('resolver unavailable')
    h.resolver.resolve.side_effect = error
    with pytest.raises(RuntimeError) as raised:
        await child_preferences(h.coordinator, {'model_role': 'coding'}, [])
    assert raised.value is error
    h.resolver.resolve.side_effect = None
    h.resolver.resolve.return_value = None  # Malformed result is not an empty candidate list.
    with pytest.raises(ProviderSelectionError, match='no available candidates'):
        await child_preferences(h.coordinator, {'model_role': 'coding'}, [])
    h.capabilities.pop('model_role_resolver')
    with pytest.raises(ProviderSelectionError, match='no mounted resolver'):
        await child_preferences(h.coordinator, {'model_role': 'coding'}, [])


async def test_auto_fallback_never_replaces_explicit_preferences_or_provider_declarations():
    from amplifier_web.host.model_selection import prepare_default_routing, child_preferences, inherited_selection
    h = routing_fixture()
    await prepare_default_routing(h.parent, automatic=True)
    pin = [{'provider': 'not-mounted', 'model': 'explicit-pin'}]
    agent_pin = [{'provider': 'other', 'model': 'agent-pin'}]
    overlay = {'model_role': 'coding', 'provider_preferences': agent_pin}
    assert await child_preferences(h.coordinator, overlay, pin) is pin
    assert await child_preferences(h.coordinator, overlay, []) == agent_pin
    assert inherited_selection(h.parent, overlay, pin, default_fallback=True) is None
    overlay = {'model_role': 'coding', 'providers': [{'module': 'provider-specialist'}]}
    assert await child_preferences(h.coordinator, overlay, []) == []
    assert inherited_selection(h.parent, overlay, [], default_fallback=True) is None
    h.resolver.resolve.assert_not_awaited()


@pytest.mark.parametrize('selection', [None, {}, {'instance': 'private-service'}, {'model': 'model-only'}])
async def test_no_selected_conversation_never_falls_back_to_provider_priority(selection):
    from amplifier_web.host.model_selection import prepare_default_routing, child_preferences, inherited_selection, ProviderSelectionError
    h = routing_fixture(selection=False)
    h.loop.root_provider = SimpleNamespace(selection=selection)
    await prepare_default_routing(h.parent, automatic=True)
    overlay = {'model_role': 'coding'}
    assert await child_preferences(h.coordinator, overlay, []) == []
    with pytest.raises(ProviderSelectionError, match='no selected provider/model'):
        inherited_selection(h.parent, overlay, [], default_fallback=True)


async def test_saved_fallback_retains_selection_but_never_grants_current_parent_authority():
    from amplifier_web.host.model_selection import prepare_default_routing, child_preferences, inherited_selection, ProviderSelectionError
    h = routing_fixture()
    await prepare_default_routing(h.parent, automatic=True)
    overlay = {'model_role': 'coding'}
    saved = {'automatic_default_routing': True, 'preferences_source': 'default_fallback',
             'agent_overlay': overlay, 'effective_selection': copy.deepcopy(h.choice)}
    h.loop.root_provider.selection = {**h.choice, 'model': 'conversation-v2', 'effort': 'low'}
    h.resolver.resolve.return_value = [{'provider': 'new-route', 'model': 'different'}]
    assert await child_preferences(h.coordinator, overlay, [], saved=saved) == []
    assert inherited_selection(h.parent, overlay, [], saved, default_fallback=True) == saved['effective_selection']
    h.resolver.resolve.assert_not_awaited()
    h.capabilities.pop('web.default_routing_fallback')
    h.resolver.resolve.return_value = []
    with pytest.raises(ProviderSelectionError, match='no available candidates'):
        await child_preferences(h.coordinator, overlay, [], saved=saved)


async def test_explicit_effective_hook_plan_cannot_restore_automatic_origin(tmp_path):
    from amplifier_web.host.session import apply_runtime_plan
    config = HostConfig(tmp_path / 'app', tmp_path, {}, tmp_path / 'registry')
    loaded = addition(ROUTING_BEHAVIOR_URI)
    loaded.session = {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}}
    loaded.providers = [{'module': 'provider-test', 'config': {'default_model': 'test-model'}}]
    loaded._host_automatic_routing = True
    result = apply_runtime_plan(loaded, loaded.to_mount_plan(), config, tmp_path)
    assert result.hooks[0]['config']['default_matrix'] == 'balanced'
    assert result._host_automatic_routing is False


@pytest.fixture
def fallback_lifecycle(tmp_path, monkeypatch):
    """Exercise host lifecycle wiring with stub mounts, not a real Core runtime."""
    import asyncio
    from dataclasses import dataclass
    from unittest.mock import AsyncMock, Mock
    from amplifier_web.host.children import Children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.model_selection import automatic_default_routing
    h = routing_fixture()
    h.capabilities['session.working_dir'] = str(tmp_path)
    h.coordinator.config.update(
        providers=[{'module': 'provider-private-service', 'instance_id': 'private-service',
                    'config': {'default_model': 'bundle-default'}}],
        agents={'worker': {'model_role': 'coding'}})
    h.observations = []
    @dataclass
    class Prepared:
        mount_plan: dict
        bundle: Bundle

        async def create_session(self, **kwargs):
            caps = {}
            context = SimpleNamespace(get_messages=AsyncMock(return_value=[]), set_messages=AsyncMock())
            mounts = {'context': context, 'providers': h.mounts['providers'], 'tools': {},
                      'orchestrator': SimpleNamespace(config={'inherit_effective_model': True}, root_provider=None)}
            coordinator = SimpleNamespace(config=copy.deepcopy(self.mount_plan), get=mounts.get,
                get_capability=caps.get, register_capability=caps.__setitem__,
                hooks=SimpleNamespace(register=Mock(return_value=Mock())))
            async def execute(instruction):
                h.observations.append({
                    'selection': copy.deepcopy(mounts['orchestrator'].root_provider.selection),
                    'automatic': automatic_default_routing(coordinator),
                    'coordinator': coordinator,
                })
                return 'done'
            child = SimpleNamespace(session_id=kwargs['session_id'], coordinator=coordinator,
                                    execute=AsyncMock(side_effect=execute), cleanup=AsyncMock())
            if kwargs.get('before_initialize'):
                await kwargs['before_initialize'](child)
            return child
    h.store = SessionStore(tmp_path / 'sessions')
    h.children = Children(SimpleNamespace(inbox=asyncio.Queue()), h.store, None)
    h.children.prepared['parent'] = Prepared(
        copy.deepcopy(h.coordinator.config), Bundle.from_dict(h.coordinator.config, base_path=tmp_path))
    h.children.install = AsyncMock()
    monkeypatch.setattr('amplifier_web.provider_recording.install_request_redaction', lambda *a, **kw: None)
    monkeypatch.setattr('amplifier_web.context_continuity.install', lambda *a, **kw: None)
    async def expand(coordinator, instruction, **kwargs):
        return instruction
    monkeypatch.setattr('amplifier_web.host.mentions.expand_input', expand)
    return h


async def test_host_fallback_propagates_and_resumes_exact_saved_selection(fallback_lifecycle):
    from amplifier_web.host.model_selection import prepare_default_routing, child_preferences, ProviderSelectionError
    h = fallback_lifecycle
    await prepare_default_routing(h.parent, automatic=True)
    result = await h.children.spawn('worker', 'first', h.parent, sub_session_id='fallback-child')
    assert result['status'] == 'success'
    original = copy.deepcopy(h.choice)
    assert h.observations[-1]['selection'] == original
    assert h.observations[-1]['automatic'] is True
    metadata = h.store.load('fallback-child')[1]
    assert metadata['automatic_default_routing'] is True
    assert metadata['preferences_source'] == 'default_fallback'
    assert metadata['effective_selection'] == original
    assert metadata['provider_preferences'] == []
    assert metadata['agent_overlay']['model_role'] == 'coding'
    assert h.children.rows['fallback-child']['routing']['selectionSource'] == 'inherited_conversation'
    # The child got its own host-created capability, usable by a nested spawn.
    assert await child_preferences(h.observations[-1]['coordinator'], {'model_role': 'coding'}, []) == []
    h.loop.root_provider.selection = {**original, 'model': 'conversation-v2', 'effort': 'low'}
    h.resolver.resolve.return_value = [{'provider': 'new-route', 'model': 'new-model'}]
    h.resolver.resolve.reset_mock()
    await h.children.resume('fallback-child', 'second', h.parent)
    assert h.observations[-1]['selection'] == original
    assert h.observations[-1]['automatic'] is True
    h.resolver.resolve.assert_not_awaited()
    saved = h.store.load('fallback-child')
    # A later explicit matrix cannot be widened by that old automatic receipt.
    h.capabilities.pop('web.default_routing_fallback')
    h.resolver.resolve.return_value = []
    with pytest.raises(ProviderSelectionError, match='no available candidates'):
        await h.children.resume('fallback-child', 'must fail', h.parent)
    assert h.store.load('fallback-child') == saved
    assert len(h.observations) == 2


@pytest.mark.parametrize('automatic', [False, True])
async def test_call_role_survives_delegate_empty_result_without_using_agent_role(fallback_lifecycle, automatic):
    from amplifier_web.host.children import PersistentDelegate, _CALL_ROLE
    from amplifier_web.host.model_selection import prepare_default_routing, ProviderSelectionError
    h = fallback_lifecycle
    await prepare_default_routing(h.parent, automatic=automatic)
    class Original:
        provider_selection_enabled = True
        async def execute(self, supplied):
            # Mimic the upstream seam: an unmatched call role reaches spawn
            # with no preferences and without a model_role keyword.
            return await h.children.spawn('worker', 'call role', h.parent, sub_session_id='call-child')
    tool = PersistentDelegate(Original(), h.children, h.coordinator)
    if automatic:
        await tool.execute({'agent': 'worker', 'model_role': 'review'})
        assert h.store.load('call-child')[1]['agent_overlay']['model_role'] == 'review'
        assert h.observations[-1]['selection'] == h.choice
    else:
        with pytest.raises(ProviderSelectionError, match='no available candidates'):
            await tool.execute({'agent': 'worker', 'model_role': 'review'})
        assert not h.observations
    assert h.resolver.resolve.await_args.args == ('review',)
    assert _CALL_ROLE.get() is None


async def test_new_resume_role_still_surfaces_resolver_errors_after_saved_fallback(fallback_lifecycle):
    from amplifier_web.host.model_selection import prepare_default_routing
    h = fallback_lifecycle
    await prepare_default_routing(h.parent, automatic=True)
    await h.children.spawn('worker', 'first', h.parent, sub_session_id='fallback-child')
    saved = h.store.load('fallback-child')
    error = RuntimeError('new role resolver failed')
    h.resolver.resolve.side_effect = error
    with pytest.raises(RuntimeError) as raised:
        await h.children.resume('fallback-child', 'new role', h.parent, model_role='coding')
    assert raised.value is error
    assert h.store.load('fallback-child') == saved
    assert len(h.observations) == 1


async def test_unavailable_explicit_pin_stays_strict_with_automatic_routing(fallback_lifecycle, monkeypatch):
    from amplifier_web.host.model_selection import prepare_default_routing, ProviderSelectionError
    h = fallback_lifecycle
    await prepare_default_routing(h.parent, automatic=True)
    async def unresolved(plan, preferences, coordinator):
        return plan
    monkeypatch.setattr('amplifier_foundation.apply_provider_preferences_with_resolution', unresolved)
    with pytest.raises(ProviderSelectionError, match='No requested child provider/model'):
        await h.children.spawn('worker', 'must fail', h.parent,
                               provider_preferences=[{'provider': 'not-mounted', 'model': 'explicit-pin'}])
    h.resolver.resolve.assert_not_awaited()
    assert not h.observations


@pytest.mark.parametrize('saved_source,call_kind,expected_source,expected_model', [
    ('explicit', 'role', 'explicit', 'saved-pin'),
    ('role', 'role', 'role', 'new-route'),
    ('role', 'cached', 'role', 'saved-pin'),
    ('explicit', 'explicit', 'explicit', 'caller-pin'),
])
async def test_resume_keeps_explicit_vs_derived_preferences(fallback_lifecycle, saved_source, call_kind,
                                                         expected_source, expected_model):
    from unittest.mock import AsyncMock
    from amplifier_web.host.children import PersistentDelegate
    from amplifier_web.host.model_selection import prepare_default_routing
    h = fallback_lifecycle
    await prepare_default_routing(h.parent, automatic=True)
    saved_pin = [{'provider': 'private-service', 'model': 'saved-pin'}]
    h.store.save('saved-child', [], {'parent_id': 'parent', 'agent_name': 'worker',
        'agent_overlay': {'model_role': 'coding'}, 'provider_preferences': saved_pin,
        'preferences_source': saved_source, 'automatic_default_routing': True})
    h.children._execute = AsyncMock(return_value={'status': 'success'})
    class Original:
        provider_selection_enabled = True
        async def execute(self, supplied):
            prefs = (supplied.get('provider_preferences')
                     or ([{'provider': 'private-service', 'model': 'new-route'}] if call_kind == 'role' else saved_pin))
            return await h.children.resume('saved-child', 'resume', h.parent,
                provider_preferences=prefs, model_role=supplied.get('model_role', 'coding'))
    supplied = {'session_id': 'saved-child'}
    if call_kind == 'role':
        supplied['model_role'] = 'review'
    elif call_kind == 'explicit':
        supplied['provider_preferences'] = [{'provider': 'private-service', 'model': 'caller-pin'}]
    await PersistentDelegate(Original(), h.children, h.coordinator).execute(supplied)
    kwargs = h.children._execute.await_args.kwargs
    assert kwargs['provider_preferences'][0]['model'] == expected_model
    assert kwargs['session_metadata']['preferences_source'] == expected_source
