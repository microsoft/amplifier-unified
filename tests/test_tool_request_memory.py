"""Normal Foundation/Core/live entry, with scripted provider and tool sources.

Source provenance has its separate package tests. These cases exercise the
actual host/approval/Work path without shell, MCP network or model effects.
Instance policy remains external: tests supply its current allow/deny decision.
"""
import asyncio
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass, field
from contextlib import contextmanager

import pytest
import pytest_asyncio
from amplifier_core import ApprovalRequest, HookResult, ProviderInfo, ToolResult
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle

from amplifier_web import runtime_worker, tool_approval_scope
from amplifier_web.app_guidance import install_app_access
from amplifier_web.host.approvals import Approvals
from amplifier_web.host.session import SelectedProvider

dispatch_module = pytest.importorskip('amplifier_module_loop_streaming.dispatch')
if not hasattr(dispatch_module.ApprovedDispatch, 'current_call'):
    pytest.skip('Selected streaming lacks current-call observation', allow_module_level=True)


class ConfiguredClient:
    server_name = 'selected'
    command = 'fixture-command'
    args = ['fixture-argument']
    def __init__(self):
        self.env = {'AUTH': 'fixture-only-not-a-credential'}
        self.session = object()
        self.tools = [{'name': 'remote/name', 'input_schema': ExactTool.input_schema}]
    def approval_binding(self):
        return {'version': 1, 'key': tool_approval_scope.fingerprint(self.env)}
    @contextmanager
    def approval_scope(self, expected, tool_name, arguments):
        assert expected == self.approval_binding()
        yield
ConfiguredClient.__module__ = 'amplifier_module_tool_mcp.client'
ConfiguredClient.__name__ = 'MCPClient'


@dataclass
class SafetyFixture:
    name: str = 'strict'
    blocked_patterns: list = field(default_factory=list)


class ValidatorFixture:
    def __init__(self):
        self.profile, self.config = SafetyFixture(), SafetyFixture()
        self.allowed_commands, self.denied_commands = [], ['forbidden']
        self._override_allows, self._override_blocks = [], []


class ExactTool:
    description = 'Effect-free fixture'
    input_schema = {'type': 'object', 'properties': {'command': {'type': 'string'}},
                    'required': ['command'], 'additionalProperties': False}
    def __init__(self, role, cwd):
        self.name = role
        self.role = role
        self.config = {'working_dir': str(cwd), 'require_approval': True}
        self.working_dir = str(cwd)
        self.require_approval = True
        self.allowed_commands = None
        self.denied_commands = ['forbidden']
        self._safety_validator = ValidatorFixture()
        self.timeout, self.max_output_bytes, self.max_concurrent = 30, 1000, None
        self.effects = []
        self.client = ConfiguredClient()
        self.server_name, self.tool_name, self.max_content_size = 'selected', 'remote/name', 1000
    async def execute(self, arguments):
        self.effects.append(copy.deepcopy(arguments))
        return ToolResult(success=True, output={'accepted': arguments})


