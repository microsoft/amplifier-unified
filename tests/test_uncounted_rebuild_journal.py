"""Candidate graph: real manager, subprocess worker, Core and loops, offline SDK."""
import asyncio
import copy
import inspect
import json
from pathlib import Path
import sys
import socket
import os

import pytest
from amplifier_web.runtime import RuntimeManager
from amplifier_web.context_continuity import digest
from amplifier_web.session_health import generation_failure
from amplifier_web.uncounted_rebuild import validate

A = {'instance': 'openai', 'model': 'gpt-6.1-sol', 'effort': 'high'}
B = {'instance': 'openai', 'model': 'gpt-6.1-astra', 'effort': 'high'}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    import test_context_continuity  # Load provider annotations before the SDK fixture.
    import openai
    constructor = openai.AsyncOpenAI
    def local_client(*args, **kwargs):
        kwargs['base_url'] = 'http://127.0.0.1:9/v1'
        return constructor(*args, **kwargs)
    monkeypatch.setattr(openai, 'AsyncOpenAI', local_client)
    connect = socket.socket.connect
    def local_socket(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('No model network call is permitted')
        return connect(self, address)
    monkeypatch.setattr(socket.socket, 'connect', local_socket)
    for key in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(key, 'http://127.0.0.1:9')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')


def candidate_context():
    from amplifier_module_context_managed.boundary import BoundaryContextManager
    if 'journal' not in inspect.signature(BoundaryContextManager._summarize).parameters:
        if os.environ.get('UNCOUNTED_REBUILD_REQUIRE_CONTEXT') == '1':
            pytest.fail('The required Context candidate is not installed')
        pytest.skip('Needs the linked Context summary-journal PR; no unmerged pin is committed')


@pytest.mark.parametrize('record', [
    {'uncountedVersion': 2}, {'uncountedVersion': True}, {'uncountedVersion': 1, 'summarySource': None}])
def test_unknown_or_malformed_rebuild_version_is_not_an_empty_journal(record):
    with pytest.raises((ValueError, TypeError, KeyError, AttributeError)):
        validate(record)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['before-second', 'kill-reserved', 'kill-response', 'kill-confirmed',
                                    'transport', 'save-failed', 'limit',
                                    'changed-source', 'changed-model', 'changed-connection', 'old-journal',
                                    *['stop-' + str(n) for n in range(1, 9)]])
