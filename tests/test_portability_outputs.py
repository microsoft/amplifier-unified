"""Delayed output reads cannot commit across a task ownership boundary."""
import asyncio
import copy
import json
from pathlib import Path
from threading import Event

import pytest

from amplifier_web import outputs
from amplifier_web.service import AppError
from test_portability import export, host_env, setup_hosts


def saved_content(app, home, sid):
    tables = ('output_records', 'output_comments', 'output_receipts', 'state_resources')
    result = {table: app.db.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall()
              for table in tables}
    result['messages'] = copy.deepcopy(app._session(sid)['messages'])
    result['task'] = copy.deepcopy(app._session(sid)['task'])
    result['draft'] = app._session(sid)['draft']
    for label, root in (('artifacts', app.data_dir / 'artifacts'),
                        ('native_history', home / 'native'),
                        ('archived_history', app.portability.node.directory / 'archives')):
        result[label] = {str(path.relative_to(root)): path.read_bytes()
                         for path in root.rglob('*') if path.is_file()}
    return result


async def delay_output(app, sid, kind, monkeypatch):
    entered = asyncio.Event()
    if kind == 'review':
        resume = asyncio.Event()
        original = outputs.git_snapshot

        async def blocked(*args, **kwargs):
            value = await original(*args, **kwargs)
            entered.set()
            await resume.wait()
            return value

        monkeypatch.setattr(outputs, 'git_snapshot', blocked)
        args = {'sessionId': sid, 'mode': 'unstaged', 'title': 'Delayed review'}
        action = 'outputs.review'
    else:
        resume = Event()
        loop = asyncio.get_running_loop()
        original = outputs.file_bytes

        def blocked(*args, **kwargs):
            value = original(*args, **kwargs)
            loop.call_soon_threadsafe(entered.set)
            if not resume.wait(120):
                raise AssertionError('Test did not release the delayed file read')
            return value

        monkeypatch.setattr(outputs, 'file_bytes', blocked)
        args = {'sessionId': sid, 'kind': 'file', 'path': 'new.txt', 'title': 'Delayed attachment'}
        action = 'outputs.attach'
    pending = asyncio.create_task(app.dispatch(action, args, command_id='delayed-output'))
    try:
        await asyncio.wait_for(entered.wait(), 10)
    except BaseException:
        resume.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        raise
    return pending, resume


async def release_to(left, right, sid, left_home, right_home, repository, monkeypatch):
    host_env(monkeypatch, left_home)
    receipt, _ = await export(left, right, sid, Path(left._session(sid)['workspace']))
    host_env(monkeypatch, right_home)
    staged = (await right.dispatch('portability.stage', {
        'path': receipt['package'], 'repository': str(repository)}))['result']
    host_env(monkeypatch, left_home)
    released = (await left.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'],
        'expectedRevision': receipt['revision'], 'path': staged['receiptPath']}))['result']
    return staged, released


async def activate(app, sid, staged, released):
    return (await app.dispatch('portability.activate', {'sessionId': sid, 'id': staged['id'],
        'expectedRevision': staged['revision'], 'path': released['receiptPath']}))['result']


