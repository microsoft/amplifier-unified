"""Commissioned limit recovery through the public bridge and native custody.

Synthetic seams: test_assignments' initial commissioning transport; a minimal
already-mounted coordinator; in-process host/worker RPC and runtime preparation.
The startup cases use real Management.ensure_runtime and RuntimeManager.start
with an explicitly synthetic _start_locked lifecycle, not a spawned process.
No guard, TaskController, Worker.command/acquisition/parking, Foundation lock,
ActivationGate, collaboration_input.admit, live.Input or Runtime.submit is mocked.
The seeded eight consumed turns are saved fixture evidence, not eight model calls.
These tests require the installed native dependencies; missing loop-live is an
error, not a skipped acceptance check. They do not qualify provider execution,
full mounting, cross-process pipe delivery, or the RuntimeManager transport.
"""
import asyncio
import copy
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError

from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError, AppService
from amplifier_web.shared_state import ActivationGate, configuration_stamp
from amplifier_web.task_continuity import publish_result
from test_assignments import app, child, agent_action, generation, until
from test_task_continuity import Coordinator


CORRECT = 'assignment.taskLimit.correct'
READ = 'assignment.taskLimit.read'
NATIVE = 'assignment.taskLimit.apply'
ADMIT = 'assignment.taskLimit.admit'
REASON = 'Revise the owner planning estimate within the original delegated task.'


async def test_runtime_manager_limit_control_uses_real_reply_and_never_revives(monkeypatch):
    from amplifier_web.runtime import RuntimeManager
    manager = RuntimeManager()
    row = {'process': SimpleNamespace(returncode=None), 'pending': {},
           'inflight': set(), 'parked': True}
    manager.workers['member'] = row
    writes = []
    async def write(owner, command):
        assert manager._locks['member'].locked()
        writes.append(command)
        owner['pending'][command['id']].set_result({'receipt': 'native-transaction'})
        owner['inflight'].discard(command['id'])
    monkeypatch.setattr(manager, '_write', write)
    assert await manager.control('member', NATIVE, {'commandId': 'limit'},
                                 guard=lambda: None) == {'receipt': 'native-transaction'}
    assert len(writes) == 1 and not row['pending']
    manager.workers.clear()
    manager._retired['member'] = ({'id': 'member'}, None)
    with pytest.raises(ValueError, match='retired'):
        await manager.control('member', NATIVE, {'commandId': 'next'}, guard=lambda: None)
    assert len(writes) == 1
    await manager.close()


class NativeCommandError(RuntimeError):
    def __init__(self, reply):
        self.reply = reply
        super().__init__(reply['error'])


class Pause:
    """A deterministic RPC/preparation suspension, not a replaced guard."""
    def __init__(self, stage):
        self.stage = stage
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def __call__(self, stage):
        if stage == self.stage:
            self.entered.set()
            await self.release.wait()


class InProcessTransport:
    def __init__(self, app, source, target, worker):
        self.app, self.source, self.target, self.worker = app, source, target, worker
        self.preparations, self.commands, self.callbacks, self.replies = [], [], [], []
        self.pause = None
        self.lose_ack = False

    async def boundary(self, stage):
        if self.pause:
            await self.pause(stage)

    async def prepare(self, session, **options):
        assert session['id'] == self.target['id']
        assert callable(options['restart_guard'])
        assert options['preserve_configuration'] is True
        self.preparations.append(session['id'])
        # Real dispatch rechecks its own guard after this awaited preparation.
        # This fixture does not claim to execute Management's mount machinery.
        await self.boundary('prepare')

    def assert_custody(self, *, task_lock=False):
        from amplifier_foundation.session import SessionBusyError
        w = self.worker
        assert w.command_lock.locked()
        assert w.shared_handle is not None and w.shared_handle.active and not w.parked
        w.activation_gate.check_current()
        if task_lock:
            assert w.controls.lock.locked()
        try:
            competing = w.shared_store.acquire(app='competing-fixture')
        except SessionBusyError:
            pass
        else:
            competing.release()
            pytest.fail('The native command did not exclude a competing canonical writer')

    async def bridge(self, operation, args):
        assert operation in {ADMIT, 'coordination.admit'}
        self.assert_custody(task_lock=operation == ADMIT)
        self.callbacks.append((operation, copy.deepcopy(args)))
        number = sum(op == operation for op, _ in self.callbacks)
        await self.boundary(f'before-native-{number}')
        # Preserve the exact Worker-stamped envelope. In particular the fixture
        # must not supply a missing principal and conceal a worker routing bug.
        if operation == ADMIT:
            assert set(args) == {'commandId', 'stage', '_runtimeSessionId'}
            assert args['_runtimeSessionId'] == self.worker.runtime.session_id
            assert args['stage'] == ('check' if number % 2 else 'commit')
        result = await self.app.app_bridge(operation, args, self.target['id'])
        if operation == ADMIT and args['stage'] == 'commit':
            # A second SQLite reader must see admission before native mutation,
            # not merely an uncommitted in-memory marker.
            database = self.app.db.execute('PRAGMA database_list').fetchone()[2]
            with sqlite3.connect(database) as reader:
                receipt = json.loads(reader.execute(
                    'SELECT receipt FROM commands WHERE id=?', (args['commandId'],)).fetchone()[0])
            assert receipt['state'] == 'pending' and receipt['effectAdmission']
            assert args['commandId'] not in self.worker.controls.tasks.receipts
        await self.boundary(f'after-native-{number}')
        return result

    async def command(self, operation, args):
        identity = f'wire-{len(self.commands) + 1}'
        self.commands.append((operation, copy.deepcopy(args)))
        await self.worker.command({'op': 'control', 'id': identity,
            'operation': operation, 'arguments': args,
            'interruptionRevision': self.target.get('interruptionRevision', 0)})
        replies = [row for row in self.replies if row.get('op') == 'reply' and row.get('id') == identity]
        assert len(replies) == 1, replies
        if 'error' in replies[0]:
            raise NativeCommandError(replies[0])
        return replies[0]['result']

    async def control(self, sid, operation, args, *, guard=None):
        assert sid == self.target['id'] and operation == NATIVE
        if guard:
            guard()
        await self.boundary('transport')
        if guard:
            guard()
        result = await self.command(operation, args)
        if self.lose_ack:
            raise ConnectionError('Synthetic lost acknowledgement after canonical commit')
        return result

    async def collaboration_input(self, session, args, guard, emit):
        assert session['id'] == self.target['id']
        reason = guard()
        if reason:
            return {'accepted': False, 'reason': reason}
        return await self.command('coordination.submit', args)

    def counts(self):
        return len(self.preparations), len(self.commands), len(self.callbacks)


