"""Actual native Runtime events retain host admission and descendant identity."""
import asyncio
from copy import deepcopy
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_module_loop_live.runtime import Input, Runtime

from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError
from test_collaboration_generation_authority import app, bound
from test_runtime_worker import coordinator, worker_bridge
from test_runtime_stop_admission import FIXTURE


def native_worker(identity, epoch, monkeypatch, published):
    worker = Worker()
    owner = coordinator(identity)
    owner.get = lambda name: None
    worker.session = SimpleNamespace(coordinator=owner)
    worker.runtime = Runtime(identity, observer=worker.observe)
    worker.execution = object()
    worker.start_config = {'id': identity, 'interruptionRevision': epoch}
    worker.activation_gate = SimpleNamespace(current_valid=True)
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', published.append)
    return worker


async def host_admission(source, operation, arguments, *, app=None):
    manager = RuntimeManager()
    manager.bind_execution_state(app.worktrees.execution_state if app else lambda _: {
        'revision': 0, 'directory': source['workspace'], 'fenced': False,
        'interruptionRevision': source.get('interruptionRevision', 0)})
    manager.workers[source['id']] = {
        'process': SimpleNamespace(returncode=None), 'pending': {}, 'inflight': set(),
        'start_session': {'interruptionRevision': 0}}
    sent = []
    async def write(row, command):
        sent.append(deepcopy(command))
    manager._write = write
    await manager._admit(source['id'], operation, arguments)
    return sent[0]


async def publish_generation(app, source, worker, published, generation, input_id):
    before = len(published)
    await worker.runtime.emit('generation.started', generation_id=generation,
                              initial_input_id=input_id)
    await worker.runtime.emit('input.delivered', input_id=input_id)
    for event in published[before:]:
        normalized = normalize_event(event, source['id'])
        if normalized:
            await app.on_runtime_event(*normalized)


@pytest.mark.parametrize('kind', ['message', 'schedule'])
async def test_delayed_native_start_keeps_pre_stop_epoch_and_fresh_work_needs_no_human(app, monkeypatch, kind):
    source = app.state['sessions'][0]
    published = []
    worker = native_worker(source['id'], 0, monkeypatch, published)
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', AsyncMock(return_value='Work'))
    monkeypatch.setattr('amplifier_web.host.mentions.expand_input', AsyncMock(return_value='Work'))

    def command(identity):
        if kind == 'message':
            return 'send', {'text': 'Work', 'input_id': identity}
        return 'control', {'operation': 'schedule.submit', 'arguments': {
            'taskId': 'task', 'taskRevision': 1, 'inputId': identity, 'text': 'Work', 'kind': 'task'}}

    def controls(target):
        target.session.coordinator.register_capability('live.continuation_guard_supported', True)
        target.controls = SimpleNamespace(coordinator=target.session.coordinator,
            tasks=SimpleNamespace(record=lambda: {'id': 'task', 'revision': 1, 'status': 'active'}),
            require_idle=lambda: None)

    controls(worker)
    original = await host_admission(source, *command('old-input'), app=app)
    assert original['interruptionRevision'] == 0
    await worker._command_serial(original)
    assert 'old-input' in worker.runtime.accepted
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    retained = deepcopy(source['collaborationGeneration'])
    # Stop has won at the host before the first native start is received.
    await publish_generation(app, source, worker, published, 'delayed-start', 'old-input')
    assert worker.runtime.generation['interruptionRevision'] == 0
    assert source['collaborationGeneration']['id'] == retained['id']
    with bound(source):
        with pytest.raises(AppError, match='Stop'):
            app.collaboration.active_binding(source)

    fresh = native_worker(source['id'], source['interruptionRevision'], monkeypatch, published)
    controls(fresh)
    admitted = await host_admission(source, *command('fresh-input'), app=app)
    assert admitted['interruptionRevision'] == 1
    await fresh._command_serial(admitted)
    await publish_generation(app, source, fresh, published, 'fresh-generation', 'fresh-input')
    assert source['collaborationGeneration']['interruptionRevision'] == 1
    with bound(source):
        assert app.collaboration.lineage(source, 'agent')['sourceInputIds'] == ['fresh-input']


async def test_native_internal_service_followup_retains_process_epoch_after_stop(app, monkeypatch):
    source = app.state['sessions'][0]
    published = []
    old = native_worker(source['id'], 0, monkeypatch, published)
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    retained = deepcopy(source['collaborationGeneration'])
    # loop-live creates child/job result Inputs internally, outside accepted.
    report = Input('service', 'Delegated result', source='amplifier-child')
    assert report.id not in old.runtime.accepted
    await publish_generation(app, source, old, published, 'old-child-result', report.id)
    assert source['collaborationGeneration']['id'] == retained['id']
    with bound(source):
        with pytest.raises(AppError, match='Stop'):
            app.collaboration.active_binding(source)
    fresh = native_worker(source['id'], 1, monkeypatch, published)
    report = Input('service', 'Fresh delegated result', source='amplifier-child')
    await publish_generation(app, source, fresh, published, 'fresh-child-result', report.id)
    with bound(source):
        assert app.collaboration.lineage(source, 'agent')['sourceInterruptionRevision'] == 1


async def test_accepted_input_retry_keeps_original_epoch_and_context(monkeypatch):
    worker = native_worker('root', 0, monkeypatch, [])
    worker.bind_input_context('original', {'interruptionRevision': 0}, {'clientId': 'original-browser'})
    await worker.runtime.submit(Input('user', 'Work', id='original'))
    worker.bind_input_context('original', {'interruptionRevision': 1}, {'clientId': 'later-browser'})
    await worker.runtime.emit('generation.started', generation_id='original-generation', initial_input_id='original')
    assert worker.runtime.generation['interruptionRevision'] == 0
    assert worker.context_bindings['original']['clientId'] == 'original-browser'


