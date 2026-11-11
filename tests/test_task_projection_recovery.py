"""Deliberate recovery updates views, never native tasks or captured inputs."""
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web import task_continuity
from amplifier_web.collaboration_input import admit
from test_assignment_task_completion import arguments, complete
from test_assignment_task_limits import limited, args, change, human, read
from test_assignments import app, agent_action, generation
from test_task_continuity import controls, create


@pytest.mark.parametrize('newer_owner', ['session', 'runtimeControl'])
def test_observation_keeps_newer_projection_and_omitted_live_fields(newer_owner):
    old = {'id': 'task', 'revision': 1}
    live = {'supported': True, 'status': 'observed'}
    previous = {'task': old, 'history': ['retained-history'], 'continuity': live,
                'continuationSupported': True, 'goal': {'turns_used': 3}}
    session = {'id': 'one', 'task': old, 'continuity': live}
    service = SimpleNamespace(state={'runtimeControl': {'one': {'task.get': previous}}},
                              _publish_changes=Mock(), _session=lambda sid: session)
    current = {'id': 'task', 'revision': 3}
    assert task_continuity.observe_task(service, session, {'task': current}) == (True, 3)
    observed = service.state['runtimeControl']['one']['task.get']
    assert observed == {**previous, 'task': current}
    assert session['continuity'] is live
    service._publish_changes.assert_not_called()
    # Either view may already know about a newer worker response.
    if newer_owner == 'session':
        service.state['runtimeControl']['one']['task.get']['task'] = old
    else:
        session['task'] = old
    before = copy.deepcopy((session, service.state))
    publication, revision = task_continuity.publish_task_observation(service, session['id'], {'task': old})
    assert publication == {'status': 'superseded'} and revision == 3
    assert (session, service.state) == before
    service._publish_changes.assert_not_called()


@pytest.mark.parametrize('operation', ['completion', 'limit'])
@pytest.mark.parametrize('publication_fails', [False, True])
async def test_exact_manager_retry_recovers_projection_without_rpc_or_new_receipt(limited, monkeypatch,
                                                                               operation, publication_fails):
    ctx = limited
    mutate = complete if operation == 'completion' else change
    request = (arguments(ctx) if operation == 'completion'
               else args(ctx, sourceMessageId=human(ctx)['id']))
    identity = 'accepted-contribution' if operation == 'completion' else 'correct-limit'
    before = copy.deepcopy(ctx.target['task'])
    original = ctx.app.runtime.control
    async def lost(*values, **fields):
        await original(*values, **fields)
        raise TimeoutError('Lost committed task reply')
    monkeypatch.setattr(ctx.app.runtime, 'control', lost)
    with pytest.raises(TimeoutError):
        await mutate(ctx, request)
    retained = await read(ctx, commandId=identity)
    assert ctx.target['task'] == before  # Passive read remains cold.
    saved = ctx.native.state_path().read_bytes()
    original_publish = ctx.app._publish_changes
    if publication_fails:
        monkeypatch.setattr(ctx.app, '_publish_changes', Mock(side_effect=OSError('Publication interrupted')))
    recovered = await mutate(ctx, request)
    assert recovered['duplicate'] and recovered['result']['receipt'] == retained['receipt']
    assert recovered['result']['publication']['status'] == ('unavailable' if publication_fails else 'published')
    assert ctx.target['task'] == ctx.native.tasks.record()
    assert len(ctx.calls) == 1 and ctx.native.state_path().read_bytes() == saved
    assert not ctx.app.runtime.inputs
    if publication_fails:
        # An in-memory update is not proof that its publication succeeded.
        monkeypatch.setattr(ctx.app, '_publish_changes', original_publish)
        again = await mutate(ctx, request)
        assert again['result']['publication']['status'] == 'published'
        assert again['result']['receipt'] == retained['receipt'] and len(ctx.calls) == 1


async def test_old_manager_receipt_publishes_current_successor_not_old_completion(limited):
    ctx = limited
    request = arguments(ctx)
    completed = await complete(ctx, request)
    successor = (await ctx.native.perform('task.create', {
        'commandId': 'successor', 'expectedRevision': 2, 'objective': 'New work'}))['task']
    assert ctx.target['task']['status'] == 'completed'
    repeated = await complete(ctx, request)
    assert repeated['result']['receipt'] == completed['result']['receipt']
    assert ctx.target['task'] == successor and len(ctx.calls) == 1
    assert ctx.app.state['runtimeControl'][ctx.target['id']]['task.get']['history'] == ctx.native.tasks.history