@pytest.fixture
async def harness(app, monkeypatch, tmp_path):
    from amplifier_foundation.session.shared_state import SharedSessionStore, file_stamp
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.management import Management

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'native-locks'))
    source, independent = app.state['sessions']
    target, created = await child(app, source, 'limit-member')
    # The commissioning helper supplies initial transport observations only.
    # Settle that synthetic generation before installing the real native path.
    target['status'] = 'idle'
    target['collaborationGeneration']['terminal'] = True
    initial_inputs = copy.deepcopy(app.runtime.inputs)
    app.management = Management(app)

    w = Worker()
    w.workspace, w.home = Path(target['workspace']), app.data_dir
    w.start_config = {'id': target['id'], 'workspace': target['workspace']}
    w.runtime = Runtime(session_id=target['id'])
    coordinator = Coordinator()
    coordinator.session_id = target['id']
    coordinator.loop.pending = []
    coordinator.loop.jobs = {}
    coordinator.loop._active_jobs = lambda: False
    w.session = SimpleNamespace(session_id=target['id'], coordinator=coordinator)
    w.execution = asyncio.create_task(w.shutdown.wait())
    w.controls = RuntimeControls(w.session, w.runtime)
    w.shared_store = SharedSessionStore(w.workspace, target['id'], root=tmp_path / 'native-locks')
    w.shared_store_stamp = file_stamp
    w.activation_gate = ActivationGate()
    transport = InProcessTransport(app, source, target, w)
    w.bridge = transport.bridge
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', transport.replies.append)

    await w.controls.perform('task.create', {
        'commandId': 'original-task', 'expectedRevision': 0, 'origin': 'agent',
        'objective': 'Qualify the same candidate', 'maxTurns': 8,
        'constraints': ['Preserve the explicit user and host limits'],
        'artifactRefs': ['failed-check-1.json', 'failed-check-2.json'],
        'operationIds': ['uncertain-operation'],
    })
    coordinator.session_state['goal'].update(
        turns_used=8, continuations=7, reasons=['Failed candidate check'],
        last_reason='Failed candidate check', fixture_progress={'attempts': 2})
    seeded = await w.controls.perform('task.update', {
        'commandId': 'retained-failure', 'expectedRevision': 1, 'origin': 'agent',
        'correction': 'The first two checks failed. Their evidence is retained.',
    })
    await publish_result(app, target['id'], seeded)
    # Start parked: the tested command must acquire the REAL Foundation lock.
    w.parked = True
    w.parked_history_stamp = w.history_stamp()
    w.parked_config_stamp = configuration_stamp(w.workspace, target['id'], w.home, file_stamp)
    monkeypatch.setattr(app.management, 'ensure_runtime', transport.prepare)
    monkeypatch.setattr(app.runtime, 'control', transport.control, raising=False)
    monkeypatch.setattr(app.runtime, 'collaboration_input', transport.collaboration_input)
    h = SimpleNamespace(app=app, source=source, target=target, independent=independent,
        worker=w, controls=w.controls, transport=transport, created=created,
        initial_inputs=initial_inputs)
    try:
        yield h
    finally:
        w.shutdown.set()
        await w.execution
        for pending in tuple(w.tasks):
            pending.cancel()
        await asyncio.gather(*tuple(w.tasks), return_exceptions=True)
        if w.ownership.registration:
            await w.ownership.registration.close()
        if w.shared_handle:
            w.shared_handle.release()
        await w.controls.close()


def arguments(h, max_turns=12):
    task = h.controls.tasks.record()
    return {'sessionId': h.source['id'], 'targetSessionId': h.target['id'],
        'taskId': task['id'], 'expectedTaskRevision': task['revision'],
        'expectedAssignmentRevision': h.app.assignments.current(h.source)['revision'],
        'expectedInterruptionRevision': h.target.get('interruptionRevision', 0),
        'maxTurns': max_turns, 'reason': REASON}


async def correct(h, args=None, identity='correct-limit'):
    return await agent_action(h.app, h.source, CORRECT,
                              arguments(h) if args is None else args, identity)


async def read(h, identity='correct-limit', source=None):
    source = source or h.source
    return (await agent_action(h.app, source, READ,
        {'sessionId': source['id'], 'commandId': identity}, 'read-' + identity))['result']


async def send(h, identity):
    result = await agent_action(h.app, h.source, 'coordination.send', {
        'sessionId': h.target['id'], 'mode': 'queue',
        'text': 'Inspect the retained failure and continue the same authorized work.',
    }, identity)
    await until(lambda: h.app.collaboration.receipt(identity)['delivery'] not in {'queued', 'submitting'})
    return result, h.app.collaboration.receipt(identity)


def saved(h):
    return json.loads(h.controls.state_path().read_text())


def assert_no_input(h):
    assert h.worker.runtime.inbox.empty()
    assert not h.worker.runtime.accepted
    assert not h.worker.context_bindings
    assert h.app.runtime.inputs == h.initial_inputs


def assert_unchanged(h, before):
    assert saved(h) == before
    assert h.controls.tasks.record() == before['task']
    assert_no_input(h)


