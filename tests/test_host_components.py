"""Host composition and late child sources share one explicit ownership policy."""
import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
from amplifier_web.host.components import (
    ComponentResolver, HostComponentConflict, HostComponents, capability_defaults,
    compose_bundles, declarations, merge_modules,
)
from amplifier_web.host.config import HostConfig
from amplifier_web.host.session import compose_configured_bundle


def source(repo='owned', ref='chosen', module='hook-owned'):
    return f'git+https://github.com/example/{repo}@{ref}#subdirectory=modules/{module}'


async def test_actual_behavior_includes_choose_sources_and_namespace_roots(tmp_path):
    from amplifier_foundation import BundleRegistry
    old, chosen = tmp_path / 'old', tmp_path / 'chosen'
    for directory, ref in ((old, 'old'), (chosen, 'chosen')):
        directory.mkdir()
        (directory / 'context.md').write_text(ref)
        (directory / 'behavior.yaml').write_text(yaml.safe_dump({
            'bundle': {'name': 'owned'},
            'hooks': [{'module': 'hook-owned', 'source': source(ref=ref), 'config': {'scalar': ref, ref: True}}],
            'context': {'include': ['context.md']},
        }))
    root = tmp_path / 'root.yaml'
    root.write_text(yaml.safe_dump({'bundle': {'name': 'root'}, 'includes': [str(old / 'behavior.yaml')],
        'tools': [{'module': 'tool-sibling', 'source': source(ref='old', module='tool-sibling')},
                  {'module': 'tool-unrelated', 'source': source(repo='other', ref='saved', module='tool-unrelated')}],
        'agents': {'worker': {'hooks': [{'module': 'hook-owned', 'source': source(ref='old'), 'config': {'child': True}}]}}}))
    registry = BundleRegistry(home=tmp_path / 'registry')
    config = HostConfig(tmp_path / 'app', tmp_path, {'bundle': {'app': [str(chosen / 'behavior.yaml')]},
        'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)},
        'config': {'hooks': [{'module': 'hook-owned', 'config': {'scalar': 'settings'}}]}}, tmp_path / 'registry')
    loaded = await compose_configured_bundle(registry, await registry.load(str(root)), config)
    owned = next(row for row in loaded.hooks if row['module'] == 'hook-owned')
    assert owned['source'] == source()
    assert owned['config'] == {'old': True, 'chosen': True, 'scalar': 'settings'}
    assert loaded.tools[0]['source'] == source(module='tool-sibling')
    assert loaded.tools[1]['source'] == source(repo='other', ref='saved', module='tool-unrelated')
    assert loaded.agents['worker']['hooks'][0]['source'] == source()
    assert loaded.agents['worker']['hooks'][0]['config'] == {'child': True}
    assert loaded.source_base_paths['owned'] == chosen
    assert all(path.is_relative_to(chosen) for key, path in loaded.context.items() if key.startswith('owned:'))
    assert [row['module'] for row in loaded.hooks].count('hook-owned') == 1


def test_named_instances_survive_host_composition_and_duplicates_merge():
    from amplifier_foundation import Bundle
    base = Bundle(name='fixture', hooks=[{'module': 'hook-owned', 'instance_id': 'a', 'config': {'base': True, 'paths': ['bundle-dir']}},
                         {'module': 'hook-owned', 'instance_id': 'b', 'config': {'scalar': 'base'}}])
    overlay = Bundle(name='fixture', hooks=[{'module': 'hook-owned', 'instance_id': 'a', 'config': {'paths': ['host-dir']}}, {'module': 'hook-owned', 'instance_id': 'b', 'config': {'scalar': 'host'}},
                            {'module': 'hook-owned', 'instance_id': 'c'}])
    policy = HostComponents()
    policy.select('hook-owned', source())
    result = policy.apply(compose_bundles(base, overlay))
    assert [row['instance_id'] for row in result.hooks] == ['a', 'b', 'c']
    assert result.hooks[1]['config'] == {'scalar': 'host'}
    assert result.hooks[0]['config']['paths'] == ['bundle-dir', 'host-dir']
    assert all(row['source'] == source() for row in result.hooks)
    assert len(base.hooks) == 2 and 'source' not in base.hooks[0]


