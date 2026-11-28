"""Offline subprocess for the model-change lifecycle acceptance tests.

Adapted from W7's read-only probe. Bootstrap and checkpoint ownership are fixture
seams; Worker.run/read/command/executed and RuntimeManager routing remain real.
The closing marker holds only the failing coroutine's return to its done callback
so the parent can test admission while the runtime is already closed. It never
changes the result of admission or substitutes a second execution policy.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from test_context_continuity import (
    ordinary_recovery_case, RecoveryWire, append_recovery_evidence,
    RECOVERY_BUDGET, RECOVERY_TRIGGER,
)
from amplifier_web.runtime_worker import Worker, publish, configure_protocol
from amplifier_web.context_continuity import digest
import openai

# Even a broken mock cannot send a provider request. The real SDK also receives
# a loopback base URL, made-up key, zero retries and httpx.MockTransport.
original_connect = socket.socket.connect

def no_network(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise AssertionError('Network is forbidden in the lifecycle fixture')
    return original_connect(self, address)

socket.socket.connect = no_network
original_sdk = openai.AsyncOpenAI

def offline_sdk(*args, **kwargs):
    kwargs['base_url'] = 'http://127.0.0.1:9/v1'
    return original_sdk(*args, **kwargs)

openai.AsyncOpenAI = offline_sdk
DIRECTORY = Path(sys.argv[1])
OVERLAY = sys.argv[2] == 'unified-defaults'
A = {'instance': 'openai', 'model': 'gpt-6.1-sol', 'effort': 'high'}
B = {'instance': 'openai', 'model': 'gpt-6.1-astra', 'effort': 'high'}


class OfflineWorker(Worker):
    async def start(self, config):
        self.start_config = config
        self.wire = RecoveryWire()
        self.wire.reducing_models.add(B['model'])
        saved = DIRECTORY / 'native' / 'transcript.json'
        messages = json.loads(saved.read_text(encoding='utf-8')) if saved.exists() else []
        selection = config.get('selection') or A
        context_config = None
        if OVERLAY:
            from amplifier_web.host.session import live_plan
            plan, _ = live_plan({'session': {
                'orchestrator': {'module': 'loop-streaming'},
                'context': {'module': 'context-managed', 'config': {
                    'max_tokens': RECOVERY_BUDGET, 'summarize_trigger': RECOVERY_TRIGGER,
                    'native_min_new_tokens': 0, 'compaction_notice_enabled': False}}}})
            context_config = plan['session']['context']['config']
        self.fixture = ordinary_recovery_case(DIRECTORY / 'native', self.wire, messages,
            live=True, selection=selection, context_config=context_config,
            provider_config={'base_url': 'http://127.0.0.1:9/v1'})
        self.case = await self.fixture.__aenter__()
        case = self.case
        case.raw.request_budget = None
        case.raw.supports_native_compaction = lambda: False
        self.session, self.runtime, self.controls = case.session, case.runtime, case.controls
        self.activation_gate, self.activation = case.gate, case.activation
        self.shared_handle = SimpleNamespace(owner=None, release=lambda: None)
        case.runtime.capture_activation = case.gate.current
        case.runtime.observer = self.observe
        coordinator = self.session.coordinator
        coordinator.register_capability('web.context_transition.input', self.transition_input)
        coordinator.register_capability('web.provider_catalog', {'connectionKeys': {'openai': 'same-account'}})
        original_transport = self.wire.transport

        async def observe_request(request):
            response = await original_transport(request)
            body = json.loads(request.content)
            if request.url.path.endswith('/responses'):
                summary = not body.get('tools') and 'Prepare a factual continuation note' in str(body.get('instructions'))
                with (DIRECTORY / 'requests.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'pid': os.getpid(), 'summary': summary,
                        'hash': digest(body), 'model': body.get('model')}) + '\n')
            return response

        case.raw.client._client._transport.handler = observe_request
        self.capture_manager_failures()
        token = case.gate.bind(case.activation)
        try:
            self.execution = asyncio.create_task(self.execute_fixture())
        finally:
            case.gate.reset(token)
        self.execution.add_done_callback(self.executed)
        if not messages:
            async def seed(identity, text):
                await self._command_serial({'op': 'send', 'input_id': identity,
                    'text': text, 'interruptionRevision': 0})
                return await case.runtime.wait_for(lambda event: event['type'] in {
                    'generation.finished', 'generation.failed'} and identity in event.get('input_ids', []), timeout=20)
            assert (await seed('seed', 'SEED-EFFECT: preserve ORBIT.'))['type'] == 'generation.finished'
            await append_recovery_evidence(case.context, [str(i) for i in range(12)])
            assert (await seed('source', 'Establish source checkpoint.'))['type'] == 'generation.finished'
            await case.checkpoint()
            record = json.loads(case.adapter.path.read_bytes())
            assert record['summary']
            record.pop('unifiedPortableOrigin', None)
            record['sha256'] = digest({k: v for k, v in record.items() if k != 'sha256'})
            case.adapter.path.write_text(json.dumps(record), encoding='utf-8')
            case.adapter._accepted_record = copy.deepcopy(record)
        elif (DIRECTORY / 'over-call-limit').exists():
            assert case.context.config.get('summary_max_calls', 16) == 16
            await append_recovery_evidence(case.context, ['extra-' + str(i) for i in range(24)])
        publish({'type': 'runtime.ready', 'report': {'session_id': config['id']},
            'workerIdentity': {'sessionId': config['id'], 'pid': os.getpid()}})

    async def execute_fixture(self):
        try:
            return await self.session.execute('')
        except Exception:
            if (DIRECTORY / 'hold-close').exists():
                assert self.runtime.closed
                (DIRECTORY / 'closing').write_text(str(os.getpid()), encoding='utf-8')
                async with asyncio.timeout(20):
                    while not (DIRECTORY / 'release-close').exists():
                        await asyncio.sleep(.01)
            raise

    async def checkpoint_terminal_outcome(self):
        if hasattr(self, 'case'):
            token = self.activation_gate.bind(self.activation)
            try:
                await self.case.checkpoint()
            finally:
                self.activation_gate.reset(token)
        return True

    async def checkpoint_idle(self):
        if self.runtime is None or self.runtime.generation or self.runtime.queued_inputs:
            return False
        token = self.activation_gate.bind(self.activation)
        try:
            await self.case.checkpoint()
        finally:
            self.activation_gate.reset(token)
        return True

    async def _run(self):
        try:
            await super()._run()
        finally:
            if hasattr(self, 'fixture'):
                (DIRECTORY / f'sentinels-{os.getpid()}.json').write_text(json.dumps({
                    'effects': self.wire.effects, 'unknown': self.wire.unknown_invocations}), encoding='utf-8')
                await self.fixture.__aexit__(None, None, None)


if __name__ == '__main__':
    configure_protocol()
    asyncio.run(OfflineWorker().run())
