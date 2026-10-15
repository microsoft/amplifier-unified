"""Retained lifetime is registry-owned; admission remains transcript-owned.

Synthetic structural media, not decoder or deployment qualification.
"""
import asyncio
import copy
import hashlib
import json
import threading
from pathlib import Path

import pytest

from test_output_media import (
    canonical_corpus, cold_alias_corpus, gif, media_app, media_args,
    media_args_without_hydration, mp4,
)


@pytest.mark.parametrize('shape', ['canonical-tail', 'resident-alias', 'cold-alias'])
@pytest.mark.parametrize('format', ['gif', 'mp4'])
async def test_real_saves_removed_origin_eviction_unlink_and_reopen(
        media_app, monkeypatch, shape, format):
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = media_app
    if shape == 'canonical-tail':
        session, corpus, store, _, mid = canonical_corpus(app)
    else:
        session, corpus, store, _, _ = cold_alias_corpus(app)
        mid = 'saved-web-assistant'
    sid = session['id']
    native_metadata = {'bundle_name': session['bundle'], 'working_dir': session['workspace']}
    args = {**media_args_without_hydration(session), 'messageId': mid, 'title': ''}
    data = gif() if format == 'gif' else mp4()
    Path(session['workspace'], args['path']).write_bytes(data)
    args['expectedSha256'] = hashlib.sha256(data).hexdigest()
    if shape == 'cold-alias':
        # Actual retirement, not an invented _coldFields marker.
        app._state['selectedSessionId'] = None
        monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
        assert sid in app.cold_display.retire(force=True)
        assert not dict.__contains__(session, 'messages')
    saved = (await app.outputs.media.attach(args, 'ui', 'lifetime-attach'))['result']
    original = copy.deepcopy(app.outputs.record(sid, saved['id']))
    exact = {**saved, 'messageId': mid}
    app.clients.attach('retained-reader')
    app.clients.records['retained-reader']['selectedSessionId'] = sid

    async def forbidden(*args, **kwargs):
        pytest.fail('Retained read attempted live transcript qualification')

    async def check(service):
        from amplifier_web import agent_canvas, cold_display
        # Background diagnostics may publish independently after a real reopen.
        # Stop and settle that owner before measuring this read-only operation;
        # retain the strict counter and all record-preservation assertions.
        await service.diagnostics.close()
        maintenance = getattr(service, '_storage_maintenance_task', None)
        if maintenance is not None:
            await asyncio.shield(maintenance)
            assert maintenance.done()
        def no_display(*args, **kwargs):
            pytest.fail('Retained metadata bridge hydrated unrelated display history')
        def no_admit(*args, **kwargs):
            pytest.fail('Retained metadata bridge admitted an output owner')
        resident = [row['id'] for row in service._state['sessions']]
        controls = set(service._state['runtimeControl'])
        references = copy.deepcopy(service._session_projection_refs)
        changes = service.db.total_changes
        with monkeypatch.context() as patch:
            patch.setattr(cold_display, 'load', no_display)
            patch.setattr(service, '_admit_session', no_admit)
            patch.setattr(agent_canvas, 'state', no_admit)
            patch.setattr(service, '_flush_pending_progress', forbidden)
            listed = (await service.app_bridge('dispatch', {
                'action': 'outputs.list', 'args': {'includeUnlinked': True}}, sid))['result']
            assert [item['id'] for item in listed['items']] == [saved['id']]
            metadata = (await service.app_bridge('dispatch', {
                'action': 'outputs.read', 'args': {'id': saved['id']}}, sid))['result']
        assert service.db.total_changes == changes
        assert [row['id'] for row in service._state['sessions']] == resident
        assert set(service._state['runtimeControl']) == controls
        assert service._session_projection_refs == references
        assert metadata['kind'] == 'media' and metadata['title'] == ''
        assert metadata['origin'] == original['origin']
        assert metadata['browserObservation']['status'] == 'unbound'
        with service.clients.bind('retained-reader'):
            assert await service.outputs.media.bytes(exact) == data
            assert await service.outputs.media.head(exact) == len(data)
            report = await service.outputs.media.observe({
                **exact, 'generation': 1, 'sequence': next(sequence),
                'renderState': 'ready', 'playback': 'paused'}, 'ui')
            assert report['accepted']
            if format == 'gif':
                opening = {key: exact[key] for key in ('sessionId', 'messageId', 'id', 'version', 'sha256')}
                assert (await service.dispatch('canvas.openGif', opening, include_state=False))['accepted']

    from itertools import count
    sequence = count(1)
    with monkeypatch.context() as patch:
        patch.setattr(app.outputs.media, 'identity', forbidden)
        for index in range(3):
            corpus.extend([{'role': 'user', 'content': f'Real appended input {index}'},
                           {'role': 'assistant', 'content': f'Real appended reply {index}'}])
            store.save(sid, corpus, native_metadata, preserve_system=True)
            await check(app)
        corpus[1] = {'role': 'user', 'content': 'Rewritten former origin'}
        store.save(sid, corpus, native_metadata, preserve_system=True)
        await check(app)
        store.save(sid, corpus[-2:], native_metadata, preserve_system=True)
        Path(session['workspace'], args['path']).unlink()
        await check(app)
        with app.clients.bind('retained-reader'):
            await app.dispatch('outputs.unlink', {
                'sessionId': sid, 'id': saved['id'], 'expectedRevision': 1})
            assert (await app.outputs.media.describe(sid, mid))['items'] == []
            assert app.outputs.store.list(sid)['items'] == []
        await check(app)
        with app.clients.bind('retained-reader'):
            await app.dispatch('outputs.relink', {
                'sessionId': sid, 'id': saved['id'], 'expectedRevision': 2})
            assert len((await app.outputs.media.describe(sid, mid))['items']) == 1
        assert app.db.execute('SELECT COUNT(*) FROM output_records WHERE session_id=?', (sid,)).fetchone()[0] == 1
    # New admission still fails against the actual truncated canonical history.
    Path(session['workspace'], args['path']).write_bytes(data)
    with pytest.raises(ValueError):
        await app.outputs.media.attach(args, 'ui', 'removed-origin-new-attach')
    Path(session['workspace'], args['path']).unlink()
    home, workspace = app.data_dir, session['workspace']
    await app.close()
    reopened = AppService(home, workspace=workspace, runtime=Runtime())
    try:
        reopened.clients.attach('retained-reader')
        reopened.clients.records['retained-reader']['selectedSessionId'] = sid
        monkeypatch.setattr(reopened.outputs.media, 'identity', forbidden)
        if shape == 'cold-alias':
            assert sid not in {row['id'] for row in reopened._state['sessions']}
        current = reopened.outputs.record(sid, saved['id'])
        for field in ('id', 'sessionId', 'body', 'sha256', 'bytes', 'media', 'origin', 'version'):
            assert current[field] == original[field]
        await check(reopened)
    finally:
        await reopened.close()


