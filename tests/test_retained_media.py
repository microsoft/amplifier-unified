"""Retained-object regressions; source fixtures, not playback acceptance."""
import asyncio
import copy
import json
from pathlib import Path
import threading

import pytest

from test_output_media import (
    canonical_corpus, cold_alias_corpus, freeze_alias_display, gif,
    gif_open_args, media_app, media_args, media_args_without_hydration,
)


def forbid_history(monkeypatch, app):
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web import automatic_history, conversation_navigation, media_saved_aliases

    def forbidden(*args, **kwargs):
        pytest.fail('Retained media read qualified or hydrated transcript history')

    monkeypatch.setattr(app.outputs.media, 'identity', forbidden)
    monkeypatch.setattr(SessionHistoryStore, 'indexed_messages', forbidden)
    monkeypatch.setattr(conversation_navigation, 'source', forbidden)
    monkeypatch.setattr(automatic_history, 'read_transcript', forbidden)
    monkeypatch.setattr(media_saved_aliases.SavedAliases, 'resolve', forbidden)
    monkeypatch.setattr(app.cold_display, 'hydrate', forbidden)


@pytest.mark.parametrize('shape', ['canonical-tail', 'resident-alias', 'cold-alias'])
async def test_retained_reads_survive_repeated_real_saves_origin_rewrite_and_reload(
        media_app, monkeypatch, shape):
    from amplifier_web import automatic_history, output_media, resource_files
    from amplifier_web.service import AppError, AppService
    from test_service import Runtime
    app = media_app
    if shape == 'canonical-tail':
        session, corpus, store, directory, mid = canonical_corpus(app)
    else:
        session, corpus, store, directory, canonical = cold_alias_corpus(app)
        mid = 'saved-web-assistant'
        if shape == 'cold-alias':
            session, _ = await freeze_alias_display(app, session)
    args = {**media_args_without_hydration(session), 'messageId': mid}
    saved = (await app.outputs.media.attach(args, 'ui', 'retained-original'))['result']
    original = app.outputs.record(session['id'], saved['id'])
    body_bytes = (resource_files.root(app.db) / (original['body']['$resource'] + '.json')).read_bytes()
    Path(session['workspace'], args['path']).unlink()
    app.clients.attach('retained-browser')
    owner = threading.get_ident()
    workers = []
    real_validate = output_media.validate_retained_body

    def validation(data, record):
        workers.append(threading.get_ident())
        assert threading.get_ident() != owner
        return real_validate(data, record)

    values = gif_open_args(args, saved)
    with app.clients.bind('retained-browser'):
        queue = app.subscribe()
        try:
            with monkeypatch.context() as patch:
                forbid_history(patch, app)
                patch.setattr(output_media, 'validate_retained_body', validation)
                for cycle in (1, 2):
                    corpus.extend([{'role': 'user', 'content': f'Unrelated saved turn {cycle}'},
                                   {'role': 'assistant', 'content': f'Unrelated saved answer {cycle}'}])
                    store.save(session['id'], corpus, {}, preserve_system=True)
                    session['nativeRevision'] = automatic_history.revision(session)
                    session['executionRevision'] = session.get('executionRevision', 0) + 1
                    assert (await app.outputs.media.describe(session['id'], mid))['items'][0]['id'] == saved['id']
                    assert await app.outputs.media.bytes(values) == gif()
                    assert await app.outputs.media.head(values) == len(gif())
                    assert (await app.outputs.media.observe({
                        **values, 'generation': 1, 'sequence': cycle, 'renderState': 'ready',
                        'playback': 'paused'}, 'ui'))['accepted']
                    metadata = (await app.dispatch('outputs.read', {
                        'sessionId': session['id'], 'id': saved['id']}, include_state=False))['result']
                    assert metadata['browserObservation']['status'] == 'observed'
                    assert (await app.dispatch('canvas.openGif', values, include_state=False))['result']['status'] == 'opened'
                # Replace and truncate actual saved history, not just a mocked
                # proof. Retained origins are historical provenance thereafter.
                corpus[1] = {'role': 'user', 'content': 'Origin replaced by an edited user turn'}
                store.save(session['id'], corpus[:4], {}, preserve_system=True)
                session['nativeRevision'] = automatic_history.revision(session)
                if shape != 'cold-alias':
                    session['messages'] = [{'id': 'replacement-row', 'role': 'user', 'text': 'Edited history'}]
                assert await app.outputs.media.bytes(values) == gif()
                assert (await app.dispatch('canvas.openGif', values, include_state=False))['result']['status'] == 'opened'
                if shape == 'cold-alias':
                    assert not dict.__contains__(session, 'messages')
                app._save_changes()
        finally:
            app.unsubscribe(queue)
        # Restore only the source file so a new admission fails on the removed
        # provenance, not an incidental file-not-found.
        Path(session['workspace'], args['path']).write_bytes(gif())
        with pytest.raises((ValueError, AppError)):
            await app.outputs.media.attach(args, 'ui', 'new-after-origin-rewrite')
        Path(session['workspace'], args['path']).unlink()
    assert workers and owner not in workers
    assert app.outputs.record(session['id'], saved['id']) == original
    assert (resource_files.root(app.db) / (original['body']['$resource'] + '.json')).read_bytes() == body_bytes
    native = (directory / 'transcript.jsonl').read_bytes()
    await app.close()
    reopened = AppService(app.data_dir, workspace=session['workspace'], runtime=Runtime())
    try:
        reopened.clients.attach('reloaded-retained', resume='retained-browser')
        with reopened.clients.bind('reloaded-retained'):
            with monkeypatch.context() as patch:
                forbid_history(patch, reopened)
                assert await reopened.outputs.media.bytes(values) == gif()
                assert (await reopened.dispatch('canvas.openGif', values, include_state=False))['result']['status'] == 'opened'
        assert (directory / 'transcript.jsonl').read_bytes() == native
        assert reopened.outputs.record(session['id'], saved['id']) == original
    finally:
        await reopened.close()