async def test_stop_replaces_process_before_new_startup_epoch_can_be_used(tmp_path):
    fixture, release, log = (tmp_path / name for name in ('worker.py', 'release', 'commands.jsonl'))
    fixture.write_text(FIXTURE.replace(
        "{'pid': os.getpid(), 'op': data['op']}",
        "{'pid': os.getpid(), 'op': data['op'], 'epoch': data.get('session', data).get('interruptionRevision')}"))
    manager = RuntimeManager(command=[sys.executable, str(fixture), str(release), str(log)])
    closed = asyncio.Event()
    async def emit(kind, data):
        if kind == 'runtime.status' and data.get('event') == 'session.closed': closed.set()
    session = {'id': 'owned', 'workspace': str(tmp_path), 'bundle': 'fixture', 'interruptionRevision': 0}
    try:
        await manager.start(session, emit)
        original = manager.workers['owned']['process']
        stop = asyncio.create_task(manager.stop('owned'))
        await asyncio.wait_for(closed.wait(), 10)
        session = {**session, 'interruptionRevision': 1}
        new_input = asyncio.create_task(manager.send(session, 'Fresh authorized work', 'fresh', emit))
        await asyncio.sleep(.05)
        assert not new_input.done() and original.returncode is None
        assert [row['op'] for row in map(json.loads, log.read_text().splitlines())] == ['start', 'stop']
        release.touch()
        await asyncio.wait_for(stop, 10)
        response = await asyncio.wait_for(new_input, 10)
        assert original.returncode == 0 and response['pid'] != original.pid
        rows = list(map(json.loads, log.read_text().splitlines()))
        assert [(row['op'], row['epoch']) for row in rows] == [
            ('start', 0), ('stop', None), ('start', 1), ('send', 1)]
    finally:
        release.touch()
        await manager.close()


@pytest.mark.parametrize('operation,args', [
    ('send', {'text': 'work', 'input_id': 'i'}),
    ('retry', {'text': 'work', 'input_id': 'i'}),
    *[('control', {'operation': name, 'arguments': {'inputId': 'i'}}) for name in
        ['conversation.steer', 'coordination.submit', 'coordination.steer', 'schedule.submit', 'observation.submit']],
    ('control', {'operation': 'history.edit', 'arguments': {'operationId': 'i'}}),
])
async def test_host_admission_epoch_is_private_and_preserves_exact_owner_arguments(operation, args):
    source = {'id': 'root', 'workspace': '/synthetic', 'interruptionRevision': 7}
    original = deepcopy(args)
    result = await host_admission(source, operation, {**args, 'interruptionRevision': 900})
    assert result['interruptionRevision'] == 7
    assert args == original
    if operation == 'control':
        assert result['arguments'] == original['arguments']


@pytest.mark.parametrize('invalid', ['stop', 'cancel', 'activation', 'closed', 'stop_requested', 'later_generation'])
async def test_child_bridge_stops_stamping_original_generation_when_invalid(worker_bridge, invalid):
    app, root, worker, envelopes = worker_bridge
    child = coordinator('native-child')
    bridge = worker.app_access_bridge(child)
    captured = []
    async def transport(operation, args):
        captured.append(args)
    worker.bridge = transport
    await bridge('history', {'action': 'list'})
    assert captured[-1]['_generationId'] == 'generation-root'
    if invalid == 'stop': worker.stop_revision += 1
    elif invalid == 'cancel': child.cancellation.is_cancelled = True
    elif invalid == 'activation': worker.activation_gate.current_valid = False
    elif invalid == 'closed': worker.runtime.closed = True
    elif invalid == 'stop_requested': worker.runtime.stop_requested = True
    else: worker.runtime.generation = {'id': 'later', 'input_ids': ['later-input']}
    await bridge('history', {'action': 'list', '_generationId': 'later', '_runtimeSessionId': 'native-root'})
    assert '_generationId' not in captured[-1]
    assert captured[-1]['_runtimeSessionId'] == 'native-child'
    assert captured[-1]['_inputBindings'] == [{'inputId': 'input-1', 'clientId': 'input-browser'}]


async def test_nested_child_keeps_original_assignment_when_installed_during_later_root_work(worker_bridge, monkeypatch):
    app, root, worker, envelopes = worker_bridge
    child = coordinator('native-child')
    worker.app_access_bridge(child)
    original_install = child.get_capability('web.app_access.install')
    worker.runtime.generation = {'id': 'later', 'input_ids': ['later-input']}
    worker.context_bindings['later-input'] = {'clientId': 'old-browser', 'targets': []}
    installed = []
    async def install(coordinator, bridge):
        coordinator.register_capability('web.app_access.bridge', bridge)
        installed.append(bridge)
    monkeypatch.setattr('amplifier_web.app_guidance.install_app_access', install)
    grandchild = coordinator('native-grandchild')
    await original_install(grandchild)
    captured = []
    async def transport(operation, args): captured.append(args)
    worker.bridge = transport
    await installed[0]('history', {'action': 'list'})
    assert captured[-1]['_runtimeSessionId'] == 'native-grandchild'
    assert '_generationId' not in captured[-1]
    assert captured[-1]['_inputBindings'] == [{'inputId': 'input-1', 'clientId': 'input-browser'}]
    assert grandchild.get_capability('web.work_origin_scope')() is None
