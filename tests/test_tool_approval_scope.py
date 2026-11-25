"""Actual Core hooks/host guards, with an explicitly synthetic source fixture.

No provider, model, command or file effects. These tests qualify scope transport
and revalidation, not the provenance of a separately installed reference tool.
"""
import asyncio
import copy
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio

from amplifier_core import AmplifierSession, ApprovalRequest, HookResult, ToolResult
from amplifier_core.message_models import ToolCall
from amplifier_web import runtime_worker, tool_approval_scope
from amplifier_web.app_guidance import install_app_access
from amplifier_web.host.approvals import Approvals, REQUEST_SCOPE

dispatch_module = pytest.importorskip('amplifier_module_loop_streaming.dispatch')
if not hasattr(dispatch_module.ApprovedDispatch, 'current_call'):
    pytest.skip('Selected streaming lacks the optional current-call capability', allow_module_level=True)


class SyntheticTool:
    name = 'fixture_tool'
    description = 'Effect-free exact request fixture'
    input_schema = {'type': 'object', 'properties': {'value': {'type': 'integer'},
                                                    'file_path': {'type': 'string'}},
                    'required': ['value', 'file_path'], 'additionalProperties': False}

    def __init__(self, workspace):
        self.config = {'working_dir': str(workspace)}
        self.working_dir = str(workspace)
        self.allowed_paths = [str(workspace)]
        self.effects = []
        self.source = {'fixture': 'selected-synthetic-source', 'revision': 'one',
                       'modules': {'tool-filesystem': 'amplifier_module_tool_filesystem:mount'}}

    async def execute(self, arguments):
        self.effects.append(copy.deepcopy(arguments))
        return ToolResult(success=True, output=arguments)

    def _is_allowed(self, path):
        return True  # Synthetic parser/permission fixture, not a file-policy proof.


# Exercise the actual existing file wrapper without installing reference tools.
# Only _source is replaced below, so this is not evidence of tool provenance.
SyntheticTool.__module__ = 'amplifier_module_tool_filesystem.read'
SyntheticTool.__name__ = 'ReadTool'


@pytest_asyncio.fixture
async def native(monkeypatch, tmp_path):
    session = AmplifierSession({'session': {'orchestrator': {'module': 'loop-live',
                                                           'config': {'programmatic_dispatch': True}},
                                           'context': {'module': 'context-simple'}}, 'providers': []})
    await session.initialize()
    coordinator = session.coordinator
    coordinator.config['tools'] = [{'module': 'tool-filesystem', 'config': {}}]
    tool = SyntheticTool(tmp_path)
    await coordinator.mount('tools', tool, name=tool.name)
    coordinator.register_capability('session.working_dir', str(tmp_path))
    facts = {'owner': str(tmp_path/'app'), 'workspace': str(tmp_path), 'accessMode': 'ask',
             'epoch': {'session': 'first', 'generation': 'first', 'stopRevision': 0}}
    coordinator.register_capability('web.tool_approval.context', lambda: copy.deepcopy(facts))
    before_body = []
    async def work_check(name, arguments):
        for callback in before_body:
            callback(arguments)
        return {'allowed': True}  # Synthetic host decision; not a Work proof.
    coordinator.register_capability('web.work_effect_check', work_check)
    async def bridge(*args):
        return {}
    await install_app_access(coordinator, bridge, execution_role='root')
    coordinator.get_capability('web.work_guard_tools')()
    original_source = tool_approval_scope._source
    monkeypatch.setattr(tool_approval_scope, '_source', lambda item, proof=None:
        (copy.deepcopy(item.source), 'read') if item is tool else original_source(item, proof))
    events = []
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    worker = runtime_worker.Worker()
    worker.session = session
    approvals = Approvals(None, worker.ask, coordinator=lambda: coordinator)
    scope_errors = []
    async def approval(event, data):
        try:
            coordinator.get_capability('web.tool_approval.scope').snapshot()
        except Exception as exc:
            scope_errors.append(repr(exc))
        response = await approvals.request_approval(ApprovalRequest(tool_name=data['tool_name'],
            action='Synthetic review', details=data['tool_input'], risk_level='medium'))
        return HookResult(action='continue' if response.approved else 'deny', reason=response.reason)
    coordinator.hooks.register('tool:pre', approval, priority=30, name='typed-approval')
    value = SimpleNamespace(session=session, coordinator=coordinator, tool=tool, facts=facts,
        events=events, worker=worker, approvals=approvals, before_body=before_body,
        loop=coordinator.get('orchestrator'), scope=coordinator.get_capability('web.tool_approval.scope'),
        scope_errors=scope_errors)
    try:
        yield value
    finally:
        await session.cleanup()