async def test_explicit_ci_root_is_chosen_without_adding_a_new_canonical_version(tmp_path):
    from amplifier_foundation import Bundle
    hook = 'hook-context-intelligence'
    chosen = source(repo='my-ci', ref='pinned', module=hook)
    sibling = 'tool-context-intelligence-transcript'
    loaded = Bundle(name='fixture', hooks=[{'module': hook, 'source': chosen}], tools=[{'module': sibling,
        'source': source(repo='my-ci', ref='old', module=sibling)}], agents={'child': {'hooks': [
            {'module': hook, 'source': source(repo='older-ci', ref='old', module=hook)}]}})
    # This ownership-only fixture has no registry; explicitly exclude defaults
    # instead of assuming an empty saved app list disables new capabilities.
    config = HostConfig(tmp_path / 'app', tmp_path, {'bundle': {'app': []},
        'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)}}, tmp_path / 'registry')
    result = await compose_configured_bundle(None, loaded, config)
    assert result.hooks[0]['source'] == chosen
    assert result.tools[0]['source'] == source(repo='my-ci', ref='pinned', module=sibling)
    assert result.agents['child']['hooks'][0]['source'] == chosen


def test_fork_ownership_needs_matching_module_identity_not_repository_basename():
    policy = HostComponents()
    policy.select('hook-owned', source())
    fork = source().replace('/example/', '/unrelated/').replace('@chosen', '@old')
    plan = {'tools': [{'module': 'tool-unrelated', 'source': fork}]}
    assert policy.normalize(plan) == plan
    plan['hooks'] = [{'module': 'hook-owned', 'source': fork}]
    plan['tools'][0] = {'module': 'tool-sibling', 'source': fork.replace('hook-owned', 'tool-sibling')}
    assert policy.normalize(plan)['tools'][0]['source'] == plan['tools'][0]['source']
    assert policy.normalize(plan)['hooks'][0]['source'] == source()
    # A vendored copy in a monorepo does not transfer ownership of its siblings.
    monorepo = source(repo='monorepo', ref='old', module='custom').replace('modules/custom', 'custom')
    plan = {'tools': [{'module': 'hook-owned', 'source': monorepo.replace('#subdirectory=custom', '#subdirectory=vendored/owned')},
                      {'module': 'tool-custom', 'source': monorepo}]}
    assert policy.normalize(plan)['tools'][1]['source'] == monorepo


def test_source_ownership_ambiguity_and_invalid_instances_fail_without_source_secrets():
    for rows in ([{'module': 'a', 'id': 'same'}, {'module': 'b', 'id': 'same'}],
                 [{'module': 'a', 'id': 'one', 'instance_id': 'two'}]):
        with pytest.raises(HostComponentConflict, match='instance IDs'):
            merge_modules(rows)
    from amplifier_web.update_diagnostics import probe_failure
    assert probe_failure(HostComponentConflict(), 'prepare')['reason'] == 'host-component-conflict'


@pytest.mark.parametrize('resumed', [False, True])
async def test_dynamic_and_resumed_child_plan_reuses_host_sources_and_instances(tmp_path, resumed):
    pytest.importorskip('amplifier_module_loop_live')
    from amplifier_foundation import Bundle
    from amplifier_web.host.children import Children
    from amplifier_web.host.storage import SessionStore
    policy = HostComponents({'loop-live': 'qualified-loop'}, {'loop-live': lambda: '/installed/loop'})
    policy.select('hook-owned', source())
    bundle = policy.apply(Bundle(name='fixture', session={'orchestrator': {'module': 'loop-live', 'source': 'qualified-loop'}}, hooks=[{'module': 'hook-owned', 'instance_id': 'a', 'source': source(),
                                        'config': {'base': True, 'paths': ['bundle-dir']}},
                                       {'module': 'hook-owned', 'instance_id': 'b', 'source': source()}]))
    overlay = {'session': {'orchestrator': {'module': 'loop-streaming', 'source': 'old-loop'}}, 'hooks': [{'module': 'hook-owned', 'instance_id': 'a', 'source': source(ref='old'),
                         'config': {'child': True, 'paths': ['child-dir']}}],
               'tools': [{'module': 'tool-sibling', 'source': source(ref='old', module='tool-sibling')}]}
    parent = SimpleNamespace(session_id='parent', coordinator=SimpleNamespace(config=bundle.to_mount_plan(),
        get=lambda _: None, get_capability=lambda name: str(tmp_path) if name == 'session.working_dir' else None))
    observed = []
    class StopBeforeModel(Exception): pass
    @dataclass
    class Prepared:
        bundle: object
        mount_plan: dict
        async def create_session(self, **kwargs):
            observed.append((self.mount_plan, self.bundle, kwargs))
            raise StopBeforeModel()
    store = SessionStore(tmp_path / 'store')
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), store, None)
    children.prepared['parent'] = Prepared(bundle, bundle.to_mount_plan())
    if resumed:
        store.save('child', [], {'parent_id': 'parent', 'agent_overlay': overlay, 'agent_name': 'dynamic'})
        operation = children.resume('child', 'no model call', parent)
    else:
        operation = children.spawn('dynamic', 'no model call', parent, agent_configs={'dynamic': overlay}, sub_session_id='child')
    with pytest.raises(StopBeforeModel):
        await operation
    plan, effective, kwargs = observed[0]
    assert plan['session']['orchestrator']['module'] == 'loop-live'
    assert plan['session']['orchestrator']['source'] == 'qualified-loop'
    assert [row['instance_id'] for row in plan['hooks']] == ['a', 'b']
    assert plan['hooks'][0]['config'] == {'base': True, 'child': True, 'paths': ['bundle-dir', 'child-dir']}
    assert all(row['source'] == source() for row in plan['hooks'])
    assert plan['tools'][0]['source'] == source(module='tool-sibling')
    assert effective._host_components is policy
    assert kwargs['is_resumed'] is resumed
    assert overlay['hooks'][0]['source'] == source(ref='old')


