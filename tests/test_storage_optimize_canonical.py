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


@pytest.fixture
def committed_candidate(tmp_path):
    """Real synthetic admission; not evidence about old user-image authority."""
    from contextlib import closing
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session import media_conversion as conversion
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding

    tmp_path.chmod(0o700)
    with closing(VersionedSessionBinding.create_new(
        tmp_path / 'native-root', session_id='native-root',
        operation_id='initial', admit=lambda: None,
    )) as binding:
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            @contextmanager
            def fence(owner):
                assert owner == 'public-root'
                with binding.publication():
                    yield

            original = inline_message()
            binding.append([original], expected=binding.expected,
                operation_id='fixture-observation', admit=lambda: None)
            expected = binding.expected
            original_bytes = (binding.directory / 'transcript.v2' / expected.body).read_bytes()
            plan = conversion.preview_committed(binding.directory, expected=expected,
                owner='public-root', fence=fence)
            origin = {'kind': 'tool', 'session_id': binding.session_id,
                'id': 'call-original', 'created_at': '2025-01-01T00:00:00+00:00',
                'expires_at': '2035-01-01T00:00:00+00:00'}
            directory = tmp_path / 'operation'
            asyncio.run(conversion.build_candidate(plan, directory, fence=fence,
                capability=store, eligible_record=lambda row: row == 0,
                origin_for=lambda row, block: origin,
                asset_reader=lambda ref: store.resolve_ref(ref, committed=False)))
            yield plan, directory, binding, store, original, original_bytes


def test_committed_actual_binding_dispatch_typed_ack_and_pixel_readback(committed_candidate):
    from amplifier_foundation.session.transcript_source import open_committed_source
    from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt
    plan, directory, binding, store, original, original_bytes = committed_candidate
    result = canonical.activate_candidate(plan, directory, media_store=store,
        binding=binding, operation_id='optimize-original', admit=lambda: True)
    assert result.state == 'acknowledged' and result.inline_removed_bytes > 0
    assert type(result.checkpoint) is VersionedCheckpointReceipt
    assert result.commit == binding.expected
    assert result.checkpoint.verify() == {
        **asdict(result.commit), 'kind': 'versioned-checkpoint-backed-v1'}
    with open_committed_source(binding.directory, expected=result.commit) as source:
        actual = source.window(0, 1).messages[0]
    assert actual['role'] == original['role']
    assert actual['tool_call_id'] == original['tool_call_id']
    assert actual['metadata']['provider'] == original['metadata']['provider']
    ref = actual['metadata']['experimental.media.v1']['refs'][0]
    with store.resolve_ref(ref) as image:
        assert image.read() == pixel()
    assert ref['provenance']['expires_at'] == '2035-01-01T00:00:00+00:00'
    assert b'base64' not in (binding.directory / 'transcript.v2' / result.commit.body).read_bytes()
    assert (binding.directory / 'transcript.v2' / plan.committed_prefix['body']).read_bytes() == original_bytes


@pytest.mark.parametrize('defect', ['missing-binding', 'missing-operation', 'changed-plan'])
def test_committed_dispatch_refuses_incomplete_or_changed_selection(committed_candidate, defect):
    from dataclasses import replace
    plan, directory, binding, store, _, before = committed_candidate
    selected = replace(plan, inline_images=plan.inline_images + 1) if defect == 'changed-plan' else plan
    with pytest.raises((canonical.CanonicalHeld, ValueError)):
        canonical.activate_candidate(selected, directory, media_store=store,
            binding=None if defect == 'missing-binding' else binding,
            operation_id=None if defect == 'missing-operation' else 'optimize-original',
            admit=lambda: True)
    assert (binding.directory / 'transcript.v2' / binding.expected.body).read_bytes() == before
    assert not (directory / 'committed-activation.json').exists()


def test_committed_dispatch_cancel_before_effect_preserves_source(committed_candidate):
    plan, directory, binding, store, _, before = committed_candidate
    with pytest.raises(InterruptedError):
        canonical.activate_candidate(plan, directory, media_store=store, binding=binding,
            operation_id='optimize-original', admit=lambda: True, cancelled=lambda: True)
    assert (binding.directory / 'transcript.v2' / binding.expected.body).read_bytes() == before
    assert not (directory / 'committed-activation.json').exists()


