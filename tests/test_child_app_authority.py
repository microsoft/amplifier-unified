"""Real Core/Foundation mounting must not lend a child its root's identity."""
import importlib.util
import asyncio
import ast
import json
import os
from dataclasses import dataclass
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live')
pytest.importorskip('amplifier_module_context_simple')
from test_collaborative_workspaces import app


@pytest.mark.parametrize('scenario', ['optional', 'essential', 'nested-reprepare'])
async def test_scripted_child_returns_result_after_root_only_refusal(app, tmp_path, monkeypatch, scenario):
    """Actual Core/Live requests and delegated returns, NOT model compliance.

    The provider is scripted, local work admission is a fixture. Collaboration
    principal rejection, child installation, nested spawn and results are real.
    Stop/stale producer coverage remains in test_child_app_assignment_capture.
    """
    from amplifier_core import ToolResult
    from amplifier_core.message_models import ChatResponse, ToolCall, Usage
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web import runtime_worker
    from amplifier_web.app_guidance import install_app_access, COMMON_GUIDANCE
    from amplifier_web.coordination_guidance import PLANNING_GUIDANCE, NATIVE_CHILD_GUIDANCE, ROOT_SCOPE_MESSAGE
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals
    from fixtures.collaboration_native_probe import ScriptedProvider, message_text, tool_output

    providers, effects, requests, late_preparations = {}, [], {}, []
    registry = None

    class NextStep:
        name = 'independent_review'
        description = 'Run the independently authorized synthetic review.'
        input_schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
        def __init__(self, session):
            self.session = session
        async def execute(self, args):
            sid = self.session.session_id
            effects.append(sid)
            result = {'review': 'two plus two is four'}
            if scenario == 'nested-reprepare' and sid == 'child':
                # Exercise the same host preparation path twice, preserving the
                # captured child bridge rather than recapturing root inputs.
                c = self.session.coordinator
                await registry.host_adapter.prepare_execution(c.get('orchestrator'), c, c.get('providers'))
                nested = await registry.spawn('worker', 'Independent nested review', self.session,
                                               sub_session_id='grandchild')
                result['nested'] = json.loads(nested['output'])
            return ToolResult(success=True, output=result)

    class Provider(ScriptedProvider):
        def __init__(self, sid):
            super().__init__()
            self.sid = sid
        async def complete(self, request, **kwargs):
            self.calls += 1
            requests.setdefault(self.sid, []).append(request.model_dump_json())
            # Core may place ephemeral system guidance at the request tail;
            # inspect the actual composed request, not a presumed role.
            system = '\n'.join(message_text(m) for m in request.messages)
            assert system.count(COMMON_GUIDANCE) == 1
            assert system.count(NATIVE_CHILD_GUIDANCE) == 1
            assert PLANNING_GUIDANCE not in system
            for forbidden in ('coordination.send mode:auto', 'coordination.create in the inherited workspace',
                              'session.handoff.start for independent work', 'Commission complete deliverables'):
                assert forbidden not in system
            if self.calls == 1:
                return self.dispatch('optional-report', 'coordination.send', {
                    'sessionId': app.state['sessions'][1]['id'], 'mode': 'auto', 'text': 'Review A complete'})
            if self.calls == 2:
                row = next(m for m in request.messages if m.role == 'tool' and m.tool_call_id == 'optional-report')
                assert ROOT_SCOPE_MESSAGE in message_text(row)
                assert 'End this delegated attempt' not in message_text(row)
                if scenario == 'essential':
                    return self.final(json.dumps({'status': 'blocked', 'refusal': ROOT_SCOPE_MESSAGE,
                        'requestedAction': 'coordination.send', 'effects': []}))
                return ChatResponse(content=[], tool_calls=[
                    ToolCall(id='independent-step', name='independent_review', arguments={})],
                    usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
            assert self.calls == 3
            review = tool_output(request, 'independent-step')
            return self.final(json.dumps({'status': 'completed', 'refusal': ROOT_SCOPE_MESSAGE,
                'requestedAction': 'coordination.send', 'effects': [review]}))

    @dataclass
    class Prepared(PreparedBundle):
        async def create_session(self, **kwargs):
            session = await super().create_session(**kwargs)
            provider = providers[session.session_id] = Provider(session.session_id)
            await session.coordinator.mount('providers', provider, name=provider.name)
            await session.coordinator.mount('tools', NextStep(session), name='independent_review')
            return session

    plan = {'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False}},
                        'context': {'module': 'context-simple'}},
            'agents': {'worker': {'instruction': 'Complete two independent synthetic reviews.'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    prepared = Prepared(plan, BundleModuleResolver(paths),
                        Bundle(name='guidance-fixture', session=plan['session'], agents=plan['agents']))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    parent = await prepared.create_session(session_id='root', approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    source = app.state['sessions'][0]
    source['runtimeSessionId'] = 'root'
    worker = runtime_worker.Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id': 'source-generation', 'input_ids': ['incoming-peer']},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = ['incoming-peer']
    worker.context_bindings = {'incoming-peer': {'clientId': None, 'targets': []}}
    # Use the real transport's private owner binding. Calling app.app_bridge
    # directly would correctly reject a child before the collaboration refusal.
    from amplifier_web.runtime import RuntimeManager
    transport = RuntimeManager()
    transport.app_bridge = app.app_bridge
    owner = {'process': SimpleNamespace(returncode=None), 'runtimeOwnerId': 'guidance-fixture-owner'}
    transport.workers[source['id']] = owner
    monkeypatch.setattr(app.runtime, 'workers', transport.workers, raising=False)
    replies = []
    async def write(row, reply):
        assert row is owner
        replies.append(reply)
    transport._write = write
    async def bridge(operation, args):
        if operation == 'work.effect.check':
            return {'allowed': True}  # Fixture admission, not a Work authorization claim.
        if operation in {'context.manifest', 'memory.context', 'coordination.current'}:
            return {}
        await transport._bridge(source['id'], owner, {'id': 'guidance-bridge', 'operation': operation, 'args': args})
        reply = replies.pop()
        if reply.get('error'):
            raise RuntimeError(reply['error'])
        return reply['result']
    worker.bridge = bridge
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator), execution_role='root')
    store = SessionStore(tmp_path / 'guidance-children')
    registry = await install_children(parent, prepared, Runtime('root'), store, approvals)
    # As in test_provider_capture_live, execute the exact production late
    # installer, with only its unrelated external activity IO replaced.
    worker.install_activity = lambda c: late_preparations.append(c.session_id)
    worker.install_collaboration_checkpoint = lambda c: None
    original_host = registry.host_adapter
    tree = ast.parse(Path(runtime_worker.__file__).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == 'ObservedHost')
    scope = {'original_host': original_host, 'host': worker, 'install_app_access': install_app_access}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 runtime_worker.__file__, 'exec'), scope)
    registry.host_adapter = scope['ObservedHost']()
    monkeypatch.setattr(runtime_worker, 'publish', lambda value: None)
    try:
        async with asyncio.timeout(30):
            returned = await registry.spawn('worker', 'Review A and B', parent, sub_session_id='child')
        report = json.loads(returned['output'])
        assert report['refusal'] == ROOT_SCOPE_MESSAGE
        assert report['requestedAction'] == 'coordination.send'
        assert returned['session_id'] == 'child'
        assert report['status'] == ('blocked' if scenario == 'essential' else 'completed')
        assert effects == ([] if scenario == 'essential' else
                           ['child', 'grandchild'] if scenario == 'nested-reprepare' else ['child'])
        assert late_preparations.count('child') == (2 if scenario == 'nested-reprepare' else 1)
        if scenario == 'nested-reprepare':
            assert report['effects'][0]['nested']['status'] == 'completed'
            assert late_preparations.count('grandchild') == 1
        assert not app.runtime.inputs, 'Denied sends must not deliver peer input'
        for sid in effects or ['child']:
            history = store.load(sid)[0]
            assert COMMON_GUIDANCE not in json.dumps(history), 'Guidance stays ephemeral'
        if artifacts := os.environ.get('AMPLIFIER_TEST_ARTIFACTS'):
            Path(artifacts, f'child-guidance-{scenario}.json').write_text(json.dumps({
                'scriptedProvider': True, 'workAdmission': 'fixture', 'requests': requests,
                'delegatedResult': returned, 'effects': effects, 'latePreparations': late_preparations}, indent=2))
    finally:
        transport.workers.clear()
        await parent.cleanup()