@pytest.mark.parametrize('max_turns', [None, 12])
async def test_exhausted_same_member_corrects_only_limit_then_new_input_admits(harness, max_turns):
    from amplifier_module_loop_live.runtime import Input
    h = harness
    # The unchanged native admission must first reject this real 8/8 task.
    _, rejected = await send(h, 'old-exhausted-input')
    assert rejected['delivery'] == 'suppressed'
    assert 'exhausted' in rejected['admission']['reason']
    assert h.worker.runtime.inbox.empty()
    # A refused input may retain its context binding; correction must not add one.
    bindings = copy.deepcopy(h.worker.context_bindings)
    before = saved(h)
    old_receipt = copy.deepcopy(rejected)
    messages = copy.deepcopy(h.target['messages'])
    assignment = copy.deepcopy(h.app.assignments.current(h.source))
    args = arguments(h, max_turns)
    counts = h.transport.counts()

    result = (await correct(h, args))['result']
    after = saved(h)
    task = after['task']
    assert result['state'] == 'applied'
    assert result['effectAdmission'] == {
        'assignmentRevision': args['expectedAssignmentRevision'],
        'sourceInterruptionRevision': h.source.get('interruptionRevision', 0),
        'targetInterruptionRevision': args['expectedInterruptionRevision'],
        'taskId': args['taskId'], 'taskRevision': args['expectedTaskRevision'],
    }
    assert result['inputSubmitted'] is False and result['taskResumed'] is False
    assert task['id'] == before['task']['id'] == args['taskId']
    assert task['revision'] == before['task']['revision'] + 1
    assert task['status'] == before['task']['status'] == 'active'
    assert task['maxTurns'] == after['goal']['cap'] == max_turns
    assert after['goal']['turns_used'] == task['goalState']['turns_used'] == 8
    for key in ('id', 'createdAt', 'originalObjective', 'objective', 'constraints',
                'operationIds', 'questionIds', 'artifactRefs', 'completionEvidence', 'continuationEpoch'):
        assert task[key] == before['task'][key]
    for key in ('turns_used', 'continuations', 'reasons', 'last_reason', 'fixture_progress'):
        assert after['goal'][key] == before['goal'][key]
    for key in ('capacity', 'capacityReceipts', 'capacityLastDenial', 'budget', 'taskHistory'):
        assert after[key] == before[key]
    assert task['corrections'][:-1] == before['task']['corrections']
    assert task['corrections'][-1]['text'] == REASON
    assert task['changes'][:-1] == before['task']['changes']
    assert task['changes'][-1]['fields'] == {'maxTurns': max_turns}
    assert all(after['taskReceipts'][key] == value for key, value in before['taskReceipts'].items())
    assert after['taskReceipts']['correct-limit']['receipt'] == result['receipt']
    assert h.app.assignments.current(h.source) == assignment
    assert h.target['messages'] == messages and h.target['status'] == 'idle'
    assert h.worker.runtime.inbox.empty() and not h.worker.runtime.accepted
    assert h.worker.context_bindings == bindings
    assert h.transport.counts() == (counts[0] + 1, counts[1] + 1, counts[2] + 2)
    assert h.worker.parked and h.worker.shared_handle is None
    assert h.app.collaboration.receipt('old-exhausted-input') == old_receipt

    # Exact repeated old input and correction are readback, not another attempt.
    counts = h.transport.counts()
    assert (await correct(h, args))['duplicate']
    assert (await read(h))['state'] == 'applied'
    duplicate, unchanged = await send(h, 'old-exhausted-input')
    assert duplicate['duplicate'] and unchanged == old_receipt
    assert h.transport.counts() == counts and saved(h) == after
    assert h.worker.runtime.inbox.empty()

    _, fresh = await send(h, 'new-authorized-input')
    assert fresh['delivery'] == 'accepted'
    assert fresh['admission']['accepted'] and fresh['admission']['completed'] is False
    assert fresh['taskId'] == task['id'] and fresh['taskRevision'] == task['revision']
    command = h.worker.runtime.inbox.get_nowait()[1]
    assert isinstance(command, Input) and command.kind == 'user'
    assert command.id == 'new-authorized-input' and command.activation is h.worker.activation
    assert 'Inspect the retained failure' in command.text
    assert h.source['id'] in command.text and h.target['id'] in command.text
    assert h.controls.tasks.record()['id'] == args['taskId']
    assert saved(h)['goal']['turns_used'] == 8  # Admission is not model execution.
    assert h.app.collaboration.receipt('old-exhausted-input') == old_receipt
    assert h.app.runtime.inputs == h.initial_inputs


@pytest.mark.parametrize('used,accepted', [(7, True), (8, False)])
async def test_unchanged_below_cap_positive_and_exhausted_negative(harness, used, accepted):
    h = harness
    h.controls.coordinator.session_state['goal']['turns_used'] = used
    h.controls.persist(task_only=True)
    # Preparation stamp must reflect this explicit fixture setup, not a remount.
    h.worker.parked_config_stamp = configuration_stamp(
        h.worker.workspace, h.target['id'], h.worker.home, h.worker.shared_store_stamp)
    _, receipt = await send(h, 'ordinary-input')
    assert (receipt['delivery'] == 'accepted') is accepted
    assert h.worker.runtime.inbox.empty() is not accepted
    assert h.controls.tasks.record()['maxTurns'] == 8
    assert saved(h)['goal']['turns_used'] == used
    assert not h.app.db.execute("SELECT 1 FROM commands WHERE json_extract(receipt,'$.commandAction')=?", (CORRECT,)).fetchone()


@pytest.mark.parametrize('status', ['paused', 'blocked'])
async def test_correction_never_resumes_suspended_task_or_clears_other_budgets(harness, status):
    h = harness
    result = await h.controls.perform('task.pause' if status == 'paused' else 'task.block', {
        'commandId': 'suspend', 'expectedRevision': h.controls.tasks.record()['revision'],
        'origin': 'ui', **({'reason': 'A user decision remains open'} if status == 'blocked' else {}),
    })
    h.controls.capacity.last_denial = {'budgetRevision': h.controls.capacity.policy['revision']}
    h.controls.persist(task_only=True)
    await publish_result(h.app, h.target['id'], result)
    h.worker.parked_config_stamp = configuration_stamp(
        h.worker.workspace, h.target['id'], h.worker.home, h.worker.shared_store_stamp)
    before = saved(h)
    response = (await correct(h))['result']
    after = saved(h)
    assert response['state'] == 'applied' and response['taskResumed'] is False
    assert after['task']['status'] == status and after['goal'] is None
    assert after['task']['goalState']['turns_used'] == 8
    assert after['task'].get('blockedReason') == before['task'].get('blockedReason')
    assert after['capacityLastDenial'] == before['capacityLastDenial']
    assert not await h.controls.tasks.continuation_allowed()
    assert_no_input(h)