async def test_rebuild_across_real_worker_lifecycle(tmp_path, monkeypatch, failure):
    candidate_context()
    for key in ('AMPLIFIER_WEB_HOME', 'AMPLIFIER_HOME', 'AMPLIFIER_SHARED_HOME', 'AMPLIFIER_WORKSPACES_HOME'):
        monkeypatch.setenv(key, str(tmp_path / key))
    for key in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(key, 'http://127.0.0.1:9')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    fixture = Path(__file__).parent / 'fixtures' / 'uncounted_rebuild_worker.py'
    events = []
    terminals = asyncio.Queue()
    ended = asyncio.Queue()
    session = {'id': 'ordinary-recovery', 'workspace': str(tmp_path), 'selection': copy.deepcopy(A),
               'interruptionRevision': 0, 'modelConnectionKey': 'same-account'}
    async def emit(kind, data):
        events.append((kind, copy.deepcopy(data)))
        if kind == 'runtime.modelSelectionApplied':
            session['selection'] = session.pop('pendingModelSelection')
        if kind == 'runtime.generation' and data.get('event') in {'generation.finished', 'generation.failed'}:
            if set(data.get('input_ids', [])) & {'switch', 'rebuild', 'continue'}:
                terminals.put_nowait(data)
        if kind == 'runtime.ended':
            ended.put_nowait(data)
    def manager(mode):
        item = RuntimeManager(command=[sys.executable, '-B', str(fixture), str(tmp_path), mode], startup_timeout=30)
        item.home = tmp_path / 'runtime-home'
        item.retention.wake = lambda: None
        return item
    def requests():
        path = tmp_path / 'requests.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    runtime = manager('normal')
    try:
        await runtime.start(session, emit)
        from amplifier_foundation.session.shared_state import SharedSessionStore, SessionBusyError
        with pytest.raises(SessionBusyError):
            SharedSessionStore(tmp_path, session['id'], root=tmp_path / 'ownership').acquire(app='contender')
        session['pendingModelSelection'] = copy.deepcopy(B)
        assert (await runtime.send(session, 'Continue with the selected model.', 'switch', emit))['accepted']
        terminal = await asyncio.wait_for(terminals.get(), 20)
        assert terminal['event'] == 'generation.failed'
        await asyncio.wait_for(ended.get(), 20)
        source = (tmp_path / 'native' / 'context-checkpoint.json').read_bytes()
        original_messages = json.loads((tmp_path / 'native' / 'transcript.json').read_text())
        await runtime.close()
        fault = failure.startswith('changed-') or failure == 'old-journal'
        runtime = manager('before-second' if fault else failure)
        before = len(requests())
        assert (await runtime.send(session, 'Continue with the selected model.', 'rebuild', emit))['accepted']
        if not failure.startswith(('kill-', 'stop-')):
            terminal = await asyncio.wait_for(terminals.get(), 20)
            assert terminal['event'] == 'generation.failed'
        await asyncio.wait_for(ended.get(), 20)
        first_requests = requests()[before:]
        assert all(row['summary'] for row in first_requests)
        recovery = tmp_path / 'native' / 'context-recovery.json'
        if failure.startswith('stop-'):
            if recovery.exists():
                journal = json.loads(recovery.read_text())
                validate(journal['attempts'][journal['current']])
            assert len(first_requests) <= (0 if int(failure[5:]) <= 4 else 1)
            assert (tmp_path / 'native' / 'context-checkpoint.json').read_bytes() == source
            effects = [json.loads(line) for line in (tmp_path / 'effects.jsonl').read_text().splitlines()]
            assert sum(row['effects'] for row in effects) == 1
            assert all(row['unknown'] == 0 for row in effects)
            return
        journal = json.loads(recovery.read_text())
        record = journal['attempts'][journal['current']]
        validate(record)
        if failure in {'before-second', 'kill-confirmed'} or fault:
            assert len(record['calls']) == record['completedParts'] == 1
        elif failure == 'limit':
            assert len(record['calls']) == record['completedParts'] == record['maxCalls'] == 2
        else:
            assert record['calls'][-1]['state'] == 'reserved'
        await runtime.close()
        if failure == 'old-journal':
            record['uncountedVersion'] = 99
            recovery.write_text(json.dumps(journal))
        elif failure == 'changed-source':
            checkpoint = json.loads(source)
            checkpoint['summary']['text'] += ' Changed by another writer.'
            checkpoint['sha256'] = digest({k: v for k, v in checkpoint.items() if k != 'sha256'})
            source = json.dumps(checkpoint).encode()
            (tmp_path / 'native' / 'context-checkpoint.json').write_bytes(source)
        elif failure == 'changed-model':
            session['pendingModelSelection'] = {**B, 'model': 'gpt-6.1-other'}
        runtime = manager('limit' if failure == 'limit' else
                          'changed-connection' if failure == 'changed-connection' else 'normal')
        before_next = len(requests())
        if failure == 'changed-connection':
            from amplifier_web.runtime import RuntimeStartupError
            with pytest.raises(RuntimeStartupError, match='saved connection'):
                await runtime.send(session, 'Continue with the selected model.', 'continue', emit)
            assert len(requests()) == before_next
            assert (tmp_path / 'native' / 'context-checkpoint.json').read_bytes() == source
            return
        assert (await runtime.send(session, 'Continue with the selected model.', 'continue', emit))['accepted']
        final = await asyncio.wait_for(terminals.get(), 20)
        if failure in {'before-second', 'kill-confirmed'}:
            assert final['event'] == 'generation.finished', events[-8:]
            later = requests()[before_next:]
            hashes = {row['hash'] for row in first_requests}
            assert not hashes & {row['hash'] for row in later}
            assert len([row for row in later if row['summary']]) == 11
            assert len([row for row in later if not row['summary']]) == 1
        else:
            assert final['event'] == 'generation.failed'
            await asyncio.wait_for(ended.get(), 20)
            assert len(requests()) == before_next
            captures = [payload for kind, payload in events if kind == 'runtime.error']
            serialized = json.dumps(captures)
            expected = 'transition_budget_exhausted' if failure == 'limit' else 'recovery_outcome_unknown'
            if not fault:
                assert expected in serialized
            public = generation_failure({'error_category': 'context_compaction', 'error_code': expected})
            assert public['retryable'] is False and public['replayed'] is False
            assert 'Synthetic' not in json.dumps(public)
        assert (tmp_path / 'native' / 'context-checkpoint.json').read_bytes() == source
        await runtime.close()
        saved = json.loads((tmp_path / 'native' / 'transcript.json').read_text())
        assert saved[:len(original_messages)] == original_messages
        effects = [json.loads(line) for line in (tmp_path / 'effects.jsonl').read_text().splitlines()]
        assert sum(row['effects'] for row in effects) == 1
        assert all(row['unknown'] == 0 for row in effects)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('counted', [False, True])
@pytest.mark.parametrize('checkpoint', [False, True])
async def test_ordinary_no_transition_has_same_calls_and_files_with_or_without_callback(tmp_path, counted, checkpoint):
    candidate_context()
    from test_context_continuity import ordinary_recovery_case, RecoveryWire, append_recovery_evidence
    results = []
    for enabled in (False, True):
        wire = RecoveryWire()
        directory = tmp_path / str(enabled)
        async with ordinary_recovery_case(directory, wire,
                provider_config={'base_url': 'http://127.0.0.1:9/v1'}) as case:
            if not counted:
                case.raw.request_budget = None
            case.raw.supports_native_compaction = lambda: False
            if not enabled:
                case.session.coordinator.register_capability('context.summary_journal', None)
            await case.session.execute('SEED-EFFECT: preserve ORBIT.')
            if checkpoint:
                await append_recovery_evidence(case.context, ['first'])
                await case.session.execute('Establish saved same-model note.')
                await case.checkpoint()
            await append_recovery_evidence(case.context, ['extra-' + str(i) for i in range(5)])
            await case.session.execute('Continue with the same model.')
            await case.checkpoint()
            results.append(([digest(body) for body in wire.summaries],
                            [digest(body) for body in wire.completions],
                            sorted(p.name for p in directory.iterdir()),
                            wire.effects, wire.unknown_invocations))
            assert not case.adapter.recovery_path.exists()
    assert results[0] == results[1]