@pytest.mark.parametrize('operation', ['bytes', 'head', 'canvas'])
async def test_real_append_between_retained_read_stages(media_app, monkeypatch, operation):
    from amplifier_web import output_media
    app = media_app
    session, corpus, store, directory, mid = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': mid}
    record = (await app.outputs.media.attach(args, 'ui', 'between-stages'))['result']
    original = app.outputs.record(session['id'], record['id'])
    app.clients.attach('between-stage-browser')
    values = gif_open_args(args, record)
    entered, release = threading.Event(), threading.Event()
    hook = '_signature' if operation == 'head' else '_saved_bytes'
    real = getattr(output_media, hook)

    def blocked(*args):
        result = real(*args)
        entered.set()
        assert release.wait(10)
        return result

    with monkeypatch.context() as patch:
        forbid_history(patch, app)
        patch.setattr(output_media, hook, blocked)
        with app.clients.bind('between-stage-browser'):
            call = (app.dispatch('canvas.openGif', values, include_state=False) if operation == 'canvas'
                    else getattr(app.outputs.media, operation)(values))
            pending = asyncio.create_task(call)
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                for cycle in (1, 2):
                    corpus.append({'role': 'user', 'content': f'Append while retained worker waits {cycle}'})
                    store.save(session['id'], corpus, {}, preserve_system=True)
                raw = (directory / 'transcript.jsonl').read_bytes()
                release.set()
                result = await asyncio.wait_for(pending, 10)
                assert (result['result']['status'] == 'opened' if operation == 'canvas'
                        else result == (gif() if operation == 'bytes' else len(gif())))
                assert (directory / 'transcript.jsonl').read_bytes() == raw
                assert app.outputs.record(session['id'], record['id']) == original
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('change', ['remove', 'client', 'root', 'id', 'version', 'hash', 'body', 'metadata', 'index', 'corrupt'])
@pytest.mark.parametrize('operation', ['bytes', 'head', 'canvas'])
async def test_retained_immutable_or_client_revocation_across_await_fails(
        media_app, monkeypatch, change, operation):
    from amplifier_web import output_media, resource_files
    from amplifier_web.service import AppError
    app = media_app
    session, _, _, _, mid = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': mid}
    record = (await app.outputs.media.attach(args, 'ui', 'revocation-origin'))['result']
    internal = app.outputs.record(session['id'], record['id'])
    app.clients.attach('revocation-browser')
    values = gif_open_args(args, record)
    entered, release = threading.Event(), threading.Event()
    hook = '_signature' if operation == 'head' else '_saved_bytes'
    real = getattr(output_media, hook)

    def blocked(*args):
        # Hold before I/O so a changed backing file is not mistaken for merely
        # changed history. Cache/index validation still uses its actual reader.
        entered.set()
        assert release.wait(10)
        return real(*args)

    with monkeypatch.context() as patch:
        forbid_history(patch, app)
        patch.setattr(output_media, hook, blocked)
        with app.clients.bind('revocation-browser'):
            call = (app.dispatch('canvas.openGif', values, include_state=False) if operation == 'canvas'
                    else getattr(app.outputs.media, operation)(values))
            pending = asyncio.create_task(call)
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                async with app.lock:
                    if change == 'remove':
                        app.db.execute('DELETE FROM output_records WHERE id=?', (record['id'],))
                    elif change == 'client':
                        app.clients.records['revocation-browser']['selectedSessionId'] = None
                    elif change == 'root':
                        session['sessionKind'] = 'worker'
                    elif change == 'index':
                        app.db.execute('UPDATE state_resources SET value=? WHERE id=?',
                            ('{"$blob":"' + '0' * 64 + '"}', internal['body']['$resource']))
                    elif change == 'corrupt':
                        path = resource_files.root(app.db) / (internal['body']['$resource'] + '.json')
                        path.unlink()  # HEAD is body-free but requires an existing regular backing file.
                    else:
                        updated = copy.deepcopy(internal)
                        key, value = {'id': ('id', 'different-record'),
                                      'version': ('version', 2), 'hash': ('sha256', '0' * 64),
                                      'body': ('body', None),
                                      'metadata': ('media', {**internal['media'], 'durationMs': 270})}[change]
                        if change == 'body':
                            value = resource_files.put(app.db, {'encoding': 'base64', 'data': 'AAAA'})
                        updated[key] = value
                        app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(updated), record['id']))
                    app.db.commit()
                release.set()
                with pytest.raises((ValueError, AppError, OSError)):
                    await asyncio.wait_for(pending, 10)
                assert not any(row.get('kind') == 'gif' for row in app.state.get('canvasArtifacts', []))
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)


