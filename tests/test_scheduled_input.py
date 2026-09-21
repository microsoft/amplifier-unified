"""Real loop-live queue admission for the internal worker schedule seam."""
from types import SimpleNamespace
import pytest
from test_task_continuity import controls
from amplifier_web.scheduled_input import admit

live = pytest.importorskip('amplifier_module_loop_live.runtime')


async def test_exact_task_revision_and_current_pause_checked_before_real_queue(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls('session')
    await worker.perform('task.create', {'commandId': 'task', 'expectedRevision': 0, 'objective': 'Check the report'})
    task = worker.tasks.record()
    runtime = live.Runtime(session_id='session')
    args = {'inputId': 'schedule:one:run:123', 'text': 'Check the report', 'taskId': task['id'], 'taskRevision': task['revision']}
    await worker.perform('task.update', {'commandId': 'correction', 'expectedRevision': 1, 'correction': 'Use revised source'})
    assert not (await admit(worker, runtime, args))['accepted']
    assert runtime.inbox.empty()
    args['taskRevision'] = 2
    result = await admit(worker, runtime, args)
    assert result['accepted'] and runtime.queued_inputs == 1
    command = runtime.inbox.get_nowait()[1]
    assert command.id == args['inputId'] and command.text == args['text']
    await worker.perform('task.pause', {'commandId': 'pause', 'expectedRevision': 2})
    args.update(taskRevision=3, inputId='schedule:one:run:456')
    assert not (await admit(worker, runtime, args))['accepted']
    assert runtime.inbox.empty()
    await worker.close()


@pytest.mark.parametrize('change', ['unchanged', 'correction', 'pause'])
async def test_monitor_has_finite_scope_and_restores_only_current_user_goal(tmp_path, monkeypatch, change):
    from amplifier_web.scheduled_input import finish
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls('session')
    await worker.perform('task.create', {'commandId': 'task', 'expectedRevision': 0, 'objective': 'Original objective'})
    task = worker.tasks.record()
    runtime = live.Runtime(session_id='session')
    args = {'kind': 'monitor', 'inputId': 'schedule:one:run:123', 'text': 'Check the current state once', 'taskId': task['id'], 'taskRevision': 1}
    assert (await admit(worker, runtime, args))['accepted']
    assert worker.coordinator.session_state['goal'] is None
    assert not await worker.coordinator.get_capability('live.continuation_guard')()
    if change == 'correction':
        await worker.perform('task.update', {'commandId': 'correction', 'expectedRevision': 1, 'objective': 'Corrected objective', 'correction': 'Retain my manual change'})
    if change == 'pause':
        await worker.perform('task.pause', {'commandId': 'pause', 'expectedRevision': 1})
    assert not await worker.coordinator.get_capability('live.continuation_guard')()
    finish(worker, {'type': 'input.delivered', 'input_id': 'manual-input-during-monitor', 'source': 'user'})
    event = {'type': 'generation.finished', 'input_ids': [args['inputId'], 'manual-input-during-monitor']}
    finish(worker, event)
    assert not event['scheduled_monitor_only']
    assert not await worker.coordinator.get_capability('live.continuation_guard')()
    finish(worker, {'type': 'session.idle'})
    goal = worker.coordinator.session_state['goal']
    if change == 'pause':
        assert goal is None and not await worker.coordinator.get_capability('live.continuation_guard')()
    else:
        assert goal['condition'] == ('Corrected objective' if change == 'correction' else 'Original objective')
        assert await worker.coordinator.get_capability('live.continuation_guard')()
    await worker.close()


async def test_monitor_guard_covers_delegated_followup_until_idle(tmp_path, monkeypatch):
    from amplifier_web.scheduled_input import finish
    from amplifier_web.runtime import normalize_event
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls('session')
    await worker.perform('task.create', {'commandId': 'task', 'expectedRevision': 0, 'objective': 'Monitor objective'})
    task = worker.tasks.record()
    runtime = live.Runtime(session_id='session')
    args = {'kind': 'monitor', 'inputId': 'schedule:one:run:123', 'text': 'Check once', 'taskId': task['id'], 'taskRevision': 1}
    await admit(worker, runtime, args)
    finish(worker, {'type': 'generation.finished', 'input_ids': [args['inputId']], 'active_job_ids': ['job']})
    assert not await worker.coordinator.get_capability('live.continuation_guard')()
    finish(worker, {'type': 'input.delivered', 'input_id': 'worker-report', 'source': 'amplifier-delegate'})
    event = {'type': 'generation.finished', 'input_ids': ['worker-report'], 'active_job_ids': []}
    finish(worker, event)
    payload = normalize_event(event, 'session')[1]
    assert payload['scheduled_monitor_input_id'] == args['inputId'] and payload['scheduled_monitor_only']
    assert not await worker.coordinator.get_capability('live.continuation_guard')()
    finish(worker, {'type': 'session.idle'})
    assert await worker.coordinator.get_capability('live.continuation_guard')()
    await worker.close()
