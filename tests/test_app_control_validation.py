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
    ({'operation': 'list_actions', 'args': {'prefix': 'session.'}}, {'prefix': 'session.'}),
])
async def test_valid_requests_and_receipts_are_unchanged(payload, expected_args):
    receipt = {'ok': True, 'revision': 13, 'commandId': 'stable-command'}
    bridge = AsyncMock(return_value=receipt)
    tool = await mounted_tool(bridge)
    result = await tool.execute(payload)
    assert result.success and result.output == receipt
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