async def test_native_display_only_id_cannot_admit_media_or_replace_saved_proof(media_app):
    from amplifier_web.service import AppError
    app = media_app
    args = media_args(app)
    row = app._session()['messages'][-1]
    row['displayOnly'] = True
    with pytest.raises(AppError, match='display-only'):
        await app.dispatch('outputs.attachMedia', args, command_id='window-admission')
    assert app.outputs.store.list(args['sessionId'])['items'] == []
    row.pop('displayOnly')
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    row['displayOnly'] = True
    app.clients.attach('display-only-reader')
    with app.clients.bind('display-only-reader'):
        assert await app.outputs.media.bytes({**saved, 'messageId': args['messageId']}) == gif()
    with pytest.raises(AppError, match='display-only'):
        await app.dispatch('outputs.attachMedia', args, command_id='new-window-admission')


@pytest.mark.parametrize('cold', [False, True])
@pytest.mark.parametrize('spelling', ['alias', 'canonical'])
async def test_cold_display_only_alias_never_becomes_canonical_admission(media_app, monkeypatch, cold, spelling):
    """Dependency discriminator: the detached alias owner must retain the flag."""
    app = media_app
    session, _, _, _, canonical = cold_alias_corpus(app)
    args = {**media_args_without_hydration(session),
            'messageId': 'saved-web-assistant' if spelling == 'alias' else canonical}
    session['messages'][0]['displayOnly'] = True
    if cold:
        app._state['selectedSessionId'] = None
        monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
        assert session['id'] in app.cold_display.retire(force=True)
        assert not dict.__contains__(session, 'messages')
    with pytest.raises(ValueError):
        await app.outputs.media.attach(args, 'ui', 'cold-window-admission')
    assert app.outputs.store.list(session['id'])['items'] == []


@pytest.mark.parametrize('change', ['append', 'client', 'root', 'registry', 'body', 'index'])
async def test_retained_await_rechecks_owner_registry_and_index_not_history(
        media_app, monkeypatch, change):
    from amplifier_web import output_media, resource_files
    app = media_app
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    exact = {**saved, 'messageId': args['messageId']}
    app.clients.attach('await-reader')
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    real = output_media._saved_bytes

    def blocked(*values):
        entered.set()
        assert release.wait(10)
        try:
            return real(*values)
        finally:
            finished.set()

    monkeypatch.setattr(output_media, '_saved_bytes', blocked)
    with app.clients.bind('await-reader'):
        pending = asyncio.create_task(app.outputs.media.bytes(exact))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            record = app.outputs.record(args['sessionId'], saved['id'])
            if change == 'append':
                await app.on_runtime_event('assistant.message', {
                    'sessionId': args['sessionId'], 'text': 'Unrelated saved append'})
            elif change == 'client':
                app.clients.records['await-reader']['selectedSessionId'] = None
            elif change == 'root':
                app._session(args['sessionId'], hydrate=False)['sessionKind'] = 'worker'
            elif change == 'index':
                app.db.execute('UPDATE state_resources SET value=? WHERE id=?',
                               ('{}', record['body']['$resource']))
                app.db.commit()
            else:
                if change == 'body':
                    record['body'] = resource_files.put(app.db, {'encoding': 'base64', 'data': 'AAAA'})
                else:
                    record['media']['durationMs'] += 10
                app.db.execute('UPDATE output_records SET value=? WHERE id=?',
                               (json.dumps(record), record['id']))
                app.db.commit()
            release.set()
            if change == 'append':
                assert await pending == gif()
            else:
                with pytest.raises(ValueError):
                    await pending
            assert finished.is_set()
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('operation', ['metadata', 'head'])
@pytest.mark.parametrize('change', ['append', 'registry', 'index', 'root'])
async def test_metadata_and_head_awaits_recheck_fresh_retained_facts(
        media_app, monkeypatch, operation, change):
    from amplifier_web import output_media
    app = media_app
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    exact = {**saved, 'messageId': args['messageId']}
    app.clients.attach('metadata-reader')
    entered, release = threading.Event(), threading.Event()
    real = output_media._signature

    def blocked(source):
        result = real(source)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(output_media, '_signature', blocked)
    with app.clients.bind('metadata-reader'):
        read = (app.outputs.media.metadata(args['sessionId'], saved['id'], {})
                if operation == 'metadata' else app.outputs.media.head(exact))
        pending = asyncio.create_task(read)
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            record = app.outputs.record(args['sessionId'], saved['id'])
            if change == 'append':
                await app.on_runtime_event('assistant.message', {
                    'sessionId': args['sessionId'], 'text': 'Saved while reading metadata'})
            elif change == 'root':
                app._session(args['sessionId'], hydrate=False)['sessionKind'] = 'worker'
            elif change == 'index':
                app.db.execute('UPDATE state_resources SET value=? WHERE id=?',
                               ('{}', record['body']['$resource']))
                app.db.commit()
            else:
                record['title'] = 'Replaced immutable metadata'
                app.db.execute('UPDATE output_records SET value=? WHERE id=?',
                               (json.dumps(record), saved['id']))
                app.db.commit()
            release.set()
            if change == 'append':
                result = await pending
                assert (result['id'] == saved['id'] if operation == 'metadata' else result == len(gif()))
            else:
                with pytest.raises(ValueError):
                    await pending
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)


