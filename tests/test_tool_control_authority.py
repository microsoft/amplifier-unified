"""Generic tool controls retain approval without bypassing owned operations."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.management import Management
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.service import AppError, AppService


class Runtime:
    def __init__(self):
        self.calls = []

    async def start(self, session, emit):
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})

    async def control(self, sid, operation, args):
        self.calls.append((sid, operation, copy.deepcopy(args)))
        return {'fixtureOnly': True}

    async def close(self):
        pass


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'host'))
    service = AppService(tmp_path / 'host', Runtime(), workspace=tmp_path)
    service.management = Management(service)
    await service.dispatch('session.create', {})
    own = service._session()['id']
    await service.dispatch('session.create', {})
    other = service._session()['id']
    try:
        yield service, own, other
    finally:
        await service.close()


@pytest.mark.parametrize('name,arguments', [
    ('bash', {'action': action, 'command': 'fixture', 'process_id': 'fixture'})
    for action in ('start', 'write', 'terminate')
] + [('compute', {'action': action}) for action in ('create', 'execute', 'interrupt', 'reset', 'close')])
async def test_managed_mutations_reject_generic_alias_for_ui_and_agent(app, name, arguments):
    service, own, _ = app
    args = {'sessionId': own, 'operation': 'tool.invoke',
            'args': {'name': name, 'arguments': arguments, 'actor': 'ui'}}
    with pytest.raises(AppError, match='shared computation and operation'):
        await service.dispatch('runtime.control', args)
    with pytest.raises(AppError, match='shared computation and operation'):
        await service.app_bridge('dispatch', {'action': 'runtime.control', 'args': args}, own)
    assert service.runtime.calls == []


async def test_generic_tool_agent_cannot_target_foreign_session_or_claim_ui_origin(app):
    service, own, other = app
    args = {'sessionId': other, 'operation': 'tool.invoke',
            'args': {'name': 'read_file', 'arguments': {'path': 'fixture'}, 'actor': 'ui'}}
    with pytest.raises(AppError, match='calling conversation'):
        await service.app_bridge('dispatch', {'action': 'runtime.control', 'args': args}, own)
    with pytest.raises(AppError, match='calling conversation'):
        await service.dispatch('runtime.control', args, origin='agent', caller_session_id=own)
    with pytest.raises(AppError, match='calling conversation'):
        await service.dispatch('runtime.control', {**args, 'sessionId': own}, origin='agent')
    assert service.runtime.calls == []


@pytest.mark.parametrize('origin', ['agent', 'ui'])
async def test_legitimate_generic_invocation_keeps_host_actor_and_current_selection(app, origin):
    service, own, other = app
    service.state['view']['draft'] = 'Keep my draft'
    args = {'sessionId': own, 'operation': 'tool.invoke',
            'args': {'name': 'read_file', 'arguments': {'path': 'fixture'}, 'actor': 'forged'}}
    if origin == 'agent':
        del args['sessionId']  # The bridge binds an omitted target to its caller.
        await service.app_bridge('dispatch', {'action': 'runtime.control', 'args': args}, own)
    else:
        await service.dispatch('runtime.control', args)
    async with asyncio.timeout(5):
        while not service.runtime.calls:
            await asyncio.sleep(.01)
    assert service.runtime.calls == [(own, 'tool.invoke', {
        'name': 'read_file', 'arguments': {'path': 'fixture'}, 'actor': origin})]
    assert service.state['selectedSessionId'] == other
    assert service.state['view']['draft'] == 'Keep my draft'


def controls_fixture(pre_action='continue', replacement=None, in_place=False):
    events = []
    tool = SimpleNamespace(input_schema={'type': 'object'}, execute=AsyncMock(return_value={'success': True}))

    async def emit(event, data):
        events.append((event, copy.deepcopy({key: value for key, value in data.items() if key != 'tool_obj'})))
        if event == 'tool:pre':
            if in_place:
                data['tool_input'].update(replacement)
            return SimpleNamespace(action=pre_action, reason='Fixture denial',
                                   data={'tool_input': replacement} if replacement else None)

    coordinator = SimpleNamespace(
        get=lambda name: {'bash': tool, 'read_file': tool, 'compute': tool} if name == 'tools' else None,
        get_capability=lambda name: None,
        hooks=SimpleNamespace(emit=emit),
        process_hook_result=AsyncMock(side_effect=lambda result, *args: result),
    )
    controls = object.__new__(RuntimeControls)
    controls.coordinator = coordinator
    controls.runtime = SimpleNamespace(generation=None, queued_inputs=0)
    controls.lock = asyncio.Lock()
    controls.checkpoint = AsyncMock()
    return controls, tool, events


@pytest.mark.parametrize('in_place', [False, True])
async def test_approval_cannot_redirect_generic_bash_to_managed_mutation(in_place):
    controls, tool, events = controls_fixture(
        'continue' if in_place else 'modify', {'action': 'write', 'process_id': 'owned'}, in_place)
    with pytest.raises(ValueError, match='shared computation and operation'):
        await controls.perform('tool.invoke', {'name': 'bash', 'arguments': {'command': 'fixture'}, 'actor': 'agent'})
    tool.execute.assert_not_awaited()
    assert [name for name, _ in events] == ['tool:pre', 'tool:error']
    assert events[0][1]['source'] == 'tool.invoke'
    assert events[0][1]['actor'] == 'agent'


@pytest.mark.parametrize('name,arguments', [
    ('bash', {'command': 'fixture'}),
    ('bash', {'command': 'fixture', 'run_in_background': True}),
    ('bash', {'action': 'status', 'process_id': 'owned'}),
    ('read_file', {'path': 'fixture'}),
])
async def test_ordinary_tools_and_passive_reads_keep_hooks_and_actor(name, arguments):
    pytest.importorskip('amplifier_module_loop_live.scope')
    controls, tool, events = controls_fixture()
    await controls.perform('tool.invoke', {'name': name, 'arguments': arguments, 'actor': 'agent'})
    tool.execute.assert_awaited_once_with(arguments)
    assert [event for event, _ in events] == ['tool:pre', 'tool:post']
    assert all(data['source'] == 'tool.invoke' and data['actor'] == 'agent' for _, data in events)
    controls.checkpoint.assert_awaited_once()


async def test_runtime_guard_denies_alias_even_without_host_dispatch():
    controls, tool, events = controls_fixture()
    with pytest.raises(ValueError, match='shared computation and operation'):
        await controls.perform('tool.invoke', {'name': 'compute', 'arguments': {'action': 'execute'}})
    tool.execute.assert_not_awaited()
    assert events == []


async def test_ordinary_generic_tool_still_requires_real_hook_approval():
    controls, tool, events = controls_fixture('deny')
    result = await controls.perform('tool.invoke', {'name': 'read_file', 'arguments': {'path': 'fixture'}, 'actor': 'agent'})
    assert result['success'] is False
    tool.execute.assert_not_awaited()
    assert [event for event, _ in events] == ['tool:pre', 'tool:error']
