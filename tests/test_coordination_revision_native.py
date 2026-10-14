"""Real Core/Foundation/live-loop reply revisions with scripted provider output.

Uses the canonical native probe's direct event transport. No network model calls
or process isolation claim; native delivery, tool bridge and terminal anchors are
real and are not synthesized by this test.
"""
import asyncio
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip('amplifier_module_loop_live.orchestrator')
pytest.importorskip('amplifier_module_context_simple')

from fixtures.collaboration_native_probe import DirectNativeHost, ScriptedProvider, peer_envelopes, tool_output
from amplifier_web import runtime_worker
from amplifier_web.service import AppService


class RevisionProvider(ScriptedProvider):
    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.request_id = peer_envelopes(request)[0][0]['inputId']
            return self.dispatch('native-question', 'coordination.reply', {
                'requestId': self.request_id, 'kind': 'question', 'outcome': 'question',
                'text': 'The scripted task needs clarification.'})
        if self.calls == 2:
            assert tool_output(request, 'native-question')['result']['responseRevision'] == 1
            return self.final('Clarification needed for the scripted task.')
        if self.calls == 3:
            return self.dispatch('native-revised-result', 'coordination.reply', {
                'requestId': self.request_id, 'expectedResponseRevision': 1,
                'kind': 'result', 'outcome': 'success', 'text': 'Clarification incorporated.',
                'references': ['artifact:scripted-result']})
        assert self.calls == 4
        value = tool_output(request, 'native-revised-result')
        assert value['accepted'] and value['result']['responseRevision'] == 2
        return self.final('Scripted final result, ready for independent verification.')


async def test_later_native_generation_seals_original_request_and_notifies_each_revision_once(tmp_path, monkeypatch):
    host = DirectNativeHost()
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path/'app'))
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    app = host.app = AppService(tmp_path/'app', host, workspace=workspace)
    app.state['sessions'] = [app._new_session({'title': title}) for title in ('Requester', 'Recipient')]
    source, target = app.state['sessions']
    source['status'] = 'working'  # Observe guarded continuations without running this fixture root.
    app._publish()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_'+name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    host.pump = asyncio.create_task(host.route_events())
    try:
        async with asyncio.timeout(45):
            peer = await host.mount(target, RevisionProvider(), paths)
            await app.dispatch('coordination.send', {'senderSessionId': source['id'], 'sessionId': target['id'],
                'text': 'Produce a scripted report, asking a question first.'}, command_id='native-request', include_state=False)
            wait = await app.dispatch('coordination.subscribe', {'sessionId': source['id'], 'requestId': 'native-request'},
                command_id='native-wait', include_state=False)
            first = await host.outcome(peer, 'native-request')
            question = app.collaboration.receipt('native-request')
            assert question['response']['status'] == 'sealed' and question['response']['outcome'] == 'question'
            assert not app.collaboration.receipt(wait['result']['continuationId'])['dependencySatisfied']
            await app.dispatch('conversation.send', {'sessionId': target['id'], 'text': 'Use the scripted clarification.'},
                command_id='native-clarification', include_state=False)
            second = await host.outcome(peer, 'native-clarification')
            assert second['generation_id'] != first['generation_id']
            result = (await app.dispatch('coordination.result', {'requestId': 'native-request'}, include_state=False))['result']
            assert result['qualified'] and result['responseRevision'] == 2
            assert result['results'][0]['declaration']['generationId'] == second['generation_id']
            assert result['results'][0]['declaration']['admittedInputIds'] == ['native-clarification']
            assert [row['responseRevision'] for row in result['revisions']] == [2, 1]
            app.collaboration.start()
            await host.flush()
            rows = app.db.execute("SELECT receipt FROM commands WHERE json_extract(receipt,'$.dependencyRequestId')='native-request'").fetchall()
            assert len(rows) == 2
            assert len(host.admissions) == 1 and host.admissions[0]['inputId'] == 'native-request'
            assert not host.errors
    finally:
        await app.close()