async def test_successful_exact_validation_is_cached_but_failure_and_body_tamper_are_not(
        media_app, monkeypatch):
    from amplifier_web import output_media, resource_files
    app = media_app
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    exact = {**saved, 'messageId': args['messageId']}
    app.clients.attach('cache-reader')
    owner, calls = threading.get_ident(), []
    real = output_media.validate_media

    def validate(data):
        assert threading.get_ident() != owner
        calls.append(len(data))
        return real(data)

    monkeypatch.setattr(output_media, 'validate_media', validate)
    with app.clients.bind('cache-reader'):
        assert await app.outputs.media.bytes(exact) == gif()
        assert await app.outputs.media.bytes(exact) == gif()
        assert calls == [len(gif())]
        record = app.outputs.record(args['sessionId'], saved['id'])
        changed = copy.deepcopy(record)
        changed['media']['durationMs'] += 1
        app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(changed), saved['id']))
        app.db.commit()
        for _ in range(2):
            with pytest.raises(ValueError, match='metadata'):
                await app.outputs.media.bytes(exact)
        assert len(calls) == 3
        app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(record), saved['id']))
        app.db.commit()
        assert await app.outputs.media.bytes(exact) == gif()
        path = resource_files.root(app.db) / (record['body']['$resource'] + '.json')
        path.write_bytes(path.read_bytes().replace(b'base64', b'bad-64'))
        with pytest.raises(ValueError):
            await app.outputs.media.bytes(exact)


async def test_cancelled_retained_read_keeps_worker_owned_until_settled(media_app, monkeypatch):
    from amplifier_web import output_media
    app = media_app
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    app.clients.attach('cancel-reader')
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    real = output_media._saved_bytes

    def blocked(*values):
        entered.set()
        assert release.wait(10)
        try:
            return real(*values)
        finally:
            finished.set()

    monkeypatch.setattr(output_media, '_saved_bytes', blocked)
    with app.clients.bind('cancel-reader'):
        pending = asyncio.create_task(app.outputs.media.bytes({**saved, 'messageId': args['messageId']}))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done() and not finished.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert finished.is_set() and not app.outputs.media.cache
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)


async def _saved_retained_alias_for_reopen(app, monkeypatch):
    """Use real saves/retirement; retain no mutable message alias."""
    from contextlib import closing
    import sqlite3
    from amplifier_web import cold_display, resource_files
    session, _, _, directory, _ = cold_alias_corpus(app)
    sid = session['id']
    args = {**media_args_without_hydration(session), 'messageId': 'saved-web-assistant'}
    saved = (await app.outputs.media.attach(args, 'ui', 'metadata-cold-attach'))['result']
    messages = copy.deepcopy(dict.get(session, 'messages'))
    record = copy.deepcopy(app.outputs.record(sid, saved['id']))
    app._state['selectedSessionId'] = None
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    assert sid in app.cold_display.retire(force=True)
    assert not dict.__contains__(session, 'messages')
    reference = copy.deepcopy(dict.get(session, '_coldFields')['messages'])
    assert cold_display.load(app.db, reference) == messages
    home, workspace = app.data_dir, session['workspace']
    database = next(row[2] for row in app.db.execute('PRAGMA database_list') if row[1] == 'main')
    await app.close()
    # Shutdown may update the view pointer: use its committed owner, not a
    # guessed marker or an old in-memory projection reference.
    with closing(sqlite3.connect(database)) as db:
        owner = json.loads(db.execute(
            "SELECT value FROM state_records WHERE kind='session' AND id=?", (sid,)).fetchone()[0])
        view = cold_display.load(db, owner['$viewPayload'])
        assert view['_coldFields']['messages'] == reference
        assert cold_display.load(db, reference) == messages
        assert view['mediaRevision'] == 1
        artifact = resource_files.root(db) / (owner['$viewPayload']['$resource'] + '.json')
    return {'home': home, 'workspace': workspace, 'sid': sid, 'args': args,
            'saved': saved, 'record': record, 'view': owner['$viewPayload'],
            'messages': reference, 'viewPath': artifact, 'mediaRevision': view['mediaRevision'],
            'transcriptPath': directory / 'transcript.jsonl'}