@pytest.mark.parametrize('mode', ['queue', 'auto', 'steer', 'notify'])
@pytest.mark.parametrize('already_current', [False, True])
async def test_new_capture_reads_only_target_once_without_extra_publication(app, monkeypatch, mode, already_current):
    source, target = app.state['sessions']
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    native = controls(target['id'])
    try:
        await create(native)
        old = copy.deepcopy(native.tasks.record())
        await native.perform('task.update', {'commandId': 'new-objective', 'expectedRevision': 1,
                                           'objective': 'Updated objective'})
        current = copy.deepcopy(native.tasks.record())
        target['task'] = current if already_current else old
        target['status'] = 'working'  # Queue remains captured and undrained.
        if mode == 'steer':
            await generation(app, target, 'recipient', [])
            app.runtime.collaboration_steer = AsyncMock(return_value={'accepted': True})
        reads, sizes = [], []
        original_load, original_open = task_continuity._load_control, Path.open
        def tracked_load(service, session, **options):
            reads.append((session['id'], options))
            return original_load(service, session, **options)
        class TrackedRead:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *exc): return self.stream.__exit__(*exc)
            def read(self, size):
                sizes.append(size)
                return self.stream.read(size)
        def tracked_open(path, *values, **options):
            stream = original_open(path, *values, **options)
            return TrackedRead(stream) if path == native.state_path() and values == ('rb',) else stream
        monkeypatch.setattr(task_continuity, '_load_control', tracked_load)
        monkeypatch.setattr(Path, 'open', tracked_open)
        published = []
        original_publish = app._publish_changes
        def publish(**options):
            published.append(copy.deepcopy(options))
            return original_publish(**options)
        monkeypatch.setattr(app, '_publish_changes', publish)
        values = {'sessionId': target['id'], 'mode': mode, 'text': 'New deliberate input'}
        await agent_action(app, source, 'coordination.send', values, 'captured')
        receipt = copy.deepcopy(app.collaboration.receipt('captured'))
        expected = current if mode != 'notify' or already_current else old
        assert receipt['taskRevision'] == expected['revision']
        assert reads == ([(target['id'], {'current_only': True})] if mode != 'notify' else [])
        assert sizes == ([task_continuity.CONTROL_BYTES + 1] if mode != 'notify' else [])
        # Steer also publishes its actual transport outcome; queue/notify only
        # publish the committed receipt. Observation adds no separate publish.
        if mode != 'steer':
            assert len(published) == 1 and published[0]['sessions'] == {source['id'], target['id']}
        prior_reads = copy.deepcopy(reads)
        await native.perform('task.update', {'commandId': 'later', 'expectedRevision': 2,
                                           'objective': 'Changed after capture'})
        await agent_action(app, source, 'coordination.send', values, 'captured')
        await app.collaboration.drain(target['id'])
        assert reads == prior_reads
        assert app.collaboration.receipt('captured')['taskRevision'] == receipt['taskRevision']
    finally:
        await native.close()


@pytest.mark.parametrize('state', ['missing', 'corrupt', 'null'])
async def test_optional_observation_unavailable_is_not_an_empty_task_or_new_gate(app, monkeypatch, state):
    source, target = app.state['sessions']
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    if state != 'missing': path.write_text('{broken' if state == 'corrupt' else '{"task":null}')
    prior = None if state == 'null' else {'id': 'previous', 'revision': 2, 'status': 'active'}
    target.update(task=prior, status='working')
    result = await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'mode': 'queue', 'text': 'Deliberate input'}, 'optional')
    assert result['accepted'] and target['task'] == prior
    assert app.collaboration.receipt('optional')['taskId'] == (prior or {}).get('id')


async def test_changed_native_task_after_capture_still_refuses_without_rebinding(app, monkeypatch):
    source, target = app.state['sessions']
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    native = controls(target['id'])
    try:
        await create(native)
        target['status'] = 'working'
        await agent_action(app, source, 'coordination.send', {
            'sessionId': target['id'], 'mode': 'queue', 'text': 'Bound to original objective'}, 'old-binding')
        retained = copy.deepcopy(app.collaboration.receipt('old-binding'))
        await native.perform('task.update', {'commandId': 'later-objective', 'expectedRevision': 1,
                                           'objective': 'Genuinely changed after capture'})
        runtime = SimpleNamespace(submit=AsyncMock())
        outcome = await admit(native, runtime, retained, None, AsyncMock(return_value={'admitted': True}))
        assert outcome == {'accepted': False, 'reason': 'The saved task changed before native admission.'}
        runtime.submit.assert_not_awaited()
        assert app.collaboration.receipt('old-binding') == retained
        assert target['task']['revision'] == 1
    finally:
        await native.close()