@pytest.mark.parametrize(('kind', 'boundary'), [
    ('review', 'release'), ('attach', 'release'),
    ('review', 'cancel'), ('attach', 'roundtrip'),
])
async def test_delayed_output_rejects_changed_authority_without_mutating_saved_content(
        tmp_path, monkeypatch, kind, boundary):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    pending = resume = None
    try:
        host_env(monkeypatch, source)
        execution_revision = app._session(sid).get('executionRevision', 0)
        pending, resume = await delay_output(app, sid, kind, monkeypatch)
        if boundary == 'cancel':
            receipt, _ = await export(app, target, sid, root)
            cancelled = (await app.dispatch('portability.cancel', {'sessionId': sid,
                'id': receipt['id'], 'expectedRevision': receipt['revision'],
                'evidence': 'Keep the task on its original host.'}))['result']
            assert cancelled['phase'] == 'cancelled' and not app.portability.fenced(sid)
            assert app._session(sid).get('executionRevision', 0) == execution_revision
        else:
            staged, released = await release_to(app, target, sid, source, destination, destrepo, monkeypatch)
            assert released['phase'] == 'released' and app.portability.fenced(sid)
            if boundary == 'roundtrip':
                host_env(monkeypatch, destination)
                await activate(target, sid, staged, released)
                staged, released = await release_to(target, app, sid, destination, source, root, monkeypatch)
                host_env(monkeypatch, source)
                returned = await activate(app, sid, staged, released)
                assert returned['phase'] == 'active' and not app.portability.fenced(sid)
                assert app._session(sid)['executionRevision'] > execution_revision

        host_env(monkeypatch, source)
        before = saved_content(app, source, sid)
        resume.set()
        with pytest.raises(AppError, match='fenced|changed|transfer|moved'):
            await pending
        assert saved_content(app, source, sid) == before
        assert not app.db.execute('SELECT 1 FROM output_receipts WHERE id=?', ('delayed-output',)).fetchone()
        assert not app.runtime.workers and not target.runtime.workers

        if boundary == 'cancel':
            # A fresh explicit action after cancellation is still admitted.
            new = (await app.dispatch('outputs.write', {'sessionId': sid, 'title': 'New deliberate output',
                'content': 'Written after cancelled transfer review.', 'variant': 'document'},
                command_id='new-after-cancel'))['result']
            assert app.outputs.content(new) == b'Written after cancelled transfer review.'
            assert app._session(sid)['messages'] == before['messages']
    finally:
        if resume is not None:
            resume.set()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


