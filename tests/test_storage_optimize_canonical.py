"""Synthetic owner fixtures only; no user history, live capture or migration."""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

from amplifier_web import storage_optimize_canonical as canonical
from amplifier_web.session_files import project_slug, sessions_dir

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Canonical durability is qualified on POSIX only')


def pixel():
    stream = io.BytesIO()
    Image.new('RGB', (2, 3), (12, 40, 92)).save(stream, format='PNG')
    return stream.getvalue()


def inline_message():
    return {'role': 'tool', 'tool_call_id': 'call-original', 'name': 'read_file',
            'content': [{'type': 'text', 'text': 'Exact original observation'},
                        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                                    'data': base64.b64encode(pixel()).decode()}}],
            'metadata': {'provider': {'opaque': '{\"signature\":\"not an image\"}'},
                         'status': 'completed', 'usage': {'output': 9}}}


@pytest.fixture
def host(tmp_path):
    workspace = str(tmp_path / 'work')
    Path(workspace).mkdir()
    path = sessions_dir(workspace) / 'native-root' / 'transcript.jsonl'
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(json.dumps(inline_message()) + '\n')
    path.chmod(0o600)
    backup = path.with_name('transcript.jsonl.backup')
    backup.write_bytes(path.read_bytes())
    backup.chmod(0o600)
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE history_local_rows(id,native_project,native_id)')
    db.execute('INSERT INTO history_local_rows VALUES(?,?,?)',
               ('public-root', project_slug(workspace), 'native-root'))
    source = {'id': 'public-root', 'workspace': workspace,
              'runtimeSessionId': 'native-root', 'executionRevision': 7, 'interruptionRevision': 4}
    app = SimpleNamespace(db=db, lock=asyncio.Lock(), source=source, leases=0)

    def session(identity, *, hydrate):
        assert identity == 'public-root' and hydrate is False
        return source

    from amplifier_web.runtime import RuntimeManager
    runtime = RuntimeManager()
    original = runtime.stopped_boundary

    @contextmanager
    def fence(value):
        assert app.lock.locked()
        with original(value) as witness:
            assert witness['nativeSessionId'] == 'native-root' and witness['workerAbsent']
            app.leases += 1
            try:
                yield witness
            finally:
                app.leases -= 1

    app._session = session
    app.runtime = SimpleNamespace(stopped_boundary=fence, actual=runtime)
    row = {'owner': 'foundation', 'sessionId': 'native-root', 'kind': 'root',
           '_path': str(path), '_project': project_slug(workspace), '_parent': None,
           '_metadata': {'host': 'amplifier-unified'}, '_stamp': canonical._stamp(path),
           'bytes': path.stat().st_size, 'status': 'held', 'reason': ''}
    operation = {'operationId': 'operation-one', '_owner': 'user', '_authority': None,
                 'extractImages': True, 'status': 'applying'}
    yield app, row, operation, path
    db.close()


@pytest.mark.asyncio
async def test_preview_whole_source_real_lease_no_authority_created(host):
    app, row, operation, path = host
    before = path.read_bytes()
    backup = path.with_name('transcript.jsonl.backup').read_bytes()
    result = await canonical.preview(app, row, operation)
    assert result['status'] == 'held'
    assert result['canonicalRecords'] == result['canonicalInlineImages'] == 1
    assert 'Original image provenance' in result['reason']
    assert result['_canonicalPlan']['source']['sha256'] == hashlib.sha256(before).hexdigest()
    assert len(result['_canonicalPlan']['backups']) == 1
    assert '_action' not in result
    assert path.read_bytes() == before
    assert path.with_name('transcript.jsonl.backup').read_bytes() == backup
    assert not (path.parent / 'media').exists()
    assert not (path.parent / 'recovery').exists()
    assert app.leases == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,owner', [('worker', 'amplifier-unified'), ('unknown', 'amplifier-unified'),
                                      ('root', 'amplifier-cli'), ('root', None)])
