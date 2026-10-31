"""A real native manager tool closes an exhausted member without a provider call."""
import asyncio
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('amplifier_module_loop_live.orchestrator')
pytest.importorskip('amplifier_module_context_simple')
from fixtures.collaboration_native_probe import DirectNativeHost, ScriptedProvider, tool_output
from amplifier_web import runtime_worker
from amplifier_web.management import Management
from amplifier_web.service import AppService


class Manager(ScriptedProvider):
    def __init__(self, source, target):
        super().__init__()
        self.source, self.target = source, target

    async def complete(self, request, **kwargs):
        self.calls += 1
        base = {'sessionId': self.source['id'], 'targetSessionId': self.target['id']}
        if self.calls == 1:
            return self.dispatch('read-contribution', 'assignment.task.read', base)
        if self.calls == 2:
            response = tool_output(request, 'read-contribution')
            assert response['accepted'], response
            value = response['result']
            assert value['task']['status'] == 'blocked' and value['turnsUsed'] == 8
            return self.dispatch('complete-contribution', 'assignment.task.complete', {
                **base, 'taskId': value['task']['id'], 'expectedRevision': value['task']['revision'],
                'expectedAssignmentRevision': value['assignmentRevision'],
                'evidence': ['Exact report accepted in the reviewed delivery.']})
        assert self.calls == 3
        response = tool_output(request, 'complete-contribution')
        assert response['accepted'] and response['result']['inputSent'] is False, response
        return self.final('The accepted contribution is closed; its retained evidence remains.')


class NoMemberCall(ScriptedProvider):
    async def complete(self, request, **kwargs):
        self.calls += 1
        raise AssertionError('Recording accepted completion must not call the member provider')


async def test_native_manager_records_completion_without_waking_stopped_member(tmp_path, monkeypatch):
    host = DirectNativeHost()
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    app = host.app = AppService(tmp_path / 'app', host, workspace=workspace)
    source, target = [app._new_session({'title': title}) for title in ('Manager', 'Member')]
    app.state['sessions'] = [source, target]
    assignment = app.assignments.ensure(source, 'fixture-assignment')
    assignment['members'][target['id']] = {'status': 'bound', 'role': 'member'}
    target['assignmentId'] = assignment['id']; app.assignments.save(assignment); app._publish()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    host.pump = asyncio.create_task(host.route_events())
    manager_provider, member_provider = Manager(source, target), NoMemberCall()
    try:
        async with asyncio.timeout(40):
            manager = await host.mount(source, manager_provider, paths)
            member = await host.mount(target, member_provider, paths)
            await member.controls.perform('task.create', {
                'commandId': 'original-task', 'expectedRevision': 0, 'origin': 'agent',
                'objective': 'Deliver the exact reviewed report', 'maxTurns': 8,
                'operationIds': ['unrelated-uncertain-cleanup'], 'questionIds': ['separate-product-choice']})
            member.session.coordinator.session_state['goal'].update(turns_used=8, continuations=7)
            task = (await member.controls.perform('task.block', {
                'commandId': 'waiting', 'expectedRevision': 1, 'origin': 'agent',
                'reason': 'Waiting for manager acceptance'}))['task']
            target.update(task=copy.deepcopy(task), status='stopped', interruptionRevision=3,
                          lastInterruption={'kind': 'user_stop', 'at': 1})
            app._publish()
            async def prepare(session, *, restart_guard=None):
                assert session['id'] == target['id']
                if restart_guard: restart_guard()
            async def control(sid, operation, values, *, guard=None):
                async with host.roots[sid].command_lock:
                    if guard: guard()
                    return await host.roots[sid].controls.perform(operation, values)
            app.management = SimpleNamespace(ensure_runtime=prepare,
                unresolved_configuration=Management.unresolved_configuration)
            host.control = control
            await app.dispatch('conversation.send', {'sessionId': source['id'],
                'text': 'Record acceptance of the reviewed member contribution.'},
                origin='ui', command_id='acceptance-record', include_state=False)
            await host.outcome(manager, 'acceptance-record')
            saved = member.controls.tasks.record()
            assert saved['id'] == task['id'] and saved['status'] == 'completed'
            assert saved['goalState']['turns_used'] == 8 and saved['maxTurns'] == 8
            assert saved['operationIds'] == task['operationIds'] and saved['questionIds'] == task['questionIds']
            assert member.session.coordinator.session_state['goal'] is None
            assert target['status'] == 'stopped' and target['interruptionRevision'] == 3
            assert member_provider.calls == 0 and manager_provider.calls == 3
            assert not host.admissions and not host.approvals and not host.errors
    finally:
        app.management = None
        await app.close()