@pytest.fixture
async def cold_fence_task(tmp_path, monkeypatch):
    """Real retirement, with all four display fields unconsumed by admission."""
    import json
    from amplifier_web import cold_display, resource_files
    from amplifier_web.service import AppService
    from test_service import Runtime
    host_env(monkeypatch, tmp_path)
    app = AppService(tmp_path / 'app', workspace=tmp_path, runtime=Runtime())
    try:
        await app.dispatch('session.create', {'title': 'Cold fence admission'})
        session = app._session()
        session.update(
            messages=[{'id': 'fence-assistant', 'role': 'assistant', 'text': 'Exact saved body ' + 'm' * 20000}],
            execution={'nodes': [{'id': 'saved-tool', 'kind': 'tool', 'output': 'e' * 20000}], 'turns': []},
            historyActivity={'retained': 'h' * 20000},
            configuration={'plan': {'tools': []}, 'provenance': {}, 'retained': 'c' * 20000})
        app._state['selectedSessionId'] = None
        monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
        assert session['id'] in app.cold_display.retire(force=True)
        references = copy.deepcopy(dict.get(session, '_coldFields'))
        assert set(references) == cold_display.SESSION_FIELDS
        assert cold_display.load(app.db, references['messages']) == [
            {'id': 'fence-assistant', 'role': 'assistant', 'text': 'Exact saved body ' + 'm' * 20000}]
        reference_bytes = json.dumps(references, sort_keys=True).encode()
        bodies = {resource_files.root(app.db) / (ref['$resource'] + '.json') for ref in references.values()}
        saved_bytes = {path: path.read_bytes() for path in bodies}
        touches = []
        original_touch, original_open = session.touch, Path.open

        def touched():
            touches.append(True)
            original_touch()

        def no_load(*args, **kwargs):
            pytest.fail('Transfer admission hydrated a saved display field')

        def no_body_open(path, *args, **kwargs):
            if path in bodies:
                pytest.fail('Transfer admission opened a saved display body')
            return original_open(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(session, 'touch', touched)
            patch.setattr(cold_display, 'load', no_load)
            patch.setattr(Path, 'open', no_body_open)
            yield app, session
            assert json.dumps(dict.get(session, '_coldFields'), sort_keys=True).encode() == reference_bytes
            assert all(not dict.__contains__(session, field) for field in cold_display.SESSION_FIELDS)
            assert not touches, 'Metadata-only fence inspection must not touch cold display demand'
        assert {path: path.read_bytes() for path in bodies} == saved_bytes
    finally:
        await app.close()


@pytest.mark.parametrize('workspace', ['present', 'missing', 'absent'])
def test_retained_fence_and_write_context_leave_exact_cold_fields_unread(
        cold_fence_task, tmp_path, workspace):
    from amplifier_web.portability import retained_native_fence
    app, session = cold_fence_task
    session.update(nativeProject='synthetic-native-project', nativeIdentity='legacy_worker__01',
                   runtimeSessionId='runtime-alias')
    original_workspace = session['workspace']
    try:
        if workspace == 'missing':
            session['workspace'] = str(tmp_path / 'disappeared-workspace')
        elif workspace == 'absent':
            session.pop('workspace')
        assert retained_native_fence(session) is False
        assert app.portability.write_context(session['id']) is None
    finally:
        session['workspace'] = original_workspace


@pytest.mark.parametrize('project_anchor', ['native', 'workspace'])
@pytest.mark.parametrize('identity_anchor', ['native', 'runtime'])
@pytest.mark.parametrize('marker_kind', ['malformed', 'unreadable', 'dangling'])
def test_each_retained_fence_anchor_denies_without_content_or_display_reads(
        cold_fence_task, tmp_path, monkeypatch, project_anchor, identity_anchor, marker_kind):
    from amplifier_web.portability import retained_native_fence
    from amplifier_web.session_files import amplifier_home, project_slug
    app, session = cold_fence_task
    session.update(nativeProject='synthetic-native-project', nativeIdentity='legacy_worker__01',
                   runtimeSessionId='runtime-alias', workspace=str(tmp_path / 'disappeared-workspace'))
    project = session['nativeProject'] if project_anchor == 'native' else project_slug(session['workspace'])
    identity = session['nativeIdentity'] if identity_anchor == 'native' else session['runtimeSessionId']
    marker = amplifier_home() / 'projects' / project / 'sessions' / identity / 'transfer-fence.json'
    marker.parent.mkdir(parents=True)
    if marker_kind == 'dangling':
        marker.symlink_to(marker.parent / 'missing-evidence')
    else:
        marker.write_bytes(b'not valid transfer evidence')
        if marker_kind == 'unreadable':
            marker.chmod(0)
    original_open = Path.open

    def no_marker_content(path, *args, **kwargs):
        if path.name == 'transfer-fence.json':
            if marker_kind == 'unreadable':
                raise PermissionError('Synthetic unreadable marker contents')
            pytest.fail('Retained fence admission read marker contents')
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', no_marker_content)
        assert retained_native_fence(session) is True
        with pytest.raises(ValueError, match='fenced for transfer'):
            app.portability.write_context(session['id'])


def test_retained_fence_lstat_oserror_is_not_permission(cold_fence_task, monkeypatch):
    from amplifier_web.portability import retained_native_fence
    app, session = cold_fence_task
    session.update(nativeProject='synthetic-native-project', nativeIdentity='legacy_worker__01')
    original = Path.lstat

    def inaccessible(path, *args, **kwargs):
        if path.name == 'transfer-fence.json':
            raise PermissionError('Synthetic lstat refusal')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', inaccessible)
    for check in (lambda: retained_native_fence(session),
                  lambda: app.portability.write_context(session['id'])):
        with pytest.raises(ValueError, match="Cannot inspect this task's transfer fence") as caught:
            check()
        assert isinstance(caught.value.__cause__, PermissionError)


@pytest.mark.parametrize(('metadata', 'identities', 'error'), [
    ({'nativeIdentity': 'legacy_worker__01'}, {'legacy_worker__01'}, None),
    ({'runtimeSessionId': 'runtime-alias'}, {'runtime-alias'}, None),
    ({'nativeIdentity': 'legacy_worker__01', 'runtimeSessionId': 'runtime-alias'},
     {'legacy_worker__01', 'runtime-alias'}, None),
    ({'nativeIdentity': False, 'runtimeSessionId': 'runtime-alias'}, {'runtime-alias'}, None),
    ({'nativeIdentity': 0, 'runtimeSessionId': 'runtime-alias'}, {'runtime-alias'}, None),
    ({'nativeIdentity': '', 'runtimeSessionId': 'runtime-alias'}, {'runtime-alias'}, None),
    ({'nativeIdentity': None, 'runtimeSessionId': None, 'id': 'web-fallback'}, {'web-fallback'}, None),
    ({'nativeIdentity': False, 'id': 'web-fallback'}, {'web-fallback'}, None),
    ({'nativeIdentity': 0, 'runtimeSessionId': False, 'id': 'web-fallback'}, {'web-fallback'}, None),
    ({'nativeIdentity': False}, set(), KeyError),
    ({'nativeIdentity': None, 'runtimeSessionId': ''}, set(), KeyError),
    ({'nativeIdentity': 7}, set(), ValueError),
    ({'nativeIdentity': False, 'id': None}, set(), ValueError),
    ({'runtimeSessionId': False, 'id': ''}, set(), ValueError),
])
def test_retained_fence_metadata_preserves_directory_fallbacks_without_eager_id(
        cold_fence_task, monkeypatch, metadata, identities, error):
    from amplifier_web import automatic_history
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.portability import retained_native_fence
    app, session = cold_fence_task
    # Standalone inputs include legacy callers with no web id. Never substitute
    # these for the live app record, or consume its four cold fields.
    row = ColdRecord({'nativeProject': 'synthetic-native-project', **metadata,
                      '_coldFields': copy.deepcopy(dict.get(session, '_coldFields'))}, app.db)
    paths = []
    original = automatic_history.directory

    def directory(value):
        assert set(value) <= {'nativeProject', 'nativeIdentity', 'runtimeSessionId', 'id'}
        assert ('id' in value) == ('id' in metadata)
        path = original(value)
        paths.append(path.name)
        return path

    monkeypatch.setattr(automatic_history, 'directory', directory)
    if error:
        with pytest.raises(error):
            retained_native_fence(row)
    else:
        assert retained_native_fence(row) is False
        assert set(paths) == identities
    assert dict.get(row, '_coldFields') == dict.get(session, '_coldFields')
    assert all(not dict.__contains__(row, field) for field in dict.get(row, '_coldFields'))


@pytest.mark.parametrize(('field', 'invalid'), [
    ('nativeProject', '../outside'), ('nativeProject', '.'), ('nativeProject', 7),
    ('nativeIdentity', '../outside'), ('nativeIdentity', '..'),
    ('nativeIdentity', 'bad\\identity'), ('nativeIdentity', '\x00invalid'),
])
def test_retained_fence_invalid_path_metadata_still_refuses(cold_fence_task, field, invalid):
    from amplifier_web.portability import retained_native_fence
    app, session = cold_fence_task
    session.update(nativeProject='synthetic-native-project', nativeIdentity='legacy_worker__01')
    session[field] = invalid
    for check in (lambda: retained_native_fence(session),
                  lambda: app.portability.write_context(session['id'])):
        with pytest.raises(ValueError, match='Invalid native'):
            check()


def test_retained_fence_project_symlink_cannot_escape_shared_root(cold_fence_task, tmp_path):
    from amplifier_web.portability import retained_native_fence
    from amplifier_web.session_files import amplifier_home
    app, session = cold_fence_task
    session.update(nativeProject='synthetic-native-project', nativeIdentity='legacy_worker__01')
    projects = amplifier_home() / 'projects'
    projects.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / 'outside-projects'
    outside.mkdir()
    (projects / session['nativeProject']).symlink_to(outside, target_is_directory=True)
    for check in (lambda: retained_native_fence(session),
                  lambda: app.portability.write_context(session['id'])):
        with pytest.raises(ValueError, match='inside the shared projects folder'):
            check()


def test_transfer_node_still_denies_cold_task_writes(cold_fence_task, tmp_path):
    from amplifier_portability import TransferNode
    from amplifier_worktrees.git import atomic
    app, session = cold_fence_task
    peer = TransferNode(tmp_path / 'paired-node', 'synthetic-peer')
    atomic(app.portability.node.directory / 'peers.json', {peer.identity['id']: peer.identity})
    row = app.portability.node.begin(session['id'], peer.identity['id'], 'cold-transfer', {})
    assert row['phase'] == 'preparing' and app.portability.node.fenced(session['id'])
    with pytest.raises(ValueError, match='fenced for transfer'):
        app.portability.write_context(session['id'])


def test_shared_session_store_still_denies_cold_task_writes(cold_fence_task):
    import uuid
    from amplifier_foundation.session import SharedSessionStore
    app, session = cold_fence_task
    # Ordinary runtime row intentionally has no indexed nativeProject; this
    # exercises the unchanged runtime-store branch rather than retained lstat.
    assert not session.get('nativeProject')
    store = SharedSessionStore(session['workspace'], session['id'])
    held = store.acquire(app='synthetic-fence-test')
    try:
        held.fence_transfer(str(uuid.uuid4()), app.portability.node.identity['id'], role='source')
    finally:
        held.release()
    assert store.transfer_fence() is not None
    assert not app.portability.node.fenced(session['id'])
    with pytest.raises(ValueError, match='fenced for transfer'):
        app.portability.write_context(session['id'])


async def attach_saved_media(app, sid, workspace, *, large=False):
    import hashlib
    from test_output_media import gif
    await app.on_runtime_event('assistant.message', {
        'sessionId': sid, 'text': 'Retain media with this assistant.', 'generationId': 'portable-media'})
    message = app._session(sid)['messages'][-1]
    data = gif()
    if large:
        # Below the media-only 32 MiB limit; serialized TOTAL transfer is larger.
        data = data[:-1] + b'\x21\xfe' + (b'\xff' + b'x' * 255) * 105_000 + b'\x00\x3b'
    path = workspace / 'portable.gif'
    path.write_bytes(data)
    result = (await app.dispatch('outputs.attachMedia', {'sessionId': sid, 'messageId': message['id'],
        'title': 'Portable synthetic animation', 'path': path.name,
        'expectedSha256': hashlib.sha256(data).hexdigest()}))['result']
    path.unlink()
    return result, message['id'], data


async def test_media_transfer_rebinds_destination_context_without_changing_history_or_nested_outputs(tmp_path, monkeypatch):
    from amplifier_web.host.storage import SessionStore
    from amplifier_web import resource_files
    from test_output_images import png
    app, target, source, destination, root, destrepo, sid, out1, out2 = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        media, mid, data = await attach_saved_media(app, sid, root)
        await app.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'A later assistant page.'})
        store = SessionStore.for_app(app.data_dir, root)
        # Retained transfer admission is exact record/body authority, not
        # continued existence of an assistant in the transferred transcript.
        store.save(sid, [{'role': 'user', 'content': 'Rewritten native history'},
                         {'role': 'assistant', 'content': 'Replacement answer'}],
                   {'bundle_name': app._session(sid)['bundle'], 'working_dir': str(root)},
                   preserve_system=True)
        app.clients.attach('transfer-wrapper')
        with app.clients.bind('transfer-wrapper'):
            await app.dispatch('canvas.openGif', {**media, 'messageId': mid}, include_state=False)
        wrapper = next(row for row in app.state['canvasArtifacts'] if row['sessionId'] == sid and row['kind'] == 'gif')
        snapshot = (await app.dispatch('outputs.attach', {'sessionId': sid, 'kind': 'canvas',
            'canvasId': wrapper['id'], 'title': 'Retained GIF adapter snapshot'}))['result']
        raw = (store.directory(sid) / 'transcript.jsonl').read_bytes()
        # Existing small PNG and nested graph must survive alongside media.
        image = png()
        (root / 'page.png').write_bytes(image)
        saved_png = (await app.dispatch('outputs.attach', {'sessionId': sid, 'kind': 'file',
            'path': 'page.png', 'title': 'Unrelated PNG'}))['result']
        (root / 'page.png').unlink()
        leaf = resource_files.put(app.db, {'content': 'Nested retained small resource'})
        parent = resource_files.put(app.db, {'children': [leaf]})
        app._session(sid)['artifactRefs'] = [parent]
        app._save()
        before_messages = copy.deepcopy(app._session(sid)['messages'])
        staged, released = await release_to(app, target, sid, source, destination, destrepo, monkeypatch)
        host_env(monkeypatch, destination)
        active = await activate(target, sid, staged, released)
        assert active['phase'] == 'active'
        session = target._session(sid)
        working = Path(session.get('workingDirectory') or session['workspace'])
        assert working != root and working.is_dir()
        assert session['messages'] == before_messages
        assert SessionStore.for_app(target.data_dir, session['workspace']).history(sid).transcript_path.read_bytes() == raw
        # Media inspection must work with the optional worker prewarming disabled.
        target.runtime.configure_retention({**target.runtime.retention.settings,
                                            'max_warm_workers': 0})
        target.clients.attach('destination-media')
        with target.clients.bind('destination-media'):
            await target.dispatch('session.select', {'id': sid})
            # Selection schedules native-history loading. The stable transfer
            # assertion starts after that owner has finished changing its view.
            await target.history.refresh_session(sid)
            items = (await target.outputs.media.describe(sid, mid))['items']
            assert len(items) == 1 and items[0]['id'] == media['id']
            values = {**items[0], 'messageId': mid}
            assert await target.outputs.media.bytes(values) == data
            assert target.outputs.media.observation(target.outputs.record(sid, media['id']))['status'] == 'not_observed'
            with pytest.raises(ValueError):
                await target.outputs.media.bytes({**values, 'messageId': session['messages'][-1]['id']})
        assert target.outputs.content(target.outputs.record(sid, saved_png['id'])) == image
        assert target.outputs.content(target.outputs.record(sid, out1['id'])) == b'First output'
        assert target.outputs.content(target.outputs.record(sid, out2['id'])) == b'Second output'
        assert target.outputs.content(target.outputs.record(sid, snapshot['id'])) == app.outputs.content(app.outputs.record(sid, snapshot['id']))
        assert target.state_resource(parent['$resource']) == {'children': [leaf]}
        assert target.state_resource(leaf['$resource']) == {'content': 'Nested retained small resource'}
        assert not app.runtime.workers and not target.runtime.workers
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


