"""Pure negative controls before the private replica may start real workers."""
import importlib.util
from pathlib import Path
import sys

import pytest

path = Path(__file__).resolve().parents[1] / 'scripts/scale_runtime_fence.py'
spec = importlib.util.spec_from_file_location('scale_runtime_fence', path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def admitted():
    row = {'id': 'new-synthetic', 'workspace': '/owned/synthetic', 'bundle': '/owned/bundle.yaml',
           'nativeProject': 'owned-synthetic', 'nativeIdentity': 'new-synthetic',
           'runtimeSessionId': 'new-synthetic', 'historyManaged': False}
    fence = module.Fence()
    fence.freeze([module.Binding(row['id'], row['workspace'], row['bundle'], row['nativeProject'])],
                 lambda identity: row if identity == row['id'] else None)
    return fence, row


def test_empty_and_frozen_allowlist_cannot_admit_imported_or_new_child():
    with pytest.raises(module.ReplicaRefusal):
        module.Fence().owner('copied')
    fence, row = admitted()
    for sid in ('copied', 'new-child', None):
        with pytest.raises(module.ReplicaRefusal):
            fence.owner(sid)
    with pytest.raises(module.ReplicaRefusal):
        fence.freeze([], lambda _: row)
    with pytest.raises(TypeError):
        fence.bindings['copied'] = next(iter(fence.bindings.values()))


@pytest.mark.parametrize('field,value', [('workspace', '/copied'), ('bundle', 'other'),
    ('nativeProject', 'copied'), ('nativeIdentity', 'copied'), ('runtimeSessionId', 'copied'),
    ('historyManaged', True), ('id', 'copied')])
def test_same_id_source_rebinding_is_refused(field, value):
    fence, row = admitted()
    row[field] = value
    with pytest.raises(module.ReplicaRefusal):
        fence.owner('new-synthetic')


def test_routes_require_explicit_exact_owner_and_unknown_effects_fail_closed():
    fence, row = admitted()
    for action, args in [('conversation.send', {}), ('conversation.stop', {'sessionId': 'copied'}),
        ('session.create', {}), ('session.delete', {'id': row['id']}),
        ('shell.command', {'action': 'conversation.send', 'args': {'sessionId': 'copied'}}),
        ('shell.command', {'command': {'action': 'conversation.send', 'args': {'sessionId': 'copied'}}}),
        ('shell.command', {'action': 'shell.command', 'args': {}})]:
        with pytest.raises(module.ReplicaRefusal):
            fence.action(action, args)
    fence.action('shell.command', {'command': {'action': 'conversation.send', 'args': {'sessionId': row['id']}}})
    fence.action('session.select', {'id': 'copied'})
    fence.action('conversation.send', {'sessionId': row['id']})
    fence.action('shell.command', {'action': 'conversation.stop', 'args': {'sessionId': row['id']}})


def test_agent_bridge_cannot_use_copied_owner_child_or_arbitrary_effect():
    fence, row = admitted()
    for operation, args, sid in [('capacity.admit', {}, 'copied'),
        ('capacity.admit', {'_runtimeSessionId': 'child'}, row['id']),
        ('dispatch', {'action': 'conversation.send', 'args': {'sessionId': 'copied'}}, row['id']),
        ('integration.admit', {}, row['id'])]:
        with pytest.raises(module.ReplicaRefusal):
            fence.bridge(operation, args, sid)
    fence.bridge('capacity.admit', {'_runtimeSessionId': row['id']}, row['id'])


async def test_runtime_refuses_before_any_base_effect_but_passes_exact_owner():
    calls = []
    class Base:
        async def start(self, *args, **kwargs): calls.append('start')
        async def send(self, *args, **kwargs): calls.append('send')
        async def stop(self, *args, **kwargs): calls.append('stop')
    fence, row = admitted()
    runtime = module.guarded_runtime(Base, fence)()
    for method, args in [('start', ({**row, 'nativeIdentity': 'copied'},)),
        ('send', ({**row, 'id': 'copied'},)), ('control', ('copied', 'ping')),
        ('stop', ('copied',)), ('takeover', (row,)), ('spawn_worker', (row,)),
        ('scheduled_input', (row['id'], {}, None)), ('shared_state_probe', ({},))]:
        with pytest.raises(module.ReplicaRefusal):
            await getattr(runtime, method)(*args)
    assert calls == []
    await runtime.start(row)
    await runtime.send(row)
    await runtime.stop(row['id'])
    assert calls == ['start', 'send', 'stop']