@pytest.mark.parametrize('forgery', ['peer', 'commissioned-member', 'native-child', 'missing-principal', 'stale-generation', 'unbound-input'])
async def test_only_authenticated_current_direct_owner_can_correct(harness, forgery):
    h = harness
    before = saved(h)
    args = arguments(h)
    caller = h.source
    if forgery in {'peer', 'commissioned-member'}:
        caller = h.independent if forgery == 'peer' else h.target
        await generation(h.app, caller, 'unrelated-generation', ['unrelated-input'])
        args['sessionId'] = caller['id']
    envelope = {'action': CORRECT, 'args': args, 'id': 'forged',
        '_runtimeSessionId': caller['id'], '_generationId': caller['collaborationGeneration']['id'],
        '_inputBindings': [{'inputId': value} for value in caller['collaborationGeneration']['inputIds']]}
    if forgery == 'native-child':
        envelope['_runtimeSessionId'] = 'native-child'
    elif forgery == 'missing-principal':
        envelope.pop('_runtimeSessionId')
    elif forgery == 'stale-generation':
        envelope['_generationId'] = 'old-generation'
    elif forgery == 'unbound-input':
        envelope['_inputBindings'] = [{'inputId': 'never-admitted'}]
    with pytest.raises(AppError):
        await h.app.app_bridge('dispatch', envelope, caller['id'])
    assert h.transport.counts() == (0, 0, 0)
    assert not h.app.db.execute("SELECT 1 FROM commands WHERE id='forged'").fetchone()
    assert_unchanged(h, before)


@pytest.mark.parametrize('field,value', [
    ('expectedAssignmentRevision', 999), ('expectedTaskRevision', 999),
    ('expectedInterruptionRevision', 999), ('taskId', 'different-task'),
])
async def test_stale_exact_boundaries_refuse_before_native_effect(harness, field, value):
    h = harness
    before = saved(h)
    with pytest.raises(AppError):
        await correct(h, {**arguments(h), field: value})
    assert h.transport.counts() == (0, 0, 0)
    assert_unchanged(h, before)


@pytest.mark.parametrize('extra', [
    {'maxTurns': True}, {'maxTurns': 0}, {'maxTurns': -1},
    {'objective': 'Replace the objective'}, {'status': 'active'}, {'turns_used': 0},
])
async def test_public_schema_is_limit_only(harness, extra):
    h = harness
    before = saved(h)
    with pytest.raises((AppError, ValidationError)):
        await correct(h, {**arguments(h), **extra})
    assert h.transport.counts() == (0, 0, 0)
    assert_unchanged(h, before)


@pytest.mark.parametrize('raw', [False, True])
async def test_ordinary_cross_root_task_mutation_is_still_forbidden(harness, raw):
    h = harness
    before = saved(h)
    args = {'sessionId': h.target['id'], 'expectedRevision': before['task']['revision'], 'maxTurns': None}
    action = 'task.update'
    if raw:
        action, args = 'runtime.control', {'sessionId': h.target['id'], 'operation': action,
                                           'args': {key: value for key, value in args.items() if key != 'sessionId'}}
    with pytest.raises(AppError, match='calling conversation'):
        await agent_action(h.app, h.source, action, args, 'ordinary-cross-root')
    assert h.transport.counts() == (0, 0, 0)
    assert_unchanged(h, before)


def change_boundary(h, change):
    """Inject an externally observed state change, without replacing a guard."""
    if change in {'owner-stop', 'member-stop'}:
        row = h.source if change == 'owner-stop' else h.target
        row['interruptionRevision'] = row.get('interruptionRevision', 0) + 1
        row['status'] = 'stopped'
    elif change in {'membership', 'assignment-stop'}:
        assignment = h.app.assignments.current(h.source)
        if change == 'membership':
            assignment['members'][h.target['id']]['creatorSessionId'] = h.independent['id']
        else:
            assignment['status'] = 'stopped'
        assignment['revision'] += 1
        h.app.assignments.save(assignment)
        h.app.db.commit()
    elif change == 'member-task':
        h.target['task'] = {**h.target['task'], 'id': 'replacement-task'}
    elif change == 'owner-generation':
        h.source['collaborationGeneration']['id'] = 'new-owner-generation'
    elif change == 'member-input':
        h.app._message(h.target, 'user', 'A new user constraint supersedes the observed boundary',
                       inputOrigin='ui', inputId='new-human-constraint')
    elif change == 'member-placement':
        h.target['executionRevision'] = h.target.get('executionRevision', 0) + 1
    elif change == 'safety-stop':
        h.target['failure'] = {'category': 'content_filter'}
    elif change == 'configuration':
        h.target['configurationPending'] = True
    else:
        raise AssertionError(change)


@pytest.mark.parametrize('change', ['owner-stop', 'member-stop', 'membership', 'assignment-stop', 'safety-stop', 'configuration'])
async def test_stopped_changed_or_unsettled_members_refuse_before_admission(harness, change):
    h = harness
    args, before = arguments(h), saved(h)
    change_boundary(h, change)
    with pytest.raises(AppError):
        await correct(h, args)
    assert h.transport.counts() == (0, 0, 0)
    assert_unchanged(h, before)


@pytest.mark.parametrize('stage', ['prepare', 'transport', 'before-native-1', 'after-native-1', 'before-native-2'])
@pytest.mark.parametrize('change', ['owner-stop', 'member-stop', 'membership', 'member-task', 'owner-generation', 'member-input', 'member-placement'])
async def test_revalidate_after_preparation_and_each_native_authorization(harness, stage, change):
    h = harness
    args, before = arguments(h), saved(h)
    pause = h.transport.pause = Pause(stage)
    pending = asyncio.create_task(correct(h, args))
    try:
        await asyncio.wait_for(pause.entered.wait(), 5)
        change_boundary(h, change)
        pause.release.set()
        with pytest.raises((AppError, ValueError, NativeCommandError)):
            await asyncio.wait_for(pending, 5)
        assert h.app.collaboration.receipt('correct-limit')['state'] == 'unknown'
        assert not h.app.collaboration.receipt('correct-limit').get('effectAdmission')
        assert_unchanged(h, before)
        counts = h.transport.counts()
        # Read remains possible after the owner turn ends. It conveys no effect.
        observed = await read(h)
        assert observed['state'] == 'unknown' and observed['receipt'] is None
        assert observed['effectAdmission'] is None
        assert h.transport.counts() == counts
    finally:
        pause.release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('change', ['owner-stop', 'membership', 'owner-generation', 'member-input'])
