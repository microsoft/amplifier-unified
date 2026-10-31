"""Real manager/member loops: repair an exhausted cap, then admit one fresh input."""
import asyncio
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('amplifier_module_loop_live.orchestrator')
pytest.importorskip('amplifier_module_context_simple')
from fixtures.collaboration_native_probe import DirectNativeHost, ScriptedProvider, peer_envelopes, tool_output
from amplifier_web import runtime_worker
from amplifier_web.collaboration_input import exhausted
from amplifier_web.management import Management
from amplifier_web.service import AppService


class Manager(ScriptedProvider):
    def __init__(self, app, source, target):
        super().__init__()
        self.app, self.source, self.target = app, source, target

    async def complete(self, request, **kwargs):
        self.calls += 1
        base = {'sessionId': self.source['id'], 'targetSessionId': self.target['id']}
        if self.calls == 1:
            return self.dispatch('read-cap', 'assignment.task.read', base)
        if self.calls == 2:
            result = tool_output(request, 'read-cap')
            assert result['accepted'], result
            value = result['result']
            assert value['turnsUsed'] == value['task']['maxTurns'] == 8
            human = next(row for row in self.source['messages'] if row.get('inputId') == 'human-cap')
            return self.dispatch('change-cap', 'assignment.task.setLimit', {
                **base, 'taskId': value['task']['id'], 'expectedRevision': value['task']['revision'],
                'expectedAssignmentRevision': value['assignmentRevision'], 'maxTurns': None,
                'sourceMessageId': human['id'], 'correction': 'User removed the planning cap; retain prior progress.'})
        if self.calls == 3:
            result = tool_output(request, 'change-cap')
            assert result['accepted'] and result['result']['inputSent'] is False, result
            return self.dispatch('fresh-member-input', 'coordination.send', {
                'sessionId': self.target['id'], 'text': 'Reconcile the saved dependency result.', 'mode': 'queue'})
        assert self.calls == 4
        result = tool_output(request, 'fresh-member-input')
        assert result['accepted'], result
        return self.final('The limit is corrected. Fresh delivery is separate from completion.')


class Member(ScriptedProvider):
    async def complete(self, request, **kwargs):
        self.calls += 1
        envelope, _ = peer_envelopes(request)[0]
        self.input_id = envelope['inputId']
        return self.final('Saved effects reconciled; pending questions and the objective remain unchanged.')


async def test_native_exhausted_member_receives_fresh_work_after_manager_control(tmp_path, monkeypatch):
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
    manager_provider, member_provider = Manager(app, source, target), Member()
    try:
        async with asyncio.timeout(40):
            manager = await host.mount(source, manager_provider, paths)
            member = await host.mount(target, member_provider, paths)
            await member.controls.perform('task.create', {
                'commandId': 'original-task', 'expectedRevision': 0, 'origin': 'agent',
                'objective': 'Resolve the already commissioned work', 'maxTurns': 8,
                'operationIds': ['uncertain-prior-effect'], 'questionIds': ['separate-open-question']})
            member.session.coordinator.session_state['goal'].update(turns_used=8, continuations=7)
            task = (await member.controls.perform('task.block', {
                'commandId': 'waiting', 'expectedRevision': 1, 'origin': 'agent',
                'reason': 'Awaiting an independently owned dependency'}))['task']
            target['task'] = copy.deepcopy(task); app._publish()
            assert exhausted(member.controls)

            # The fixture transports the ordinary native control under its
            # worker lock; all task/peer guards and both loops remain real.
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
                'text': 'Remove the member’s planning cap and continue the existing work.'},
                origin='ui', command_id='human-cap', include_state=False)
            await host.outcome(manager, 'human-cap')
            while not host.admissions:
                await asyncio.sleep(.01)
            assert host.admissions[0]['accepted'], host.admissions
            await host.outcome(member, host.admissions[0]['inputId'])
            current = member.controls.tasks.record()
            assert current['id'] == task['id'] and current['status'] == 'blocked'
            assert current['maxTurns'] is None and current['goalState']['turns_used'] == 9
            assert current['operationIds'] == task['operationIds']
            assert current['questionIds'] == task['questionIds']
            assert member_provider.calls == 1 and manager_provider.calls == 4
            assert member_provider.input_id == 'fresh-member-input'
            assert not host.approvals and not host.errors
    finally:
        app.management = None
        await app.close()
