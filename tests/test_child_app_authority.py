"""Real Core/Foundation mounting must not lend a child its root's identity."""
import importlib.util
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live')
pytest.importorskip('amplifier_module_context_simple')


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
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
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
                await install_app_access(coordinator, worker.app_access_bridge(coordinator))
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
    worker.runtime = SimpleNamespace(generation={'id':'generation'})
    worker.context_inputs = ['input']
    worker.context_bindings = {'input': {'clientId':None, 'targets':[]}}
    async def bridge(operation, args):
        return await app.app_bridge(operation, args, root['id'])
    worker.bridge = bridge
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
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
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
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