async def test_after_commit_admission_owner_change_settles_only_the_admitted_effect(harness, change):
    h = harness
    args, before = arguments(h), saved(h)
    pause = h.transport.pause = Pause('after-native-2')
    pending = asyncio.create_task(correct(h, args))
    try:
        await asyncio.wait_for(pause.entered.wait(), 5)
        admitted = copy.deepcopy(h.app.collaboration.receipt('correct-limit')['effectAdmission'])
        assert admitted['taskId'] == args['taskId']
        assert admitted['taskRevision'] == args['expectedTaskRevision']
        assert saved(h) == before  # Host admission is not the native write.
        change_boundary(h, change)
        pause.release.set()
        result = (await asyncio.wait_for(pending, 5))['result']
        assert result['state'] == 'applied'
        assert result['effectAdmission'] == admitted
        after = saved(h)
        assert after['task']['id'] == before['task']['id']
        assert after['task']['revision'] == before['task']['revision'] + 1
        assert after['task']['maxTurns'] == 12 and after['goal']['turns_used'] == 8
        assert after['task']['corrections'][:-1] == before['task']['corrections']
        assert_no_input(h)
        if change == 'owner-stop':
            assert h.source['status'] == 'stopped'
        elif change == 'membership':
            assert h.app.assignments.current(h.source)['members'][h.target['id']]['creatorSessionId'] == h.independent['id']
        counts = h.transport.counts()
        assert (await read(h))['effectAdmission'] == admitted
        assert (await correct(h, args))['duplicate']
        assert h.transport.counts() == counts and saved(h) == after
    finally:
        pause.release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_native_commit_stage_is_single_use_before_task_acknowledgement(harness):
    h = harness
    before = saved(h)
    pause = h.transport.pause = Pause('after-native-2')
    pending = asyncio.create_task(correct(h))
    try:
        await asyncio.wait_for(pause.entered.wait(), 5)
        admitted = copy.deepcopy(h.app.collaboration.receipt('correct-limit'))
        assert admitted['effectAdmission'] and saved(h) == before
        with pytest.raises(ValueError, match='already admitted'):
            await h.app.app_bridge(ADMIT, {
                'commandId': 'correct-limit', 'stage': 'commit',
                '_runtimeSessionId': h.target['id'],
            }, h.target['id'])
        assert h.app.collaboration.receipt('correct-limit') == admitted
        assert saved(h) == before
        pause.release.set()
        assert (await asyncio.wait_for(pending, 5))['result']['state'] == 'applied'
        assert len(h.controls.tasks.record()['corrections']) == len(before['task']['corrections']) + 1
        assert_no_input(h)
    finally:
        pause.release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('stage', ['after-native-1', 'after-native-2'])
async def test_native_stop_after_host_authorization_cannot_commit(harness, stage):
    h = harness
    before = saved(h)
    pause = h.transport.pause = Pause(stage)
    args = arguments(h)
    pending = asyncio.create_task(correct(h, args))
    try:
        await asyncio.wait_for(pause.entered.wait(), 5)
        # Real Stop bypasses command_lock and advances the native Stop epoch.
        await h.worker.command({'op': 'stop', 'id': 'native-stop'})
        assert h.worker.stop_revision == 1
        pause.release.set()
        with pytest.raises(NativeCommandError):
            await asyncio.wait_for(pending, 5)
        assert saved(h) == before
        record = h.app.collaboration.receipt('correct-limit')
        assert record['state'] == 'unknown'
        assert bool(record.get('effectAdmission')) is (stage == 'after-native-2')
        counts = h.transport.counts()
        observed = await read(h)
        assert observed['state'] == 'unknown' and observed['receipt'] is None
        assert observed['effectAdmission'] == record.get('effectAdmission')
        assert (await correct(h, args))['duplicate']
        assert h.transport.counts() == counts and saved(h) == before
        queued = []
        while not h.worker.runtime.inbox.empty():
            queued.append(h.worker.runtime.inbox.get_nowait()[1])
        assert all(command.kind == 'stop' for command in queued)
        assert not h.worker.context_bindings
    finally:
        pause.release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_canonical_native_writer_contention_refuses_without_replay(harness):
    h = harness
    args, before = arguments(h), saved(h)
    other = h.worker.shared_store.acquire(app='other-native-owner')
    try:
        with pytest.raises(NativeCommandError) as error:
            await correct(h, args)
        assert error.value.reply['code'] == 'session_busy'
        assert not h.transport.callbacks
        assert_unchanged(h, before)
    finally:
        other.release()
    counts = h.transport.counts()
    assert (await read(h))['state'] == 'unknown'
    assert (await correct(h, args))['duplicate']
    assert h.transport.counts() == counts
    assert_unchanged(h, before)


async def test_correction_serializes_with_canonical_task_control_lock(harness):
    h = harness
    args = arguments(h)
    await h.controls.lock.acquire()
    pending = asyncio.create_task(correct(h, args))
    try:
        try:
            await until(lambda: h.worker.command_lock.locked() and h.worker.shared_handle is not None)
            assert not h.transport.callbacks
            assert h.controls.tasks.record()['maxTurns'] == 8
            assert not pending.done()
        finally:
            h.controls.lock.release()
        assert (await asyncio.wait_for(pending, 5))['result']['state'] == 'applied'
        assert len(h.transport.callbacks) == 2
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_native_revision_wins_over_stale_host_projection(harness):
    h = harness
    args = arguments(h)
    # Model a canonical task write whose host publication has not arrived yet.
    await h.controls.perform('task.update', {
        'commandId': 'newer-native-task', 'expectedRevision': args['expectedTaskRevision'],
        'correction': 'A concurrent native task edit already committed.',
    })
    h.worker.parked_config_stamp = configuration_stamp(
        h.worker.workspace, h.target['id'], h.worker.home, h.worker.shared_store_stamp)
    before = saved(h)
    assert h.target['task']['revision'] == args['expectedTaskRevision']
    with pytest.raises(NativeCommandError, match='Native task or Stop changed'):
        await correct(h, args)
    assert_unchanged(h, before)
    assert (await read(h))['state'] == 'unknown'