@pytest.mark.parametrize('mode', ['finite', 'resumed', 'persistent', 'nested', 'nested-reprepare'])
async def test_mounted_child_app_access_has_own_principal(tmp_path, monkeypatch, mode):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.host.children import install_children, _PERSISTENT
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals

    spec = importlib.util.spec_from_file_location('authority_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'agents': {'worker': {'instruction': 'Inspect only'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
                                    Bundle(name='fixture', session=plan['session'], agents=plan['agents']))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    parent = await prepared.create_session(session_id='root', approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id': 'root-generation', 'input_ids': ['original-input']},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = ['original-input']
    worker.context_bindings = {'original-input': {'clientId': 'original-client', 'targets': []}}
    worker.bridge = AsyncMock(return_value={'accepted': True})
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator), execution_role='root')
    registry = await install_children(parent, prepared, Runtime('root'), SessionStore(tmp_path / 'children'), approvals)
    tools_seen = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        control = tools['app_control']
        tools_seen.append(control)
        assert control is not parent.coordinator.get('tools')['app_control']
        result = await control.execute({'operation': 'dispatch', 'args': {
            'action': 'coordination.create', 'id': 'request',
            '_runtimeSessionId': 'root', '_generationId': 'root-generation',
            'args': {'title': 'Peer', 'text': 'Inspect'}}})
        assert result.success, result.error
        _, envelope = worker.bridge.call_args.args
        assert envelope['_runtimeSessionId'] == coordinator.session_id
        assert envelope['_generationId'] == 'root-generation'
        assert envelope['_inputBindings'] == [{'inputId': 'original-input', 'clientId': 'original-client'}]
        if mode in {'nested', 'nested-reprepare'} and coordinator.session_id == 'child':
            worker.context_inputs = ['unrelated-later-input']
            worker.context_bindings = {'unrelated-later-input': {'clientId': 'other-client', 'targets': []}}
            if mode == 'nested-reprepare':
                # Native loop preparation evaluates bridge construction even
                # when install_app_access subsequently sees an existing tool.
                await install_app_access(coordinator, worker.app_access_bridge(coordinator), execution_role='native_child')
            await registry.spawn('worker', 'nested', registry.sessions['child'], sub_session_id='grandchild')
        return 'done'

    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    token = _PERSISTENT.set(mode == 'persistent')
    try:
        await registry.spawn('worker', 'inspect', parent, sub_session_id='child')
        if mode == 'resumed':
            await registry.resume('child', 'continue', parent)
        if mode in {'resumed', 'nested', 'nested-reprepare'}:
            assert len(tools_seen) == 2 and tools_seen[0] is not tools_seen[1]
        await parent.coordinator.get('tools')['app_control'].execute({'operation': 'list_actions'})
        assert worker.bridge.call_args.args[1]['_runtimeSessionId'] == 'root'
        assert worker.bridge.call_args.args[1]['_generationId'] == 'root-generation'
    finally:
        _PERSISTENT.reset(token)
        await parent.cleanup()