@pytest.fixture
async def cold_retained_owner(media_app, monkeypatch):
    from amplifier_web.service import AppService
    from test_service import Runtime
    saved = await _saved_retained_alias_for_reopen(media_app, monkeypatch)
    app = AppService(saved['home'], workspace=saved['workspace'], runtime=Runtime())
    try:
        assert saved['sid'] not in {row['id'] for row in app._state['sessions']}
        assert saved['sid'] not in app._state['runtimeControl']
        assert saved['sid'] not in app._session_projection_refs
        app.clients.attach('cold-metadata-reader')
        app.clients.records['cold-metadata-reader']['selectedSessionId'] = saved['sid']
        client_record = app.clients.records['cold-metadata-reader']
        yield app, saved
    finally:
        # Revocation cases remove this synthetic client while a read is pending.
        # Restore only its fixture record after assertions, so normal app.close
        # can persist its existing dirty entry rather than failing teardown.
        if 'cold-metadata-reader' not in app.clients.records and 'client_record' in locals():
            app.clients.records['cold-metadata-reader'] = client_record
        await app.close()


def _retained_read_baseline(app, sid):
    """Only the test inspects saved owner/control bodies for equality."""
    return {
        'sessions': [(row['id'], id(row), tuple(dict.keys(row)),
                      copy.deepcopy(dict.get(row, '_coldFields', {})))
                     for row in app._state['sessions']],
        'controls': [(key, id(row), tuple(dict.keys(row)),
                      copy.deepcopy(dict.get(row, '_coldFields', {})))
                     for key, row in app._state['runtimeControl'].items()],
        'references': copy.deepcopy(app._session_projection_refs),
        'owners': app.db.execute(
            "SELECT kind,id,value FROM state_records WHERE id=? AND kind IN ('session','runtime') ORDER BY kind",
            (sid,)).fetchall(),
        'directory': app.db.execute('SELECT * FROM history_local_rows WHERE id=?', (sid,)).fetchall(),
        'outputs': app.db.execute('SELECT * FROM output_records WHERE session_id=? ORDER BY id', (sid,)).fetchall(),
        'receipts': app.db.execute('SELECT * FROM output_receipts ORDER BY id').fetchall(),
        'resources': app.db.execute('SELECT * FROM state_resources ORDER BY id').fetchall(),
        'changes': app.db.total_changes,
    }


def _forbid_retained_owner_hydration(patch, app):
    from amplifier_web import agent_canvas, cold_display, state_records

    def forbidden(*args, **kwargs):
        pytest.fail('Retained metadata read admitted or materialized a session/control/display body')

    async def forbidden_async(*args, **kwargs):
        forbidden()

    patch.setattr(app, '_admit_session', forbidden)
    patch.setattr(app, '_session', forbidden)
    patch.setattr(cold_display, 'load', forbidden)
    patch.setattr(state_records, 'load', forbidden)
    patch.setattr(state_records, 'load_owner', forbidden)
    patch.setattr(agent_canvas, 'state', forbidden)
    patch.setattr(app.outputs.media, 'identity', forbidden_async)
    patch.setattr(app, '_flush_pending_progress', forbidden_async)


async def test_corrupt_cold_view_reopen_keeps_registry_bridge_and_http_reads_metadata_only(
        media_app, authenticated_client, monkeypatch):
    from amplifier_web import cold_display, storage_migration
    from amplifier_web.server import create_app
    from test_service import Runtime
    saved = await _saved_retained_alias_for_reopen(media_app, monkeypatch)
    # Corrupt this actual owner's unrelated presentation body BEFORE reopening.
    # Its authentic output registry, resource index and media bytes stay intact.
    saved['viewPath'].write_bytes(b'{"unrelated":"damaged saved view"}')
    monkeypatch.setattr(storage_migration, 'maintenance', lambda service: None)
    server = await create_app(saved['home'], workspace=saved['workspace'], runtime=Runtime(),
                              voice=False, preload_providers=False, background_updates=False)
    app = server['service']
    await app.history.close()
    await app.event_log_view.close()
    # Isolate this read boundary from the background diagnostics publisher.
    # It independently saves revision/global/client state; retain total_changes
    # and all retained-record equality assertions during the measured reads.
    await app.diagnostics.close()
    client = await authenticated_client(server)
    try:
        sid = saved['sid']
        assert sid not in {row['id'] for row in app._state['sessions']}
        assert sid not in app._state['runtimeControl'] and sid not in app._session_projection_refs
        with pytest.raises(ValueError, match='saved display payload changed'):
            cold_display.load(app.db, saved['view'])
        app.clients.attach('cold-metadata-reader')
        app.clients.records['cold-metadata-reader']['selectedSessionId'] = sid
        client.session.headers['X-Amplifier-Client'] = 'cold-metadata-reader'
        before = _retained_read_baseline(app, sid)
        transcript = saved['transcriptPath'].read_bytes()
        record = saved['saved']
        query = {'sessionId': sid, 'messageId': saved['args']['messageId'],
                 'version': str(record['version']), 'sha256': record['sha256']}
        with monkeypatch.context() as patch:
            _forbid_retained_owner_hydration(patch, app)
            listed = await app.app_bridge('dispatch', {'action': 'outputs.list', 'args': {}}, sid)
            assert listed['accepted'] and [row['id'] for row in listed['result']['items']] == [record['id']]
            read = await app.app_bridge('dispatch', {
                'action': 'outputs.read', 'args': {'id': record['id']}}, sid)
            assert read['result']['origin'] == saved['record']['origin']
            assert read['result']['browserObservation']['status'] == 'unbound'
            assert read['state']['sessionId'] == listed['state']['sessionId'] == sid
            assert set(read['state']) == set(listed['state']) == {'revision', 'sessionId', '_stateAccess'}
            descriptors = await client.get('/api/outputs/media', params={
                'sessionId': sid, 'messageId': saved['args']['messageId']})
            assert descriptors.status == 200
            described = await descriptors.json()
            assert described['mediaRevision'] == saved['mediaRevision']
            assert [row['id'] for row in described['items']] == [record['id']]
            route = '/api/outputs/' + record['id'] + '/media'
            response = await client.get(route, params=query)
            assert response.status == 200 and await response.read() == gif()
            head = await client.head(route, params=query)
            assert head.status == 200 and await head.read() == b''
            assert head.headers['Content-Length'] == str(len(gif()))
            report = await client.post('/api/actions', json={
                'action': 'outputs.mediaReport', 'args': {
                    **query, 'version': record['version'], 'id': record['id'],
                    'generation': 1, 'sequence': 1, 'renderState': 'ready', 'playback': 'paused'}})
            assert report.status == 200 and (await report.json())['accepted']
        assert _retained_read_baseline(app, sid) == before
        assert saved['transcriptPath'].read_bytes() == transcript
        assert saved['viewPath'].read_bytes() == b'{"unrelated":"damaged saved view"}'
    finally:
        await client.close()