@pytest.mark.parametrize('loss', ['before-native', 'after-commit'])
async def test_unknown_and_lost_ack_readback_never_repeat_effect(harness, loss):
    h = harness
    args, before = arguments(h), saved(h)
    if loss == 'before-native':
        async def failed_prepare(session, **options):
            h.transport.preparations.append(session['id'])
            raise ConnectionError('Synthetic preparation transport disconnect')
        # Transport failure only; admission and native guards remain untouched.
        h.app.management.ensure_runtime = failed_prepare
    else:
        h.transport.lose_ack = True
    with pytest.raises(ConnectionError):
        await correct(h, args)
    record = copy.deepcopy(h.app.collaboration.receipt('correct-limit'))
    assert record['state'] == 'unknown'
    assert bool(record.get('effectAdmission')) is (loss == 'after-commit')
    counts, after = h.transport.counts(), saved(h)
    expected = 'unknown' if loss == 'before-native' else 'applied'
    assert (await read(h))['state'] == expected
    assert (await read(h))['effectAdmission'] == record.get('effectAdmission')
    duplicate = await correct(h, args)
    assert duplicate['duplicate'] and duplicate['result']['state'] == expected
    assert h.transport.counts() == counts and saved(h) == after
    # Reading proof must not rewrite the original lost transport observation.
    assert h.app.collaboration.receipt('correct-limit') == record
    assert_no_input(h)
    if loss == 'before-native':
        assert after == before
    else:
        assert after['task']['id'] == before['task']['id']
        assert after['task']['revision'] == before['task']['revision'] + 1
        assert after['goal']['turns_used'] == 8
        assert len(after['taskReceipts']) == len(before['taskReceipts']) + 1
    with pytest.raises(AppError, match='different contents'):
        await correct(h, {**args, 'maxTurns': 15})
    assert h.transport.counts() == counts


async def test_missing_canonical_receipt_is_unknown_even_after_host_success(harness):
    h = harness
    args = arguments(h)
    assert (await correct(h, args))['result']['state'] == 'applied'
    counts = h.transport.counts()
    path = h.controls.state_path()
    retained = path.with_suffix('.retained-fixture')
    path.rename(retained)
    try:
        observed = await read(h)
        assert observed['state'] == 'unknown' and observed['receipt'] is None
        assert observed['receiptObservation'] == 'unavailable'
        duplicate = await correct(h, args)
        assert duplicate['duplicate'] and duplicate['result']['state'] == 'unknown'
        assert h.transport.counts() == counts and not path.exists()
    finally:
        retained.rename(path)


async def test_failed_canonical_save_rolls_back_and_keeps_unknown_host_receipt(harness, monkeypatch):
    from amplifier_web import runtime_controls
    h = harness
    args, before = arguments(h), saved(h)
    write = runtime_controls.write_private
    def fail(path, text):
        if path == h.controls.state_path():
            raise OSError('Synthetic canonical save failure')
        return write(path, text)
    monkeypatch.setattr(runtime_controls, 'write_private', fail)
    with pytest.raises(NativeCommandError, match='canonical save failure'):
        await correct(h, args)
    assert_unchanged(h, before)
    assert h.app.collaboration.receipt('correct-limit')['state'] == 'unknown'
    admission = copy.deepcopy(h.app.collaboration.receipt('correct-limit')['effectAdmission'])
    assert admission
    monkeypatch.setattr(runtime_controls, 'write_private', write)
    counts = h.transport.counts()
    assert (await read(h))['state'] == 'unknown'
    assert (await read(h))['effectAdmission'] == admission
    assert (await correct(h, args))['duplicate']
    assert h.transport.counts() == counts
    assert_unchanged(h, before)


@pytest.mark.parametrize('forgery', ['owner', 'child', 'missing', 'extra-fields', 'missing-stage', 'invalid-stage'])
async def test_private_native_admission_requires_exact_recipient_and_payload(harness, forgery):
    h = harness
    before = saved(h)
    pause = h.transport.pause = Pause('prepare')
    pending = asyncio.create_task(correct(h))
    try:
        await asyncio.wait_for(pause.entered.wait(), 5)
        payload = {'commandId': 'correct-limit', 'stage': 'check',
                   '_runtimeSessionId': h.target['id']}
        if forgery == 'owner':
            payload['_runtimeSessionId'] = h.source['id']
        elif forgery == 'child':
            payload['_runtimeSessionId'] = 'native-child'
        elif forgery == 'missing':
            payload.pop('_runtimeSessionId')
        elif forgery == 'missing-stage':
            payload.pop('stage')
        elif forgery == 'invalid-stage':
            payload['stage'] = 'apply'
        else:
            payload['maxTurns'] = None
        with pytest.raises((AppError, ValueError)):
            await h.app.app_bridge(ADMIT, payload, h.target['id'])
        assert_unchanged(h, before)
        # Cancellation ends this intentionally held attempt, retaining unknown.
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        assert h.app.collaboration.receipt('correct-limit')['state'] == 'unknown'
        assert not h.transport.commands
    finally:
        pause.release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_peer_cannot_read_another_owners_correction_receipt(harness):
    h = harness
    await correct(h)
    await generation(h.app, h.independent, 'peer-read', ['unrelated-input'])
    counts, before = h.transport.counts(), saved(h)
    with pytest.raises(AppError, match='your exact'):
        await read(h, source=h.independent)
    assert h.transport.counts() == counts
    assert_unchanged(h, before)


