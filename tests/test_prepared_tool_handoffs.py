"""Component checks may share only a completely prepared forward tool stage."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import smart_tool_update_batch
from amplifier_web.updates import availability_check_blocked, prepared_tool_activations
from amplifier_web.smart_tools import SmartToolsManager
from test_updates import app, repo


FORWARD = {'previous': 'old-installation', 'revision': 'new-revision', 'target': 'new-installation'}


@pytest.mark.parametrize(('phase', 'extra'), [
    ('interrupted', None), ('error', None), ('unknown', None), ('activating', None), ('staging', None),
    ('staged', {'previous': 'old', 'revision': 'rev'}),  # Stage did not return an installation.
    ('staged', {'previous': 'new', 'target': 'old'}),  # Rollback owns a separate handoff.
    ('staged', {'previous': 'old', 'target': 'old', 'revision': 'rev'}),
    ('staged', {'previous': 'old', 'target': 'new', 'revision': 1}),
])
def test_incomplete_or_uncertain_tool_stage_retains_component_handoff(phase, extra):
    rows = [FORWARD, extra] if extra else [FORWARD]
    state = {'phase': phase, 'pendingSmartTools': rows}
    assert not prepared_tool_activations(state)
    manager = SimpleNamespace(closed=False, awaiting_restart=lambda: False)
    assert availability_check_blocked(manager, state, 'included')


async def test_prepared_tool_keeps_ordinary_batch_ownership(app, monkeypatch):
    manager = app.update_manager
    state = app.state['updates']
    await manager.publish(phase='staged', pendingSmartTools=[FORWARD.copy()])
    assert prepared_tool_activations(state)
    assert not availability_check_blocked(manager, state, 'included')
    assert not manager.independent_tool_results(state)
    manager.inventory = [{'kind': 'smart tool', 'status': 'update', 'eligible': True,
                          'installationId': 'other-tool', 'latest': 'other-revision'}]
    app.smart_tools = SmartToolsManager(app)
    stage = AsyncMock()
    monkeypatch.setattr(app.smart_tools, 'stage_update', stage)
    monkeypatch.setattr(manager, 'busy', lambda: True)
    await smart_tool_update_batch.stage(manager)
    assert state['pendingSmartTools'] == [FORWARD]
    stage.assert_not_awaited()