@pytest_asyncio.fixture
async def flow(tmp_path, monkeypatch, request):
    modules = ('loop-live', 'context-simple')
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in modules}
    plan = {'session': {'orchestrator': {'module': 'loop-live', 'config': {
                'max_iterations': 2, 'min_delay_between_calls_ms': 0}},
             'context': {'module': 'context-simple'}}}
    if getattr(request, 'param', None) is False:
        plan['session']['orchestrator']['config']['programmatic_dispatch'] = False
    session = await PreparedBundle(plan, BundleModuleResolver(paths),
        Bundle(name='request-memory', session=plan['session'])).create_session(
            session_id='request-memory', session_cwd=tmp_path)
    c = session.coordinator
    tools = {role: ExactTool(role, tmp_path) for role in ('bash', 'catalog', 'mcp')}
    for name, tool in tools.items():
        await c.mount('tools', tool, name=name)
    c.config['tools'] = [{'module': 'tool-' + role, 'config': {'configured': True}} for role in tools]
    c.register_capability('session.working_dir', str(tmp_path))
    facts = {'owner': str(tmp_path/'app'), 'workspace': str(tmp_path), 'accessMode': 'ask',
             'epoch': {'session': 'first', 'generation': 'first', 'stopRevision': 0}}
    c.register_capability('web.tool_approval.context', lambda: copy.deepcopy(facts))
    body_callbacks = []
    async def work(name, args):
        for callback in body_callbacks:
            callback()
        return {'allowed': True}
    c.register_capability('web.work_effect_check', work)
    async def bridge(*args):
        return {}
    await install_app_access(c, bridge, execution_role='root')
    c.get_capability('web.work_guard_tools')()
    source = tool_approval_scope._source
    monkeypatch.setattr(tool_approval_scope, '_source', lambda item, proof=None:
        ({'fixture': 'selected-source', 'modules': {'tool-' + item.role: 'fixture:mount'}},
         'ordinary' if item.role == 'catalog' else item.role) if item in tools.values() else source(item, proof))
    implementation = tool_approval_scope._implementation
    monkeypatch.setattr(tool_approval_scope, '_implementation', lambda item, proof, method='execute':
        {'fixture': type(item).__name__} if type(item) in (ConfiguredClient, ValidatorFixture)
        else implementation(item, proof, method))
    events, scope_errors = [], []
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    worker = runtime_worker.Worker(); worker.session = session
    approvals = Approvals(None, worker.ask, coordinator=lambda: c)
    require_approval = [True]
    async def policy(event, data):
        if not require_approval[0]:
            return HookResult()
        try:
            c.get_capability('web.tool_approval.scope').snapshot()
        except Exception as exc:
            run, call = dispatch_module._RUN.get(), dispatch_module._CALL.get()
            scope_errors.append({'error': repr(exc), 'run': run is not None, 'call': call is not None,
                'coordinator': run is not None and run.coordinator is c,
                'dispatcher': run is not None and run.owner is c.get_capability('tools.dispatch'),
                'tool': call is not None and call.tools.get(data['tool_name']) is c.get('tools').get(data['tool_name'])})
        decision = await approvals.request_approval(ApprovalRequest(tool_name=data['tool_name'],
            action='Current fixture request', details=data['tool_input'], risk_level='medium'))
        return HookResult(action='continue' if decision.approved else 'deny', reason=decision.reason)
    c.hooks.register('tool:pre', policy, priority=30, name='existing-policy')
    class Provider:
        name = 'scripted'
        def __init__(self):
            self.pending = None
        def get_info(self):
            return ProviderInfo(id=self.name, display_name=self.name, capabilities=[],
                defaults={'model': 'fixture', 'context_window': 100000, 'max_output_tokens': 1000})
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        async def complete(self, request, **kwargs):
            usage = Usage(input_tokens=1, output_tokens=1, total_tokens=2)
            if self.pending:
                tool_call, self.pending = self.pending, None
                return ChatResponse(content=[], tool_calls=[tool_call], usage=usage)
            return ChatResponse(content=[TextBlock(text='Finished')], usage=usage)
    provider = Provider()
    await c.mount('providers', provider, name=provider.name)
    c.get('orchestrator').root_provider = SelectedProvider(provider, {'model': 'fixture'})
    async def turn(role, arguments=None):
        provider.pending = ToolCall(id='fresh-' + str(len(events)), name=role,
                                   arguments=arguments or {'command': 'exact request'})
        return await session.execute('Perform the current fixture request.')
    value = SimpleNamespace(session=session, coordinator=c, tools=tools, facts=facts,
        events=events, errors=scope_errors, worker=worker, turn=turn,
        callbacks=body_callbacks, require_approval=require_approval)
    try:
        yield value
    finally:
        await session.cleanup()


async def decide(flow, role, *, decision='allow', change=None, arguments=None, scoped=True):
    count = len(flow.events)
    task = asyncio.create_task(flow.turn(role, arguments))
    try:
        for _ in range(1000):
            pending = [e for e in flow.events[count:] if e['type'] == 'approval.requested']
            if pending:
                break
            if task.done():
                await task
                pytest.fail('Normal live entry completed without the expected approval')
            await asyncio.sleep(.002)
        else:
            pytest.fail('Approval did not arrive')
        request, = pending
        if scoped:
            assert request.get('toolScope') and not flow.errors, flow.errors
        else:
            assert 'toolScope' not in request
        if change:
            change()
        answer = {'approval_id': request['id'], 'decision': decision}
        if scoped:
            answer['expected_scope'] = request['toolScope']['descriptor']['key']
        await flow.worker.resolve_approval(answer)
        await asyncio.wait_for(task, 5)
        return request
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('role', ['bash', 'mcp'])
async def test_normal_live_entry_scopes_new_calls_and_obeys_current_denial(flow, role):
    first = await decide(flow, role)
    assert flow.tools[role].effects == [{'command': 'exact request'}]
    flow.facts['epoch'] = {'session': 'another', 'generation': 'new', 'stopRevision': 8}
    # Instance may have revoked its rule; Unified must obey this new denial.
    second = await decide(flow, role, decision='deny')
    assert first['toolScope']['descriptor']['key'] == second['toolScope']['descriptor']['key']
    assert flow.tools[role].effects == [{'command': 'exact request'}]


