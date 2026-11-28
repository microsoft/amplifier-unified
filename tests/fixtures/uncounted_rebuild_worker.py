"""Offline process fixture: real Worker lifecycle, synthetic bootstrap and model."""
import asyncio
import copy
import json
import os
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_context_continuity import ordinary_recovery_case, RecoveryWire, append_recovery_evidence
from amplifier_web.runtime_worker import Worker, publish, configure_protocol
from amplifier_web.context_continuity import digest
from amplifier_foundation.session.shared_state import SharedSessionStore
import openai

A = {'instance': 'openai', 'model': 'gpt-6.1-sol', 'effort': 'high'}
B = {'instance': 'openai', 'model': 'gpt-6.1-astra', 'effort': 'high'}


def forbid_network():
    connect = socket.socket.connect
    def checked(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('Model network traffic is forbidden')
        return connect(self, address)
    socket.socket.connect = checked
    constructor = openai.AsyncOpenAI
    def local_sdk(*args, **kwargs):
        kwargs['base_url'] = 'http://127.0.0.1:9/v1'
        return constructor(*args, **kwargs)
    openai.AsyncOpenAI = local_sdk


class RebuildWorker(Worker):
    async def start(self, config):
        try:
            self.start_config = config
            self.directory = Path(sys.argv[1])
            self.mode = sys.argv[2]
            self.wire = RecoveryWire()
            self.wire.reducing_models.add(B['model'])
            self.shared_store = SharedSessionStore(self.directory, config['id'], root=self.directory / 'ownership')
            self.shared_handle = self.shared_store.acquire(app='amplifier-unified', fixture=True)
            saved = self.directory / 'native' / 'transcript.json'
            messages = json.loads(saved.read_text()) if saved.exists() else []
            self.fixture = ordinary_recovery_case(self.directory / 'native', self.wire, messages,
                live=True, selection=config.get('selection') or A,
                provider_config={'base_url': 'http://127.0.0.1:9/v1'})
            self.case = await self.fixture.__aenter__()
            case = self.case
            case.raw.request_budget = None
            case.raw.supports_native_compaction = lambda: False
            self.session, self.runtime, self.controls = case.session, case.runtime, case.controls
            self.activation_gate, self.activation = case.gate, case.activation
            case.runtime.capture_activation = case.gate.current
            case.runtime.observer = self.observe
            coordinator = self.session.coordinator
            coordinator.register_capability('web.context_transition.input', self.transition_input)
            coordinator.register_capability('web.provider_catalog', {'connectionKeys': {
                'openai': 'other-account' if self.mode == 'changed-connection' else 'same-account'}})
            original_guard = case.adapter.writer_guard
            def writer_guard():
                original_guard()
                self.shared_handle.check()
            case.adapter.writer_guard = writer_guard
            await self.controls.restore()
            original_transport = self.wire.transport
            async def observed(request):
                body = json.loads(request.content)
                summary = (request.url.path.endswith('/responses') and not body.get('tools')
                           and 'Prepare a factual continuation note' in str(body.get('instructions')))
                journal = case.adapter.recovery_path
                if summary and journal.exists():
                    record = json.loads(journal.read_text())
                    current = record['attempts'][record['current']]
                    assert current['calls'][-1]['state'] == 'reserved'
                    if self.mode == 'kill-reserved':
                        os._exit(23)  # Exact fixture process; no effect is retried.
                    if self.mode == 'transport' and self.wire.summaries:
                        import httpx
                        raise httpx.ConnectError('Synthetic unreturned request', request=request)
                response = await original_transport(request)
                if request.url.path.endswith('/responses'):
                    with (self.directory / 'requests.jsonl').open('a') as stream:
                        stream.write(json.dumps({'pid': os.getpid(), 'summary': summary,
                            'hash': digest(body), 'model': body.get('model')}) + '\n')
                if summary and self.mode == 'kill-response' and journal.exists():
                    os._exit(24)
                return response
            case.raw.client._client._transport.handler = observed
            self.capture_manager_failures()
            token = case.gate.bind(case.activation)
            try:
                self.execution = asyncio.create_task(case.session.execute(''))
            finally:
                case.gate.reset(token)
            self.execution.add_done_callback(self.executed)
            if not messages:
                async def seed(identity, text):
                    await self._command_serial({'op': 'send', 'input_id': identity,
                        'text': text, 'interruptionRevision': 0})
                    return await case.runtime.wait_for(lambda event: event['type'] in {
                        'generation.finished', 'generation.failed'} and identity in event.get('input_ids', []), timeout=15)
                assert (await seed('seed', 'SEED-EFFECT: preserve ORBIT.'))['type'] == 'generation.finished'
                await append_recovery_evidence(case.context, [str(i) for i in range(12)])
                assert (await seed('source', 'Establish source checkpoint.'))['type'] == 'generation.finished'
                await case.checkpoint()
                record = json.loads(case.adapter.path.read_bytes())
                record.pop('unifiedPortableOrigin', None)
                record['sha256'] = digest({k: v for k, v in record.items() if k != 'sha256'})
                case.adapter.path.write_text(json.dumps(record))
                case.adapter._accepted_record = copy.deepcopy(record)
            if self.mode == 'before-second':
                # A local preparation failure BEFORE another reservation is not
                # a vendor error with an unknown result. The first save stands.
                import amplifier_module_context_managed.boundary as boundary
                fits = boundary.request_fits
                async def refuse_second(*args, **kwargs):
                    if case.adapter.recovery_path.exists() and self.wire.summaries:
                        raise OSError('Synthetic preparation fault; no next request sent')
                    return await fits(*args, **kwargs)
                boundary.request_fits = refuse_second
            if self.mode == 'save-failed':
                from amplifier_web import context_continuity
                write = context_continuity.write_private
                def fail_save(path, text):
                    if path == case.adapter.recovery_path:
                        rows = json.loads(text)['attempts'].values()
                        if any('text' in call for row in rows for call in row.get('calls', [])):
                            raise OSError('Synthetic confirmed-result save failure')
                    return write(path, text)
                context_continuity.write_private = fail_save
            if self.mode == 'kill-confirmed':
                original_write = case.adapter._write_recovery
                def kill_confirmed():
                    original_write()
                    row = case.adapter._recovery_record()
                    if row.get('completedParts') == 1:
                        os._exit(25)
                case.adapter._write_recovery = kill_confirmed
            if self.mode.startswith('stop-'):
                from amplifier_web import uncounted_rebuild
                guarded = uncounted_rebuild._guarded
                count = 0
                async def stop_at_await(*args, **kwargs):
                    nonlocal count
                    result = await guarded(*args, **kwargs)
                    count += 1
                    if count == int(self.mode.removeprefix('stop-')):
                        self.wire.epoch += 1
                        self.wire.stop_revision += 1
                        await self.command({'op': 'stop'})
                    return result
                uncounted_rebuild._guarded = stop_at_await
            if self.mode == 'limit':
                case.context.config['summary_max_calls'] = 2
            publish({'type': 'runtime.ready', 'report': {'session_id': config['id']},
                'workerIdentity': {'sessionId': config['id'], 'pid': os.getpid()}})
        except BaseException:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.shutdown.set()
            raise

    async def checkpoint_terminal_outcome(self):
        if hasattr(self, 'case'):
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
            if hasattr(self, 'wire'):
                with (self.directory / 'effects.jsonl').open('a') as stream:
                    stream.write(json.dumps({'effects': self.wire.effects, 'unknown': self.wire.unknown_invocations}) + '\n')
            if hasattr(self, 'fixture'):
                await self.fixture.__aexit__(None, None, None)


if __name__ == '__main__':
    forbid_network()
    configure_protocol()
    asyncio.run(RebuildWorker().run())