@pytest.mark.parametrize('kind', ['unknown', '', False, None, [], {}])
async def test_dormant_explicit_invalid_kind_cannot_gain_root_authority(
        cold_retained_owner, monkeypatch, kind):
    from amplifier_web.service import AppError
    app, saved = cold_retained_owner
    sid = saved['sid']
    # Keep the directory grouping normalized to root while preserving the
    # malformed explicit scalar as normal summary projection does.
    app.db.execute("""UPDATE history_local_rows SET session_kind='root',
        summary=json_set(summary,'$.sessionKind',json(?)) WHERE id=?""",
        (json.dumps(kind), sid))
    app.db.commit()
    assert app.directory.resolve(sid)['sessionKind'] == 'root'
    exact = {**saved['saved'], 'messageId': saved['args']['messageId']}
    with app.clients.bind('cold-metadata-reader'), monkeypatch.context() as patch:
        _forbid_retained_owner_hydration(patch, app)
        with pytest.raises(AppError, match='root'):
            await app.app_bridge('dispatch', {
                'action': 'outputs.read', 'args': {'id': exact['id']}}, sid)
        with pytest.raises(ValueError, match='root'):
            await app.outputs.media.describe(sid, exact['messageId'])
        for operation in (app.outputs.media.bytes, app.outputs.media.head):
            with pytest.raises(ValueError, match='root'):
                await operation(exact)
    assert sid not in {row['id'] for row in app._state['sessions']}
    assert not app.outputs.media.observations and not app.outputs.media.cache


def _change_cold_retained_owner(app, sid, change):
    """Synthetic metadata authority changes, not a real deletion workflow."""
    if change == 'hidden':
        hidden = app.db.execute('SELECT hidden_id FROM history_local_rows WHERE id=?', (sid,)).fetchone()[0]
        app._state.setdefault('hiddenNativeSessions', []).append(hidden)
    elif change == 'deleted':
        owner = app.outputs._owner(sid)
        app.db.execute('INSERT INTO managed_deletions VALUES(?,?,?,?)',
                       ('retained-owner-deletion', 'retained-owner-token', 'done',
                        json.dumps({'id': 'retained-owner-deletion', 'project': owner['nativeProject'],
                                    'ids': [sid], 'workspace': owner['workspace'], 'preserveWorkspace': True})))
    elif change == 'deleting':
        app.db.execute("UPDATE history_local_rows SET summary=json_set(summary,'$._deleting',1) WHERE id=?", (sid,))
    elif change == 'project':
        app.db.execute("UPDATE history_local_rows SET native_project=?,summary=json_set(summary,'$.nativeProject',?) WHERE id=?",
                       ('different-native-project', 'different-native-project', sid))
    elif change == 'root':
        app.db.execute("UPDATE history_local_rows SET session_kind='worker',summary=json_set(summary,'$.sessionKind','worker') WHERE id=?",
                       (sid,))
    elif change == 'client':
        app.clients.records['cold-metadata-reader']['selectedSessionId'] = None
    elif change == 'detached-client':
        app.clients.records.pop('cold-metadata-reader')
    else:
        raise AssertionError(change)
    app.db.commit()


@pytest.mark.parametrize('change', ['hidden', 'deleted', 'deleting', 'root', 'client',
                                  'unattached-client', 'project', 'missing'])