@pytest.mark.parametrize('change', ['args', 'config', 'authorization', 'mode'])
async def test_matching_is_exact_across_new_calls(flow, change):
    first = await decide(flow, 'mcp', decision='deny')
    args = None
    if change == 'args': args = {'command': 'a different request'}
    elif change == 'config': flow.coordinator.config['tools'][-1]['config']['configured'] = False
    elif change == 'authorization': flow.tools['mcp'].client.env['AUTH'] = 'changed-fixture'
    else: flow.coordinator.session_state['require_approval_tools'] = ['mcp']
    second = await decide(flow, 'mcp', decision='deny', arguments=args)
    assert first['toolScope']['descriptor']['key'] != second['toolScope']['descriptor']['key']
    assert not flow.tools['mcp'].effects
    assert 'fixture-only-not-a-credential' not in str(first)


@pytest.mark.parametrize('change', ['client', 'authorization', 'stop'])
async def test_inflight_binding_or_stop_change_prevents_effect(flow, change):
    def changed():
        if change == 'client': flow.tools['mcp'].client = ConfiguredClient()
        elif change == 'authorization': flow.tools['mcp'].client.env['AUTH'] = 'changed'
        else: flow.facts['epoch']['stopRevision'] += 1
    await decide(flow, 'mcp', change=changed)
    assert not flow.tools['mcp'].effects


async def test_same_configured_mcp_reconnect_invalidates_pending_approval(flow):
    await decide(flow, 'mcp', change=lambda: setattr(flow.tools['mcp'].client, 'session', object()))
    assert not flow.tools['mcp'].effects


async def test_unstructured_registered_tool_keeps_once_without_memory(flow):
    await decide(flow, 'catalog', scoped=False)
    assert len(flow.tools['catalog'].effects) == 1


async def test_mcp_new_call_after_same_binding_reconnect_keeps_rule_key(flow):
    first = await decide(flow, 'mcp', decision='deny')
    flow.tools['mcp'].client.session = object()
    second = await decide(flow, 'mcp', decision='deny')
    assert first['toolScope']['descriptor']['key'] == second['toolScope']['descriptor']['key']


async def test_mcp_changed_connected_registry_keeps_once(flow):
    flow.tools['mcp'].client.tools = []
    await decide(flow, 'mcp', decision='deny', scoped=False)
    assert not flow.tools['mcp'].effects


async def test_legacy_mcp_client_keeps_once(flow, monkeypatch):
    monkeypatch.delattr(ConfiguredClient, 'approval_binding')
    await decide(flow, 'mcp', decision='deny', scoped=False)
    assert not flow.tools['mcp'].effects


@pytest.mark.parametrize('change', ['environment', 'safety', 'limit'])
async def test_effective_shell_change_requires_a_new_match(flow, monkeypatch, change):
    first = await decide(flow, 'bash', decision='deny')
    if change == 'environment':
        monkeypatch.setenv('APPROVAL_FIXTURE_VALUE', 'changed-private-fixture')
    elif change == 'safety':
        flow.tools['bash']._safety_validator._override_blocks.append('changed')
    else:
        flow.tools['bash'].timeout += 1
    second = await decide(flow, 'bash', decision='deny')
    assert first['toolScope']['descriptor']['key'] != second['toolScope']['descriptor']['key']
    assert not flow.tools['bash'].effects


async def test_shell_environment_change_after_approval_stops_at_work_boundary(flow, monkeypatch):
    flow.callbacks.append(lambda: monkeypatch.setenv('APPROVAL_FIXTURE_VALUE', 'changed'))
    await decide(flow, 'bash')
    assert not flow.tools['bash'].effects