async def test_unknown_or_worker_ownership_held(host, kind, owner):
    app, row, operation, path = host
    row.update(kind=kind, _metadata={'host': owner})
    result = await canonical.preview(app, row, operation)
    assert result['status'] == 'held' and 'exclusion' in result['reason']
    assert '_canonicalPlan' not in result and app.leases == 0


@pytest.mark.asyncio
async def test_discovery_source_race_held(host):
    app, row, operation, path = host
    path.write_text(json.dumps({'role': 'user', 'content': 'new source'}) + '\n')
    result = await canonical.preview(app, row, operation)
    assert result['status'] == 'held' and '_canonicalPlan' not in result
    assert app.leases == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [404, 409])
async def test_unavailable_target_lookup_is_held(host, status):
    from amplifier_web.errors import AppError
    app, row, operation, _ = host

    def missing(*_, **__):
        raise AppError('fixture deleted/deleting owner', status)

    app._session = missing
    result = await canonical.preview(app, row, operation)
    assert result['status'] == 'held' and app.leases == 0


@pytest.mark.asyncio
async def test_actual_runtime_active_worker_refuses(host):
    app, row, operation, path = host
    app.runtime.actual._locks['public-root'] = asyncio.Lock()
    await app.runtime.actual._locks['public-root'].acquire()
    result = await canonical.preview(app, row, operation)
    assert result['status'] == 'held' and '_canonicalPlan' not in result
    assert app.leases == 0


@pytest.mark.asyncio
async def test_actual_native_owner_lock_refuses_without_requesting_release(host):
    from amplifier_foundation.session import SharedSessionStore
    app, row, operation, _ = host
    held = SharedSessionStore(app.source['workspace'], row['sessionId']).acquire(app='fixture-other-owner')
    try:
        result = await canonical.preview(app, row, operation)
        assert result['status'] == 'held' and '_canonicalPlan' not in result
        assert app.leases == 0
    finally:
        held.release()


@pytest.mark.asyncio
async def test_giant_single_record_known_owner_string_preview_is_bounded(host):
    import tracemalloc
    app, row, operation, path = host
    with path.open('wb') as stream:
        stream.write(b'{"role":"tool","content":"opaque \\\"json\\\" ')
        for _ in range(256):
            stream.write(b'x' * 65536)
        stream.write(b'","metadata":{"provider":{"signature":"untouched"}}}\n')
    row['_stamp'] = canonical._stamp(path)
    tracemalloc.start()
    try:
        result = await canonical.preview(app, row, operation)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result['canonicalRecords'] == 1 and result['canonicalInlineImages'] == 0
    assert peak < 3 * 1024 * 1024
    assert app.leases == 0


def test_project_and_native_identity_lookup_not_global_alias(host):
    app, row, _, _ = host
    app.db.execute('INSERT INTO history_local_rows VALUES(?,?,?)',
                   ('unrelated', '-another-project', 'native-root'))
    assert canonical._registered(app, row) is app.source
    app.db.execute('INSERT INTO history_local_rows VALUES(?,?,?)',
                   ('ambiguous', row['_project'], row['sessionId']))
    assert canonical._registered(app, row) is None


@pytest.mark.asyncio
async def test_apply_and_resume_do_not_promote_candidate_possession(host):
    app, row, operation, path = host
    directory = canonical._operation_dir(path, operation)
    directory.mkdir(parents=True)
    (directory / 'verified.json').write_text('{}')
    for resume in (False, True):
        result = await canonical.apply(app, row, operation, resume=resume)
        assert result['status'] == 'held' and 'admission' in result['reason']
    assert app.leases == 0 and not (path.parent / 'media').exists()