async def test_unlink_keeps_exact_library_version_report_and_relink_cas(media_app, monkeypatch):
    from amplifier_web.service import AppError
    app = media_app
    session, _, _, _, mid = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': mid}
    record = (await app.outputs.media.attach(args, 'ui', 'unlink-original'))['result']
    original = app.outputs.record(session['id'], record['id'])
    app.clients.attach('unlink-browser')
    values = gif_open_args(args, record)
    with app.clients.bind('unlink-browser'):
        opened = (await app.dispatch('canvas.openGif', values, include_state=False))['result']
        versions = copy.deepcopy(next(row for row in app.state['canvasArtifacts'] if row['id'] == opened['id']))
        await app.dispatch('outputs.unlink', {'sessionId': session['id'], 'id': record['id'], 'expectedRevision': 1}, include_state=False)
        queue = app.subscribe()
        try:
            with monkeypatch.context() as patch:
                forbid_history(patch, app)
                assert (await app.outputs.media.describe(session['id'], mid))['items'] == []
                assert app.outputs.store.list(session['id'])['items'] == []
                assert await app.outputs.media.bytes(values) == gif()
                await app.outputs.media.observe({**values, 'generation': 1, 'sequence': 1,
                    'renderState': 'ready', 'playback': 'paused'}, 'ui')
                metadata = (await app.dispatch('outputs.read', {'sessionId': session['id'], 'id': record['id']}, include_state=False))['result']
                assert metadata['attached'] is False
                assert metadata['browserObservation']['status'] == 'observed'
                assert metadata['browserObservation']['linked'] is False
                assert (await app.dispatch('canvas.openGif', values, include_state=False))['result']['id'] == opened['id']
                # Old wrapper IDs/versions/bodies survive; reopening does not
                # fabricate an active message or append another saved version.
                current = next(row for row in app.state['canvasArtifacts'] if row['id'] == opened['id'])
                assert current['body'] == versions['body'] and current['versions'] == versions['versions']
                assert current['publications'] == [] and current['messageId'] is None
        finally:
            app.unsubscribe(queue)
        with pytest.raises(AppError, match='revision|relationship'):
            await app.dispatch('outputs.relink', {'sessionId': session['id'], 'id': record['id'], 'expectedRevision': 1}, include_state=False)
        await app.dispatch('outputs.relink', {'sessionId': session['id'], 'id': record['id'], 'expectedRevision': 2}, include_state=False)
    current = app.outputs.record(session['id'], record['id'])
    assert current['body'] == original['body'] and current['origin'] == original['origin']
    assert current['revision'] == 3 and current['linked'] is True


async def test_retained_http_head_range_original_download_after_origin_removal(
        authenticated_client, tmp_path, monkeypatch):
    from amplifier_web import output_media
    from amplifier_web.server import create_app
    from test_service import Runtime
    server = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                              voice=False, preload_providers=False, background_updates=False)
    app = server['service']
    await app.dispatch('session.create', {'title': 'Retained HTTP save corpus'})
    session, corpus, store, directory, mid = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': mid}
    record = (await app.outputs.media.attach(args, 'ui', 'http-retained'))['result']
    Path(session['workspace'], args['path']).unlink()
    app.clients.attach('retained-http')
    app.clients.attach('foreign-http')
    app.clients.records['foreign-http']['selectedSessionId'] = None
    client = await authenticated_client(server)
    client.session.headers['X-Amplifier-Client'] = 'retained-http'
    query = {'sessionId': session['id'], 'messageId': mid,
             'version': str(record['version']), 'sha256': record['sha256']}
    route = '/api/outputs/' + record['id'] + '/media'
    for cycle in (1, 2):
        corpus.append({'role': 'user', 'content': f'Actual unrelated HTTP save {cycle}'})
        store.save(session['id'], corpus, {}, preserve_system=True)
    store.save(session['id'], corpus[2:], {}, preserve_system=True)
    raw = (directory / 'transcript.jsonl').read_bytes()
    await app.dispatch('outputs.unlink', {'sessionId': session['id'], 'id': record['id'], 'expectedRevision': 1}, include_state=False)
    with monkeypatch.context() as patch:
        forbid_history(patch, app)
        empty = await client.get('/api/outputs/media', params={'sessionId': session['id'], 'messageId': mid})
        assert empty.status == 200 and (await empty.json())['items'] == []
        with patch.context() as head_patch:
            def no_body(*args, **kwargs):
                pytest.fail('HEAD decoded retained media')
            head_patch.setattr(output_media, '_saved_bytes', no_body)
            head_patch.setattr(output_media, 'validate_retained_body', no_body)
            head = await client.head(route, params=query, headers={'Range': 'bytes=2-4'})
            assert head.status == 206 and await head.read() == b''
            assert head.headers['Content-Length'] == '3'
        ranged = await client.get(route, params=query, headers={'Range': 'bytes=2-4'})
        assert ranged.status == 206 and await ranged.read() == gif()[2:5]
        assert ranged.headers['Content-Range'] == f'bytes 2-4/{len(gif())}'
        downloaded = await client.get(route, params={**query, 'download': '1'})
        assert downloaded.status == 200 and await downloaded.read() == gif()
        assert downloaded.headers['Content-Disposition'].startswith('attachment;')
        for key, wrong in [('sessionId', 'foreign'), ('messageId', 'wrong-provenance'),
                           ('version', '2'), ('sha256', '0' * 64)]:
            assert (await client.get(route, params={**query, key: wrong})).status == 400
        client.session.headers['X-Amplifier-Client'] = 'foreign-http'
        assert (await client.get(route, params=query)).status == 400
    assert (directory / 'transcript.jsonl').read_bytes() == raw