@pytest.mark.parametrize('defect', ['bytes', 'dimensions', 'structure', 'wrapper-owner', 'wrapper-version', 'snapshot-owner'])
async def test_transfer_rejects_exactly_hashed_but_malformed_media_without_install(
        tmp_path, monkeypatch, defect):
    import base64
    from amplifier_web import portability_data, resource_files
    from amplifier_worktrees.git import digest
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        saved, mid, raw = await attach_saved_media(app, sid, root)
        app.clients.attach('transfer-validation')
        with app.clients.bind('transfer-validation'):
            await app.dispatch('canvas.openGif', {**saved, 'messageId': mid}, include_state=False)
        wrapper = next(row for row in app.state['canvasArtifacts'] if row['kind'] == 'gif')
        snapshot = (await app.dispatch('outputs.attach', {'sessionId': sid, 'kind': 'canvas',
            'canvasId': wrapper['id'], 'title': 'GIF snapshot'}))['result']
        payload = portability_data.capture(app, app._session(sid))
        record = next(row for row in payload['outputs'] if row['id'] == saved['id'])
        if defect == 'bytes':
            record['bytes'] += 1
        elif defect == 'dimensions':
            record['media']['width'] += 1
            record['media']['decodedPixelBytesEstimate'] *= 2
        elif defect == 'structure':
            damaged = raw[:-1]
            body = {'encoding': 'base64', 'data': base64.b64encode(damaged).decode()}
            prepared = resource_files.prepare(body)
            payload['resources'][prepared.identity] = body
            record.update(body={'$resource': prepared.identity, 'bytes': prepared.size},
                          bytes=len(damaged), sha256=digest(damaged))
        elif defect == 'snapshot-owner':
            snapshot = next(row for row in payload['outputs'] if row['id'] == snapshot['id'])
            body = copy.deepcopy(payload['resources'][snapshot['canvasSource']['$resource']])
            body['mediaReference']['item']['sessionId'] = 'foreign-owner'
            prepared = resource_files.prepare(body)
            payload['resources'][prepared.identity] = body
            snapshot['canvasSource'] = {'$resource': prepared.identity, 'bytes': prepared.size}
            content = json.dumps(body, ensure_ascii=False, sort_keys=True)
            encoded = {'encoding': 'utf-8', 'data': content}
            prepared = resource_files.prepare(encoded)
            payload['resources'][prepared.identity] = encoded
            snapshot.update(body={'$resource': prepared.identity, 'bytes': prepared.size},
                            sha256=digest(content.encode()), bytes=len(content.encode()))
        else:
            row = next(row for row in payload['canvas'] if row['kind'] == 'gif')
            body = copy.deepcopy(payload['resources'][row['body']['$resource']])
            item = body['mediaReference']['item']
            item['sessionId' if defect == 'wrapper-owner' else 'version'] = 'foreign' if defect == 'wrapper-owner' else 99
            prepared = resource_files.prepare(body)
            payload['resources'][prepared.identity] = body
            row['body'] = {'$resource': prepared.identity, 'bytes': prepared.size}
        before = target.db.execute('SELECT * FROM output_records').fetchall()
        with pytest.raises(ValueError):
            portability_data.validate(payload)
        assert target.db.execute('SELECT * FROM output_records').fetchall() == before
        assert target.state['canvasArtifacts'] == []
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