async def call(native, identity='call', value=1, **changes):
    tool_call = ToolCall(id=identity, name=native.tool.name,
                        arguments={'value': value, 'file_path': 'fixture.txt', **changes})
    with native.loop.approved_dispatch.run_scope(native.coordinator):
        return await native.loop._execute_tool_only(tool_call, native.coordinator.get('tools'),
                                                    native.coordinator.hooks, None, native.coordinator)


async def pending(native, count=1):
    for _ in range(200):
        requests = [event for event in native.events if event['type'] == 'approval.requested']
        if len(requests) == count:
            return requests
        await asyncio.sleep(.001)
    pytest.fail('The bounded synthetic approval did not arrive')


async def answer(native, request, decision='allow', **extra):
    return await native.worker.resolve_approval({'approval_id': request['id'], 'decision': decision, **extra})


async def test_parallel_typed_calls_keep_exact_scope_and_private_worker_binding(native):
    first = asyncio.create_task(call(native, 'first', 1))
    second = asyncio.create_task(call(native, 'second', 2))
    try:
        a, b = sorted(await pending(native, 2), key=lambda event: event['prompt'])
        assert not native.scope_errors
        assert a['toolScope']['descriptor']['key'] != b['toolScope']['descriptor']['key']
        assert a['toolScope']['descriptor']['authorityLifetime'] == 'request'
        with pytest.raises(ValueError, match='changed'):
            await answer(native, a, expected_scope=b['toolScope']['descriptor']['key'])
        assert not native.tool.effects
        await answer(native, b, 'deny')
        await answer(native, a, expected_scope=a['toolScope']['descriptor']['key'])
        await asyncio.gather(first, second)
        assert native.tool.effects == [{'value': 1, 'file_path': 'fixture.txt'}]
        assert not native.worker.approval_scopes
        assert not native.scope.accepted and not native.scope.calls
    finally:
        first.cancel(); second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.parametrize('change', ['schema', 'source', 'config', 'roots', 'cwd', 'mode', 'access', 'stop'])
async def test_changed_request_or_context_while_waiting_never_executes(native, change):
    task = asyncio.create_task(call(native))
    try:
        request, = await pending(native)
        assert request.get('toolScope')
        if change == 'schema': native.tool.input_schema = {**native.tool.input_schema, 'title': 'Changed'}
        elif change == 'source': native.tool.source['revision'] = 'two'
        elif change == 'config': native.tool.config['different'] = True
        elif change == 'roots': native.tool.allowed_paths = ['/different']
        elif change == 'cwd': native.tool.working_dir = '/different'
        elif change == 'mode': native.coordinator.session_state['require_approval_tools'] = ['fixture_tool']
        elif change == 'access': native.facts['accessMode'] = 'full'
        elif change == 'stop': native.facts['epoch']['stopRevision'] = 1
        await answer(native, request)
        result = await task
        assert not native.tool.effects
        assert 'not been approved' in str(result).lower()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('change', ['context', 'arguments', 'expired'])
async def test_post_approval_work_wait_recheck_prevents_effect(native, change):
    def mutate(arguments):
        if change == 'context':
            native.facts['epoch']['stopRevision'] += 1
        elif change == 'arguments':
            # Actual args changed after ordinary hooks/approval. No text hashing.
            arguments['value'] = 2
        else:
            native.loop.approved_dispatch.validate_context = lambda _: False
    native.before_body.append(mutate)
    task = asyncio.create_task(call(native))
    try:
        request, = await pending(native)
        await answer(native, request)
        result = await task
        assert not native.tool.effects
        assert 'changed' in str(result).lower() or 'no longer active' in str(result).lower()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_same_exact_request_across_chat_epochs_keeps_key(native):
    keys = []
    for index in range(2):
        task = asyncio.create_task(call(native, str(index)))
        request = (await pending(native, index + 1))[-1]
        keys.append(request['toolScope']['descriptor']['key'])
        await answer(native, request, 'deny')
        await task
        native.facts['epoch'] = {'session': 'second', 'generation': 'second', 'stopRevision': 6}
    assert keys[0] == keys[1]
    assert not native.tool.effects


