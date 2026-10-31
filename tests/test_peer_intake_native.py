"""Same-receipt intake across actual native task edits; scripted providers only."""
import asyncio
import copy
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live.orchestrator')
pytest.importorskip('amplifier_module_context_simple')

from amplifier_web import runtime_worker
from amplifier_web.service import AppService
from amplifier_web.management import Management
from fixtures.collaboration_native_probe import DirectNativeHost, ScriptedProvider, peer_envelopes, tool_output


@pytest.mark.parametrize('mode', ['auto', 'queue'])
@pytest.mark.parametrize('change', ['progress', 'objective', 'pause-resume', 'stop'])
async def test_native_peer_intake_keeps_only_the_original_continuing_objective(tmp_path, monkeypatch, mode, change):
    host = DirectNativeHost()
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    app = host.app = AppService(tmp_path / 'app', host, workspace=workspace)
    app.management = Management(app)
    app.management.ensure_runtime = AsyncMock()
    sender, target = [app._new_session({'title': title}) for title in ('Sender', 'Task owner')]
    app.state['sessions'] = [sender, target]
    app.state['selectedSessionId'] = sender['id']
    app._publish()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    waiting, release = asyncio.Event(), asyncio.Event()
    original_admission = host.automatic_input

    async def held_admission(session, args, guard, steering):
        if args['inputId'] == 'original-peer-request':
            waiting.set()
            await release.wait()
        return await original_admission(session, args, guard, steering)
    host.automatic_input = held_admission

    async def control(sid, operation, args, guard=None):
        worker = host.roots[sid]
        async with worker.command_lock:
            if guard:
                guard()
            return await worker.controls.perform(operation, args)
    host.control = control

    async def stop(sid):
        await host.roots[sid].command({'op': 'stop'})
    host.stop = stop

    class Sender(ScriptedProvider):
        async def complete(self, request, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return self.dispatch('original-peer-request', 'coordination.send', {
                    'sessionId': target['id'], 'mode': mode, 'text': 'Inspect the same objective.',
                    'returnNotification': False})
            result = tool_output(request, 'original-peer-request')
            assert result['accepted'] and result['delivery'] == 'queued', result
            return self.final('Original request retained.')

    class Recipient(ScriptedProvider):
        async def complete(self, request, **kwargs):
            self.calls += 1
            assert change == 'progress', 'A replaced or interrupted objective admitted old input'
            if self.calls == 2:
                result = tool_output(request, 'finish-objective')
                assert result['accepted'], result
                return self.final('The unchanged objective used its latest constraints.')
            assert self.calls == 1
            envelopes = [e for e, _ in peer_envelopes(request)]
            assert [e['inputId'] for e in envelopes] == ['original-peer-request']
            assert envelopes[0]['mode'] == mode
            record = host.roots[target['id']].controls.tasks.record()
            assert record['revision'] == 2 and record['continuationEpoch'] == 1
            assert record['constraints'] == ['Inspect only; do not publish.']
            return self.dispatch('finish-objective', 'task.complete', {
                'sessionId': target['id'], 'expectedRevision': 2, 'condition': record['objective'],
                'evidence': ['urn:fixture:inspected']})

    async def task(operation, **args):
        return await app.dispatch(operation, {'sessionId': target['id'], **args}, origin='ui',
            command_id=operation + '-' + str(args['expectedRevision']), include_state=False)

    provider = Recipient()
    host.pump = asyncio.create_task(host.route_events())
    try:
        async with asyncio.timeout(60):
            source = await host.mount(sender, Sender(), paths)
            recipient = await host.mount(target, provider, paths)
            await task('task.create', expectedRevision=0, objective='Inspect the objective.', maxTurns=1)
            original_task = copy.deepcopy(recipient.controls.tasks.record())
            assert target['task']['id'] == original_task['id']
            await app.dispatch('conversation.send', {'sessionId': sender['id'], 'text': 'Send the peer update.'},
                origin='ui', command_id='human-request', include_state=False)
            await waiting.wait()
            saved = copy.deepcopy(app.collaboration.receipt('original-peer-request'))
            assert saved['taskRevision'] == saved['replyTaskContinuationEpoch'] == 1
            if change == 'progress':
                await task('task.update', expectedRevision=1, constraints=['Inspect only; do not publish.'],
                           artifactRefs=['urn:fixture:latest-plan'])
            elif change == 'objective':
                await task('task.update', expectedRevision=1, objective='A different objective.')
            elif change == 'pause-resume':
                await task('task.pause', expectedRevision=1)
                await task('task.resume', expectedRevision=2)
            else:
                await app.dispatch('conversation.stop', {'sessionId': target['id']}, origin='ui',
                                   command_id='human-stop', include_state=False)
            release.set()
            await host.outcome(source, 'human-request')
            while app.collaboration.receipt('original-peer-request')['delivery'] in {'queued', 'submitting'}:
                await asyncio.sleep(.01)
            result = app.collaboration.receipt('original-peer-request')
            assert result['delivery'] == ('accepted' if change == 'progress' else 'suppressed'), result
            if change == 'progress':
                terminal = await host.outcome(recipient, 'original-peer-request')
                assert terminal['input_ids'] == ['original-peer-request']
            assert all(result[k] == saved[k] for k in ('requestId', 'inputId', 'taskId', 'taskRevision',
                       'replyTaskContinuationEpoch', 'sourceInputIds', 'displayBinding'))
            await app.collaboration.drain(target['id'])
            inputs = [e['input_id'] for e in recipient.runtime.events if e['type'] == 'input.delivered']
            assert inputs == (['original-peer-request'] if change == 'progress' else [])
            assert provider.calls == (2 if change == 'progress' else 0)
            if change == 'progress':
                # Peer intake tolerance never changes exact task mutation CAS.
                with pytest.raises(ValueError, match='revision changed'):
                    await recipient.controls.tasks.perform('task.update', {
                        'commandId': 'stale-mutation', 'expectedRevision': 1, 'constraints': []})
                assert recipient.controls.tasks.record()['revision'] == 3
            assert not host.errors and not host.approvals
    finally:
        release.set()
        await host.close()
        app.management = None
        await app.close()