async def test_fork_legacy_gif_wrapper_remaps_every_saved_reference_and_filters_future(
        media_app):
    import hashlib
    from amplifier_web.automatic_history import display_message
    from test_output_media import _exact_media_fork_corpus
    app = media_app
    source, corpus, store = _exact_media_fork_corpus(app, partial=True)
    raw = (store.directory(source['id']) / 'transcript.jsonl').read_bytes()
    app.clients.attach('fork-wrapper-browser')
    originals = {}
    wrappers = {}
    with app.clients.bind('fork-wrapper-browser'):
        for index in (6, 10):
            mid = display_message(corpus[index], index, source)['id']
            args = {**media_args_without_hydration(source), 'messageId': mid}
            saved = (await app.outputs.media.attach(args, 'ui', f'wrapper-media-{index}'))['result']
            originals[index] = app.outputs.record(source['id'], saved['id'])
            opened = (await app.dispatch('canvas.openGif', gif_open_args(args, saved), include_state=False))['result']
            wrapper = next(row for row in app.state['canvasArtifacts'] if row['id'] == opened['id'])
            # Compatibility corpus: retained old GIF wrapper with an existing
            # user anchor. Production openGif stays library-only; no migration.
            anchor = display_message(corpus[index - 4 if index == 6 else index - 1],
                                     index - 4 if index == 6 else index - 1, source)['id']
            wrapper['messageId'] = anchor
            wrapper['versions'][0]['messageId'] = anchor
            wrapper['publications'] = [{'messageId': anchor, 'version': 1}]
            wrappers[index] = copy.deepcopy(wrapper)
        await app.dispatch('session.fork', {'id': source['id'], 'turn': 2}, include_state=False)
        target = app._session()
        media = [row for row in app.outputs.store.list(target['id'])['items'] if row['kind'] == 'media']
        assert len(media) == 1 and media[0]['forkSource']['outputId'] == originals[6]['id']
        clones = [row for row in app.state['canvasArtifacts'] if row.get('sessionId') == target['id'] and row['kind'] == 'gif']
        assert len(clones) == 1
        for definition in [clones[0], *clones[0]['versions']]:
            reference = app.state_resource(definition['body']['$resource'])['mediaReference']
            assert reference['item']['id'] == media[0]['id']
            assert reference['item']['sessionId'] == target['id']
            assert reference['messageId'] == media[0]['origin']['messageId']
            assert reference['item']['sha256'] == hashlib.sha256(gif()).hexdigest()
        assert media[0]['body'] == originals[6]['body']
        for index, original in wrappers.items():
            assert next(row for row in app.state['canvasArtifacts'] if row['id'] == original['id']) == original
    assert (store.directory(source['id']) / 'transcript.jsonl').read_bytes() == raw


async def test_zero_loaded_restart_fast_reads_leave_exact_display_unchanged(media_app, monkeypatch):
    from amplifier_web import cold_display
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = media_app
    session, _, _, directory, mid = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': mid}
    saved = (await app.outputs.media.attach(args, 'ui', 'zero-loaded-save'))['result']
    sid, workspace = session['id'], session['workspace']
    original = app.outputs.record(sid, saved['id'])
    raw = (directory / 'transcript.jsonl').read_bytes()
    await app.close()
    reopened = AppService(app.data_dir, workspace=workspace, runtime=Runtime())
    try:
        source = reopened._session(sid, hydrate=False)
        before = copy.deepcopy(cold_display.raw(source))
        assert len(dict.get(source, 'messages', [])) == 0
        assert not source.get('historyLoaded')
        reopened.clients.attach('zero-loaded-reader')
        reopened.clients.records['zero-loaded-reader']['selectedSessionId'] = sid
        with monkeypatch.context() as patch:
            forbid_history(patch, reopened)
            def no_loader(*args, **kwargs):
                pytest.fail('Fast retained read invoked the intentional unrun history loader')
            patch.setattr(reopened.history, 'load', no_loader)
            with reopened.clients.bind('zero-loaded-reader'):
                values = gif_open_args(args, saved)
                assert (await reopened.outputs.media.describe(sid, mid))['items'][0]['id'] == saved['id']
                assert await reopened.outputs.media.head(values) == len(gif())
                assert await reopened.outputs.media.bytes(values) == gif()
                metadata = (await reopened.dispatch('outputs.read', {'sessionId': sid,
                    'id': saved['id']}, include_state=False))['result']
                assert metadata['origin'] == original['origin']
                assert (await reopened.dispatch('canvas.openGif', values, include_state=False))['result']['status'] == 'opened'
            assert cold_display.raw(source) == before
        assert reopened.outputs.record(sid, saved['id']) == original
        assert (directory / 'transcript.jsonl').read_bytes() == raw
    finally:
        await reopened.close()


@pytest.mark.parametrize('admission', ['new', 'legacy'])
async def test_empty_title_preserves_media_record_adapter_bytes_and_library(media_app, admission):
    from amplifier_web import resource_files
    app = media_app
    args = media_args(app)
    args['title'] = '' if admission == 'new' else 'Previously titled'
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    if admission == 'legacy':
        legacy = app.outputs.record(args['sessionId'], saved['id'])
        legacy['title'] = ''
        app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(legacy), saved['id']))
        app.db.commit()
    original = app.outputs.record(args['sessionId'], saved['id'])
    path = resource_files.root(app.db) / (original['body']['$resource'] + '.json')
    encoded = path.read_bytes()
    app.clients.attach('empty-title-reader')
    with app.clients.bind('empty-title-reader'):
        values = gif_open_args(args, saved)
        assert (await app.outputs.media.describe(args['sessionId'], args['messageId']))['items'][0]['title'] == ''
        assert app.outputs.store.list(args['sessionId'])['items'][0]['title'] == ''
        metadata = (await app.dispatch('outputs.read', {'sessionId': args['sessionId'],
            'id': saved['id']}, include_state=False))['result']
        assert metadata['title'] == '' and metadata['origin'] == original['origin']
        assert await app.outputs.media.head(values) == len(gif())
        assert await app.outputs.media.bytes(values) == gif()
        opened = (await app.dispatch('canvas.openGif', values, include_state=False))['result']
        canvas = next(row for row in app.state['canvasArtifacts'] if row['id'] == opened['id'])
        for definition in [canvas, *canvas['versions']]:
            ref = app.state_resource(definition['body']['$resource'])['mediaReference']
            assert ref['item']['title'] == ''
        assert app.outputs.record(args['sessionId'], saved['id']) == original
        assert path.read_bytes() == encoded