async def test_lazy_resolution_keeps_installed_host_path_and_owned_sibling_source():
    resolver = SimpleNamespace(async_resolve=AsyncMock(return_value='resolved'))
    policy = HostComponents({'loop-live': 'declared'}, {'loop-live': lambda: '/qualified/installed/package'})
    policy.select('hook-owned', source())
    wrapped = ComponentResolver(resolver, policy)
    assert await wrapped.async_resolve('loop-live', 'old-checkout') == 'resolved'
    resolver.async_resolve.assert_awaited_with('loop-live', '/qualified/installed/package')
    await wrapped.async_resolve('tool-sibling', source(ref='old', module='tool-sibling'))
    resolver.async_resolve.assert_awaited_with('tool-sibling', source(module='tool-sibling'))


def test_local_owned_namespace_rebases_sibling_source_and_concrete_resources(tmp_path):
    from amplifier_foundation import Bundle
    old, chosen = tmp_path / 'old', tmp_path / 'chosen'
    policy = HostComponents()
    policy.roots['owned'] = chosen
    bundle = Bundle(name='root', source_base_paths={'owned': old},
        context={'owned:notes.md': old / 'notes.md'},
        tools=[{'module': 'tool-sibling', 'source': (old / 'modules/tool-sibling').as_uri()},
               {'module': 'tool-unrelated', 'source': str(tmp_path / 'unrelated/modules/tool-other')}])
    result = policy.apply(bundle)
    assert result.tools[0]['source'] == (chosen / 'modules/tool-sibling').as_uri()
    assert result.tools[1]['source'] == str(tmp_path / 'unrelated/modules/tool-other')
    assert result.context['owned:notes.md'] == chosen / 'notes.md'
    assert result.source_base_paths['owned'] == chosen
    from amplifier_web.host.session import module_source
    config = SimpleNamespace(module_sources={'tool-sibling': '/unrelated/override'})
    assert module_source(config, False, 'tool-sibling', result.tools[0]['source'], policy) == result.tools[0]['source']


def test_resolver_cannot_reintroduce_an_override_for_an_owned_sibling():
    from amplifier_web.host.session import module_source
    policy = HostComponents()
    policy.select('hook-owned', source())
    config = SimpleNamespace(module_sources={'tool-sibling': source(repo='other', ref='old', module='tool-sibling'),
                                            'tool-independent': 'explicit-independent-source'})
    assert module_source(config, False, 'tool-sibling', source(module='tool-sibling'), policy) == source(module='tool-sibling')
    assert module_source(config, False, 'tool-independent', 'original-independent-source', policy) == 'explicit-independent-source'