@pytest.mark.parametrize('change', ['execution', 'cancel'])
async def test_transfer_media_worker_settles_and_rechecks_source_before_admission(
        tmp_path, monkeypatch, change):
    import threading
    from amplifier_web import portability_data
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    entered, release, finished = Event(), Event(), Event()
    pending = None
    try:
        host_env(monkeypatch, source)
        await attach_saved_media(app, sid, root)
        inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
        owner, real = threading.get_ident(), portability_data.validate

        def blocked(payload, **kwargs):
            assert threading.get_ident() != owner
            result = real(payload, **kwargs)
            entered.set()
            assert release.wait(10)
            finished.set()
            return result

        # capture's existing baseline validation remains on-owner; only the new
        # complete detached validation is delayed.
        def validate(payload, **kwargs):
            return real(payload, **kwargs) if kwargs.get('media') is False else blocked(payload, **kwargs)

        monkeypatch.setattr(portability_data, 'validate', validate)
        pending = asyncio.create_task(app.dispatch('portability.export', {
            'sessionId': sid, 'destination': target.portability.node.identity['id'],
            'sourceRevision': inspected['repository']['sourceRevision'],
            'expectedExecutionRevision': app._session(sid).get('executionRevision', 0),
            'mode': 'carry_dirty', 'reviewedContent': True}, command_id='media-worker-export'))
        assert await asyncio.to_thread(entered.wait, 10)
        if change == 'execution':
            app._session(sid)['executionRevision'] = app._session(sid).get('executionRevision', 0) + 1
        else:
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done()
        release.set()
        with pytest.raises(asyncio.CancelledError if change == 'cancel' else AppError):
            await pending
        assert finished.is_set()
        assert not app.portability.node.records(sid) and not app.portability.jobs
        assert not app.portability.fenced(sid)
    finally:
        release.set()
        if pending:
            await asyncio.gather(pending, return_exceptions=True)
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