@pytest.mark.parametrize('late_old_reply', [False, True])
async def test_different_task_revision_is_ordered_by_current_native_owner(app, monkeypatch, late_old_reply):
    """A retained high revision is not an ordering proof for another task ID."""
    source = app.state['sessions'][0]
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    native = controls(source['id'])
    try:
        await create(native)
        current = native.tasks.canonical_snapshot()
        old = copy.deepcopy(current['task'])
        old.update(id='retained-task', revision=73, status='completed')
        # Model the stale host view surviving native control-state relocation.
        source['task'] = old
        app.state.setdefault('runtimeControl', {})[source['id']] = {'task.get': {'task': old}}
        saved = native.state_path().read_bytes()
        publication, revision = task_continuity.publish_task_observation(app, source['id'], current)
        assert publication == {'status': 'published'} and revision == current['task']['revision']
        assert source['task'] == current['task']
        assert app.coordination.task(source['id']) == current['task']
        if late_old_reply:
            before = copy.deepcopy((source['task'], app.state['runtimeControl'][source['id']]))
            publication, revision = task_continuity.publish_task_observation(app, source['id'], {'task': old})
            assert publication == {'status': 'superseded'} and revision == current['task']['revision']
            assert (source['task'], app.state['runtimeControl'][source['id']]) == before
        assert native.state_path().read_bytes() == saved
    finally:
        await native.close()


@pytest.mark.parametrize('saved_authority', ['current', 'missing', 'corrupt'])
async def test_unrelated_high_revision_cannot_replace_current_task(app, monkeypatch, saved_authority):
    source = app.state['sessions'][0]
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    native = controls(source['id'])
    try:
        await create(native)
        current = native.tasks.canonical_snapshot()
        task_continuity.publish_task_observation(app, source['id'], current)
        before = copy.deepcopy((source['task'], app.state['runtimeControl'][source['id']]))
        if saved_authority == 'missing':
            native.state_path().unlink()
        elif saved_authority == 'corrupt':
            native.state_path().write_text('{broken')
        old = {**current['task'], 'id': 'unrelated-old-task', 'revision': 73, 'status': 'completed'}
        publication, revision = task_continuity.publish_task_observation(app, source['id'], {'task': old})
        assert publication == {'status': 'superseded'} and revision == current['task']['revision']
        assert (source['task'], app.state['runtimeControl'][source['id']]) == before
    finally:
        await native.close()


def test_same_task_progress_never_reads_control_history(tmp_path, monkeypatch):
    old = {'id': 'same-task', 'revision': 3, 'status': 'active'}
    session = {'id': 'one', 'task': old}
    service = SimpleNamespace(data_dir=tmp_path, state={'runtimeControl': {
        'one': {'task.get': {'task': old}}}})
    monkeypatch.setattr(task_continuity, '_load_control',
                        Mock(side_effect=AssertionError('ordinary task progress must not read persisted history')))
    assert task_continuity.observe_task(service, session, {'task': {**old, 'revision': 4}}) == (True, 4)
    assert task_continuity.observe_task(service, session, {'task': old}) == (False, 4)


async def test_delayed_other_task_equal_revision_reply_is_not_current(app, monkeypatch):
    source = app.state['sessions'][0]
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    native = controls(source['id'])
    try:
        await create(native)
        await native.perform('task.update', {'commandId': 'progress-B', 'expectedRevision': 1,
                                            'correction': 'Current task B'})
        current = native.tasks.canonical_snapshot()
        task_continuity.publish_task_observation(app, source['id'], current)
        old = {**copy.deepcopy(current['task']), 'id': 'old-task-A', 'status': 'completed'}
        receipt = {'commandId': 'old-task-reply', 'taskId': old['id'], 'revision': old['revision'],
                   'action': 'task.complete', 'status': 'completed'}
        saved = native.state_path().read_bytes()
        async def delayed(*args, **kwargs):
            return {'task': old, 'history': [], 'goal': None, 'receipt': copy.deepcopy(receipt)}
        monkeypatch.setattr(app.runtime, 'control', delayed, raising=False)
        monkeypatch.setattr(app, 'management', SimpleNamespace(
            ensure_runtime=AsyncMock(), close=AsyncMock(), setup_manager=None,
            global_setup_manager=None, workspace_setup_managers={},
            provider_catalog=SimpleNamespace(close=AsyncMock())))
        result = (await task_continuity.dispatch(app, 'task.complete', {
            'sessionId': source['id'], 'expectedRevision': 1, 'condition': old['objective'],
            'evidence': ['Historical result']}, 'agent', 'old-task-reply', False))['result']
        assert result['publication'] == {'status': 'superseded'}
        assert result['currentRevision'] == old['revision'] == current['task']['revision']
        assert result['snapshotIsCurrent'] is False
        assert result['task']['id'] == old['id'] and result['receipt'] == receipt
        assert source['task'] == current['task']
        assert native.state_path().read_bytes() == saved
    finally:
        await native.close()