@pytest.mark.asyncio
async def test_cancel_waits_for_thread_before_fence_release():
    started, finish, cancelled = threading.Event(), threading.Event(), threading.Event()

    def work():
        started.set()
        finish.wait(5)
        assert cancelled.is_set()

    task = asyncio.create_task(canonical._settle_thread(work, cancelled))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # Repeated cancellation must not abandon its writer thread.
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.fixture
def admitted_host(host, monkeypatch):
    """Host lifecycle isolation, not production admission or physical proof."""
    app, row, operation, path = host
    from amplifier_foundation.session import media_conversion as conversion
    from contextlib import nullcontext
    from amplifier_web.host.storage import SessionStore
    plan = conversion.preview(path, session_id=row['sessionId'], owner=app.source['id'],
                              fence=lambda _: nullcontext())
    row.update(_action='canonical-activation', _canonicalPlan=asdict(plan),
               _sourceId=app.source['id'], _sourceBinding=canonical._binding(app.source),
               _sourceSha256=plan.source.sha256)
    # The registry is owned in a separate lane; isolate only its actual _get seam.
    registry = ModuleType('amplifier_web.storage_optimize')
    registry.owner = lambda _: SimpleNamespace(_get=lambda *_: operation)
    monkeypatch.setitem(sys.modules, registry.__name__, registry)
    try:
        from amplifier_foundation.session import media_activation  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, 'amplifier_foundation.session.media_activation',
                            ModuleType('amplifier_foundation.session.media_activation'))
    store = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(SessionStore, 'media', lambda *_: store, raising=False)
    invalidated = []
    app.history = SimpleNamespace(index=SimpleNamespace(_lock=threading.RLock(), _pending_invalidations=set()),
        windows=SimpleNamespace(invalidate=lambda ids: invalidated.append((ids, app.leases))))

    async def refresh(**_):
        assert app.leases == 1 and not app.lock.locked()
        return True

    app.history.refresh_catalog = refresh
    app.invalidated = invalidated
    return host


@pytest.mark.asyncio
async def test_uncertain_activation_invalidates_only_after_thread_settles(admitted_host, monkeypatch):
    app, row, operation, path = admitted_host

    def effect(*_, **kwargs):
        kwargs['admit']()
        assert app.leases == 1
        path.write_text('fixture partial replacement')
        raise OSError('lost checkpoint reply')

    monkeypatch.setattr(canonical, 'activate_candidate', effect)
    result = await canonical.apply(app, row, operation)
    assert result['status'] == 'attempting'
    assert app.invalidated == [({'public-root'}, 1)]
    assert (row['_project'], row['sessionId'], 'transcript') in app.history.index._pending_invalidations
    assert app.leases == 0


@pytest.mark.asyncio
async def test_view_join_pending_is_not_false_checkpoint_failure(admitted_host, monkeypatch):
    app, row, operation, _ = admitted_host

    def effect(*_, **kwargs):
        kwargs['admit']()
        return SimpleNamespace(sha256='a' * 64, bytes=100, records=1)

    async def unavailable(**_):
        return None

    monkeypatch.setattr(canonical, 'activate_candidate', effect)
    app.history.refresh_catalog = unavailable
    result = await canonical.apply(app, row, operation)
    assert result['status'] == 'completed' and result['viewJoin'] == 'pending'
    assert result['canonicalCheckpoint']['durability'] == 'legacy-checkpoint-backed'
    assert app.leases == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stop', 'identity'])
async def test_final_host_admission_refuses_stop_and_source_binding_drift(admitted_host, monkeypatch, change):
    app, row, operation, path = admitted_host
    before = path.read_bytes()

    def effect(*_, **kwargs):
        if change == 'stop':
            operation['status'] = 'cancelling'
        else:
            # Model host state owner-loop mutation rather than a thread editing
            # its SQLite connection or mutable session row.
            app.source['interruptionRevision'] += 1
        kwargs['admit']()
        pytest.fail('Stopped/changed operation reached its physical effect')

    monkeypatch.setattr(canonical, 'activate_candidate', effect)
    result = await canonical.apply(app, row, operation)
    assert result['status'] == 'attempting' and path.read_bytes() == before
    assert app.leases == 0


@pytest.mark.asyncio
async def test_cancelled_partial_effect_invalidates_before_releasing_fence(admitted_host, monkeypatch):
    app, row, operation, path = admitted_host
    started, finish = threading.Event(), threading.Event()

    def effect(*_, **kwargs):
        kwargs['admit']()
        path.write_text('fixture changed canonical bytes before cancellation')
        started.set()
        finish.wait(5)
        assert kwargs['cancelled']()
        assert app.leases == 1 and not app.invalidated
        raise InterruptedError('fixture settled cancelled writer')

    monkeypatch.setattr(canonical, 'activate_candidate', effect)
    task = asyncio.create_task(canonical.apply(app, row, operation))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert app.leases == 1 and not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.invalidated == [({'public-root'}, 1)] and app.leases == 0


