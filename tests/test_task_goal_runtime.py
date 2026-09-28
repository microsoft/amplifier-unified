"""Qualify app task controls against the optional real live/streaming runtime.

Run in the qualified worker environment. Providers are synthetic; no bundle
downloads, credentials, persisted user history, or model requests are needed.
The two plans cover Work's live loop and Anchors' streaming-to-live overlay,
not full preparation of every tool included by those bundles.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

live = pytest.importorskip('amplifier_module_loop_live.orchestrator')
streaming = pytest.importorskip('amplifier_module_loop_streaming')

from amplifier_web.host.session import live_plan
from amplifier_web.runtime_controls import RuntimeControls
from test_task_continuity import Coordinator


def runtime_controls(source_loop):
    plan, _ = live_plan({'session': {'orchestrator': {'module': source_loop}}})
    loop = live.BundleLiveOrchestrator({**plan['session']['orchestrator']['config'], 'goal_stall_threshold': 100})
    coordinator = Coordinator()
    coordinator.loop = loop
    coordinator.cancellation = SimpleNamespace(is_cancelled=False)
    controls = RuntimeControls(SimpleNamespace(session_id='goal-fixture', coordinator=coordinator),
                               SimpleNamespace(generation='running', queued_inputs=0))
    loop._summarize_goal_run = AsyncMock(return_value='Fixture summary')
    hooks = SimpleNamespace(emit=AsyncMock())
    return controls, loop, hooks


async def create_task(controls):
    await controls.perform('task.create', {'commandId': 'create', 'expectedRevision': 0, 'objective': 'Verify fixture evidence'})
    await controls.tasks.boundary('provider:request', {'session_id': 'goal-fixture'})


@pytest.mark.parametrize('source_loop', ['loop-live', 'loop-streaming'], ids=['work-loop', 'anchors-overlay'])
@pytest.mark.parametrize('timing', ['before-execution', 'during-execution'])
async def test_task_goal_creation_uses_shared_runtime_defaults(tmp_path, monkeypatch, source_loop, timing):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, loop, hooks = runtime_controls(source_loop)
    captured = []
    async def answer(*args, **kwargs):
        if timing == 'during-execution':
            await create_task(controls)
        captured.append(controls.coordinator.session_state['goal'])
        return 'Verified answer'
    monkeypatch.setattr(streaming.StreamingOrchestrator, '_execute_one_turn', answer)
    loop._evaluate_goal = AsyncMock(return_value=(True, 'Fixture evidence verified'))
    try:
        if timing == 'before-execution':
            await create_task(controls)
        assert await loop._execute_guarded_goal('Explicit input', None, {}, {}, hooks, controls.coordinator) == 'Verified answer'
        assert loop._evaluate_goal.await_count == 1
        assert 'progress_evidence' in captured[0]
        assert controls.coordinator.session_state['goal'] is None
        # Goal evaluation does not forge explicit app task-completion evidence.
        assert controls.tasks.record()['status'] == 'active'
    finally:
        await controls.close()


@pytest.mark.parametrize('source_loop', ['loop-live', 'loop-streaming'], ids=['work-loop', 'anchors-overlay'])
async def test_app_pause_during_goal_evaluation_cannot_become_achievement(tmp_path, monkeypatch, source_loop):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, loop, hooks = runtime_controls(source_loop)
    answer = AsyncMock(return_value='First answer')
    monkeypatch.setattr(streaming.StreamingOrchestrator, '_execute_one_turn', answer)
    async def evaluate(*args):
        await controls.perform('task.pause', {'commandId': 'pause', 'expectedRevision': 1})
        assert not await controls.tasks.continuation_allowed()
        return True, 'Stale evaluator success'
    loop._evaluate_goal = evaluate
    try:
        await create_task(controls)
        assert await loop._execute_guarded_goal('Explicit input', None, {}, {}, hooks, controls.coordinator) == 'First answer'
        assert answer.await_count == 1
        assert controls.tasks.record()['status'] == 'paused'
        assert controls.coordinator.session_state['goal'] is None
        assert not any(call.args[0] == 'orchestrator:goal_progress' and call.args[1].get('state') == 'achieved'
                       for call in hooks.emit.await_args_list)
    finally:
        await controls.close()


@pytest.mark.parametrize('source_loop', ['loop-live', 'loop-streaming'], ids=['work-loop', 'anchors-overlay'])
async def test_revised_task_survives_old_goal_evaluation_without_replay(tmp_path, monkeypatch, source_loop):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, loop, hooks = runtime_controls(source_loop)
    answer = AsyncMock(return_value='Answer to original input')
    monkeypatch.setattr(streaming.StreamingOrchestrator, '_execute_one_turn', answer)
    async def evaluate(*args):
        await controls.perform('task.update', {'commandId': 'revision', 'expectedRevision': 1,
                                               'objective': 'Verify the corrected fixture'})
        await controls.tasks.boundary('provider:request', {'session_id': 'goal-fixture'})
        return True, 'Old objective appeared satisfied'
    loop._evaluate_goal = AsyncMock(side_effect=evaluate)
    try:
        await create_task(controls)
        assert await loop._execute_guarded_goal('Explicit input', None, {}, {}, hooks, controls.coordinator) == 'Answer to original input'
        goal = controls.coordinator.session_state['goal']
        assert goal['condition'] == 'Verify the corrected fixture'
        assert goal['task_revision'] == 2
        assert 'Old objective appeared satisfied' not in goal['reasons']
        assert controls.tasks.record()['status'] == 'active'
        assert loop._evaluate_goal.await_count == answer.await_count == 1
        assert not any(call.args[0] == 'orchestrator:goal_progress' and call.args[1].get('state') == 'achieved'
                       for call in hooks.emit.await_args_list)
    finally:
        await controls.close()
