"""Malformed public tool envelopes fail before any host action or context access."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.app_guidance import install_app_access


async def mounted_tool(bridge):
    capabilities, tools = {}, {}

    async def mount(kind, tool, name):
        tools[name] = tool

    coordinator = SimpleNamespace(
        get=lambda kind: tools if kind == 'tools' else None,
        get_capability=capabilities.get, register_capability=capabilities.__setitem__,
        mount=mount, hooks=SimpleNamespace(register=lambda *args, **kwargs: None))
    await install_app_access(coordinator, bridge)
    return tools['app_control']


@pytest.mark.parametrize('payload,path,correction', [
    (None, '', 'must be an object'),
    ({}, '/operation', 'operation must be one of'),
    ({'operation': []}, '/operation', 'operation must be one of'),
    ({'operation': 'unknown'}, '/operation', 'operation must be one of'),
    ({'operation': 'dispatch', 'parameters': {'action': 'session.rename'}}, '/parameters', 'top-level args, not parameters'),
    ({'operation': 'dispatch', 'action': 'session.rename'}, '', 'Only operation and args'),
    ({'operation': 'dispatch'}, '/args', 'requires a top-level args object'),
    ({'operation': 'dispatch', 'args': {}}, '/args/action', 'nonempty action string'),
    ({'operation': 'dispatch', 'args': {'action': None}}, '/args/action', 'nonempty action string'),
    ({'operation': 'dispatch', 'args': {'action': 3}}, '/args/action', 'nonempty action string'),
    ({'operation': 'dispatch', 'args': {'action': ' '}}, '/args/action', 'nonempty action string'),
    ({'operation': 'dispatch', 'args': {'action': 'session.rename', 'parameters': {'title': 'Lost'}}}, '/args/parameters', 'args.args, not args.parameters'),
])
async def test_malformed_envelope_never_reaches_bridge(payload, path, correction):
    bridge = AsyncMock()
    tool = await mounted_tool(bridge)
    before = deepcopy(payload)
    result = await tool.execute(payload)
    assert not result.success
    assert result.error['code'] == 'invalid_app_control_input'
    assert result.error['path'] == path
    assert result.error['effect'] == 'none'
    assert correction in result.error['message']
    assert '{"operation":"dispatch","args":{"action":"ACTION_NAME","args":{}}}' in result.error['message']
    assert payload == before
    bridge.assert_not_awaited()


@pytest.mark.parametrize("exc", [TimeoutError(), ConnectionError(), RuntimeError(), ValueError("  ")])
async def test_empty_errors_are_actionable_without_claiming_no_effect(exc):
    bridge = AsyncMock(side_effect=exc)
    tool = await mounted_tool(bridge)
    result = await tool.execute({"operation": "dispatch", "args": {
        "action": "session.rename", "id": "stable-command", "args": {"title": "Private value"}}})
    assert not result.success and result.error["message"].strip()
    assert "Check saved state" in result.error["message"]
    assert "do not automatically replay" in result.error["message"]
    assert result.error.get("effect") != "none"
    assert "Private value" not in str(result.error)
    bridge.assert_awaited_once()


async def test_nonempty_error_text_is_preserved():
    tool = await mounted_tool(AsyncMock(side_effect=ValueError("Exact original diagnostic")))
    result = await tool.execute({"operation": "get_state"})
    assert result.error == {"message": "Exact original diagnostic"}


@pytest.mark.parametrize("exc", [TimeoutError(), RuntimeError(), ValueError("Original error")])
async def test_image_selection_error_fields_survive_diagnostic_fallback(exc):
    tool = await mounted_tool(AsyncMock(side_effect=exc))
    result = await tool.execute({"operation": "dispatch", "args": {"action": "outputs.images", "args": {}}})
    assert not result.success
    assert result.error["imageSelectionUnchanged"] is True
    assert "activeImages" in result.error
    assert result.error["message"].endswith("The current image selection is unchanged.")
    assert result.error["message"].split(" The current")[0].strip()


@pytest.mark.parametrize('value', [None, [], 'text', 1, False])
@pytest.mark.parametrize('operation', ['dispatch', 'get_state', 'context.read', 'context.focus'])
async def test_wrong_type_operation_args_never_reaches_bridge(value, operation):
    bridge = AsyncMock()
    tool = await mounted_tool(bridge)
    result = await tool.execute({'operation': operation, 'args': value})
    assert not result.success and result.error['path'] == '/args'
    assert result.error['effect'] == 'none'
    bridge.assert_not_awaited()


@pytest.mark.parametrize('value', [None, [], 'text', 1, False])
async def test_wrong_type_action_args_never_reaches_bridge(value):
    bridge = AsyncMock()
    tool = await mounted_tool(bridge)
    result = await tool.execute({'operation': 'dispatch', 'args': {'action': 'session.rename', 'args': value}})
    assert not result.success and result.error['path'] == '/args/args'
    assert result.error['effect'] == 'none'
    bridge.assert_not_awaited()


@pytest.mark.parametrize('payload,expected_args', [
    ({'operation': 'dispatch', 'args': {'action': 'session.rename', 'args': {'id': 'chat', 'title': 'New'}, 'id': 'stable-command', 'expectedRevision': 12}},
     {'action': 'session.rename', 'args': {'id': 'chat', 'title': 'New'}, 'id': 'stable-command', 'expectedRevision': 12}),
    ({'operation': 'dispatch', 'args': {'action': 'canvas.close'}}, {'action': 'canvas.close'}),
    ({'operation': 'get_state'}, {}),
    ({'operation': 'get_state', 'args': {'path': '/sessions/17/title', 'revision': 12, 'directoryRevision': 'observed-directory'}},
     {'path': '/sessions/17/title', 'revision': 12, 'directoryRevision': 'observed-directory'}),
    ({'operation': 'list_actions', 'args': {'prefix': 'session.'}}, {'prefix': 'session.'}),
])
async def test_valid_requests_preserve_exact_model_args(payload, expected_args):
    receipt = {'ok': True, 'revision': 13, 'commandId': 'stable-command'}
    bridge = AsyncMock(return_value=receipt)
    tool = await mounted_tool(bridge)
    before = deepcopy(payload)
    result = await tool.execute(payload)
    assert result.success and result.output == receipt
    assert payload == before
    bridge.assert_awaited_once_with(payload['operation'], expected_args)


async def test_wrong_envelope_cannot_mutate_real_app_then_corrected_request_can(tmp_path):
    from amplifier_web.service import AppService

    runtime = SimpleNamespace(close=AsyncMock())
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session_id = app._session()['id']
        async def real_bridge(operation, args):
            return await app.app_bridge(operation, args, session_id)

        bridge = AsyncMock(side_effect=real_bridge)
        tool = await mounted_tool(bridge)
        title, revision = app._session()['title'], app.state['revision']
        action = {'action': 'session.rename', 'args': {'id': session_id, 'title': 'Renamed'}}
        result = await tool.execute({'operation': 'dispatch', 'parameters': action, 'args': action})
        assert not result.success  # Even a valid args sibling cannot rescue a malformed envelope.
        bridge.assert_not_awaited()
        assert app._session()['title'] == title and app.state['revision'] == revision
        result = await tool.execute({'operation': 'dispatch', 'args': action})
        assert result.success
        assert app._session()['title'] == 'Renamed'
        bridge.assert_awaited_once_with('dispatch', action)
    finally:
        await app.close()


async def test_reported_theme_envelope_has_executable_read_only_recovery(tmp_path):
    from amplifier_web.service import AppService

    app = AppService(tmp_path, SimpleNamespace(close=AsyncMock()), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session_id = app._session()['id']
        async def real_bridge(operation, args):
            return await app.app_bridge(operation, args, session_id)
        bridge = AsyncMock(side_effect=real_bridge)
        tool = await mounted_tool(bridge)
        result = await tool.execute({'operation': 'list_actions', 'parameters': {'prefix': 'theme.'}})
        assert not result.success and result.error['effect'] == 'none'
        bridge.assert_not_awaited()
        correction = result.error['recovery']
        assert correction == {'operation': 'list_actions', 'args': {'prefix': 'theme.'}}
        result = await tool.execute(correction)
        assert result.success and 'theme.apply' in str(result.output)
        bridge.assert_awaited_once_with('list_actions', {'prefix': 'theme.'})
    finally:
        await app.close()


async def test_malformed_mutation_suggests_discovery_and_never_echoes_action_arguments():
    bridge = AsyncMock()
    tool = await mounted_tool(bridge)
    result = await tool.execute({'operation': 'dispatch', 'parameters': {
        'action': 'theme.apply', 'args': {'css': 'private payload'}}})
    assert result.error['recovery'] == {'operation': 'list_actions', 'args': {'prefix': 'theme.'}}
    assert 'private payload' not in str(result.error)
    bridge.assert_not_awaited()