@pytest.mark.parametrize('title', [None, {}, 'x' * 201])
async def test_malformed_retained_title_fails_loudly_without_empty_list_fallback(media_app, title):
    from amplifier_web.service import AppError
    app = media_app
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    broken = app.outputs.record(args['sessionId'], saved['id'])
    broken['title'] = title
    app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(broken), saved['id']))
    app.db.commit()
    app.clients.attach('bad-title-reader')
    with app.clients.bind('bad-title-reader'):
        with pytest.raises(ValueError, match='retained'):
            await app.outputs.media.describe(args['sessionId'], args['messageId'])
        with pytest.raises(AppError, match='retained'):
            await app.dispatch('outputs.read', {'sessionId': args['sessionId'], 'id': saved['id']})
        for operation in ('head', 'bytes'):
            with pytest.raises(ValueError, match='retained'):
                await getattr(app.outputs.media, operation)(gif_open_args(args, saved))


async def test_http_small_ranges_cache_exact_successful_validation_not_only_hash(
        authenticated_client, tmp_path, monkeypatch):
    from amplifier_web import output_media, resource_files
    from amplifier_web.server import create_app
    from test_service import Runtime
    server = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                              voice=False, preload_providers=False, background_updates=False)
    app = server['service']
    await app.dispatch('session.create', {})
    await app.on_runtime_event('assistant.message', {'sessionId': app._session()['id'],
        'text': 'Cache validation origin', 'generationId': 'cache-generation'})
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    original = app.outputs.record(args['sessionId'], saved['id'])
    app.clients.attach('cache-http')
    client = await authenticated_client(server)
    client.session.headers['X-Amplifier-Client'] = 'cache-http'
    query = {'sessionId': args['sessionId'], 'messageId': args['messageId'],
             'version': str(saved['version']), 'sha256': saved['sha256']}
    route = '/api/outputs/' + saved['id'] + '/media'
    validations, owner = [], threading.get_ident()
    real = output_media.validate_retained_body

    def validated(data, record):
        validations.append((threading.get_ident(), copy.deepcopy(record)))
        return real(data, record)

    monkeypatch.setattr(output_media, 'validate_retained_body', validated)

    async def small_range():
        response = await client.get(route, params=query, headers={'Range': 'bytes=2-4'})
        if response.status == 206:
            assert await response.read() == gif()[2:5]
        return response.status

    for _ in range(3):
        assert await small_range() == 206
    assert len(validations) == 1
    changed = {**original, 'title': 'Changed immutable descriptor, same bytes and hash'}
    app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(changed), saved['id']))
    app.db.commit()
    assert await small_range() == 206
    assert len(validations) == 2
    # Same media SHA is insufficient when structurally valid saved metadata
    # disagrees with the body; failure must never become a successful cache hit.
    bad = {**changed, 'media': {**changed['media'], 'durationMs': 270}}
    app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(bad), saved['id']))
    app.db.commit()
    assert await small_range() == 400
    assert len(validations) == 3
    assert await small_range() == 400
    assert len(validations) == 4
    app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(changed), saved['id']))
    app.db.commit()
    # Explicit expiry, not an arbitrary speed or wall-clock assertion.
    for key, (data, signature, at) in list(app.outputs.media.cache.items()):
        app.outputs.media.cache[key] = (data, signature, at - 6)
    assert await small_range() == 206
    assert len(validations) == 5
    path = resource_files.root(app.db) / (original['body']['$resource'] + '.json')
    encoded = path.read_bytes()
    path.write_bytes(encoded.replace(b'base64', b'broken'))
    assert await small_range() == 400
    path.write_bytes(encoded)
    assert await small_range() == 206
    assert len(validations) == 6
    assert all(thread != owner for thread, _ in validations)
    assert len(app.outputs.media.cache) <= 2
    assert sum(len(entry[0]) for entry in app.outputs.media.cache.values()) <= 64 * 1024 * 1024