def test_committed_dispatch_rechecks_source_before_write(committed_candidate):
    plan, directory, binding, store, _, before = committed_candidate
    binding.append([{'role': 'user', 'content': 'newer actual source'}],
        expected=binding.expected, operation_id='later', admit=lambda: None)
    current = binding.expected
    current_bytes = (binding.directory / 'transcript.v2' / current.body).read_bytes()
    assert current_bytes[:plan.committed_prefix['committed_bytes']] == before
    with pytest.raises((ValueError, RuntimeError)):
        canonical.activate_candidate(plan, directory, media_store=store, binding=binding,
            operation_id='optimize-original', admit=lambda: True)
    assert binding.expected == current
    assert (binding.directory / 'transcript.v2' / current.body).read_bytes() == current_bytes


def test_committed_dispatch_current_admission_refusal_precedes_intent(committed_candidate):
    plan, directory, binding, store, _, before = committed_candidate
    with pytest.raises(ValueError, match='admission'):
        canonical.activate_candidate(plan, directory, media_store=store, binding=binding,
            operation_id='optimize-original', admit=lambda: False)
    assert (binding.directory / 'transcript.v2' / binding.expected.body).read_bytes() == before
    assert not (directory / 'committed-activation.json').exists()


def test_committed_dispatch_unknown_no_effect_does_not_replay(committed_candidate, monkeypatch):
    plan, directory, binding, store, _, before = committed_candidate
    calls = []

    def uncertain(*args, **kwargs):
        calls.append(1)
        raise OSError('fixture write never started but caller cannot infer that')

    monkeypatch.setattr(binding, 'rewrite_serialized', uncertain)
    with pytest.raises(OSError):
        canonical.activate_candidate(plan, directory, media_store=store, binding=binding,
            operation_id='optimize-original', admit=lambda: True)
    with pytest.raises(canonical.CanonicalHeld, match='unconfirmed'):
        canonical.activate_candidate(plan, directory, media_store=store, binding=binding,
            operation_id='optimize-original', admit=lambda: True, resume=True)
    assert calls == [1]
    assert (binding.directory / 'transcript.v2' / binding.expected.body).read_bytes() == before


def test_committed_cold_resume_keeps_original_predecessor_without_rewrite(committed_candidate, monkeypatch):
    from contextlib import closing
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_foundation.session.transcript_source import open_committed_source
    plan, directory, binding, store, _, before = committed_candidate
    actual = binding.rewrite_serialized
    calls = []

    def lost(*args, **kwargs):
        calls.append(1)
        actual(*args, **kwargs)
        raise OSError('fixture reply lost after real commit')

    monkeypatch.setattr(binding, 'rewrite_serialized', lost)
    with pytest.raises(OSError, match='reply lost'):
        canonical.activate_candidate(plan, directory, media_store=store, binding=binding,
            operation_id='optimize-original', admit=lambda: True)
    current, session_dir = binding.expected, binding.directory
    store.close()
    binding.close()
    with closing(VersionedSessionBinding.resume(session_dir,
            session_id='native-root', expected=current)) as fresh:
        with closing(SessionMediaStore(session_dir / 'media', 'native-root',
                checkpoint_verifier=verified_media_checkpoint_refs)) as media:
            def forbidden(*args, **kwargs):
                raise AssertionError('Cold reconciliation must not rewrite a second time')
            monkeypatch.setattr(fresh, 'rewrite_serialized', forbidden)
            result = canonical.activate_candidate(plan, directory, media_store=media,
                binding=fresh, operation_id='optimize-original', admit=lambda: True,
                resume=True)
            assert result.state == 'acknowledged' and result.commit == current
            assert calls == [1]
            assert asdict(current) != plan.committed_prefix
            with open_committed_source(session_dir, expected=current) as source:
                ref = source.window(0, 1).messages[0]['metadata']['experimental.media.v1']['refs'][0]
            with media.resolve_ref(ref) as image:
                assert image.read() == pixel()
            assert (session_dir / 'transcript.v2' / plan.committed_prefix['body']).read_bytes() == before


