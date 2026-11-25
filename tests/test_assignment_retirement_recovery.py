"""Lost RPC acknowledgement with real task persistence and native lease fencing.

The transport/coordinator are synthetic; Core/live-loop coverage is maintained
separately in test_assignment_readmission_native.py. No provider or process claim.
"""
import copy
import asyncio
import json
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_operations.coordination import fingerprint
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError, AppService
from amplifier_web.task_continuity import CONTROL_BYTES
from test_assignments import app, agent_action
from test_assignment_task_limits import limited
from test_assignment_task_completion import arguments, complete
from test_assignment_retirement import (adoption_args, full_access, reviewed_args,
                                       retire, save_task, settle, setup)
from test_collaborative_workspaces import Runtime


@pytest.fixture
async def member(limited):
    ctx = limited
    await settle(ctx.app, ctx.target)
    manager = RuntimeManager()
    ctx.app.runtime.stopped_boundary = manager.stopped_boundary
    try:
        yield ctx
    finally:
        await manager.close()


@asynccontextmanager
async def reopened(ctx, restart):
    if not restart:
        yield ctx.app, ctx.source, ctx.target
        return
    original = ctx.app
    original.management = None  # Restore the transport fixture's original owner.
    await original.close()
    runtime = Runtime()
    runtime.stopped_boundary = original.runtime.stopped_boundary
    restored = runtime.app = AppService(original.data_dir, runtime, workspace=ctx.source['workspace'])
    try:
        yield restored, restored._session(ctx.source['id']), restored._session(ctx.target['id'])
    finally:
        await restored.close()


def lose_reply(ctx):
    original = ctx.app.runtime.control

    async def lost(sid, operation, values, *, guard=None):
        await original(sid, operation, values, guard=guard)
        raise TimeoutError('Lost after canonical task commit')

    ctx.app.runtime.control = lost


async def retire_ui(app, source, target):
    return await app.dispatch('assignment.retire', {
        'sessionId': source['id'], 'targetSessionId': target['id'],
        'expectedRevision': app.assignments.current(source)['revision']},
        command_id='retire-after-recovery', origin='ui')


@pytest.mark.parametrize('restart', [False, True])
@pytest.mark.parametrize('retry', [False, True])
async def test_lost_completion_retry_publishes_but_retirement_does_not_hydrate(member, retry, restart):
    ctx = member
    request = arguments(ctx)
    stale = copy.deepcopy(ctx.target['task'])
    lose_reply(ctx)
    with pytest.raises(TimeoutError):
        await complete(ctx, request)
    if retry:
        assert (await complete(ctx, request))['duplicate']
    expected_projection = ctx.native.tasks.record() if retry else stale
    assert len(ctx.calls) == 1 and ctx.target['task'] == expected_projection
    ctx.app._save()
    saved = ctx.native.state_path().read_bytes()
    task = json.loads(saved)['task']
    assert task['status'] == 'completed'
    async with reopened(ctx, restart) as (app, source, target):
        before = copy.deepcopy(target)
        receipt = await retire_ui(app, source, target)
        assert receipt['reuseBinding']['targetTask'] == fingerprint(task)
        assert receipt['result']['inputSent'] is False
        assert app.assignments.current(source)['members'][target['id']]['status'] == 'retired'
        assert target['task'] == expected_projection
        for key in ('messages', 'lastInterruption', 'interruptionRevision'):
            assert target.get(key) == before.get(key)
        assert not app.runtime.inputs
    assert ctx.native.state_path().read_bytes() == saved and len(ctx.calls) == 1


@pytest.mark.parametrize('restart', [False, True])
async def test_lost_successor_can_retire_but_cannot_authorize_standing_reuse(member, restart):
    ctx = member
    await complete(ctx)
    stale = copy.deepcopy(ctx.target['task'])
    ctx.app._save()
    lose_reply(ctx)
    with pytest.raises(TimeoutError):
        await ctx.app.dispatch('task.create', {
            'sessionId': ctx.target['id'], 'expectedRevision': stale['revision'],
            'objective': 'Unfinished successor'}, command_id='successor', origin='ui')
    saved = ctx.native.state_path().read_bytes()
    assert json.loads(saved)['task']['status'] == 'active'
    async with reopened(ctx, restart) as (app, source, target):
        receipt = await retire_ui(app, source, target)
        assert receipt['reuseBinding'] is None
        assert app.assignments.current(target) is None
        assert target['task'] == stale and not app.runtime.inputs
        from amplifier_web.assignment_adoption import reusable_retirement
        assert not reusable_retirement(app, source, target)
    assert ctx.native.state_path().read_bytes() == saved