@pytest.mark.parametrize('change', ['index', 'client', 'metadata'])
async def test_warm_validated_hit_rechecks_scope_index_and_metadata_after_await(
        media_app, monkeypatch, change):
    from amplifier_web import output_media
    app = media_app
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    original = app.outputs.record(args['sessionId'], saved['id'])
    app.clients.attach('warm-race-reader')
    values = gif_open_args(args, saved)
    entered, release = threading.Event(), threading.Event()
    real = output_media._signature

    def blocked(source):
        signature = real(source)
        entered.set()
        assert release.wait(10)
        return signature

    with app.clients.bind('warm-race-reader'):
        assert await app.outputs.media.bytes(values) == gif()
        monkeypatch.setattr(output_media, '_signature', blocked)
        pending = asyncio.create_task(app.outputs.media.bytes(values))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            async with app.lock:
                if change == 'client':
                    app.clients.records['warm-race-reader']['selectedSessionId'] = None
                elif change == 'index':
                    app.db.execute('UPDATE state_resources SET value=? WHERE id=?',
                                   ('{}', original['body']['$resource']))
                else:
                    changed = {**original, 'title': 'Different during warm read'}
                    app.db.execute('UPDATE output_records SET value=? WHERE id=?',
                                   (json.dumps(changed), saved['id']))
                app.db.commit()
            release.set()
            with pytest.raises(ValueError):
                await asyncio.wait_for(pending, 10)
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('change', ['navigate', 'metadata', 'index', 'delete'])
async def test_metadata_retained_recheck_not_browser_selection_across_worker_await(
        media_app, monkeypatch, change):
    from amplifier_web import output_media
    from amplifier_web.service import AppError
    app = media_app
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args))['result']
    record = app.outputs.record(args['sessionId'], saved['id'])
    app.clients.attach('metadata-race-reader')
    entered, release = threading.Event(), threading.Event()
    real = output_media._signature

    def blocked(source):
        value = real(source)
        entered.set()
        assert release.wait(10)
        return value

    monkeypatch.setattr(output_media, '_signature', blocked)
    with app.clients.bind('metadata-race-reader'):
        pending = asyncio.create_task(app.app_bridge('dispatch', {
            'action': 'outputs.read', 'args': {'id': saved['id']},
            '_inputClients': ['metadata-race-reader']}, args['sessionId']))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            async with app.lock:
                if change == 'navigate':
                    app.clients.records['metadata-race-reader']['selectedSessionId'] = None
                elif change == 'metadata':
                    changed = {**record, 'title': 'Changed metadata across await'}
                    app.db.execute('UPDATE output_records SET value=? WHERE id=?',
                                   (json.dumps(changed), saved['id']))
                elif change == 'index':
                    app.db.execute('UPDATE state_resources SET value=? WHERE id=?',
                                   ('{}', record['body']['$resource']))
                else:
                    app.db.execute('DELETE FROM output_records WHERE id=?', (saved['id'],))
                app.db.commit()
            release.set()
            if change == 'navigate':
                result = (await asyncio.wait_for(pending, 10))['result']
                assert result['browserObservation']['status'] == 'unbound'
                assert result['origin'] == record['origin']
            else:
                with pytest.raises(AppError):
                    await asyncio.wait_for(pending, 10)
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('defect', [None, 'cross-output-canvas', 'cross-output-snapshot'])
async def test_real_attached_gif_snapshot_fork_skips_invalid_adapters_before_dependents(
        media_app, defect):
    from amplifier_web.automatic_history import display_message
    from amplifier_web import resource_files
    from test_output_media import _exact_media_fork_corpus
    app = media_app
    source, corpus, store = _exact_media_fork_corpus(app, partial=True)
    app.clients.attach('snapshot-fork-reader')
    raw = (store.directory(source['id']) / 'transcript.jsonl').read_bytes()
    with app.clients.bind('snapshot-fork-reader'):
        mid = display_message(corpus[6], 6, source)['id']
        args = {**media_args_without_hydration(source), 'messageId': mid}
        saved = (await app.dispatch('outputs.attachMedia', args))['result']
        opened = (await app.dispatch('canvas.openGif', gif_open_args(args, saved),
                                     include_state=False))['result']
        wrapper = next(row for row in app.state['canvasArtifacts'] if row['id'] == opened['id'])
        assert wrapper['messageId'] is None and wrapper['publications'] == []
        # Historical compatibility anchor, not a new general publication policy.
        anchor = display_message(corpus[2], 2, source)['id']
        wrapper['messageId'] = anchor
        wrapper['versions'][0]['messageId'] = anchor
        wrapper['publications'] = [{'messageId': anchor, 'version': 1}]
        # The public generic attach route needs a currently visible message.
        source['messages'].insert(0, display_message(corpus[2], 2, source))
        snapshot = (await app.dispatch('outputs.attach', {'sessionId': source['id'],
            'kind': 'canvas', 'canvasId': wrapper['id'], 'title': 'Saved GIF adapter',
            'messageId': anchor}, include_state=False))['result']
        if defect:
            malformed = copy.deepcopy(app.state_resource(wrapper['body']['$resource']))
            # A real source-owned nonmedia output with a kept anchor is not
            # authority for the media adapter, despite its valid output ID.
            other = (await app.dispatch('outputs.write', {'sessionId': source['id'],
                'title': 'Nonmedia output', 'content': 'No GIF authority', 'variant': 'document',
                'messageId': anchor}, include_state=False))['result']
            malformed['mediaReference']['item']['id'] = other['id']
            body = resource_files.put(app.db, malformed)
            if defect == 'cross-output-canvas':
                wrapper['body'] = body
                wrapper['versions'][0]['body'] = body
            else:
                broken = app.outputs.record(source['id'], snapshot['id'])
                broken['canvasSource'] = body
                app.db.execute('UPDATE output_records SET value=? WHERE id=?',
                               (json.dumps(broken), snapshot['id']))
            app.db.commit()
        before_source = copy.deepcopy(wrapper)
        forked = await app.dispatch('session.fork', {'id': source['id'], 'turn': 2},
                                    include_state=False)
        target = app._session()
        records = app.outputs.store.list(target['id'])['items']
        canvases = [row for row in app.state['canvasArtifacts'] if row.get('sessionId') == target['id']]
        for record in records:
            if record.get('canvasId'):
                assert any(row['id'] == record['canvasId'] for row in canvases)
        clones = [row for row in records if row.get('forkSource', {}).get('outputId') == snapshot['id']]
        if defect:
            assert not clones
            assert forked['result']['retainedAdapterSkips']
            if defect == 'cross-output-canvas':
                assert not any(row['kind'] == 'gif' for row in canvases)
        else:
            assert len(clones) == 1
            clone = clones[0]
            media = next(row for row in records if row['kind'] == 'media')
            body = json.loads(app.outputs.content(clone))
            assert body == app.state_resource(clone['canvasSource']['$resource'])
            assert body['mediaReference']['item']['id'] == media['id']
            assert body['mediaReference']['item']['sessionId'] == target['id']
            assert clone['canvasId'] in {row['id'] for row in canvases}
        assert next(row for row in app.state['canvasArtifacts'] if row['id'] == wrapper['id']) == before_source
    assert (store.directory(source['id']) / 'transcript.jsonl').read_bytes() == raw