@pytest.mark.parametrize('default_source', [
    source(repo='desktop', module='tool-computer-use'),
    '/default/modules/tool-computer-use',
    'file:///default/modules/tool-computer-use',
    './modules/tool-computer-use',
])
def test_defaults_do_not_own_unrelated_root_or_late_child_sources(tmp_path, default_source):
    from amplifier_foundation import Bundle
    from amplifier_web.host.session import module_source

    def row(module, ref):
        return {'module': module, 'source': source(repo='unrelated', ref=ref, module=module)}

    root = Bundle(name='root', providers=[row('provider-independent', 'provider-pin')],
        tools=[row('tool-independent', 'tool-pin'),
               {'module': 'tool-local', 'source': '/root/modules/tool-local'},
               {'module': 'tool-file', 'source': 'file:///root/modules/tool-file'},
               # A matching basename is not a matching repository.
               {'module': 'tool-fork', 'source': source(repo='desktop', ref='fork-pin',
                   module='tool-fork').replace('/example/', '/another/')}],
        hooks=[row('hook-independent', 'hook-pin')],
        session={'context': row('context-independent', 'context-pin')},
        spawn={'tools': [row('tool-spawn', 'spawn-pin')]},
        agents={'worker': {'providers': [row('provider-agent', 'agent-provider-pin')],
                           'tools': [row('tool-agent', 'agent-tool-pin')]}})
    extra = Bundle(name='desktop', base_path=tmp_path / 'default',
                   tools=[{'module': 'tool-computer-use', 'source': default_source}])
    original = root.to_mount_plan()
    base, behavior = capability_defaults(root, extra, {})
    policy = HostComponents()
    policy.select_defaults(behavior, base, {})
    result = policy.apply(compose_bundles(base, behavior))
    assert [row for row in declarations(result.to_mount_plan())
            if row['module'] != 'tool-computer-use'] == list(declarations(original))
    assert policy.normalize(original) == original
    assert root.to_mount_plan() == original
    for item in declarations(original):
        module, saved = item['module'], item['source']
        assert module not in policy.sources
        assert not policy.owns(module, saved)
        late = source(repo='child-choice', ref='child-pin', module=module)
        assert policy.source(module, late) == late
        config = SimpleNamespace(module_sources={module: late})
        assert module_source(config, False, module, saved, policy) == late
    # The same root module and a new sibling may both choose later child pins.
    child = {'providers': [row('provider-independent', 'child-provider-pin')],
             'tools': [row('tool-independent', 'child-tool-pin'), row('tool-late', 'child-sibling-pin')],
             'spawn': {'tools': [row('tool-spawn', 'child-spawn-pin')]},
             'agents': {'worker': {'tools': [row('tool-agent', 'child-agent-pin')]}}}
    assert policy.normalize(child) == child


@pytest.mark.parametrize('nested', [False, True])
def test_default_collision_pin_survives_removed_duplicate_and_nested_specialist(nested):
    from amplifier_foundation import Bundle
    module = 'tool-computer-use'
    chosen = source(repo='desktop', ref='explicit', module=module)
    default = source(repo='desktop', ref='main', module=module)
    root = Bundle(name='root', tools=[{'module': module, 'source': chosen,
                                      'config': {'read_only': False}}])
    extra = Bundle(name='desktop', tools=[{'module': module, 'source': default}],
                   agents={'operator': {'tools': [{'module': module, 'source': default}]}}
                   if nested else {})
    base, behavior = capability_defaults(root, extra, {module: {'read_only': True}})
    assert behavior.tools == []
    policy = HostComponents()
    policy.select_defaults(behavior, base, {})
    result = policy.apply(compose_bundles(base, behavior))
    assert result.tools == root.tools
    assert policy.source(module, default) == chosen
    assert policy.source('tool-late', source(repo='desktop', ref='main', module='tool-late')) == (
        source(repo='desktop', ref='explicit', module='tool-late'))
    if nested:
        assert result.agents['operator']['tools'] == [
            {'module': module, 'source': chosen, 'config': {'read_only': True}}]
    assert extra.tools[0]['source'] == default