async def test_media_transfer_total_32mib_refuses_without_omission_or_limit_raise(tmp_path, monkeypatch):
    from amplifier_web.output_media import MAX_BYTES as MEDIA_LIMIT
    from amplifier_web.portability_data import MAX_BYTES as TRANSFER_LIMIT
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        media, mid, data = await attach_saved_media(app, sid, root, large=True)
        assert len(data) < TRANSFER_LIMIT == 32 * 1024 * 1024
        assert MEDIA_LIMIT == 64 * 1024 * 1024
        inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
        before = saved_content(app, source, sid)
        with pytest.raises(AppError, match='32 MiB transfer limit'):
            await app.dispatch('portability.export', {'sessionId': sid,
                'destination': target.portability.node.identity['id'],
                'sourceRevision': inspected['repository']['sourceRevision'],
                'expectedExecutionRevision': app._session(sid).get('executionRevision', 0),
                'mode': 'carry_dirty', 'reviewedContent': True}, command_id='oversized-media-transfer')
        assert saved_content(app, source, sid) == before
        assert not app.portability.fenced(sid) and not app.portability.jobs
        assert not target.state['sessions']
        app.clients.attach('oversized-retained')
        with app.clients.bind('oversized-retained'):
            assert await app.outputs.media.bytes({**media, 'messageId': mid}) == data
        assert not app.runtime.workers and not target.runtime.workers
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()