@pytest.mark.parametrize('defect', [
    'versions-none', 'version-entry-none', 'version-number-missing',
    'publications-none', 'publication-entry-none', 'publication-version-missing',
    'publication-version-unavailable',
])
async def test_session_fork_malformed_gif_is_skipped_at_first_copy_boundary(
        media_app, monkeypatch, defect):
    from amplifier_web import canvas_versions
    from amplifier_web.automatic_history import display_message
    from test_output_media import _exact_media_fork_corpus
    app = media_app
    source, corpus, store = _exact_media_fork_corpus(app, partial=True)
    raw = (store.directory(source['id']) / 'transcript.jsonl').read_bytes()
    app.clients.attach('malformed-fork-reader')
    with app.clients.bind('malformed-fork-reader'):
        mid = display_message(corpus[6], 6, source)['id']
        anchor_message = display_message(corpus[2], 2, source)
        anchor = anchor_message['id']
        args = {**media_args_without_hydration(source), 'messageId': mid}
        saved = (await app.dispatch('outputs.attachMedia', args))['result']
        media_before = app.outputs.record(source['id'], saved['id'])
        opened = (await app.dispatch('canvas.openGif', gif_open_args(args, saved),
                                     include_state=False))['result']
        wrapper = next(row for row in app.state['canvasArtifacts'] if row['id'] == opened['id'])
        # Synthetic historical compatibility record: its kept user anchor would
        # reach fork_definition if first-copy validation were moved too late.
        wrapper['messageId'] = anchor
        wrapper['versions'][0]['messageId'] = anchor
        wrapper['publications'] = [{'messageId': anchor, 'version': 1}]
        source['messages'].append(anchor_message)
        unrelated = (await app.dispatch('canvas.show', {
            'sessionId': source['id'], 'kind': 'markdown',
            'title': 'Unrelated retained document', 'content': 'Synthetic document survives'},
            include_state=False))['result']
        document = next(row for row in app.state['canvasArtifacts'] if row['id'] == unrelated['id'])
        document_before = copy.deepcopy(document)
        assert document['messageId'] == anchor
        # The active Canvas is now the unrelated document, so passive remember
        # cannot repair or overwrite the malformed GIF before the copy boundary.
        if defect == 'versions-none':
            wrapper['versions'] = None
        elif defect == 'version-entry-none':
            wrapper['versions'] = [None]
        elif defect == 'version-number-missing':
            wrapper['versions'][0].pop('version')
        elif defect == 'publications-none':
            wrapper['publications'] = None
        elif defect == 'publication-entry-none':
            wrapper['publications'] = [None]
        elif defect == 'publication-version-missing':
            wrapper['publications'][0].pop('version')
        else:
            wrapper['publications'][0]['version'] = 2
        before_source = copy.deepcopy(wrapper)
        original_ids = {row['id'] for row in app.state['sessions']}
        copied = []
        real_fork_definition = canvas_versions.fork_definition

        def tracked_definition(row, *args, **kwargs):
            copied.append(row['id'])
            return real_fork_definition(row, *args, **kwargs)

        monkeypatch.setattr(canvas_versions, 'fork_definition', tracked_definition)
        forked = await app.dispatch('session.fork', {'id': source['id'], 'turn': 2},
                                    include_state=False)
        assert forked['accepted'] is True
        target = app._session()
        assert target['id'] not in original_ids and target['status'] == 'idle'
        assert {row['id'] for row in app.state['sessions']} == original_ids | {target['id']}
        assert app.state['selectedSessionId'] == target['id']
        assert store.directory(target['id']).joinpath('transcript.jsonl').is_file()
        assert wrapper['id'] not in copied
        assert document['id'] in copied
        skips = forked['result']['retainedAdapterSkips']
        adapter_skips = [item for item in skips if item.get('canvasId') == wrapper['id']]
        assert len(adapter_skips) == 1
        assert isinstance(adapter_skips[0]['reason'], str) and adapter_skips[0]['reason']
        assert not any(item.get('canvasId') == document['id'] for item in skips)
        canvases = [row for row in app.state['canvasArtifacts'] if row.get('sessionId') == target['id']]
        assert len(canvases) == 1 and canvases[0]['kind'] == 'markdown'
        assert canvases[0]['body'] == document_before['body']
        assert canvases[0]['versions'][0]['body'] == document_before['versions'][0]['body']
        assert canvases[0]['messageId'] in {row['id'] for row in target['messages'] if row['role'] == 'user'}
        media = [row for row in app.outputs.store.list(target['id'])['items'] if row['kind'] == 'media']
        assert len(media) == 1 and media[0]['forkSource']['outputId'] == saved['id']
        assert media[0]['body'] == media_before['body']
        assert next(row for row in app.state['canvasArtifacts'] if row['id'] == wrapper['id']) == before_source
        assert next(row for row in app.state['canvasArtifacts'] if row['id'] == document['id']) == document_before
        assert app.outputs.record(source['id'], saved['id']) == media_before
    assert (store.directory(source['id']) / 'transcript.jsonl').read_bytes() == raw