@pytest.mark.parametrize('section', ['providers', 'tools', 'spawn', 'agents'])
def test_default_repository_collision_preserves_base_pin_and_aligns_default(section):
    from amplifier_foundation import Bundle
    pinned = {'module': 'module-existing', 'source': source(
        repo='desktop', ref='explicit', module='module-existing')}
    plan = {section: [pinned]}
    if section == 'spawn':
        plan = {'spawn': {'tools': [pinned]}}
    elif section == 'agents':
        plan = {'agents': {'existing': {'tools': [pinned]}}}
    root = Bundle(name='root', **plan)
    extra = Bundle(name='desktop', tools=[{'module': 'tool-computer-use', 'source': source(
        repo='desktop', ref='main', module='tool-computer-use')}])
    base, behavior = capability_defaults(root, extra, {})
    policy = HostComponents()
    policy.select_defaults(behavior, base, {})
    result = policy.apply(compose_bundles(base, behavior))
    added = next(row for row in result.tools if row['module'] == 'tool-computer-use')
    assert added['source'] == source(repo='desktop', ref='explicit', module='tool-computer-use')
    assert policy.source('tool-late', source(repo='desktop', ref='main', module='tool-late')) == (
        source(repo='desktop', ref='explicit', module='tool-late'))
    assert policy.normalize(root.to_mount_plan()) == root.to_mount_plan()


@pytest.mark.parametrize('chosen', [
    source(repo='desktop-fork', ref='explicit', module='tool-computer-use'),
    '/chosen/modules/tool-computer-use',
    'file:///chosen/modules/tool-computer-use',
    './modules/tool-computer-use',
    'package-source',
])
def test_default_module_collision_preserves_git_and_non_git_base_pins(tmp_path, chosen):
    from amplifier_foundation import Bundle
    module = 'tool-computer-use'
    root = Bundle(name='root', base_path=tmp_path / 'root',
                  tools=[{'module': module, 'source': chosen}])
    extra = Bundle(name='desktop', base_path=tmp_path / 'default',
                   agents={'operator': {'tools': [{'module': module, 'source': './modules/default'}]}})
    base, behavior = capability_defaults(root, extra, {})
    policy = HostComponents()
    policy.select_defaults(behavior, base, {})
    result = policy.apply(compose_bundles(base, behavior))
    expected = str((root.base_path / chosen).resolve()) if chosen.startswith('./') else chosen
    assert result.tools[0]['source'] == expected
    assert result.agents['operator']['tools'][0]['source'] == expected


def test_default_collisions_use_effective_sources_without_owning_unrelated_overrides():
    from amplifier_foundation import Bundle
    module = 'tool-computer-use'
    chosen = source(repo='configured-desktop', ref='explicit', module=module)
    root = Bundle(name='root', tools=[
        {'module': 'tool-sibling', 'source': source(
            repo='configured-desktop', ref='explicit', module='tool-sibling')},
        {'module': 'tool-independent', 'source': '/independent/original'}])
    extra = Bundle(name='desktop', tools=[{'module': module, 'source': source(
        repo='upstream-desktop', ref='main', module=module)}])
    overrides = {module: chosen, 'tool-independent': '/independent/override'}
    policy = HostComponents()
    policy.select_defaults(extra, root, overrides)
    assert policy.sources[module] == chosen
    assert policy.sources['tool-sibling'] == root.tools[0]['source']
    assert not policy.owns('tool-independent', root.tools[1]['source'])
    assert policy.source('tool-independent', '/independent/child') == '/independent/child'


def test_default_collision_selection_keeps_required_loop_and_naming_owners():
    from amplifier_foundation import Bundle
    loop, naming = 'loop-live', 'hooks-session-naming'
    policy = HostComponents({loop: 'qualified-loop'}, {
        loop: lambda: '/installed/loop', naming: lambda: '/installed/naming'})
    root = Bundle(name='root', session={'orchestrator': {'module': loop, 'source': 'root-loop'}},
                  hooks=[{'module': naming, 'source': 'root-naming'}])
    extra = Bundle(name='desktop', session={'orchestrator': {'module': loop, 'source': 'default-loop'}},
                   hooks=[{'module': naming, 'source': 'default-naming'}])
    base, behavior = capability_defaults(root, extra, {})
    policy.select_defaults(behavior, base, {})
    assert policy.sources == {loop: 'qualified-loop'}
    assert policy.source(loop, 'child-loop', installed=True) == '/installed/loop'
    assert policy.source(naming, 'child-naming', installed=True) == '/installed/naming'