@pytest.mark.parametrize('max_turns', [None, 12])
async def test_field_only_save_and_restart_retain_unrelated_controls(harness, max_turns):
    from amplifier_module_loop_live.runtime import Runtime
    from test_collaborative_workspaces import Runtime as InitialRuntime

    h = harness
    args = arguments(h, max_turns)
    baseline = saved(h)
    # Explicit fixture state deliberately differs from the mounted coordinator.
    # A reconstruction from that coordinator would erase these saved settings.
    baseline.update(
        configurator={'disabled': {'tools': ['tool-fixture'], 'hooks': ['hook-fixture']}},
        selection={'instance': 'fixture', 'model': 'retained-model', 'effort': 'high'},
        mode='fixture-safe',
        budget={'maxIterations': 23, 'contextTokens': 24000},
        capacity={**baseline['capacity'], 'maxTotalTokens': 12345, 'maxCostUsd': 4.5},
        capacityReceipts={'unknown-charge': {'status': 'unknown', 'tokens': 29}},
        capacityLastDenial={'budgetRevision': baseline['capacity']['revision'], 'reason': 'Retained denial'},
        futureControl={'nested': ['retain', {'enabled': False}]},
    )
    h.controls.state_path().write_text(json.dumps(baseline))
    other_files = {
        h.controls.state_path().with_name('configuration.json'):
            b'{"providers":[{"module":"provider-fixture","config":{"model":"retained-model"}}]}\n',
        h.controls.state_path().with_name('effective-configuration.json'):
            b'{"hooks":[{"module":"hook-fixture","enabled":false}]}\n',
    }
    for path, body in other_files.items():
        path.write_bytes(body)
    h.worker.parked_config_stamp = configuration_stamp(
        h.worker.workspace, h.target['id'], h.worker.home, h.worker.shared_store_stamp)
    assert h.controls.selection != baseline['selection']
    result = (await correct(h, args))['result']
    assert result['state'] == 'applied'
    after = saved(h)
    task_fields = {'task', 'goal', 'taskReceipts', 'taskHistory'}
    assert {key: value for key, value in after.items() if key not in task_fields} == {
        key: value for key, value in baseline.items() if key not in task_fields}
    assert after['task']['maxTurns'] == max_turns and after['goal']['turns_used'] == 8
    assert after['taskHistory'] == baseline['taskHistory']
    assert after['task']['artifactRefs'] == baseline['task']['artifactRefs']
    assert after['task']['operationIds'] == baseline['task']['operationIds']
    assert {path: path.read_bytes() for path in other_files} == other_files
    assert_no_input(h)

    class RestoredCoordinator(Coordinator):
        """Synthetic mounted modules; restore and control persistence are real."""
        def __init__(self):
            super().__init__()
            self.providers = {'fixture': SimpleNamespace()}
            self.loop.max_iterations = 2
            self.context.max_tokens = 256
            self.session_state.update(
                active_mode='fixture-safe',
                mode_discovery=SimpleNamespace(
                    find=lambda name: SimpleNamespace(name=name) if name == 'fixture-safe' else None,
                    list_modes=lambda: [('fixture-safe', 'Fixture only', 'fixture')]))
            self.restored_settings = None
            async def apply_settings(value):
                self.restored_settings = copy.deepcopy(value)
            self.capabilities['web.configurator'] = SimpleNamespace(
                apply_saved_settings=apply_settings,
                snapshot=lambda: {key: {'disabled': value} for key, value in
                    (self.restored_settings or {}).get('disabled', {}).items()})

        def get(self, name):
            return self.providers if name == 'providers' else super().get(name)

    coordinator = RestoredCoordinator()
    restarted_controls = RuntimeControls(
        SimpleNamespace(session_id=h.target['id'], coordinator=coordinator),
        Runtime(session_id=h.target['id']), preserve_configuration=True)
    persisted_bytes = h.controls.state_path().read_bytes()
    try:
        await restarted_controls.restore()
        restored = restarted_controls.tasks.record()
        assert restored['id'] == args['taskId']
        assert restored['revision'] == args['expectedTaskRevision'] + 1
        assert restored['maxTurns'] == max_turns
        assert coordinator.session_state['goal']['turns_used'] == 8
        assert restored['corrections'] == after['task']['corrections']
        assert restarted_controls.tasks.receipts == after['taskReceipts']
        assert restarted_controls.tasks.history == after['taskHistory']
        assert restarted_controls.selection == baseline['selection']
        assert coordinator.restored_settings == baseline['configurator']
        assert coordinator.session_state['active_mode'] == baseline['mode']
        assert coordinator.loop.max_iterations == 23 and coordinator.context.max_tokens == 24000
        assert restarted_controls.capacity.policy == baseline['capacity']
        assert restarted_controls.capacity.receipts == baseline['capacityReceipts']
        assert restarted_controls.capacity.last_denial == baseline['capacityLastDenial']
        assert not await restarted_controls.tasks.continuation_allowed()
        assert h.controls.state_path().read_bytes() == persisted_bytes
        assert {path: path.read_bytes() for path in other_files} == other_files
    finally:
        await restarted_controls.close()

    # Re-open the host's saved database, not the original service projections.
    # Its minimal transport never starts a process or installs a dependency.
    h.app._save()
    await h.app.close()
    transport = InitialRuntime()
    restarted_app = AppService(h.app.data_dir, transport, workspace=h.source['workspace'])
    transport.app = restarted_app
    try:
        source = restarted_app._session(h.source['id'])
        observed = (await agent_action(restarted_app, source, READ, {
            'sessionId': source['id'], 'commandId': 'correct-limit',
        }, 'read-after-host-restart'))['result']
        assert observed['state'] == 'applied' and observed['receipt'] == result['receipt']
        assert observed['effectAdmission'] == result['effectAdmission']
        assert not transport.inputs
        assert h.controls.state_path().read_bytes() == persisted_bytes
        assert {path: path.read_bytes() for path in other_files} == other_files
    finally:
        await restarted_app.close()