@pytest.mark.parametrize('mode', ['finite', 'resumed', 'persistent', 'nested', 'recipe'])
@pytest.mark.parametrize('effect', ['configuration', 'task-storage'])
async def test_mounted_child_cannot_borrow_configuration_grant(tmp_path, monkeypatch, mode, effect):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.host.children import install_children, _PERSISTENT
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.service import AppService

    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    spec = importlib.util.spec_from_file_location('granted_child_fixture', Path(__file__).parent/'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'agents': {'worker': {'instruction': 'Inspect only'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_'+name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths), Bundle(name='fixture', session=plan['session'], agents=plan['agents']))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    parent = await prepared.create_session(session_id='root', approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    app = AppService(tmp_path/'app', workspace=tmp_path)
    root = app._new_session({'title': 'Root'})
    root.update(runtimeSessionId='root', collaborationGeneration={'id':'generation', 'inputIds':['input'], 'terminal':False})
    app.state['sessions'] = [root]
    app.state['selectedSessionId'] = root['id']
    app._publish()
    from amplifier_web.management import Management
    app.management = Management(app)
    await app.dispatch('capability.grant', {'sessionId':root['id'], 'effects':['file_access'], 'scopes':['global'], 'reason':'Explicit setup'})
    # This identity fixture has no mounted file tools. Supply its root's allowed
    # observation explicitly; real file/patch policy is covered by
    # test_workspace_task_storage. A child must be rejected before this check.
    async def storage_access(sid, paths):
        assert sid == root['id']
        assert all(Path(path).is_relative_to(tmp_path) for path in paths)
        return {'status': 'observed', 'paths': {path: {'status': 'allowed'} for path in paths}}
    policy = AsyncMock(side_effect=storage_access)
    if effect == 'task-storage':
        monkeypatch.setattr(app, 'runtime', SimpleNamespace(resource_access=policy, close=AsyncMock()))
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id':'generation', 'input_ids':['input']},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = ['input']
    worker.context_bindings = {'input': {'clientId':None, 'targets':[]}}
    # Use the real host bridge to stamp the current worker owner, as production
    # does before validating the child's private execution scope.
    from amplifier_web.runtime import RuntimeManager
    transport = RuntimeManager()
    transport.app_bridge = app.app_bridge
    owner = {'process': SimpleNamespace(returncode=None), 'runtimeOwnerId': 'fixture-owner'}
    transport.workers[root['id']] = owner
    prior_runtime = app.runtime
    if app.runtime is None:
        app.runtime = SimpleNamespace(close=AsyncMock())
    host_runtime = app.runtime
    host_runtime.workers = dict(transport.workers)
    async def bridge(operation, args):
        replies = []
        async def write(row, reply): replies.append(reply)
        transport._write = write
        await transport._bridge(root['id'], owner, {'id':'fixture-bridge', 'operation':operation, 'args':args})
        if replies[0].get('error'): raise RuntimeError(replies[0]['error'])
        return replies[0]['result']
    worker.bridge = bridge
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator), execution_role='root')
    registry = await install_children(parent, prepared, Runtime('root'), SessionStore(tmp_path/'children'), approvals)
    observed = []
    workspace = next(row for row in app.state['workspaces'] if row['path']==root['workspace'])
    action, action_args = ('permissions.save', {'scope':'global', 'allowed':[], 'denied':[]}) if effect=='configuration' else (
        'workspace.resources.provision', {'workspaceId':workspace['id'], 'sessionId':root['id'], 'kind':'scratch-directory'})
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await tools['app_control'].execute({'operation':'dispatch', 'args':{
            'action':action, 'id':'child-forged-storage', 'args':action_args,
            '_runtimeSessionId':'root', '_generationId':'generation'}})
        assert not result.success
        assert ('child cannot borrow' if effect=='configuration' else 'host-bound root owner') in str(result.error)
        observed.append(coordinator.session_id)
        if mode == 'nested' and coordinator.session_id == 'child':
            await registry.spawn('worker', 'nested', registry.sessions['child'], sub_session_id='grandchild')
        return 'done'
    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    token = _PERSISTENT.set(mode == 'persistent')
    try:
        await registry.spawn('worker', 'inspect', parent, sub_session_id='child', **({'use_subprocess':False} if mode=='recipe' else {}))
        if mode == 'resumed':
            await registry.resume('child', 'continue', parent)
        assert len(observed) == (2 if mode in {'resumed','nested'} else 1)
        policy.assert_not_awaited()
        assert app.configuration_authority.snapshot(root)['grants'][0]['usable']
        host_runtime.workers.clear()
        app.runtime = prior_runtime
        result = await parent.coordinator.get('tools')['app_control'].execute({'operation':'dispatch','args':{
            'action':action,'id':'native-root-admin','args':action_args}})
        assert result.success, result.error
        if effect=='configuration':
            from test_coordination_generation import until
            await until(lambda: 'native-root-admin' in app.state.get('managementResults', {}))
            assert app.state['managementResults']['native-root-admin']['phase']=='ready'
        else:
            from amplifier_web.task_resources import read
            rows = read(app.data_dir, root['id'], {})['items']
            assert len(rows)==1 and rows[0]['status']=='ready'
            assert Path(rows[0]['path']).is_dir()
            assert policy.await_count == 2  # Admission and returned access observation.
    finally:
        _PERSISTENT.reset(token)
        host_runtime.workers.clear()
        app.runtime = prior_runtime
        transport.workers.clear()
        await parent.cleanup()
        await app.close()