@pytest.fixture
def candidate(tmp_path, request):
    from amplifier_foundation.session.media import MediaPolicy, SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_checkpoint_refs
    from amplifier_foundation.session import media_conversion as conversion
    directory = tmp_path / 'qualified-fixture'
    directory.mkdir(mode=0o700)
    path = directory / 'transcript.jsonl'
    message = inline_message()
    with path.open('wb') as stream:
        if getattr(request, 'param', None) == 'huge':
            stream.write(b'{"opaque_provider_state":"')
            for _ in range(256):
                stream.write(b'x' * 65536)
            stream.write(b'",')
            stream.write(json.dumps(message).encode()[1:] + b'\n')
        else:
            stream.write(json.dumps(message).encode() + b'\n')
    path.chmod(0o600)
    backup = path.with_name('transcript.jsonl.backup')
    backup.write_bytes(path.read_bytes())
    backup.chmod(0o600)
    store = SessionMediaStore(directory / 'media', 'fixture-session',
        policy=MediaPolicy(max_bytes=1024 * 1024, max_pixels=1000, max_images=8),
        checkpoint_verifier=verified_checkpoint_refs)
    # Synthetic producer-attested original fixture only. This is NOT evidence of
    # historical user admission, an integration callback, or an enabled UI cohort.
    original = {'kind': 'upload', 'session_id': 'fixture-session', 'id': 'fixture-admission-1',
                'created_at': '2026-01-02T03:04:05+00:00', 'expires_at': None}
    from contextlib import nullcontext
    plan = conversion.preview(path, owner='fixture-owner', session_id=store.session_id,
                              backups=(backup,), fence=lambda _: nullcontext())
    operation = directory / 'candidate-operation'
    asyncio.run(conversion.build_candidate(plan, operation, fence=lambda _: nullcontext(),
        capability=store, origin_for=lambda *_: original,
        asset_reader=lambda ref: store.resolve_ref(ref, committed=False)))
    yield plan, operation, path, store, message
    store.close()


def test_real_f1_activation_and_readback(candidate):
    from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
    plan, directory, path, store, original = candidate
    before_backup = path.with_name('transcript.jsonl.backup').read_bytes()
    result = canonical.activate_candidate(plan, directory, media_store=store,
        guard=NativeTranscriptGuard(path), admit=lambda *_: True)
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    message = json.loads(path.read_text())
    assert message['role'] == original['role'] and message['tool_call_id'] == original['tool_call_id']
    assert message['name'] == original['name']
    assert message['metadata']['provider'] == original['metadata']['provider']
    assert message['metadata']['status'] == original['metadata']['status']
    assert message['metadata']['usage'] == original['metadata']['usage']
    ref = message['metadata']['experimental.media.v1']['refs'][0]
    store.validate_ref(ref, committed=True)
    with store.resolve_ref(ref) as stream:
        assert stream.read() == pixel()
    assert path.with_name('transcript.jsonl.backup').read_bytes() == before_backup
    assert (directory / 'original-0').read_bytes() == before_backup
    assert (directory / 'original-1').read_bytes() == before_backup
    assert b'base64' not in path.read_bytes()


def test_activation_cancelled_does_not_replace(candidate):
    from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
    plan, directory, path, store, _ = candidate
    original = path.read_bytes()
    with pytest.raises((ValueError, InterruptedError)):
        canonical.activate_candidate(plan, directory, media_store=store,
            guard=NativeTranscriptGuard(path), admit=lambda *_: True, cancelled=lambda: True)
    assert path.read_bytes() == original


def test_activation_source_race_preserves_new_source(candidate):
    from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
    plan, directory, path, store, _ = candidate
    guard = NativeTranscriptGuard(path)
    path.write_text('{"role":"user","content":"external edit"}\n')
    with pytest.raises(RuntimeError):
        canonical.activate_candidate(plan, directory, media_store=store, guard=guard, admit=lambda *_: True)
    assert 'external edit' in path.read_text()


