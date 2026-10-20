"""Real native input/tool bridge with scripted providers and no network calls."""
import asyncio
import copy
import importlib.util
import time
from pathlib import Path

import pytest

pytest.importorskip("amplifier_module_loop_live.orchestrator")
pytest.importorskip("amplifier_module_context_simple")
from fixtures.collaboration_native_probe import DirectNativeHost, ScriptedProvider, peer_envelopes, tool_output
from amplifier_web import runtime_worker
from amplifier_web.service import AppService


class Manager(ScriptedProvider):
    def __init__(self, app, source, target):
        super().__init__()
        self.app, self.source, self.target = app, source, target

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            human = next(m for m in self.source['messages'] if m.get('inputId') == 'resume-human')
            return self.dispatch('request-resume', 'coordination.send', {
                'sessionId': self.target['id'], 'mode': 'queue',
                'text': 'Inspect what finished, then continue the existing task.',
                'resume': {'sourceMessageId': human['id'], 'expectedInterruptionRevision': 1}})
        result = tool_output(request, 'request-resume')
        assert result['accepted'], result
        self.request_id = result['requestId']
        return self.final('Resume requested; delivery is separate from task completion.')


class Member(ScriptedProvider):
    async def complete(self, request, **kwargs):
        self.calls += 1
        envelope, text = peer_envelopes(request)[0]
        assert envelope['humanResume']['sourceMessageId']
        self.input_id = envelope['inputId']
        return self.final('Previous results reconciled; saved unresolved questions remain unresolved.')


@pytest.mark.parametrize("task_state", ["paused", "blocked"])
async def test_human_instruction_in_manager_reaches_paused_member_native_input(tmp_path, monkeypatch, task_state):
    host = DirectNativeHost()
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path/'app'))
    workspace = tmp_path/'workspace'; workspace.mkdir()
    app = host.app = AppService(tmp_path/'app', host, workspace=workspace)
    source, target = [app._new_session({'title': title}) for title in ('Manager', 'Member')]
    app.state['sessions'] = [source, target]
    assignment = app.assignments.ensure(source, 'fixture-assignment')
    assignment['members'][target['id']] = {'status': 'bound', 'role': 'member'}
    target['assignmentId'] = assignment['id']; app.assignments.save(assignment); app._publish()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_'+name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    host.pump = asyncio.create_task(host.route_events())
    parent, child = Manager(app, source, target), Member()
    try:
        async with asyncio.timeout(30):
            manager = await host.mount(source, parent, paths)
            member = await host.mount(target, child, paths)
            await member.controls.perform('task.create', {
                'commandId': 'task', 'expectedRevision': 0, 'objective': 'Preserve the prior results', 'maxTurns': 3})
            task = (await member.controls.perform('task.pause' if task_state == 'paused' else 'task.block', {
                'commandId': 'pause', 'expectedRevision': 1, 'reason': 'Awaiting resume and reconciliation'}))['task']
            target.update(task=copy.deepcopy(task), status='stopped', interruptionRevision=1,
                          lastInterruption={'at': time.time()-1, 'origin': 'agent'})
            # Host stopped state and native saved goal are independently checked.
            # Process re-creation is covered by the companion subprocess test.
            before = copy.deepcopy(member.controls.tasks.snapshot())
            await app.dispatch('conversation.send', {'sessionId': source['id'], 'text': 'Resume the paused member.'},
                               command_id='resume-human', include_state=False)
            await host.outcome(manager, 'resume-human')
            while not host.admissions:
                await asyncio.sleep(.01)
            assert host.admissions[0]['accepted'], host.admissions
            await host.outcome(member, host.admissions[0]['inputId'])
            assert child.calls == 1 and parent.calls == 2
            assert member.controls.tasks.record()['status'] == task_state
            assert member.controls.tasks.snapshot()['task']['revision'] == before['task']['revision']
            assert not host.approvals and not host.errors
            assert not [m for m in target['messages'] if m.get('inputOrigin') == 'ui']
    finally:
        await app.close()