@pytest.mark.asyncio
async def test_committed_ordinary_preview_inventories_but_does_not_create_admission(host):
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_web.storage_optimize_owners import OwnerAdapters
    app, row, operation, path = host
    # Replace only the synthetic legacy fixture with a genuinely enrolled root.
    path.unlink()
    path.with_name('transcript.jsonl.backup').unlink()
    path.parent.rmdir()
    path.parent.parent.chmod(0o700)
    binding = VersionedSessionBinding.create_new(path.parent,
        session_id=row['sessionId'], operation_id='initial', admit=lambda: None)
    try:
        binding.append([inline_message()], expected=binding.expected,
            operation_id='fixture-observation', admit=lambda: None)
        expected = binding.expected
    finally:
        binding.close()
    row.pop('_stamp')
    row.update(_canonicalFormat='committed', _sessionDir=str(path.parent), bytes=None)
    original = (path.parent / 'transcript.v2' / expected.body).read_bytes()
    recovery = {str(item.relative_to(path.parent)): item.read_bytes()
        for item in (path.parent / 'recovery').rglob('*') if item.is_file()}
    result = await OwnerAdapters(app).preview(row, operation)
    assert result['status'] == 'held' and '_action' not in result
    assert result['_canonicalPlan']['committed_prefix'] == asdict(expected)
    assert result['canonicalRecords'] == result['canonicalInlineImages'] == 1
    assert result['bytes'] == expected.committed_bytes
    assert result['_sourceSha256'] == expected.sha256
    assert 'Original image provenance' in result['reason']
    assert (path.parent / 'transcript.v2' / expected.body).read_bytes() == original
    assert not (path.parent / 'media').exists()
    assert {str(item.relative_to(path.parent)): item.read_bytes()
        for item in (path.parent / 'recovery').rglob('*') if item.is_file()} == recovery
    assert not (path.parent / 'recovery/storage-optimize').exists()
    assert app.leases == 0
    # Preview's actual enrolled lease has physically ended.
    resumed = VersionedSessionBinding.resume(path.parent,
        session_id=row['sessionId'], expected=expected)
    resumed.close()


@pytest.fixture
async def committed_host(host, monkeypatch, request):
    """Real writer/store candidate, with explicitly synthetic original admission."""
    from amplifier_foundation.session import media_conversion as conversion
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_web.host.storage import SessionStore
    app, row, operation, path = host
    path.unlink()
    path.with_name('transcript.jsonl.backup').unlink()
    path.parent.rmdir()
    path.parent.parent.chmod(0o700)
    binding = VersionedSessionBinding.create_new(path.parent,
        session_id=row['sessionId'], operation_id='initial', admit=lambda: None)
    store = SessionStore(path.parent.parent).media(row['sessionId'])
    try:
        rows = [inline_message()]
        if getattr(request, 'param', None) == 'mixed':
            rows.append({**inline_message(), 'tool_call_id': 'unknown-original'})
        binding.append(rows, expected=binding.expected,
            operation_id='fixture-observation', admit=lambda: None)
        expected = binding.expected
        plan = conversion.preview_committed(path.parent, expected=expected,
            owner=app.source['id'], fence=lambda _: binding.publication())
        directory = canonical._operation_dir(path, operation)
        directory.parent.mkdir(mode=0o700)
        await conversion.build_candidate(plan, directory, capability=store,
            fence=lambda _: binding.publication(), eligible_record=lambda row: row == 0,
            origin_for=lambda row, block: {
                'kind': 'tool', 'session_id': store.session_id, 'id': 'call-original',
                'created_at': '2025-01-01T00:00:00+00:00',
                'expires_at': '2035-01-01T00:00:00+00:00'},
            asset_reader=lambda ref: store.resolve_ref(ref, committed=False))
    finally:
        store.close()
        binding.close()
    row.pop('_stamp')
    row.update(_canonicalFormat='committed', _sessionDir=str(path.parent),
        _action='canonical-activation', _canonicalPlan=asdict(plan),
        _sourceId=app.source['id'], _sourceBinding=canonical._binding(app.source),
        _sourceSha256=expected.sha256, bytes=expected.committed_bytes)
    registry = ModuleType('amplifier_web.storage_optimize')
    registry.owner = lambda _: SimpleNamespace(_get=lambda *_: operation)
    monkeypatch.setitem(sys.modules, registry.__name__, registry)
    app.invalidated = []
    app.history = SimpleNamespace(index=SimpleNamespace(
        _lock=threading.RLock(), _pending_invalidations=set()),
        windows=SimpleNamespace(invalidate=lambda ids: app.invalidated.append((ids, app.leases))))

    async def refresh(**_):
        assert app.leases == 1 and not app.lock.locked()
        return True

    app.history.refresh_catalog = refresh
    return app, row, operation, path