async def test_cold_retained_reads_keep_independent_owner_root_client_and_project_guards(
        cold_retained_owner, monkeypatch, change):
    from amplifier_web.service import AppError
    app, saved = cold_retained_owner
    sid = saved['sid']
    exact = {**saved['saved'], 'messageId': saved['args']['messageId']}
    # Bind before revoking metadata so client reconciliation cannot be mistaken
    # for the output guard under test.
    with app.clients.bind(None if change == 'unattached-client' else 'cold-metadata-reader'):
        if change in {'project', 'missing'}:
            before = _retained_read_baseline(app, sid)
            target = sid if change == 'project' else 'unregistered-output-owner'
            scope = {'nativeProject': 'different-native-project'} if change == 'project' else {}
            with monkeypatch.context() as patch:
                _forbid_retained_owner_hydration(patch, app)
                if change == 'project':
                    with pytest.raises(ValueError, match='registered'):
                        app.outputs._owner(sid, scope['nativeProject'])
                for action, args in [('outputs.list', {}),
                                     ('outputs.read', {'id': saved['saved']['id']})]:
                    with pytest.raises(AppError, match='registered'):
                        await app.app_bridge('dispatch', {'action': action, 'args': {**args, **scope}}, target)
                if change == 'missing':
                    app.clients.records['cold-metadata-reader']['selectedSessionId'] = target
                    with pytest.raises(ValueError, match='registered'):
                        await app.outputs.media.bytes({**exact, 'sessionId': target})
            assert _retained_read_baseline(app, sid) == before
            assert not app.outputs.media.observations and not app.outputs.media.cache
            return
        if change != 'unattached-client':
            _change_cold_retained_owner(app, sid, change)
        before = _retained_read_baseline(app, sid)
        with monkeypatch.context() as patch:
            _forbid_retained_owner_hydration(patch, app)
            if change in {'hidden', 'deleted', 'deleting'}:
                for action, args in [('outputs.list', {}), ('outputs.read', {'id': exact['id']})]:
                    with pytest.raises(AppError):
                        await app.app_bridge('dispatch', {'action': action, 'args': args}, sid)
            else:
                # Lists are not root-only and agent metadata is not browser-bound.
                assert (await app.app_bridge('dispatch', {'action': 'outputs.list', 'args': {}}, sid))['accepted']
                if change in {'client', 'unattached-client'}:
                    read = await app.app_bridge('dispatch', {'action': 'outputs.read', 'args': {'id': exact['id']}}, sid)
                    assert read['result']['browserObservation']['status'] == 'unbound'
                else:
                    with pytest.raises(AppError, match='root'):
                        await app.app_bridge('dispatch', {'action': 'outputs.read', 'args': {'id': exact['id']}}, sid)
                    assert (await app.outputs.media.describe(sid, exact['messageId']))['items'] == []
            for operation in (app.outputs.media.bytes, app.outputs.media.head):
                with pytest.raises(ValueError):
                    await operation(exact)
            with pytest.raises(AppError):
                await app.dispatch('outputs.mediaReport', {
                    **{key: exact[key] for key in ('sessionId', 'messageId', 'id', 'version', 'sha256')},
                    'generation': 1, 'sequence': 1, 'renderState': 'ready', 'playback': 'paused'},
                    include_state=False)
        assert _retained_read_baseline(app, sid) == before
        assert not app.outputs.media.observations and not app.outputs.media.cache