@pytest.mark.parametrize('outcome', ['settled', 'delayed-idle', 'ready-only', 'owner-stop', 'member-stop', 'different-guard'])
async def test_real_manager_start_scopes_only_its_own_preparation_guard(harness, monkeypatch, outcome):
    from amplifier_web.management import Management
    from amplifier_web.runtime import RuntimeManager

    h = harness
    before = saved(h)
    events, checked = [], []
    original_runtime = h.app.runtime

    class StartupLifecycle(RuntimeManager):
        """Actual start/admission; deterministic process-free startup lifecycle."""
        async def _start_locked(self, session, emit, *, preserve_emit=False,
                                guard=None, preserve_configuration=False):
            sid = session['id']
            assert self._locks[sid].locked()
            assert self._starting_guards[sid] is guard
            assert preserve_configuration is True and callable(guard)
            guard()
            await emit('runtime.status', {
                'sessionId': sid, 'status': 'starting', 'phase': 'runtime-setup',
                'detail': 'Synthetic mount preparation; no process is launched.',
            })
            events.append(h.target['status'])
            assert h.target['status'] == 'starting'
            if outcome in {'owner-stop', 'member-stop'}:
                change_boundary(h, outcome)
            if outcome == 'different-guard':
                # Same behavior is insufficient: only callback identity scopes
                # the tolerated starting state. Do not replace the tested guard.
                self._starting_guards[sid] = lambda: guard()
            guard()
            checked.append('guard-after-starting')
            await emit('runtime.status', {
                'sessionId': sid, 'status': 'ready', 'phase': 'ready',
                'detail': 'Synthetic runtime.ready observation.',
            })
            events.append(h.target['status'])
            if outcome == 'delayed-idle':
                async def idle_later():
                    await asyncio.sleep(.02)
                    await emit('runtime.status', {'sessionId': sid, 'status': 'idle', 'event': 'session.idle'})
                    events.append(h.target['status'])
                self.idle_delivery = asyncio.create_task(idle_later())
            if outcome == 'settled':
                await emit('runtime.status', {
                    'sessionId': sid, 'status': 'idle', 'event': 'session.idle',
                })
                events.append(h.target['status'])

    manager = StartupLifecycle()
    manager.home = h.app.data_dir
    # Test the real Management -> RuntimeManager.start path. Only the expensive
    # _start_locked process/mount lifecycle and control RPC transport are fixtures.
    manager.inputs = h.initial_inputs
    monkeypatch.setattr(manager, 'control', h.transport.control)
    monkeypatch.setattr(h.app, 'runtime', manager)
    monkeypatch.setattr(h.app.management, 'ensure_runtime',
                        Management.ensure_runtime.__get__(h.app.management))
    try:
        if outcome in {'settled', 'delayed-idle'}:
            result = (await correct(h))['result']
            assert result['state'] == 'applied'
            assert events == ['starting', 'ready', 'idle']
            assert checked == ['guard-after-starting']
            assert saved(h)['goal']['turns_used'] == 8
            assert h.worker.parked and h.worker.shared_handle is None
            assert_no_input(h)
        else:
            with pytest.raises((AppError, ValueError, TimeoutError)):
                await correct(h)
            assert_unchanged(h, before)
            assert not h.transport.commands
            assert h.app.collaboration.receipt('correct-limit')['state'] == 'unknown'
            assert not h.app.collaboration.receipt('correct-limit').get('effectAdmission')
            if outcome == 'ready-only':
                # Readiness without a settled idle observation is NOT an
                # authorized native effect. This is fail-closed coverage,
                # not a claim that a ready-only cold start completes recovery.
                assert events == ['starting', 'ready'] and h.target['status'] == 'ready'
                assert checked == ['guard-after-starting']
            else:
                assert events == ['starting'] and not checked
        assert manager._starting_guards == {}
        assert not manager._locks[h.target['id']].locked()
        assert not manager.workers
    finally:
        monkeypatch.setattr(h.app, 'runtime', original_runtime)
        await manager.close()


def test_work_recovery_read_classification_does_not_exempt_correction():
    from amplifier_web.workspace_work_effects import SAFE_ACTIONS, recovery_read

    assert READ in SAFE_ACTIONS and CORRECT not in SAFE_ACTIONS
    assert recovery_read('app_control', {'operation': 'dispatch', 'args': {
        'action': READ, 'args': {'sessionId': 'owner', 'commandId': 'existing'}}})
    for operation in (CORRECT, NATIVE, ADMIT):
        assert not recovery_read('app_control', {'operation': 'dispatch', 'args': {
            'action': operation, 'args': {'sessionId': 'owner'}}})
        assert not recovery_read('app_control', {'operation': 'dispatch', 'args': {
            'action': 'runtime.control', 'args': {
                'sessionId': 'owner', 'operation': operation, 'args': {}}}})


@pytest.mark.parametrize('covered', [False, True])
async def test_real_work_scope_keeps_read_domain_checks_and_refuses_correction(app, covered):
    from test_work_effects import app_effect, app_witness, check
    from test_work_invocation import prepared_work, assert_available_commission, current_work, retained_bytes

    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'limit-recovery-work')
    _, bound = await assert_available_commission(app, owner, item, 'limit-work-commission')
    required = (await current_work(app, bound))['required']
    args = {
        'sessionId': bound['id'], 'targetSessionId': owner['id'],
        'taskId': 'no-task-effect-admitted', 'expectedTaskRevision': 1,
        'expectedAssignmentRevision': app.assignments.current(bound)['revision'],
        'expectedInterruptionRevision': owner.get('interruptionRevision', 0),
        'maxTurns': 12, 'reason': REASON,
    }
    before, inputs = retained_bytes(app), copy.deepcopy(app.runtime.inputs)
    read_args = {'sessionId': bound['id'], 'commandId': 'missing-limit-receipt'}
    permission = await check(app, bound, {
        'toolName': 'app_control', 'toolCallId': 'uncovered-read',
        'toolInput': {'operation': 'dispatch', 'args': {'action': READ, 'args': read_args}},
    })
    assert permission['applicable'] and permission['allowed']
    assert permission['effectAuthorized'] is False
    # Recovery classification does not invent receipt access or conceal a 404.
    with pytest.raises(AppError) as missing:
        await app_effect(app, bound, READ, read_args, 'uncovered-read')
    assert missing.value.status == 404
    witness = app_witness(bound, required, CORRECT, args, 'work-correction') if covered else None
    with pytest.raises(AppError) as denied:
        await app_effect(app, bound, CORRECT, args, 'work-correction', witness)
    assert denied.value.code == (
        'work_effect_owner_unavailable' if covered else 'work_coverage_unavailable')
    assert retained_bytes(app) == before and app.runtime.inputs == inputs