@pytest.mark.parametrize('broken', ['missing', 'json', 'task-field', 'task-shape'])
async def test_unavailable_saved_authority_does_not_block_retirement_or_use_cached_completion(app, broken):
    source, target, _ = await setup(app)
    target['task'] = await save_task(app, target, 'completed')
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    if broken == 'missing':
        path.unlink()
    else:
        path.write_text({'json': '{', 'task-field': '{}', 'task-shape': '{"task":{}}'}[broken])
    before = None if broken == 'missing' else path.read_bytes()
    receipt = await retire(app, source, target)
    assert receipt['reuseBinding'] is None
    assert app.assignments.current(target) is None
    assert target['task']['status'] == 'completed'
    assert (None if not path.exists() else path.read_bytes()) == before


async def test_unfinished_task_is_not_read_before_idle_worker_retirement(app, monkeypatch):
    source, target, _ = await setup(app)
    await save_task(app, target, 'active')
    from amplifier_web import assignment_adoption
    from amplifier_web import assignment_retirement
    original = assignment_retirement._load_control
    settled = []
    async def acknowledge(*args, **kwargs):
        settled.append(True)
    def read_after_settled(*args, **kwargs):
        assert settled == [True]
        return original(*args, **kwargs)
    monkeypatch.setattr(assignment_adoption, 'retire_idle', acknowledge)
    monkeypatch.setattr(assignment_retirement, '_load_control', read_after_settled)
    assert (await retire(app, source, target))['reuseBinding'] is None


async def test_unfinished_task_warm_retirement_preserves_task_stop_and_input(app, monkeypatch):
    """Real retirement/admission locks and native lease; synthetic transport."""
    from test_assignment_adoption import warm_idle
    from amplifier_web.assignment_adoption import reusable_retirement

    source, target, created = await setup(app)
    target['task'] = await save_task(app, target, 'active')
    target.update(interruptionRevision=3,
                  lastInterruption={'at': 1, 'kind': 'user_stop'})
    task_path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    saved = task_path.read_bytes()
    manager = await warm_idle(app, target, monkeypatch)
    before = copy.deepcopy(target)
    inputs = copy.deepcopy(app.runtime.inputs)
    original_receipt = copy.deepcopy(app.collaboration.receipt(created['initialInputId']))
    try:
        receipt = await retire(app, source, target, 'warm-unfinished-retire')
        assert receipt['reuseBinding'] is None
        assert receipt['result']['inputSent'] is False
        assert app.assignments.current(source)['members'][target['id']]['status'] == 'retired'
        assert app.assignments.current(target) is None
        assert not reusable_retirement(app, source, target)
        manager._request_unlocked.assert_awaited_once_with(target['id'], 'retire')
        manager.stop.assert_awaited_once_with(target['id'], reason='retention')
        assert target['id'] not in manager.workers
        assert task_path.read_bytes() == saved
        for key in ('task', 'status', 'messages', 'interruptionRevision', 'lastInterruption'):
            assert target[key] == before[key]
        assert app.runtime.inputs == inputs
        assert app.collaboration.receipt(created['initialInputId']) == original_receipt
    finally:
        await manager.close()


@pytest.mark.parametrize('broken', ['missing', 'json'])
async def test_unavailable_task_cannot_reuse_full_retirement_from_cached_completion(app, broken):
    source, target, _ = await setup(app)
    await full_access(app, source)
    target['task'] = await save_task(app, target, 'completed')
    await retire(app, source, target)
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    if broken == 'missing':
        path.unlink()
    else:
        path.write_text('{')
    before = copy.deepcopy(app.assignments.current(source))
    with pytest.raises(AppError, match='human instruction'):
        await agent_action(app, source, 'assignment.adopt',
                           await reviewed_args(app, source, target), 'unknown-full-readmit')
    assert app.assignments.current(source) == before


@pytest.mark.parametrize('action', ['retire', 'readmit'])
async def test_task_change_at_lease_acquisition_is_rechecked(app, action):
    source, target, _ = await setup(app)
    await full_access(app, source)
    if action == 'readmit':
        await retire(app, source, target)
        args = await reviewed_args(app, source, target)
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    prior = path.read_bytes()
    await save_task(app, target, 'active')
    successor = path.read_bytes()
    path.write_bytes(prior)
    original = app.runtime.stopped_boundary
    entered = []
    @contextmanager
    def race(session):
        # A preceding writer commits just before the real acquisition succeeds.
        path.write_bytes(successor)
        with original(session) as held:
            entered.append(held)
            yield held
    app.runtime.stopped_boundary = race
    before = copy.deepcopy(app.assignments.current(source))
    if action == 'retire':
        receipt = await retire(app, source, target)
        assert receipt['reuseBinding'] is None
        assert app.assignments.current(target) is None
    else:
        with pytest.raises(AppError):
            await agent_action(app, source, 'assignment.adopt', args, 'raced-readmit')
        assert app.assignments.current(source) == before
    assert len(entered) == 1
    assert path.read_bytes() == successor