@pytest.mark.parametrize('boundary,change', [
    (boundary, change)
    for boundary in ('metadata-index', 'head-index', 'bytes-index',
                     'bytes-body', 'bytes-validation', 'bytes-final-index')
    for change in ('hidden', 'deleted', 'deleting', 'project', 'root', 'client', 'detached-client')
    # Agent metadata has no attached-client dependency; removal of a current
    # UI context record is meaningful only for the client-bound consumers.
    if (boundary, change) != ('metadata-index', 'detached-client')
])
async def test_cold_retained_every_worker_boundary_rechecks_current_authority(
        cold_retained_owner, monkeypatch, boundary, change):
    from amplifier_web import output_media, resource_files
    from amplifier_web.service import AppError
    app, saved = cold_retained_owner
    sid = saved['sid']
    exact = {**saved['saved'], 'messageId': saved['args']['messageId']}
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    owner_thread = threading.get_ident()
    real = resource_files.settle_worker
    selected = (output_media._saved_bytes if boundary == 'bytes-body' else
                output_media._validate_retained_source if boundary == 'bytes-validation' else output_media._signature)
    occurrence = 2 if boundary == 'bytes-final-index' else 1
    calls = 0

    def blocked(function, values):
        assert threading.get_ident() != owner_thread
        result = function(*values)
        entered.set()
        try:
            assert release.wait(10)
            return result
        finally:
            finished.set()

    async def worker(function, *values):
        nonlocal calls
        # Count actual top-level worker awaits, not _signature's nested calls
        # inside the body/validation workers.
        if function is selected:
            calls += 1
            if calls == occurrence:
                return await real(blocked, function, values)
        return await real(function, *values)

    with app.clients.bind('cold-metadata-reader'):
        with monkeypatch.context() as patch:
            _forbid_retained_owner_hydration(patch, app)
            patch.setattr(resource_files, 'settle_worker', worker)
            operation = (app.app_bridge('dispatch', {'action': 'outputs.read', 'args': {'id': exact['id']}}, sid)
                         if boundary == 'metadata-index' else app.outputs.media.head(exact)
                         if boundary == 'head-index' else app.outputs.media.bytes(exact))
            pending = asyncio.create_task(operation)
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                _change_cold_retained_owner(app, sid, change)
                before = _retained_read_baseline(app, sid)
                release.set()
                if boundary == 'metadata-index' and change == 'client':
                    # Only byte/HEAD/report consumers require an attached browser.
                    result = await pending
                    assert result['accepted'] and result['result']['browserObservation']['status'] == 'unbound'
                else:
                    with pytest.raises((ValueError, AppError)):
                        await pending
                assert finished.is_set()
                assert _retained_read_baseline(app, sid) == before
                assert not app.outputs.media.cache and not app.outputs.media.observations
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('legacy_revision', ['known', 'unavailable', 'invalid'])
async def test_old_directory_summary_revision_is_saved_scalar_or_explicitly_unavailable(
        cold_retained_owner, monkeypatch, legacy_revision):
    app, saved = cold_retained_owner
    sid, record = saved['sid'], saved['saved']
    assert app.outputs._owner(sid)['mediaRevision'] == saved['mediaRevision'] == 1
    app.db.execute("UPDATE history_local_rows SET summary=json_remove(summary,'$.mediaRevision') WHERE id=?", (sid,))
    # Simulate the legacy keyed-scalar layout using the genuinely saved revision
    # witnessed in the view, never a manufactured default. Normal pointer-only
    # owner records do not carry it; their absence must remain unavailable.
    if legacy_revision == 'known':
        app.db.execute("UPDATE state_records SET value=json_set(value,'$.mediaRevision',?) WHERE kind='session' AND id=?",
                       (saved['mediaRevision'], sid))
    elif legacy_revision == 'invalid':
        app.db.execute("UPDATE state_records SET value=json_set(value,'$.mediaRevision','not-a-revision') WHERE kind='session' AND id=?",
                       (sid,))
    else:
        app.db.execute("UPDATE state_records SET value=json_remove(value,'$.mediaRevision') WHERE kind='session' AND id=?", (sid,))
    project = app.outputs._owner(sid)['nativeProject']
    # Ordinary native metadata reconciliation also reprojects dormant summaries.
    # It must not turn an omitted revision into a seemingly authoritative zero.
    app.directory.refresh_native([{'nativeProject': project, 'nativeIdentity': sid}],
                                 [], app._state)
    assert app.db.execute("SELECT json_type(summary,'$.mediaRevision') FROM history_local_rows WHERE id=?",
                          (sid,)).fetchone()[0] is None
    app.db.commit()
    before = _retained_read_baseline(app, sid)
    exact = {**record, 'messageId': saved['args']['messageId']}
    with app.clients.bind('cold-metadata-reader'):
        with monkeypatch.context() as patch:
            _forbid_retained_owner_hydration(patch, app)
            assert app.outputs._owner(sid)['mediaRevision'] == (1 if legacy_revision == 'known' else None)
            if legacy_revision == 'known':
                result = await app.outputs.media.describe(sid, exact['messageId'])
                assert result['mediaRevision'] == 1 and [row['id'] for row in result['items']] == [record['id']]
            else:
                with pytest.raises(ValueError, match='revision is unavailable'):
                    await app.outputs.media.describe(sid, exact['messageId'])
            assert (await app.app_bridge('dispatch', {'action': 'outputs.list', 'args': {}}, sid))['accepted']
            assert (await app.app_bridge('dispatch', {
                'action': 'outputs.read', 'args': {'id': record['id']}}, sid))['result']['id'] == record['id']
            assert await app.outputs.media.bytes(exact) == gif()
            assert await app.outputs.media.head(exact) == len(gif())
            assert (await app.outputs.media.observe({
                **exact, 'generation': 1, 'sequence': 1, 'renderState': 'ready', 'playback': 'paused'}, 'ui'))['accepted']
    assert _retained_read_baseline(app, sid) == before


async def test_scalar_output_owner_requires_explicit_unambiguous_project_and_live_root_kind(media_app, monkeypatch):
    from amplifier_web.service import AppError
    from amplifier_web.session_identity import project
    app = media_app
    args = media_args(app)
    saved = (await app.outputs.media.attach(args, 'ui', 'owner-facts-attach'))['result']
    source = app._session(args['sessionId'], hydrate=False)
    native_project = project(source)
    facts = app.outputs._owner(source['id'])
    assert set(facts) == {'id', 'sessionKind', 'nativeProject', 'workspace', 'workingDirectory', 'mediaRevision'}
    assert all(value is None or type(value) in (str, int) for value in facts.values())
    assert app._state['selectedSessionId'] == source['id']
    for identity in (None, '', ' ', 'unregistered-output-owner'):
        with pytest.raises(ValueError):
            app.outputs._owner(identity)
    for kind in (None, '', False, 'worker', 'internal'):
        source['sessionKind'] = kind
        assert app.outputs._owner(source['id'])['sessionKind'] == kind
        with pytest.raises(ValueError, match='root'):
            await app.outputs.media.metadata(source['id'], saved['id'], {})
    source['sessionKind'] = 'root'
    source['_deleting'] = True
    with pytest.raises(ValueError, match='being deleted'):
        app.outputs._owner(source['id'])
    source.pop('_deleting')
    await app.dispatch('session.create', {'title': 'Synthetic conflicting native identity'})
    peer = app._session()
    with monkeypatch.context() as patch:
        _forbid_retained_owner_hydration(patch, app)
        for action, args in [('outputs.list', {}), ('outputs.read', {'id': saved['id']})]:
            with pytest.raises(AppError, match='calling conversation'):
                await app.app_bridge('dispatch', {
                    'action': action, 'args': {**args, 'sessionId': peer['id']}}, source['id'])
    peer.update(nativeIdentity=source['id'], nativeProject='different-native-project')
    with pytest.raises(ValueError, match='more than one project'):
        app.outputs._owner(source['id'])
    assert app.outputs._owner(source['id'], native_project)['id'] == source['id']
    with monkeypatch.context() as patch:
        _forbid_retained_owner_hydration(patch, app)
        for action, args in [('outputs.list', {}), ('outputs.read', {'id': saved['id']})]:
            result = await app.app_bridge('dispatch', {
                'action': action, 'args': {**args, 'nativeProject': native_project}}, source['id'])
            assert result['accepted']
            assert (result['result']['items'][0]['id'] if action == 'outputs.list'
                    else result['result']['id']) == saved['id']
    # A qualified request must not leak its scope into a later unqualified read.
    with pytest.raises(ValueError, match='more than one project'):
        app.outputs._owner(source['id'])
    with pytest.raises(ValueError, match='registered'):
        app.outputs._owner(source['id'], 'unregistered-native-project')


