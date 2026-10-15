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
        from amplifier_web import cold_display
        def no_display(*args, **kwargs):
            pytest.fail('Retained metadata bridge hydrated unrelated display history')
        with monkeypatch.context() as patch:
            patch.setattr(cold_display, 'load', no_display)
            metadata = (await service.app_bridge('dispatch', {
                'action': 'outputs.read', 'args': {'id': saved['id']}}, sid))['result']
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
                assert (await service.dispatch('canvas.openGif', exact, include_state=False))['accepted']

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
    with pytest.raises(ValueError):
        await app.outputs.media.attach(args, 'ui', 'removed-origin-new-attach')
    home, workspace = app.data_dir, session['workspace']
    await app.close()
    reopened = AppService(home, workspace=workspace, runtime=Runtime())
    try:
        reopened.clients.attach('retained-reader')
        reopened.clients.records['retained-reader']['selectedSessionId'] = sid
        monkeypatch.setattr(reopened.outputs.media, 'identity', forbidden)
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