@pytest.mark.asyncio
async def test_ordinary_committed_apply_uses_actual_source_store_and_result(committed_host):
    from amplifier_foundation.session.transcript_source import open_committed_source
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_web.host.storage import SessionStore
    app, row, operation, path = committed_host
    before = (path.parent / 'transcript.v2' / row['_canonicalPlan']['committed_prefix']['body']).read_bytes()
    result = await canonical.apply(app, row, operation)
    assert result['status'] == 'completed' and result['viewJoin'] == 'completed'
    assert result['canonicalCheckpoint']['durability'] == 'versioned-checkpoint-backed-v1'
    assert result['_accounting']['inlineRemoved'] > 0
    assert result['_accounting']['unlinked'] == 0
    with open_committed_source(path.parent) as source:
        actual = source.window(0, 1).messages[0]
        assert result['canonicalCheckpoint']['sha256'] == source.commit.sha256
        expected = source.commit
    media = SessionStore(path.parent.parent).media(row['sessionId'])
    resumed = VersionedSessionBinding.resume(path.parent,
        session_id=row['sessionId'], expected=expected)
    try:
        resumed.checkpoint(expected=expected, media_store=media)
        with media.resolve_ref(actual['metadata']['experimental.media.v1']['refs'][0]) as stream:
            assert stream.read() == pixel()
    finally:
        media.close()
        resumed.close()
    assert (path.parent / 'transcript.v2' / row['_canonicalPlan']['committed_prefix']['body']).read_bytes() == before
    assert app.leases == 0 and app.invalidated == [({'public-root'}, 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize('committed_host', ['mixed'], indirect=True)
async def test_ordinary_committed_mixed_result_reports_preserved_unknown_rows(committed_host):
    from amplifier_foundation.session.transcript_source import open_committed_source
    app, row, operation, path = committed_host
    result = await canonical.apply(app, row, operation)
    assert result['status'] == 'held' and result['canonicalHeldRecords'] == 1
    assert result['_accounting']['inlineRemoved'] > 0
    assert result['canonicalCheckpoint']['durability'] == 'versioned-checkpoint-backed-v1'
    with open_committed_source(path.parent) as source:
        actual = source.window(0, 2).messages
    assert actual[1] == {**inline_message(), 'tool_call_id': 'unknown-original'}
    assert actual[0]['metadata']['experimental.media.v1']['refs']
    assert app.leases == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('effect_happened', [False, True])
async def test_ordinary_committed_resume_reconciles_original_intent(committed_host, monkeypatch, effect_happened):
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_foundation.session.transcript_source import open_committed_source
    app, row, operation, path = committed_host
    rewrite = VersionedSessionBinding.rewrite_serialized
    calls = []

    def uncertain(self, *args, **kwargs):
        calls.append(1)
        if effect_happened:
            rewrite(self, *args, **kwargs)
        raise OSError('fixture lost actual operation outcome')

    monkeypatch.setattr(VersionedSessionBinding, 'rewrite_serialized', uncertain)
    first = await canonical.apply(app, row, operation)
    assert first['status'] == 'attempting' and app.leases == 0
    with open_committed_source(path.parent) as source:
        installed = source.commit
    result = await canonical.apply(app, first, operation, resume=True)
    assert result['status'] == ('completed' if effect_happened else 'attempting')
    assert calls == [1] and app.leases == 0
    with open_committed_source(path.parent) as source:
        assert source.commit == installed