@pytest.mark.parametrize('cold', [False, True])
async def test_display_only_flip_during_real_identity_worker_prevents_new_attach_publication(
        media_app, monkeypatch, cold):
    from amplifier_web import cold_display, media_message_identity
    app = media_app
    session, _, _, _, _ = cold_alias_corpus(app)
    sid = session['id']
    args = {**media_args_without_hydration(session), 'messageId': 'saved-web-assistant'}
    original_messages = copy.deepcopy(dict.get(session, 'messages'))
    if cold:
        app._state['selectedSessionId'] = None
        monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
        assert sid in app.cold_display.retire(force=True)
        assert not dict.__contains__(session, 'messages')
        reference = copy.deepcopy(dict.get(session, '_coldFields')['messages'])
        assert cold_display.load(app.db, reference) == original_messages
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    owner_thread = threading.get_ident()
    real = media_message_identity.complete

    def blocked(*values):
        assert threading.get_ident() != owner_thread
        result = real(*values)  # Real canonical/cold-alias qualification first.
        entered.set()
        try:
            assert release.wait(10)
            return result
        finally:
            finished.set()

    monkeypatch.setattr(media_message_identity, 'complete', blocked)
    command = 'display-only-worker-flip'
    pending = asyncio.create_task(app.outputs.media.attach(args, 'ui', command))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with app.lock:
            # For cold aliases this deliberately opens the REAL saved body to
            # change its row, then retires it normally into a new committed ref.
            session['messages'][0]['displayOnly'] = True
            if cold:
                assert sid in app.cold_display.retire(force=True)
                replacement = copy.deepcopy(dict.get(session, '_coldFields')['messages'])
                assert replacement != reference and not dict.__contains__(session, 'messages')
                expected = copy.deepcopy(original_messages)
                expected[0]['displayOnly'] = True
                assert cold_display.load(app.db, replacement) == expected
        release.set()
        with pytest.raises(ValueError):
            await pending
        assert finished.is_set()
        assert app.outputs.store.list(sid)['items'] == []
        assert app.db.execute('SELECT COUNT(*) FROM output_records WHERE session_id=?', (sid,)).fetchone()[0] == 0
        assert app.db.execute('SELECT COUNT(*) FROM output_receipts').fetchone()[0] == 0
        assert dict.get(session, 'mediaRevision', 0) == 0
        if cold:
            assert dict.get(session, '_coldFields')['messages'] == replacement
            assert not dict.__contains__(session, 'messages')
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_retained_reads_release_worker_permits_before_waiting_for_canonical_attach_lock(
        media_app, monkeypatch):
    """Two reads cannot invert canonical attachment's app-lock/worker ordering."""
    from amplifier_web import output_media, resource_files
    app = media_app
    session, _, _, _, _ = cold_alias_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': 'saved-web-assistant'}
    saved = (await app.outputs.media.attach(args, 'ui', 'permit-original'))['result']
    exact = {**saved, 'messageId': args['messageId']}
    app.clients.attach('permit-reader')
    app.clients.records['permit-reader']['selectedSessionId'] = session['id']
    qualified, continue_attach = asyncio.Event(), asyncio.Event()
    reading, release_reads, writing = threading.Event(), threading.Event(), threading.Event()
    calls, readers = 0, 0
    counter = threading.Lock()
    real_identity = app.outputs.media.identity
    real_signature, real_write = output_media._signature, resource_files.write_prepared

    async def identity(*values, **options):
        nonlocal calls
        proof = await real_identity(*values, **options)
        calls += 1
        if calls == 2:
            qualified.set()
            await continue_attach.wait()
        return proof

    def signature(source):
        nonlocal readers
        with counter:
            readers += 1
            first = readers <= 2
            if readers == 2:
                reading.set()
        if first:
            assert release_reads.wait(10)
        return real_signature(source)

    def write(*values):
        writing.set()
        assert release_reads.wait(10)
        return real_write(*values)

    monkeypatch.setattr(app.outputs.media, 'identity', identity)
    monkeypatch.setattr(output_media, '_signature', signature)
    monkeypatch.setattr(resource_files, 'write_prepared', write)
    tasks = []
    try:
        attach = asyncio.create_task(app.outputs.media.attach(args, 'ui', 'permit-new-attach'))
        tasks.append(attach)
        await asyncio.wait_for(qualified.wait(), 10)
        with app.clients.bind('permit-reader'):
            tasks.extend(asyncio.create_task(app.outputs.media.bytes(exact)) for _ in range(2))
        assert await asyncio.to_thread(reading.wait, 10)
        continue_attach.set()
        assert await asyncio.to_thread(writing.wait, 10)
        assert app.lock.locked()
        release_reads.set()
        result, first, second = await asyncio.wait_for(asyncio.gather(*tasks), 10)
        assert result['accepted'] and first == second == gif()
        assert app.outputs.media.workers._value == 2
    finally:
        continue_attach.set()
        release_reads.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)