async def test_plain_text_even_inside_known_call_never_gets_scope(native):
    current = ToolCall(id='opaque', name=native.tool.name,
                       arguments={'value': 1, 'file_path': 'fixture.txt'})
    with native.loop.approved_dispatch.run_scope(native.coordinator):
        with native.loop.approved_dispatch.call_scope(current, native.coordinator.get('tools'),
                                                       native.coordinator.hooks, None):
            task = asyncio.create_task(native.approvals.request_approval('fixture_tool: value 1'))
            request, = await pending(native)
            assert 'toolScope' not in request
            await answer(native, request)
            assert await task == 'allow'
    assert not native.scope.accepted and not native.tool.effects


@pytest.mark.parametrize('arguments', [{'value': 'invalid'}, {'value': 1, 'unknown': True}])
async def test_invalid_args_have_no_reusable_scope(native, arguments):
    current = ToolCall(id='invalid', name=native.tool.name, arguments=arguments)
    with native.loop.approved_dispatch.run_scope(native.coordinator):
        with native.loop.approved_dispatch.call_scope(current, native.coordinator.get('tools'),
                                                       native.coordinator.hooks, None):
            request = ApprovalRequest(tool_name=native.tool.name, action='Review', details=arguments,
                                      risk_level='medium')
            assert native.scope.request(request) is None
    assert not native.tool.effects


async def test_context_changed_while_file_lock_waits_prevents_effect(native, monkeypatch):
    from amplifier_web import file_source_guard
    locked = file_source_guard._locked
    entered = asyncio.Event()
    @asynccontextmanager
    async def observed(*args, **kwargs):
        entered.set()
        async with locked(*args, **kwargs):
            yield
    monkeypatch.setattr(file_source_guard, '_locked', observed)
    guard = native.coordinator.get_capability('web.file_source_guard')
    path = file_source_guard.Path(native.tool.working_dir)/'fixture.txt'
    async with locked(guard.directory, [path]):
        task = asyncio.create_task(call(native))
        request, = await pending(native)
        await answer(native, request)
        await asyncio.wait_for(entered.wait(), 1)
        native.facts['epoch']['stopRevision'] += 1
    result = await task
    assert not native.tool.effects
    assert 'changed' in str(result) and 'none' in str(result)


async def test_hook_callback_failure_keeps_known_call_unapproved(native, monkeypatch):
    async def fail(*args):
        assert REQUEST_SCOPE.get() is not None
        raise RuntimeError('Synthetic approval transport failure')
    monkeypatch.setattr(native.approvals, 'ask', fail)
    result = await call(native)
    assert not native.tool.effects
    assert 'not been approved' in str(result)


async def test_missing_observer_keeps_ordinary_once_approval(native):
    native.coordinator.register_capability('tools.dispatch', SimpleNamespace())
    task = asyncio.create_task(call(native))
    request, = await pending(native)
    assert 'toolScope' not in request
    await answer(native, request)
    await task
    assert native.tool.effects == [{'value': 1, 'file_path': 'fixture.txt'}]


async def test_mismatched_request_or_unconfigured_tool_stays_once_only(native, monkeypatch):
    current = ToolCall(id='mismatch', name=native.tool.name,
                       arguments={'value': 1, 'file_path': 'fixture.txt'})
    with native.loop.approved_dispatch.run_scope(native.coordinator):
        with native.loop.approved_dispatch.call_scope(current, native.coordinator.get('tools'),
                                                       native.coordinator.hooks, None):
            for name, args in [('different', current.arguments), (native.tool.name, {'value': 2})]:
                request = ApprovalRequest(tool_name=name, action='Review', details=args, risk_level='medium')
                assert native.scope.request(request) is None
            native.coordinator.config['tools'] = []
            request = ApprovalRequest(tool_name=native.tool.name, action='Review',
                                      details=current.arguments, risk_level='medium')
            assert native.scope.request(request) is None
    assert not native.scope.accepted and not native.tool.effects


async def test_inherited_request_context_cannot_describe_another_ask(native):
    task = asyncio.create_task(call(native))
    first, = await pending(native)
    prepared = native.worker.approval_scopes[first['id']]
    token = REQUEST_SCOPE.set(prepared)
    try:
        other = asyncio.create_task(native.worker.ask('An unstructured inherited ask', ['allow', 'deny']))
    finally:
        REQUEST_SCOPE.reset(token)
    requests = await pending(native, 2)
    second = next(item for item in requests if item['id'] != first['id'])
    assert 'toolScope' not in second
    await answer(native, second, 'deny')
    await other
    await answer(native, first, 'deny')
    await task
    assert not native.tool.effects
