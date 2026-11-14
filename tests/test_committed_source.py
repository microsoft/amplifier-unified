"""Real Foundation writer output, bounded host reads; no live activation."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_foundation.session.transcript_writer import TranscriptWriter, observe_source
from amplifier_web.host.storage import SessionStore
from amplifier_web.transcript_window import TranscriptWindowReader
from amplifier_web.session_files import sessions_dir, project_slug
from amplifier_web import committed_source


@pytest.fixture
def committed(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    root = sessions_dir(workspace) / 'fixture'
    root.mkdir(parents=True)
    # Only this fixture creates or writes this unique private directory. There
    # are no live, old or open-FD writers; this is not a production fence recipe.
    active = [True]
    @contextmanager
    def fence(path):
        assert path == root.absolute()
        def check():
            if not active[0]:
                raise ValueError('fixture owner revoked')
        check()
        yield check
        check()
    rows = [{'role': 'user', 'content': 'committed request',
             'metadata': {'amplifier_input': {'id': 'real-input', 'kind': 'user'}}},
            {'role': 'assistant', 'content': 'committed answer'}]
    (root / 'transcript.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (root / 'transcript.jsonl.backup').write_text('{"role":"user","content":"STALE-BACKUP"}\n')
    (root / 'metadata.json').write_text(json.dumps({'session_id': 'fixture', 'parent_id': None,
                                                   'working_dir': str(workspace), 'bundle': 'fixture'}))
    (root / 'checkpoint.json').write_text(json.dumps({'version': 1, 'messages': [
        {'role': 'user', 'content': 'STALE-PRIVATE'}], 'metadata': {}}))
    writer = TranscriptWriter(root, fence=fence)
    sources = {name: observe_source(root, name) for name in committed_source.LEGACY_NAMES}
    ack = writer.migrate(session_id='fixture', operation_id='migrate-fixture',
                         expected_sources=sources, source='transcript.jsonl', admit=lambda: None)
    owner = {'project': project_slug(workspace), 'nativeId': 'fixture'}
    return root, workspace, rows, writer, ack.commit, owner, fence, active


def inventory(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_actual_fences_store_windows_forward_and_export(committed, tmp_path):
    root, workspace, rows, writer, commit, owner, _, _ = committed
    for name in committed_source.LEGACY_NAMES:
        assert (root / name).is_dir()
        assert any((root / name).iterdir())
    body = root / 'transcript.v2' / commit.body
    with body.open('ab') as stream:
        stream.write(b'{"role":"user","content":"UNCOMMITTED"}\n')
    before = inventory(root)
    store = SessionStore(root.parent)
    assert store.has_transcript('fixture')
    assert store.load('fixture')[0] == rows
    reader = TranscriptWindowReader()
    try:
        page = reader.read_window(root, owner, limit=1)
        assert [row['value'] for row in page['rows']] == rows[-1:]
        first = reader.read_forward(root, owner, limit=1)
        second = reader.read_forward(root, owner, after=first['after'], limit=1)
        assert [row['value'] for row in first['rows'] + second['rows']] == rows
        assert second['after'] is None
        assert page['messageWindow']['committedPrefix']['committed_bytes'] == commit.committed_bytes
    finally:
        reader.close()
    from amplifier_web.conversation_export import _saved_messages
    session = {'id': 'fixture', 'nativeIdentity': 'fixture', 'nativeProject': owner['project'],
               'workspace': str(workspace)}
    assert _saved_messages(tmp_path, session) == rows
    from amplifier_web.shared_state_probe import query
    value = query({'version': 1, 'op': 'open', 'workspace': str(workspace), 'sessionId': 'fixture'})
    assert [row['text'] for row in value['messages']] == ['committed request', 'committed answer']
    assert inventory(root) == before


@pytest.mark.parametrize('damage', ['partial', 'malformed', 'missing-body', 'symlink'])
def test_recognized_unreadable_never_falls_back(committed, tmp_path, damage):
    root, workspace, _, _, commit, owner, _, _ = committed
    descriptor = root / 'transcript.v2/current.json'
    if damage == 'partial':
        descriptor.unlink()
    elif damage == 'malformed':
        descriptor.write_text('{')
    elif damage == 'symlink':
        descriptor.unlink()
        descriptor.symlink_to(root / 'checkpoint.json')
    else:
        (root / 'transcript.v2' / commit.body).unlink()
    before = inventory(root)
    store = SessionStore(root.parent)
    assert store.has_transcript('fixture')
    with pytest.raises(ValueError):
        store.load('fixture')
    reader = TranscriptWindowReader()
    try:
        assert reader.read_window(root, owner)['status'] == 'unavailable'
        assert reader.read_forward(root, owner)['status'] == 'unavailable'
    finally:
        reader.close()
    from amplifier_web.shared_state_probe import query
    with pytest.raises(ValueError):
        query({'version': 1, 'op': 'open', 'workspace': str(workspace), 'sessionId': 'fixture'})
    from amplifier_web.conversation_export import _saved_messages
    with pytest.raises(ValueError):
        _saved_messages(tmp_path, {'id': 'fixture', 'nativeProject': owner['project']})
    with pytest.raises(ValueError):
        store.save('fixture', [], {})
    assert inventory(root) == before


def test_cursor_generation_change_and_pool_closes(committed, monkeypatch):
    root, _, rows, writer, commit, owner, _, _ = committed
    from amplifier_foundation.session import transcript_source
    opened = []
    original = transcript_source.open_committed_source
    @contextmanager
    def counted(*args, **kwargs):
        with original(*args, **kwargs) as source:
            opened.append(source)
            yield source
    monkeypatch.setattr(transcript_source, 'open_committed_source', counted)
    reader = TranscriptWindowReader()
    first = reader.read_window(root, owner, limit=1)
    cursor = first['messageWindow']['before']
    assert reader.read_window(root, owner, before=cursor, limit=1)['rows'][0]['value'] == rows[0]
    assert len(opened) == 1  # no cold O(history) open per page
    writer.rewrite(rows[::-1], expected=commit, operation_id='rewrite', admit=lambda: None)
    assert reader.read_window(root, owner, before=cursor)['reason'] == 'source_changed'
    reader.close()
    for source in opened:
        with pytest.raises(ValueError):
            source.read_range(0, 0)


def test_owner_bound_append_reconcile_and_legacy_refusals(committed, tmp_path):
    root, workspace, rows, _, commit, _, fence, active = committed
    store = SessionStore(root.parent)
    owned = store.transcript_writer('fixture', fence=fence)
    suffix = [{'role': 'assistant', 'content': 'settled suffix'}]
    ack = owned.append(suffix, expected=commit, operation_id='append', admit=lambda: None)
    from amplifier_foundation.session.transcript_encoding import canonical_bytes
    digest = hashlib.sha256(b''.join(chunk for row in suffix
                                    for chunk in (*canonical_bytes(row), b'\n'))).hexdigest()
    reconciled = owned.reconcile(operation_id='append', expected=commit, suffix_sha256=digest, kind='append')
    assert reconciled.commit == ack.commit
    assert store.load('fixture')[0] == rows + suffix
    active[0] = False
    with pytest.raises(ValueError, match='revoked'):
        owned.append(suffix, expected=ack.commit, operation_id='refused', admit=lambda: None)
    before = inventory(root)
    with pytest.raises(ValueError, match='owner-bound'):
        store.acknowledge_media('fixture', rows, object())
    from amplifier_web.bundle_selection import BundleTransaction
    transaction = BundleTransaction(tmp_path / 'app', workspace, 'fixture')
    with pytest.raises(ValueError, match='owner-bound'):
        transaction.begin()
    assert not transaction.path.exists()
    from amplifier_web.native_history import NativeHistory
    # Validation checks the real prefix and native identity; no private fallback.
    NativeHistory().validate_resume({'id': 'fixture', 'nativeProject': project_slug(workspace),
                                     'workspace': str(workspace)})
    assert inventory(root) == before


def test_committed_naming_delivery_and_exact_fork(committed, tmp_path):
    root, workspace, rows, _, _, owner, _, _ = committed
    from amplifier_web.naming_context import saved_sample
    from amplifier_web.chat_title_preview import first_request
    from amplifier_web.message_delivery import saved_delivery
    from amplifier_web.session_store import fork_session
    before = inventory(root)
    sample = saved_sample(root, owner)
    assert 'committed request' in sample.text
    source = {'id': 'fixture', 'nativeIdentity': 'fixture', 'runtimeSessionId': 'fixture',
              'nativeProject': owner['project'], 'workspace': str(workspace), 'status': 'idle',
              'messages': [], 'createdAt': 1}
    assert first_request(tmp_path, source)['title'] == 'committed request'
    assert saved_delivery(source, 'real-input') == 'accepted'
    assert saved_delivery(source, 'not-committed') == 'unknown'
    fork_session(tmp_path / 'app', source, 'exact-fork')
    saved = SessionStore(root.parent).load('exact-fork')
    assert saved[0] == rows
    assert inventory(root) == before


def test_actual_old_host_refuses_real_new_writer_output(committed, tmp_path):
    old_host = os.environ.get('AMPLIFIER_T2_OLD_HOST')
    old_foundation = os.environ.get('AMPLIFIER_T2_OLD_FOUNDATION')
    if not old_host or not old_foundation:
        pytest.skip('Explicit retained old host/Foundation source pair is required')
    root, workspace, _, _, _, owner, _, _ = committed
    before = inventory(root)
    script = r'''
import json, sys
from pathlib import Path
from amplifier_web.host.storage import SessionStore
from amplifier_web.transcript_window import TranscriptWindowReader
from amplifier_web.shared_state_probe import query
from amplifier_web.conversation_export import _saved_messages
from amplifier_web.native_history import NativeHistory
root, workspace, project, app = map(Path, sys.argv[1:])
owner = {'project': str(project), 'nativeId': 'fixture'}
source = {'id': 'fixture', 'nativeProject': str(project), 'workspace': str(workspace)}
reader = TranscriptWindowReader()
calls = {
 'store': lambda: SessionStore(root.parent).load('fixture'),
 'window': lambda: reader.read_window(root, owner),
 'forward': lambda: reader.read_forward(root, owner),
 'shared': lambda: query({'version':1,'op':'open','workspace':str(workspace),'sessionId':'fixture'}),
 'export': lambda: _saved_messages(app, source),
 'resume': lambda: NativeHistory().validate_resume(source)}
result = {}
for key, call in calls.items():
    try:
        value = call()
    except (ValueError, OSError):
        result[key] = 'refused'
    else:
        assert isinstance(value, dict) and value.get('status') == 'unavailable', (key, value)
        result[key] = 'refused'
print(json.dumps(result, sort_keys=True))
'''
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([old_foundation, old_host])}
    process = subprocess.run([sys.executable, '-c', script, str(root), str(workspace),
                              owner['project'], str(tmp_path)], env=env, cwd=tmp_path,
                             text=True, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
    assert set(json.loads(process.stdout).values()) == {'refused'}
    assert inventory(root) == before


def test_transfer_snapshot_is_exact_committed_prefix_not_physical_tail(committed, tmp_path):
    from types import SimpleNamespace
    from amplifier_web.service import AppService
    from amplifier_web import portability_data as data
    root, workspace, rows, _, commit, owner, _, _ = committed
    with (root / 'transcript.v2' / commit.body).open('ab') as stream:
        stream.write(b'{"role":"user","content":"UNCOMMITTED"}\n')
    app = AppService(tmp_path / 'app', workspace=workspace)
    try:
        source = {'id': 'fixture', 'runtimeSessionId': 'fixture', 'nativeProject': owner['project'],
                  'workspace': str(workspace), 'bundle': 'fixture', 'messages': [],
                  'selection': {'provider': 'fixture', 'model': 'fixture'}}
        before = inventory(root)
        capsule = data.capture(app, source)
        raw = data.decode_file(capsule['native']['transcript.jsonl'])
        assert [json.loads(line) for line in raw.splitlines()] == rows
        assert 'transcript.jsonl.backup' not in capsule['native']
        assert not any(key.startswith('transcript.v2') for key in capsule['native'])
        assert inventory(root) == before
    finally:
        app.db.close()


def test_revision_tracks_inplace_body_and_fence_marker(committed):
    root, _, _, _, commit, owner, _, _ = committed
    stamp = committed_source.revision(root)
    marker = root / 'transcript.jsonl/format.json'
    marker.write_bytes(marker.read_bytes() + b' ')
    assert committed_source.revision(root) != stamp
    stamp = committed_source.revision(root)
    body = root / 'transcript.v2' / commit.body
    raw = body.read_bytes()
    body.write_bytes(raw.replace(b'committed answer', b'corrupted answer'))
    assert committed_source.revision(root) != stamp
    reader = TranscriptWindowReader()
    try:
        assert reader.read_window(root, owner)['status'] == 'unavailable'
    finally:
        reader.close()


def test_cold_owner_does_not_block_warm_owner_and_eviction_closes(committed, tmp_path, monkeypatch):
    import shutil
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from amplifier_foundation.session import transcript_source
    root, _, _, _, _, owner, _, _ = committed
    pool = committed_source.SourcePool()
    with pool.source(root, owner) as first:
        pass
    cold = tmp_path / 'cold'
    shutil.copytree(root, cold)
    entered, release = threading.Event(), threading.Event()
    original = transcript_source.open_committed_source
    @contextmanager
    def delayed(path, **kwargs):
        if Path(path) == cold:
            entered.set()
            assert release.wait(5)
        with original(path, **kwargs) as source:
            yield source
    monkeypatch.setattr(transcript_source, 'open_committed_source', delayed)
    def read(path):
        with pool.source(path, owner) as source:
            return source.window(0, 1).messages[0]
    try:
        with ThreadPoolExecutor(2) as executor:
            future = executor.submit(read, cold)
            assert entered.wait(5)
            warm = executor.submit(read, root)
            try:
                assert warm.result(timeout=2)['content'] == 'committed request'
            finally:
                release.set()
            assert future.result(timeout=5)['content'] == 'committed request'
        for index in range(8):
            path = tmp_path / f'evict-{index}'
            shutil.copytree(root, path)
            read(path)
        with pytest.raises(ValueError):
            first.read_range(0, 0)
        assert len(pool._sources) == 8
    finally:
        release.set()
        pool.close()


def test_repeated_small_background_reads_reuse_checked_source(committed, monkeypatch, tmp_path):
    from amplifier_foundation.session import transcript_source
    from amplifier_web.chat_title_preview import first_request
    from amplifier_web.naming_context import saved_sample
    root, workspace, _, _, _, owner, _, _ = committed
    opened = []
    original = transcript_source.open_committed_source
    @contextmanager
    def counted(*args, **kwargs):
        opened.append(args[0])
        with original(*args, **kwargs) as source:
            yield source
    monkeypatch.setattr(transcript_source, 'open_committed_source', counted)
    pool = committed_source.SourcePool()
    monkeypatch.setattr(committed_source, 'background_sources', lambda: pool)
    source = {'id': 'fixture', 'nativeProject': owner['project'], 'workspace': str(workspace)}
    try:
        for _ in range(3):
            assert first_request(tmp_path, source)['title'] == 'committed request'
            assert saved_sample(root, {'project': str(root.parent), 'nativeId': 'fixture'}).latest_user == 'committed request'
        assert len(opened) == 1
    finally:
        pool.close()


def test_catalog_real_fences_preserve_activity_and_reconcile_metadata(committed, tmp_path, monkeypatch):
    from amplifier_web.native_history import NativeHistory
    from amplifier_web.session_files import amplifier_home
    root, workspace, _, _, _, owner, _, _ = committed
    metadata = json.loads((root / 'metadata.json').read_text())
    metadata.update(name='Original', created_at=100, last_event_at=200)
    (root / 'metadata.json').write_text(json.dumps(metadata))
    cache = tmp_path / 'catalog.sqlite'
    history = NativeHistory(amplifier_home(), cache_path=cache)
    result = history.scan()
    assert result['issues'] == []
    assert result['sessions'][0]['recentActivityAt'] == 200
    history.close()
    history = NativeHistory(amplifier_home(), watch=False, cache_path=cache)
    try:
        metadata['name'] = 'Changed without watcher'
        (root / 'metadata.json').write_text(json.dumps(metadata))
        result = history.scan(force=True)
        assert result['sessions'][0]['title'] == 'Changed without watcher'
        assert result['sessions'][0]['recentActivityAt'] == 200
        assert result['issues'] == []
    finally:
        history.close()


def test_work_associations_track_exact_commit_and_reject_corruption(committed, monkeypatch):
    from amplifier_web.event_log_view import EventIndex
    root, _, rows, writer, commit, _, _, _ = committed
    index = EventIndex(root / 'context-intelligence/events.jsonl', 'fixture')
    calls = []
    original = committed_source.load
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(committed_source, 'load', counted)
    assert index.associations(root) == {}
    first = index.association_revision
    assert index.associations(root) == {} and len(calls) == 1
    ack = writer.append([{'role': 'user', 'content': 'second input'}],
                        expected=commit, operation_id='work-append', admit=lambda: None)
    assert index.associations(root) == {}
    assert index.association_revision != first and len(calls) == 2
    assert root / 'transcript.v2/current.json' in index.association_paths
    assert root / 'transcript.v2' / ack.commit.body in index.association_paths
    body = root / 'transcript.v2' / ack.commit.body
    body.write_bytes(body.read_bytes().replace(b'committed request', b'corrupted request'))
    with pytest.raises(ValueError):
        index.associations(root)


async def test_typed_media_ack_real_append_resolve_and_fork(committed, tmp_path):
    import io
    from contextlib import closing
    from PIL import Image
    from amplifier_web.session_store import fork_session
    pytest.importorskip('amplifier_foundation.session.versioned_checkpoint')
    root, workspace, rows, _, commit, owner, fence, _ = committed
    output = io.BytesIO()
    Image.new('RGB', (2, 2), (4, 90, 170)).save(output, format='PNG')
    raw = output.getvalue()
    store = SessionStore(root.parent)
    owned = store.transcript_writer('fixture', fence=fence)
    origin = {'kind': 'tool', 'session_id': 'fixture', 'id': 'actual-tool'}
    with closing(store.media('fixture')) as media:
        ref = (await media.ingest(raw, source_format='image-bytes', origin=origin, policy=None))['value']
        suffix = [{'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'tool-1', 'type': 'function', 'function': {'name': 'fixture', 'arguments': '{}'}}]},
                  {'role': 'tool', 'content': 'Image', 'tool_call_id': 'tool-1', 'name': 'fixture',
                   'metadata': {'experimental.media.v1': {'refs': [ref]}}}]
        ack = owned.append(suffix, expected=commit, operation_id='media-append',
                           admit=lambda: media.publish_sync([ref], origin=origin, disposition='retain'))
        # A raw selector cannot be used directly as a media ACK.
        with pytest.raises((ValueError, TypeError)):
            media.acknowledge_checkpoint(ack)
        receipt = store.acknowledge_committed_media('fixture', owned, ack, media)
        assert receipt.verify()['kind'] == 'versioned-checkpoint-backed-v1'
        with media.resolve_ref(ref) as stream:
            assert stream.read() == raw
        source = {'id': 'fixture', 'runtimeSessionId': 'fixture', 'nativeIdentity': 'fixture',
                  'nativeProject': owner['project'], 'workspace': str(workspace), 'status': 'idle',
                  'messages': [], 'createdAt': 1}
        fork_session(tmp_path / 'app', source, 'media-fork')
    fork = store.load('media-fork')[0]
    copied = fork[-1]['metadata']['experimental.media.v1']['refs'][0]
    assert copied['session_id'] == 'media-fork'
    assert copied['provenance'] == ref['provenance']
    with closing(store.media('media-fork')) as media:
        # Process-local ACK is reacquired through the actual legacy saved prefix.
        store.acknowledge_media('media-fork', fork, media)
        with media.resolve_ref(copied) as stream:
            assert stream.read() == raw


def test_delivery_positive_before_huge_record_is_cached(committed, monkeypatch):
    from amplifier_web.message_delivery import saved_delivery
    from amplifier_foundation.session import transcript_source
    root, workspace, _, writer, commit, owner, _, _ = committed
    writer.append([{'role': 'tool', 'content': 'x' * (4 * 1024 * 1024 + 1)}],
                  expected=commit, operation_id='huge', admit=lambda: None)
    calls = []
    original = transcript_source.CommittedSource.iter_bytes
    def counted(self, *args, **kwargs):
        calls.append(args)
        yield from original(self, *args, **kwargs)
    monkeypatch.setattr(transcript_source.CommittedSource, 'iter_bytes', counted)
    pool = committed_source.SourcePool()
    monkeypatch.setattr(committed_source, 'background_sources', lambda: pool)
    source = {'id': 'fixture', 'workspace': str(workspace)}
    try:
        assert saved_delivery(source, 'real-input') == 'accepted'
        count = len(calls)
        assert saved_delivery(source, 'real-input') == 'accepted'
        assert len(calls) == count
    finally:
        pool.close()


@pytest.mark.parametrize('when', ['before', 'after'])
def test_fork_source_change_before_mutation_or_disclosed_retained_target(committed, tmp_path, monkeypatch, when):
    from amplifier_web.session_store import fork_session
    root, workspace, _, writer, commit, owner, _, _ = committed
    source = {'id': 'fixture', 'runtimeSessionId': 'fixture', 'nativeIdentity': 'fixture',
              'nativeProject': owner['project'], 'workspace': str(workspace), 'status': 'idle',
              'messages': [], 'createdAt': 1}
    target = 'conflicted-fork'
    if when == 'before':
        original = SessionStore.load
        def changing(self, identity):
            value = original(self, identity)
            if identity == 'fixture':
                writer.append([{'role': 'assistant', 'content': 'raced'}], expected=commit,
                              operation_id='race-before', admit=lambda: None)
            return value
        monkeypatch.setattr(SessionStore, 'load', changing)
        with pytest.raises(ValueError):
            fork_session(tmp_path / 'app', source, target)
        assert not committed_source.present(root.parent / target)
    else:
        original = SessionStore.save
        def changing(self, identity, *args, **kwargs):
            value = original(self, identity, *args, **kwargs)
            if identity == target:
                writer.append([{'role': 'assistant', 'content': 'raced'}], expected=commit,
                              operation_id='race-after', admit=lambda: None)
            return value
        monkeypatch.setattr(SessionStore, 'save', changing)
        with pytest.raises(ValueError, match='Target conflicted-fork was retained'):
            fork_session(tmp_path / 'app', source, target)
        assert committed_source.present(root.parent / target)


def test_delivery_cache_overflow_does_not_lose_positive_evidence(committed, monkeypatch):
    from amplifier_web.message_delivery import saved_delivery
    root, workspace, _, writer, commit, _, _, _ = committed
    suffix = [{'role': 'user', 'content': str(index), 'metadata': {
        'amplifier_input': {'id': f'overflow-{index}', 'kind': 'user'}}} for index in range(4098)]
    writer.append(suffix, expected=commit, operation_id='overflow', admit=lambda: None)
    pool = committed_source.SourcePool()
    monkeypatch.setattr(committed_source, 'background_sources', lambda: pool)
    source = {'id': 'fixture', 'workspace': str(workspace)}
    try:
        assert saved_delivery(source, 'overflow-4097') == 'accepted'
        assert saved_delivery(source, 'overflow-4097') == 'accepted'
        assert saved_delivery(source, 'not-present') == 'unknown'
        assert saved_delivery(source, 'overflow-4096') == 'accepted'
    finally:
        pool.close()