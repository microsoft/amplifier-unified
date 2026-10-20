"""Offline proof with actual Core, loop-live, shell-hook and Unified boundaries."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from amplifier_core import AmplifierSession
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
import amplifier_module_hook_shell
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_web.execution_events import CALL_PURPOSE, ExecutionEvents
from amplifier_web.host.prompt_events import install
from amplifier_web.runtime import normalize_event
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppService


async def main():
    report = {'providerCalls': [], 'privateSource': {}, 'limits': ['Deterministic local provider; no external model or reporter Mac configuration.']}
    with tempfile.TemporaryDirectory(prefix='hook-stream833-', dir=os.environ['TMPDIR']) as tmp:
        home = Path(tmp)
        workspace = home / 'workspace'
        hooks_dir = workspace / '.amplifier' / 'hooks'
        hooks_dir.mkdir(parents=True)
        (hooks_dir / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [{
            'matcher': '*', 'hooks': [{'type': 'prompt', 'prompt': 'Verify the completed response. Return JSON ok/reason. $ARGUMENTS'}]
        }]}}))
        app = AppService(home / 'app', workspace=str(workspace))
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        runtime = Runtime(session_id=sid)
        session = AmplifierSession({'session': {
            'orchestrator': {'module': 'loop-live', 'config': {'configured_bundle': True,
                'min_delay_between_calls_ms': 0}},
            'context': {'module': 'context-simple'}}, 'providers': []}, session_id=sid)
        task = None
        try:
            await session.initialize()
            co = session.coordinator
            co.register_capability('session.working_dir', str(workspace))
            co.register_capability('live.runtime', runtime)
            check_json = json.dumps({'ok': True, 'reason': 'Verified: synthetic completion check'})

            class Provider:
                name = 'fixture'
                config = {}
                answers = 0

                def get_info(self):
                    return SimpleNamespace(id='fixture', defaults={'model': 'offline'}, default_model='offline')

                def parse_tool_calls(self, response):
                    return response.tool_calls or []

                async def complete(self, request, **kwargs):
                    auxiliary = request.max_output_tokens == 256
                    if not auxiliary:
                        self.answers += 1
                    text = check_json if auxiliary else f'User-facing answer {self.answers}'
                    report['providerCalls'].append({'auxiliary': auxiliary, 'purpose': CALL_PURPOSE.get(),
                        'public': co.get_capability('live.public_stream')()})
                    await co.hooks.emit('llm:stream_block_delta', {'session_id': sid,
                        'request_id': f'call-{len(report["providerCalls"])}', 'block_index': 0,
                        'block_type': 'text', 'text': text})
                    return ChatResponse(content=[TextBlock(text=text)],
                        usage=Usage(input_tokens=4, output_tokens=2, total_tokens=6))

            await co.mount('providers', Provider(), name='fixture')
            observed = []
            import amplifier_web.runtime_worker as worker_module
            worker_module.publish = observed.append
            worker = Worker()
            worker.session, worker.runtime = session, runtime
            worker.telemetry = ExecutionEvents(sid, observed.append)
            worker.telemetry.turn_id = 'turn-1'
            worker.install_activity(co)
            install(co)
            await amplifier_module_hook_shell.mount(co, {})
            task = asyncio.create_task(session.execute(''))
            await runtime.wait_for(lambda e: e['type'] == 'session.ready', 5)
            for number in (1, 2):
                await runtime.submit(Input('user', f'Question {number}', id=f'input-{number}'))
                await runtime.wait_for(lambda e: e['type'] == 'session.idle'
                    and e.get('text') == f'User-facing answer {number}', 5)
            # Replay actual serialized runtime events through the real host projection.
            for event in runtime.events:
                normalized = normalize_event(event, sid)
                if normalized:
                    await app.on_runtime_event(*normalized)
            public_deltas = [event.get('text') for event in runtime.events if event['type'] == 'assistant.delta']
            public_messages = [event.get('text') for event in runtime.events if event['type'] == 'assistant.message']
            saved = app._session(sid)['messages']
            usage = [item['event'] for item in observed if item.get('type') == 'execution.event'
                     and item['event'].get('kind') == 'llm' and item['event'].get('phase') == 'completed']
            report.update(publicDeltas=public_deltas, publicMessages=public_messages,
                savedAssistantTexts=[row['text'] for row in saved if row['role'] == 'assistant'],
                modelCalls=len(usage), totalTokens=sum(row['usage']['totalTokens'] for row in usage))
            report['passed'] = (public_deltas == ['User-facing answer 1', 'User-facing answer 2']
                and public_messages == ['User-facing answer 1', 'User-facing answer 2']
                and report['savedAssistantTexts'] == ['User-facing answer 1', 'User-facing answer 2']
                and len(report['providerCalls']) == 4 and len(usage) == 4 and report['totalTokens'] == 24)
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await session.cleanup()
            await app.close()
    for module in (amplifier_module_hook_shell,):
        p = Path(module.__file__)
        report['privateSource'][str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    Path(os.environ['PROBE_OUTPUT']).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    assert report['passed'], 'Auxiliary completion text escaped into public conversation streaming'


asyncio.run(main())