def test_real_f1_lost_ack_reconciles_without_second_replace(candidate, monkeypatch):
    from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
    plan, directory, path, store, _ = candidate
    acknowledge = store.acknowledge_checkpoint

    def lost_ack(receipt):
        acknowledge(receipt)
        raise OSError('fixture: acknowledgement reply lost after actual store commit')

    monkeypatch.setattr(store, 'acknowledge_checkpoint', lost_ack)
    with pytest.raises(OSError):
        canonical.activate_candidate(plan, directory, media_store=store,
            guard=NativeTranscriptGuard(path), admit=lambda *_: True)
    installed = path.read_bytes()
    assert b'base64' not in installed
    monkeypatch.setattr(store, 'acknowledge_checkpoint', acknowledge)
    replace = os.replace

    def reject_transcript_replay(source, target, *args, **kwargs):
        assert Path(target).name != 'transcript.jsonl', 'Unknown transcript replacement was replayed'
        return replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', reject_transcript_replay)
    result = canonical.activate_candidate(plan, directory, media_store=store,
        guard=NativeTranscriptGuard(path), admit=lambda *_: True, resume=True)
    assert result.sha256 == hashlib.sha256(installed).hexdigest()
    assert path.read_bytes() == installed
    ref = json.loads(path.read_text())['metadata']['experimental.media.v1']['refs'][0]
    store.validate_ref(ref, committed=True)


def test_activation_backup_race_preserves_every_original(candidate):
    from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
    plan, directory, path, store, _ = candidate
    guard = NativeTranscriptGuard(path)
    before = path.read_bytes()
    path.with_name('transcript.jsonl.backup').write_bytes(b'new backup evidence')
    with pytest.raises(RuntimeError):
        canonical.activate_candidate(plan, directory, media_store=store, guard=guard, admit=lambda *_: True)
    assert path.read_bytes() == before
    assert path.with_name('transcript.jsonl.backup').read_bytes() == b'new backup evidence'


@pytest.mark.parametrize('candidate', ['huge'], indirect=True)
def test_huge_actual_f1_activation_and_checkpoint_no_whole_row(candidate):
    import tracemalloc
    from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
    plan, directory, path, store, _ = candidate
    tracemalloc.start()
    try:
        result = canonical.activate_candidate(plan, directory, media_store=store,
            guard=NativeTranscriptGuard(path), admit=lambda *_: True)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.bytes > 16 * 1024 * 1024 and result.records == 1
    assert peak < 3 * 1024 * 1024
    assert path.stat().st_mtime_ns == plan.source.mtime_ns
    print(f'canonical fixture={plan.source.size} installed={result.bytes} tracked_peak={peak}')


@pytest.mark.parametrize('effect_happened', [False, True])
def test_uncertain_replace_is_reconciled_never_replayed(candidate, monkeypatch, effect_happened):
    from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
    plan, directory, path, store, _ = candidate
    before = path.read_bytes()
    replace = os.replace
    calls = []

    def uncertain(source, target, *args, **kwargs):
        if Path(target).name == 'transcript.jsonl':
            calls.append('replace')
            if effect_happened:
                replace(source, target, *args, **kwargs)
            raise OSError('fixture: replacement outcome unknown')
        return replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', uncertain)
    with pytest.raises(OSError):
        canonical.activate_candidate(plan, directory, media_store=store,
            guard=NativeTranscriptGuard(path), admit=lambda: True)
    if effect_happened:
        result = canonical.activate_candidate(plan, directory, media_store=store,
            guard=NativeTranscriptGuard(path), admit=lambda: True, resume=True)
        assert result.state == 'acknowledged' and path.read_bytes() != before
    else:
        with pytest.raises(canonical.CanonicalHeld, match='unconfirmed'):
            canonical.activate_candidate(plan, directory, media_store=store,
                guard=NativeTranscriptGuard(path), admit=lambda: True, resume=True)
        assert path.read_bytes() == before
    assert calls == ['replace']