async def test_managed_process_action_has_no_reusable_scope(flow):
    flow.tools['bash'].input_schema = {'type': 'object'}
    await decide(flow, 'bash', arguments={'command': 'unused', 'action': 'write'},
                 decision='deny', scoped=False)
    assert not flow.tools['bash'].effects


async def test_postapproval_stop_at_work_boundary_prevents_shell_effect(flow):
    flow.callbacks.append(lambda: flow.facts['epoch'].update(stopRevision=1))
    await decide(flow, 'bash')
    assert not flow.tools['bash'].effects


@pytest.mark.parametrize('resolved', [(r'C:\Program Files\Git\bin\bash.exe', False),
                                      (r'C:\Windows\System32\bash.exe', True), (None, False)])
async def test_windows_shell_field_serialization_in_unix_fixture(flow, monkeypatch, resolved):
    # This tests lossless metadata only; the separate Windows environment cases
    # below require Once for unresolved and WSL execution.
    monkeypatch.setattr(tool_approval_scope, 'sys',
                        SimpleNamespace(platform='linux', modules=tool_approval_scope.sys.modules))
    meanings = []
    original = tool_approval_scope.fingerprint
    def observe(value):
        if value.get('operation') == 'tool.invoke':
            meanings.append(copy.deepcopy(value))
        return original(value)
    monkeypatch.setattr(tool_approval_scope, 'fingerprint', observe)
    flow.tools['bash']._windows_bash_resolved = resolved
    first = await decide(flow, 'bash', decision='deny')
    assert all(item['binding']['resolvedWindowsBash'] == list(resolved) for item in meanings)
    flow.facts['epoch']['session'] = 'another-chat'
    same = await decide(flow, 'bash', decision='deny')
    assert same['toolScope']['descriptor']['key'] == first['toolScope']['descriptor']['key']
    flow.tools['bash']._windows_bash_resolved = (r'D:\Other\bash.exe', resolved[1])
    changed_path = await decide(flow, 'bash', decision='deny')
    flow.tools['bash']._windows_bash_resolved = (r'D:\Other\bash.exe', not resolved[1])
    changed_kind = await decide(flow, 'bash', decision='deny')
    assert len({item['toolScope']['descriptor']['key']
                for item in (first, changed_path, changed_kind)}) == 3
    assert not flow.tools['bash'].effects


@pytest.mark.parametrize('resolved', [[], ['bash.exe', False], 'bash.exe', ('bash.exe',),
                                      ('bash.exe', False, 'extra'), (42, False), ('bash.exe', 0)])
async def test_invalid_cached_windows_shell_has_no_reusable_scope(flow, resolved):
    flow.tools['bash']._windows_bash_resolved = resolved
    await decide(flow, 'bash', decision='deny', scoped=False)
    assert flow.errors and all('resolved Windows shell binding is invalid' in item['error']
                               for item in flow.errors)
    assert not flow.tools['bash'].effects


@pytest.mark.parametrize('resolved', [None, (None, False), ('wsl.exe', True)])
async def test_unwitnessed_windows_environment_remains_once(flow, monkeypatch, resolved):
    monkeypatch.setattr(tool_approval_scope, 'sys',
                        SimpleNamespace(platform='win32', modules=tool_approval_scope.sys.modules))
    flow.tools['bash']._windows_bash_resolved = resolved
    await decide(flow, 'bash', decision='deny', scoped=False)
    assert not flow.tools['bash'].effects


async def test_previously_allowed_tool_does_not_gain_an_approval_prompt(flow):
    flow.require_approval[0] = False
    await flow.turn('catalog')
    assert flow.tools['catalog'].effects == [{'command': 'exact request'}]
    assert not [e for e in flow.events if e['type'] == 'approval.requested']


@pytest.mark.parametrize('flow', [False], indirect=True)
@pytest.mark.parametrize('role', ['bash', 'mcp'])
async def test_explicitly_disabled_programmatic_dispatch_still_describes_ordinary_call(flow, role):
    def no_lease():
        dispatcher = flow.coordinator.get_capability('tools.dispatch')
        assert dispatcher.enabled is False
        with pytest.raises(RuntimeError, match='No active authorized programmatic caller'):
            dispatcher.bind()
    flow.callbacks.append(no_lease)
    await decide(flow, role)
    assert len(flow.tools[role].effects) == 1
