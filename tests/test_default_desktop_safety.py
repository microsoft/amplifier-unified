"""Separate installed desktop-ceiling proof, never part of the 12-case IC-13 count.

The first four test functions preserve the 22 previously unexecuted cases from
c7c6e6c29 (8 expanded-value, 6 resume, 4 dispatch controls, 4 joined cases).
They run against the CURRENT installed product, not that historical product.
Only fixture adaptation: wrap the current lifecycle fixture's context with
stateful message retention before the real host restores/checkpoints history.
Mounts and finite execution remain stubs; actual ComputerTool/DesktopTool policy
and dispatch run against a no-effect backend. No real Core/desktop/SSH claim.

The remote group adds two missing effective-default discriminators and one
explicit-write control for the existing no-widening promise. It does not test
transport, authentication, OS consent or a live remote machine. Do not mark these
xfail or reinterpret earlier source predictions as observed failing runs.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


if not os.environ.get('UNIFIED_BEHAVIOR_REPOS'):
    pytest.skip('Owned installed-wheel desktop qualification requires UNIFIED_BEHAVIOR_REPOS', allow_module_level=True)
assert Path('/.dockerenv').exists() or Path('/run/systemd/container').exists(), 'Owned container required'


def _load_support(name, filename):
    path = Path(__file__).with_name(filename)
    assert path.is_file(), f'Copy unchanged supporting test beside this file: {filename}'
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_composition = _load_support('_desktop_composition_support', 'test_default_behaviors_integration.py')
# Guard origins before importing the current policy/lifecycle fixture.
_composition._installed('amplifier_web', 'amplifier-unified')
_composition._installed('amplifier_foundation', 'amplifier-foundation')
_policy = _load_support('_desktop_current_policy_support', 'test_default_capability_policy.py')
_safety = _load_support('_desktop_runtime_safety_support', 'test_default_runtime_safety.py')
offline_defaults = _safety.offline_defaults
runtime_defaults = _safety.runtime_defaults
_current_lifecycle = _policy.fallback_lifecycle


@pytest.fixture
def fallback_lifecycle(_current_lifecycle, monkeypatch):
    """Only restore-state adaptation; do not repair or inject desktop policy."""
    h = _current_lifecycle
    prepared_class = type(h.children.prepared['parent'])
    original_create = prepared_class.create_session

    async def create_session(prepared, **kwargs):
        child = await original_create(prepared, **kwargs)
        context = child.coordinator.get('context')
        messages = []

        async def set_messages(value):
            messages[:] = copy.deepcopy(value)

        async def get_messages():
            return copy.deepcopy(messages)

        context.set_messages.side_effect = set_messages
        context.get_messages.side_effect = get_messages
        return child

    monkeypatch.setattr(prepared_class, 'create_session', create_session)
    return h


@pytest.fixture
def desktop_policy_boundary(runtime_defaults, monkeypatch):
    """Real pinned constructor/dispatch, no-effect backend, no consent UI."""
    module = _safety._upstream(
        runtime_defaults, 'computer-use', 'tool-computer-use',
        'amplifier_module_tool_computer_use', 'amplifier-module-tool-computer-use',
    )
    assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == (
        '53fd72ca4b49b1b0093398a8a48468b491d45f39e93cef4ed7bd655f91a958ea'
    ), 'Requalify the desktop policy API before changing this exact source attestation'
    backend_api = importlib.import_module(module.__name__ + '.backend')
    monkeypatch.setattr(module, '_build_announcement', lambda *args, **kwargs: None)

    class Backend:
        name = 'desktop-ceiling-fake'

        def __init__(self, remote):
            # The actual constructor consumes this public Backend attribute.
            # No RemoteBackend is constructed; target strings never contact SSH.
            self.is_remote = remote
            self.writes = []

        def list_monitors(self):
            return [backend_api.MonitorInfo('fake', 0, 0, 800, 600, primary=True)]

        def cursor_position(self):
            return 20, 30

        def focus_window(self, handle):
            self.writes.append(handle)

        def list_windows(self):
            return SimpleNamespace(foreground='fake-window', windows=[])

        def __getattr__(self, name):
            pytest.fail(f'Unexpected fake-backend access: {name}')

    def create(config, *, remote=False):
        backend = Backend(remote)
        computer = module.ComputerTool(backend, config=copy.deepcopy(config))
        computer.resolve_display()
        return computer, module.DesktopTool(computer), backend

    return create


async def _assert_desktop_boundary(create, config, key, restricted, *, remote=False):
    computer, desktop, backend = create(config, remote=remote)
    read = await computer.execute({'action': 'cursor_position'})
    assert read.success is True, read.error
    assert backend.writes == []
    # No approval or unattended grant. This is the actual DesktopTool fail-safe.
    result = await desktop.execute({'action': 'focus_window', 'handle': 'fake-window'})
    if restricted:
        assert result.success is False, {'output': result.output, 'backend_writes': backend.writes}
        assert ('read_only' if key == 'read_only' else 'requires confirmation') in result.error['message']
        assert backend.writes == [], 'Restriction failed before backend dispatch'
    else:
        assert result.success is True, result.error
        assert backend.writes == ['fake-window'], 'Permitted control must actually reach the backend'


@pytest.mark.parametrize('restriction', ['read_only', 'gate_writes'])
@pytest.mark.parametrize('value_kind,restricted', [
    ('environment-true', True), ('boolean-true', True),
    ('boolean-false', False), ('environment-false', True),
])
def test_expanded_desktop_restriction_survives_child_alias(monkeypatch, restriction, value_kind, restricted):
    # PR815 discussion_r4299532265; preserve effective string truthiness.
    from amplifier_web.host.children import child_plan
    from amplifier_web.host.config import expand_environment
    from amplifier_web.host.session import _expand_module_configuration

    other = 'gate_writes' if restriction == 'read_only' else 'read_only'
    if value_kind.startswith('environment-'):
        monkeypatch.setenv('UNIFIED_TEST_DESKTOP_RESTRICTION', value_kind.removeprefix('environment-'))
        declared_value = '${UNIFIED_TEST_DESKTOP_RESTRICTION}'
        expanded_value = value_kind.removeprefix('environment-')
    else:
        declared_value = restricted
        expanded_value = restricted
    declared = {'tools': [{'module': 'tool-computer-use', 'instance_id': 'parent-desktop',
                           'config': {restriction: declared_value, other: False}}]}
    overlay = {'tools': [{'module': 'tool-computer-use', 'instance_id': 'child-alias',
                         'config': {'read_only': False, 'gate_writes': False}}]}
    original_declared, original_overlay = copy.deepcopy((declared, overlay))
    parent = _expand_module_configuration(declared)
    assert parent['tools'][0]['config'] == expand_environment(declared['tools'][0]['config'])
    actual_value = parent['tools'][0]['config'][restriction]
    assert actual_value == expanded_value and type(actual_value) is type(expanded_value)
    original_parent = copy.deepcopy(parent)

    result = child_plan(parent, overlay)

    assert declared == original_declared
    assert parent == original_parent
    assert overlay == original_overlay
    alias, = [row for row in result['tools'] if row.get('instance_id') == 'child-alias']
    assert alias['config'][restriction] is restricted
    assert alias['config'][other] is False
    assert result['_unified_desktop_policy'] == ({restriction: True} if restricted else {})
    if restricted:
        assert all(row['config'][restriction] is True for row in result['tools']
                   if row.get('module') == 'tool-computer-use')


@pytest.mark.parametrize('restriction', ['read_only', 'gate_writes'])
@pytest.mark.parametrize('saved_shape', ['legacy-tools-only', 'marker-present', 'explicitly-writable'])
async def test_resume_preserves_saved_desktop_ceiling_and_history(
        fallback_lifecycle, monkeypatch, restriction, saved_shape):
    # Synthetic pre-marker shape, NOT generated by executing historical code.
    # SessionStore, Children.resume and checkpoints are real; Core mounts stubbed.
    from unittest.mock import Mock

    h = fallback_lifecycle
    identity = 'saved-desktop-child'
    other = 'gate_writes' if restriction == 'read_only' else 'read_only'
    restricted = saved_shape != 'explicitly-writable'
    assert h.coordinator.config.get('tools', []) == []
    assert 'spawn' not in h.coordinator.config
    assert '_unified_desktop_policy' not in h.coordinator.config
    overlay = {'tools': [{'module': 'tool-computer-use', 'instance_id': 'saved-desktop',
                         'config': {}}]}
    retained_plan = copy.deepcopy(h.coordinator.config)
    retained_plan['tools'] = [{'module': 'tool-computer-use', 'instance_id': 'saved-desktop',
                               'config': {restriction: restricted, other: False}}]
    if saved_shape == 'marker-present':
        retained_plan['_unified_desktop_policy'] = {restriction: True}
    else:
        assert '_unified_desktop_policy' not in retained_plan
    messages = [
        {'role': 'user', 'content': 'Keep this saved request verbatim.\nSecond line.'},
        {'role': 'assistant', 'content': 'Retained response: no desktop operation was performed.'},
    ]
    metadata = {'parent_id': 'parent', 'agent_name': 'desktop-worker',
                'agent_overlay': overlay, 'mount_plan': retained_plan, 'status': 'interrupted'}
    prepared = h.children.prepared['parent']
    originals = copy.deepcopy((messages, metadata, h.coordinator.config,
                               prepared.mount_plan, prepared.bundle.to_mount_plan()))
    h.store.save(identity, messages, metadata)
    before_messages, before_metadata = h.store.load(identity)
    assert before_messages == messages
    assert before_metadata['mount_plan'] == retained_plan
    assert before_metadata['agent_overlay'] == overlay
    save = Mock(wraps=h.store.save)
    monkeypatch.setattr(h.store, 'save', save)

    result = await h.children.resume(identity, 'Resume without relaxing desktop policy.', h.parent)

    assert result['status'] == 'success'
    observation, = h.observations
    coordinator = observation['coordinator']
    h.children.install.assert_awaited_once()
    child_prepared = h.children.install.await_args.args[1]
    coordinator.get('context').set_messages.assert_awaited_once_with(messages)
    assert [call.args[2]['status'] for call in save.call_args_list] == ['running', 'completed']
    assert all(call.args[0] == identity and call.args[1] == messages for call in save.call_args_list)
    saved_messages, saved_metadata = h.store.load(identity)
    assert saved_messages == messages
    assert saved_metadata['status'] == 'completed'
    assert saved_metadata['agent_overlay'] == overlay
    assert (messages, metadata, h.coordinator.config,
            prepared.mount_plan, prepared.bundle.to_mount_plan()) == originals

    expected_ceiling = {restriction: True} if restricted else {}
    plans = [coordinator.config, child_prepared.mount_plan, saved_metadata['mount_plan']]
    plans.extend(call.args[2]['mount_plan'] for call in save.call_args_list)
    for plan in plans:
        desktop, = [row for row in plan['tools'] if row.get('module') == 'tool-computer-use']
        assert desktop['config'].get(restriction, False) is restricted
        assert desktop['config'].get(other, False) is False
        assert plan['_unified_desktop_policy'] == expected_ceiling
    desktop, = [row for row in child_prepared.bundle.tools if row.get('module') == 'tool-computer-use']
    assert desktop['config'].get(restriction, False) is restricted
    assert desktop['config'].get(other, False) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['read_only', 'gate_writes'])
@pytest.mark.parametrize('restricted', [True, False], ids=['restricted-control', 'write-control'])
async def test_runtime_desktop_ceiling_controls(desktop_policy_boundary, key, restricted):
    config = {'read_only': False, 'gate_writes': False, key: restricted}
    await _assert_desktop_boundary(desktop_policy_boundary, config, key, restricted)


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['read_only', 'gate_writes'])
@pytest.mark.parametrize('source', ['legacy-saved-tools', 'environment'])
async def test_runtime_inherited_desktop_ceiling_survives_representation(
    desktop_policy_boundary, fallback_lifecycle, monkeypatch, key, source,
):
    from amplifier_web.host.children import child_plan
    from amplifier_web.host.session import _expand_module_configuration

    h = fallback_lifecycle
    restriction = {'read_only': False, 'gate_writes': False, key: True}
    if source == 'environment':
        monkeypatch.setenv('DESKTOP_LIMIT', 'true')
        expanded = _expand_module_configuration({
            'tools': [{'module': 'tool-computer-use',
                       'config': {**restriction, key: '${DESKTOP_LIMIT}'}}],
        })
        assert expanded['tools'][0]['config'][key] == 'true'
        await _assert_desktop_boundary(desktop_policy_boundary, expanded['tools'][0]['config'], key, True)
        overlay = {'tools': [{'module': 'tool-computer-use', 'instance_id': 'child-alias',
                              'config': {'read_only': False, 'gate_writes': False}}]}
        before = copy.deepcopy((expanded, overlay))
        result = child_plan(expanded, overlay, working_dir=h.store.base_dir.parent)
        assert (expanded, overlay) == before
        config = next(row['config'] for row in result['tools'] if row.get('instance_id') == 'child-alias')
    else:
        assert not h.coordinator.config.get('tools'), 'Current parent must not supply the missing ceiling'
        saved_plan = {'tools': [{'module': 'tool-computer-use',
                                'instance_id': 'legacy-alias', 'config': restriction}]}
        assert '_unified_desktop_policy' not in saved_plan
        await _assert_desktop_boundary(desktop_policy_boundary, restriction, key, True)
        metadata = {'parent_id': 'parent', 'agent_name': 'worker',
                    'agent_overlay': {'tools': [{'module': 'tool-computer-use',
                                                'instance_id': 'legacy-alias', 'config': {}}]},
                    'effective_selection': copy.deepcopy(h.choice), 'mount_plan': saved_plan}
        h.store.save('legacy-desktop', [], metadata)
        before = copy.deepcopy(metadata)
        await h.children.resume('legacy-desktop', 'resume without new permission', h.parent)
        assert metadata == before
        actual = h.observations[-1]['coordinator'].config
        config = next(row['config'] for row in actual['tools'] if row.get('instance_id') == 'legacy-alias')
        retained = h.store.load('legacy-desktop')[1]['mount_plan']
        assert next(row['config'] for row in retained['tools'] if row.get('instance_id') == 'legacy-alias') == config
    await _assert_desktop_boundary(desktop_policy_boundary, config, key, True)


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['read_only', 'gate_writes'])
async def test_runtime_remote_implicit_restriction_cannot_be_disabled_by_child(desktop_policy_boundary, key):
    """Existing effective access cannot become a child-authored write grant."""
    from amplifier_web.host.children import child_plan

    config = {'target': 'ssh://synthetic@desktop.invalid'}
    if key == 'gate_writes':
        config['read_only'] = False
    # Establish actual upstream implicit policy, rather than reimplementing it.
    await _assert_desktop_boundary(desktop_policy_boundary, config, key, True, remote=True)
    parent = {'tools': [{'module': 'tool-computer-use', 'instance_id': 'same-desktop', 'config': config}]}
    overlay = {'tools': [{'module': 'tool-computer-use', 'instance_id': 'same-desktop',
                         'config': {'read_only': False, 'gate_writes': False}}]}
    original = copy.deepcopy((parent, overlay))
    result = child_plan(parent, overlay)
    assert (parent, overlay) == original
    child, = [row for row in result['tools'] if row.get('instance_id') == 'same-desktop']
    assert child['config']['target'] == config['target'], 'Keep the same backend target, not a new consent scope'
    await _assert_desktop_boundary(desktop_policy_boundary, child['config'], key, True, remote=True)


@pytest.mark.asyncio
async def test_runtime_remote_explicit_write_opt_in_control(desktop_policy_boundary):
    # Unconditional refusal must not satisfy the two implicit-policy cases.
    from amplifier_web.host.children import child_plan

    config = {'target': 'ssh://synthetic@desktop.invalid', 'read_only': False, 'gate_writes': False}
    await _assert_desktop_boundary(desktop_policy_boundary, config, 'read_only', False, remote=True)
    parent = {'tools': [{'module': 'tool-computer-use', 'instance_id': 'same-desktop', 'config': config}]}
    overlay = {'tools': [{'module': 'tool-computer-use', 'instance_id': 'same-desktop',
                         'config': {'read_only': False, 'gate_writes': False}}]}
    original = copy.deepcopy((parent, overlay))
    child, = child_plan(parent, overlay)['tools']
    assert (parent, overlay) == original
    assert child['config']['target'] == config['target']
    await _assert_desktop_boundary(desktop_policy_boundary, child['config'], 'read_only', False, remote=True)