@pytest.mark.parametrize('scenario', [
    'kept', 'future-media-origin', 'future-snapshot-anchor',
    'invalid-canvas-adapter', 'invalid-canvas-reference-none', 'invalid-snapshot-adapter',
])
async def test_library_only_open_gif_snapshot_fork_remaps_without_inventing_canvas_anchor(
        media_app, scenario):
    import hashlib
    from amplifier_web import resource_files
    from amplifier_web.automatic_history import display_message
    from test_output_media import _exact_media_fork_corpus
    app = media_app
    source, corpus, store = _exact_media_fork_corpus(app, partial=True)
    raw = (store.directory(source['id']) / 'transcript.jsonl').read_bytes()
    origin_index = 10 if scenario == 'future-media-origin' else 6
    anchor_index = 9 if scenario == 'future-snapshot-anchor' else 2
    app.clients.attach('library-snapshot-reader')
    with app.clients.bind('library-snapshot-reader'):
        mid = display_message(corpus[origin_index], origin_index, source)['id']
        anchor_message = display_message(corpus[anchor_index], anchor_index, source)
        anchor = anchor_message['id']
        args = {**media_args_without_hydration(source), 'messageId': mid}
        saved = (await app.dispatch('outputs.attachMedia', args))['result']
        media_before = app.outputs.record(source['id'], saved['id'])
        opened = (await app.dispatch('canvas.openGif', gif_open_args(args, saved),
                                     include_state=False))['result']
        wrapper = next(row for row in app.state['canvasArtifacts'] if row['id'] == opened['id'])
        assert wrapper['messageId'] is None and wrapper['publications'] == []
        assert all(entry['messageId'] is None for entry in wrapper['versions'])
        if not any(row['id'] == anchor for row in source['messages']):
            source['messages'].insert(0, anchor_message)
        snapshot = (await app.dispatch('outputs.attach', {
            'sessionId': source['id'], 'kind': 'canvas', 'canvasId': wrapper['id'],
            'title': 'Explicitly anchored library GIF snapshot', 'messageId': anchor},
            include_state=False))['result']
        snapshot_before = app.outputs.record(source['id'], snapshot['id'])
        assert snapshot_before['canvasSource'] == wrapper['body']
        assert snapshot_before['origin']['messageId'] == anchor
        assert wrapper['messageId'] is None and wrapper['publications'] == []
        assert all(entry['messageId'] is None for entry in wrapper['versions'])
        # Closing an admitted view is a real UI operation, not hand-anchoring or
        # a replacement producer. It also prevents passive remember masking a
        # deliberately malformed stored adapter in the negative cases.
        await app.dispatch('canvas.tabClose', {'id': wrapper['id']}, include_state=False)
        assert app.state['canvas'].get('id') != wrapper['id']
        if scenario.startswith('invalid-'):
            other = (await app.dispatch('outputs.write', {
                'sessionId': source['id'], 'title': 'Unrelated nonmedia output',
                'content': 'Synthetic text is not GIF authority', 'variant': 'document',
                'messageId': anchor}, include_state=False))['result']
            malformed = copy.deepcopy(app.state_resource(wrapper['body']['$resource']))
            if scenario == 'invalid-canvas-reference-none':
                malformed['mediaReference'] = None
            else:
                malformed['mediaReference']['item']['id'] = other['id']
            body = resource_files.put(app.db, malformed)
            if scenario in {'invalid-canvas-adapter', 'invalid-canvas-reference-none'}:
                wrapper['body'] = body
                wrapper['versions'][0]['body'] = body
            else:
                broken = copy.deepcopy(snapshot_before)
                broken['canvasSource'] = body
                app.db.execute('UPDATE output_records SET value=? WHERE id=?',
                               (json.dumps(broken), snapshot['id']))
                snapshot_before = broken
            app.db.commit()
        before_source = copy.deepcopy(wrapper)
        forked = await app.dispatch('session.fork', {'id': source['id'], 'turn': 2},
                                    include_state=False)
        assert forked['accepted'] is True
        target = app._session()
        records = app.outputs.store.list(target['id'])['items']
        canvases = [row for row in app.state['canvasArtifacts'] if row.get('sessionId') == target['id']]
        assert not any(row['kind'] == 'gif' for row in canvases), 'Library-only wrappers must not gain a fabricated publication'
        media = [row for row in records if row['kind'] == 'media']
        assert len(media) == (0 if scenario == 'future-media-origin' else 1)
        if media:
            assert media[0]['forkSource']['outputId'] == saved['id']
            assert media[0]['body'] == media_before['body']
        clones = [row for row in records if row.get('forkSource', {}).get('outputId') == snapshot['id']]
        skips = forked.get('result', {}).get('retainedAdapterSkips', [])
        if scenario == 'kept':
            assert len(clones) == 1
            clone = clones[0]
            assert clone['kind'] == 'canvas' and clone['canvasKind'] == 'gif'
            assert 'canvasId' not in clone, 'No source-owned or nonexistent Canvas ID may escape'
            assert clone['origin']['messageId'] in {row['id'] for row in target['messages'] if row['role'] == 'user'}
            body = json.loads(app.outputs.content(clone))
            assert body == app.state_resource(clone['canvasSource']['$resource'])
            reference = body['mediaReference']
            assert reference['item']['id'] == media[0]['id']
            assert reference['item']['sessionId'] == target['id']
            assert reference['item']['version'] == media[0]['version']
            assert reference['messageId'] == media[0]['origin']['messageId']
            assert reference['item']['sha256'] == hashlib.sha256(gif()).hexdigest()
            assert media[0]['origin']['messageId'] in {
                row['id'] for row in target['messages']
                if row['role'] == 'assistant' and not row.get('observation')}
            encoded = app.outputs.content(clone)
            assert clone['sha256'] == hashlib.sha256(encoded).hexdigest()
            assert clone['bytes'] == len(encoded)
            assert not any(item.get('canvasId') == wrapper['id'] or item.get('outputId') == snapshot['id'] for item in skips)
        else:
            assert not clones
            if scenario != 'future-snapshot-anchor':
                assert any(item.get('outputId') == snapshot['id'] and item.get('reason') for item in skips)
            if scenario in {'invalid-canvas-adapter', 'invalid-canvas-reference-none'}:
                assert any(item.get('canvasId') == wrapper['id'] and item.get('reason') for item in skips)
        for record in records:
            if 'canvasId' in record:
                assert record['canvasId'] in {row['id'] for row in canvases}
        assert next(row for row in app.state['canvasArtifacts'] if row['id'] == wrapper['id']) == before_source
        assert app.outputs.record(source['id'], saved['id']) == media_before
        assert app.outputs.record(source['id'], snapshot['id']) == snapshot_before
    assert (store.directory(source['id']) / 'transcript.jsonl').read_bytes() == raw