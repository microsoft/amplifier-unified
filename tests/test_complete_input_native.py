"""Real native burst admission/typed reply; only providers/transport are scripted."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('amplifier_module_loop_live.orchestrator')
pytest.importorskip('amplifier_module_context_simple')

from amplifier_core import ToolResult
from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_web import runtime_worker
from amplifier_web.service import AppService
from fixtures.collaboration_native_probe import DirectNativeHost, ScriptedProvider, peer_envelopes, tool_output


async def test_seventeen_peer_inputs_retain_native_reply_and_effect_identity(tmp_path, monkeypatch):
    host = DirectNativeHost()
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    app = host.app = AppService(tmp_path / 'app', host, workspace=workspace)
    sender, target = [app._new_session({'title': title}) for title in ('Sender', 'Busy manager')]
    app.state['sessions'] = [sender, target]
    app.state['selectedSessionId'] = sender['id']
    app._publish()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    ids = [f'native-peer-{i}' for i in range(17)]
    started, submitted = asyncio.Event(), asyncio.Event()
    effects = []

    class Sender(ScriptedProvider):
        async def complete(self, request, **kwargs):
            index = self.calls
            self.calls += 1
            if index:
                result = tool_output(request, ids[index - 1])
                assert result['accepted'], result
                while app.collaboration.receipt(ids[index - 1])['delivery'] not in {'accepted', 'applied'}:
                    await asyncio.sleep(.01)
            if index == len(ids):
                submitted.set()
                return self.final('All updates admitted under their original IDs.')
            return self.dispatch(ids[index], 'coordination.send', {
                'sessionId': target['id'], 'mode': 'auto', 'text': f'Update {index}.',
                'returnNotification': False})

    class Manager(ScriptedProvider):
        async def complete(self, request, **kwargs):
            self.calls += 1
            if self.calls == 1:
                started.set()
                await submitted.wait()
                return self.tool('boundary', 'get_state', {})
            if self.calls == 2:
                observed = [e['inputId'] for e, _ in peer_envelopes(request)]
                assert observed == ids
                return ChatResponse(content=[], tool_calls=[ToolCall(
                    id='ordinary-effect', name='probe', arguments={'value': 'checked'})],
                    usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
            if self.calls == 3:
                assert effects == [{'value': 'checked'}]
                return self.dispatch('typed-result', 'coordination.reply', {
                    'requestId': ids[-1], 'kind': 'result', 'outcome': 'success',
                    'text': 'All updates inspected.', 'references': ['urn:fixture:checked']})
            assert self.calls == 4
            result = tool_output(request, 'typed-result')
            assert result['accepted'] and result['result']['status'] == 'staged', result
            return self.final('Result retained after the complete native input burst.')

    class Probe:
        name = 'probe'
        description = 'Record one test-owned effect.'
        input_schema = {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}
        async def execute(self, args):
            effects.append(args)
            return ToolResult(success=True, output='checked')

    host.pump = asyncio.create_task(host.route_events())
    try:
        async with asyncio.timeout(60):
            source = await host.mount(sender, Sender(), paths)
            manager = await host.mount(target, Manager(), paths)
            manager.activation_gate = SimpleNamespace(current_valid=True)
            await manager.session.coordinator.mount('tools', Probe(), name='probe')
            await app.dispatch('conversation.send', {'sessionId': target['id'], 'text': 'Wait for updates.'},
                               origin='ui', command_id='manager-original', include_state=False)
            await started.wait()
            await app.dispatch('conversation.send', {'sessionId': sender['id'], 'text': 'Send the updates.'},
                               origin='ui', command_id='sender-original', include_state=False)
            terminal = await host.outcome(manager, 'manager-original')
            await host.outcome(source, 'sender-original')
            expected = ['manager-original', *ids]
            assert terminal['input_ids'] == expected
            reply = next(row for row in host.bridges if row['args'].get('id') == 'typed-result')
            assert [row['inputId'] for row in reply['args']['_inputBindings']] == expected
            checks = [row for row in host.bridges if row['operation'] == 'work.effect.check'
                      and row['args'].get('toolName') == 'probe']
            assert len(checks) == 1
            assert [row['inputId'] for row in checks[0]['args']['_inputBindings']] == expected
            outcome = app.collaboration.receipt(ids[-1])['response']
            assert outcome['status'] == 'sealed' and outcome['qualified'] is True
            assert outcome['nativeTerminal']['generationId'] == terminal['generation_id']
            assert len([e for e in manager.runtime.events if e['type'] == 'generation.started']) == 1
            assert [e['input_id'] for e in manager.runtime.events if e['type'] == 'input.delivered'] == expected
            assert not host.errors and not host.approvals
    finally:
        await host.close()
        await app.close()