@pytest.mark.parametrize('origin', ['ui', 'human'])
async def test_unknown_task_preserves_ordinary_adoption_path(app, origin):
    source, target, _ = await setup(app)
    await full_access(app, source)
    await retire(app, source, target)
    (app.data_dir / 'sessions' / target['id'] / 'control-state.json').unlink()
    if origin == 'human':
        args = await adoption_args(app, source, target)
        receipt = await agent_action(app, source, 'assignment.adopt', args, 'human-readmit')
    else:
        receipt = await app.dispatch('assignment.adopt', await reviewed_args(app, source, target),
                                     command_id='ui-readmit', origin='ui')
    assert receipt['accepted'] and 'readmissionAuthority' not in receipt
    assert not receipt['result']['inputSent']


async def test_available_null_task_is_read_only_bounded_and_reused_inside_lease(app, monkeypatch, record_property):
    source, target, _ = await setup(app)
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    # History is irrelevant to current eligibility; the existing reader still
    # parses the bounded whole file, but does not validate historical tasks.
    saved = json.loads(path.read_text())
    saved['taskHistory'] = 'Not current task authority'
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    original = Path.open
    reads = []
    class Observed:
        def __enter__(self):
            self.stream = original(path, 'rb')
            return self
        def read(self, size):
            raw = self.stream.read(size)
            reads.append((size, len(raw)))
            return raw
        def __exit__(self, *args):
            self.stream.close()
    def observe(current, *args, **kwargs):
        return Observed() if current == path and args == ('rb',) else original(current, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', observe)
    receipt = await retire(app, source, target)
    assert reads == [(CONTROL_BYTES + 1, len(before))]
    assert receipt['reuseBinding']['targetTask'] == fingerprint({})
    record_property('current_task_reads', len(reads))
    record_property('current_task_bytes', sum(size for _, size in reads))
    with original(path, 'rb') as stream:
        assert stream.read() == before


@pytest.mark.parametrize('status', ['active', 'blocked', 'paused'])
async def test_retirement_preserves_unfinished_native_task_and_forbids_standing_readmission(app, status):
    source, target, created = await setup(app)
    await full_access(app, source)
    target['task'] = await save_task(app, target, status)
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    saved = path.read_bytes()
    before = copy.deepcopy(target)
    old = copy.deepcopy(app.collaboration.receipt(created['initialInputId']))
    inputs = len(app.runtime.inputs)
    receipt = await retire(app, source, target)
    assert receipt['reuseBinding'] is None
    assert app.assignments.current(target) is None
    assert target == {key: value for key, value in before.items() if key != 'assignmentId'}
    with pytest.raises(AppError, match='human instruction'):
        await agent_action(app, source, 'assignment.adopt',
                           await reviewed_args(app, source, target), 'unfinished-readmit')
    assert app.collaboration.receipt(created['initialInputId']) == old
    assert 'retired' in app.collaboration.guard(old)
    assert len(app.runtime.inputs) == inputs
    assert path.read_bytes() == saved


@pytest.mark.parametrize('case,status', [
    ('appearance-preview', 'blocked'), ('appearance-client', 'blocked'),
    ('appearance-server', 'blocked'), ('appearance-review', 'active'),
])
async def test_archived_stopped_failed_settings_members_retire_without_repair(app, case, status):
    """The four historical shapes, not an assertion about those live chats."""
    source, target, created = await setup(app)
    target['task'] = await save_task(app, target, status)
    target.update(status='stopped', interruptionRevision=1,
        lastInterruption={'at': 1, 'kind': 'user_stop'},
        configurationBusy=True, configurationPending=False,
        pendingConfiguration={'phase': 'error', 'effect': 'saved_not_mounted',
            'stage': 'readback_mismatch', 'operationId': 'old-write-policy-' + case,
            'error': 'Saved and loaded write policies did not match', 'recoveryRequired': True},
        configurationOperationFence='old-write-policy-' + case)
    target['workers'].append({'id': 'historical-child', 'status': 'stopped'})
    app.state['conversationOrganization'].setdefault('archived', {})[target['id']] = 1
    # These are inert failure evidence, not a valid plan to mount or replay.
    directory = app.data_dir / 'sessions' / target['id']
    config = directory / 'queued-configuration.json'
    config.write_text('{"historical-failed-plan":"preserve exact bytes"}')
    native = directory / 'control-state.json'
    saved, settings = native.read_bytes(), config.read_bytes()
    old = copy.deepcopy(app.collaboration.receipt(created['initialInputId']))
    before = copy.deepcopy(target)
    inputs = len(app.runtime.inputs)
    receipt = await retire(app, source, target)
    assert receipt['accepted'] and not receipt['result']['inputSent']
    assert receipt['reuseBinding'] is None
    assert app.assignments.current(target) is None
    assert app.assignments.active_member_count(app.assignments.current(source)) == 1
    assert target == {key: value for key, value in before.items() if key != 'assignmentId'}
    assert app.state['conversationOrganization']['archived'][target['id']] == 1
    assert native.read_bytes() == saved and config.read_bytes() == settings
    assert app.collaboration.receipt(created['initialInputId']) == old
    assert 'retired' in app.collaboration.guard(old)
    assert len(app.runtime.inputs) == inputs


@pytest.mark.parametrize('busy', [
    'queued', 'applying', 'refresh-queued', 'refresh-applying', 'task', 'cancelling-task', 'lock',
])
async def test_failed_settings_history_does_not_hide_active_settings_work(app, busy):
    source, target, _ = await setup(app)
    target.update(configurationBusy=True,
        pendingConfiguration={'phase': 'error', 'effect': 'unknown', 'recoveryRequired': True})
    task = asyncio.get_running_loop().create_future()
    lock = asyncio.Lock()
    app.management = SimpleNamespace(pending_config_tasks={}, lock=lock)
    if busy in {'queued', 'applying'}:
        target['pendingConfiguration']['phase'] = busy
    elif busy.startswith('refresh-'):
        target['configurationRefresh'] = {'phase': busy.removeprefix('refresh-')}
    elif busy in {'task', 'cancelling-task'}:
        if busy == 'cancelling-task':
            task = asyncio.create_task(asyncio.sleep(60))
            task.cancel()  # A cancellation request is not completion.
        app.management.pending_config_tasks[target['id']] = task
    else:
        await lock.acquire()
    before = copy.deepcopy(target)
    try:
        with pytest.raises(AppError, match='settings change'):
            await retire(app, source, target)
        assert target == before and app.assignments.current(target)
        assert not app.db.execute("SELECT 1 FROM commands WHERE id='retire'").fetchone()
    finally:
        if lock.locked():
            lock.release()
        task.cancel()
        if isinstance(task, asyncio.Task):
            await asyncio.gather(task, return_exceptions=True)
        app.management = None


@pytest.mark.parametrize('change', ['settings', 'input', 'call', 'handoff'])
async def test_retirement_rechecks_effects_after_wait(app, monkeypatch, change):
    from amplifier_web import assignment_adoption
    from amplifier_web.execution import ingest
    from test_assignments import call
    source, target, _ = await setup(app)
    target['task'] = await save_task(app, target, 'blocked')
    before = copy.deepcopy(app.assignments.current(source))
    async def changed(*args, **kwargs):
        if change == 'settings':
            target['pendingConfiguration'] = {'phase': 'applying'}
        elif change == 'input':
            app.collaboration.insert('raced-input', 'fixture', {
                'commandAction': 'coordination.send', 'target': {'sessionId': target['id']},
                'delivery': 'unknown'})
        elif change == 'call':
            ingest(target, {**call(target, 'raced-call'), 'phase': 'outcome_unknown'})
        else:
            target['worktreeHandoffs'] = [{'phase': 'unknown'}]
    monkeypatch.setattr(assignment_adoption, 'retire_idle', changed)
    with pytest.raises(AppError):
        await retire(app, source, target)
    assert app.assignments.current(source) == before
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='retire'").fetchone()


@pytest.mark.parametrize('busy', ['process', 'bridge', 'stop', 'preparation'])
async def test_stopped_history_still_requires_actual_native_writer_absence(app, busy):
    source, target, _ = await setup(app)
    target.update(status='stopped', interruptionRevision=1, configurationBusy=True,
        pendingConfiguration={'phase': 'error', 'effect': 'saved_not_mounted'})
    manager = RuntimeManager()
    pending = asyncio.get_running_loop().create_future()
    row = {'process': SimpleNamespace(returncode=0), 'executionEnded': True}
    if busy == 'process':
        row['process'].returncode = None
    elif busy == 'bridge':
        row['bridge_tasks'] = [pending]
    elif busy == 'stop':
        row['stop_task'] = pending
    else:
        manager._preparations[target['id']] = pending
    manager.workers[target['id']] = row
    # retire_idle sees no transport fixture worker; the real final lease must
    # still refuse this separately observed native writer/pending operation.
    app.runtime.stopped_boundary = manager.stopped_boundary
    before = copy.deepcopy(target)
    try:
        with pytest.raises(AppError, match='stopped'):
            await retire(app, source, target)
        assert target == before and app.assignments.current(target)
    finally:
        pending.cancel()
        manager.workers.clear()
        manager._preparations.clear()
        await manager.close()