@pytest.mark.parametrize('decision', ['allow', 'deny', 'revoke', 'cancel', 'immediate'])
async def test_native_configuration_request_waits_for_human_without_replaying_effects(tmp_path, monkeypatch, decision):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    from test_configuration_authority import Runtime
    from test_coordination_generation import until

    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    spec = importlib.util.spec_from_file_location('approval_wait_fixture', Path(__file__).parent/'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_'+name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths), Bundle(name='fixture', session=plan['session']))
    parent = await prepared.create_session(session_id='root', approval_system=Approvals(None, AsyncMock(return_value='deny')))
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id':'generation'})
    worker.context_inputs = ['input']
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    class NativeApprovalRuntime(Runtime):
        async def approval(self, sid, identity, response):
            await worker._command_serial({'id':'native-decision','op':'approval','approval_id':identity,'decision':response})
    app = AppService(tmp_path/'app', NativeApprovalRuntime(), workspace=tmp_path)
    app.management = Management(app)
    root = app._new_session({'title':'Root'})
    root.update(runtimeSessionId='root', collaborationGeneration={'id':'generation','inputIds':['input'],'terminal':False})
    app.state['sessions'] = [root]
    app.state['selectedSessionId'] = root['id']
    app._publish()
    async def bridge(operation, args):
        result = await app.app_bridge(operation, args, root['id'])
        if decision == 'immediate' and args.get('action') == 'capability.request':
            await app.dispatch('approval.respond', {'sessionId':root['id'],'id':result['result']['id'],'decision':'allow'})
            await asyncio.sleep(0)  # Decision can arrive before bridge response.
        return result
    worker.bridge = bridge
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator), execution_role='root')
    control = parent.coordinator.get('tools')['app_control']
    task = asyncio.create_task(control.execute({'operation':'dispatch','args':{'action':'capability.request','id':'native-request',
        'args':{'sessionId':root['id'],'effects':['file_access'],'scopes':['global'],'reason':'User requested setup'}}}))
    try:
        await until(lambda: bool(root['approvals']))
        row = app.configuration_authority.rows(root['id'])[0]
        assert row['nativeWait']
        if decision != 'immediate':
            assert not task.done() and row['id'] in worker.approvals
            if decision == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not worker.approvals
                # A later decision stores the grant; no old tool/turn starts.
                await app.dispatch('approval.respond', {'sessionId':root['id'],'id':row['id'],'decision':'allow'})
                assert not app.state.get('managementResults')
                return
            if decision == 'revoke':
                await app.dispatch('capability.revoke', {'sessionId':root['id'],'id':row['id'],'expectedRevision':row['revision']})
            else:
                await app.dispatch('approval.respond', {'sessionId':root['id'],'id':row['id'],'decision':decision}, command_id='human-decision')
                await app.dispatch('approval.respond', {'sessionId':root['id'],'id':row['id'],'decision':decision}, command_id='human-decision')
        response = await asyncio.wait_for(task, 2)
        assert response.success, response.error
        assert response.output['waitCompleted']
        assert response.output['result']['usable'] == (decision in {'allow','immediate'})
        assert not app.state.get('managementResults'), 'Approval must not replay a privileged effect'
        assert not worker.approvals
        result = await control.execute({'operation':'dispatch','args':{'action':'permissions.save','id':'explicit-next-action',
            'args':{'scope':'global','allowed':[],'denied':[]}}})
        assert result.success == (decision in {'allow','immediate'})
        if result.success:
            await until(lambda: 'explicit-next-action' in app.state.get('managementResults', {}))
            assert app.state['managementResults']['explicit-next-action']['phase']=='ready'
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await parent.cleanup()
        await app.close()
