"""Retained-object regressions; source fixtures, not playback acceptance."""
import asyncio
import copy
import json
from pathlib import Path
import threading

import pytest

from test_output_media import (
    canonical_corpus, cold_alias_corpus, freeze_alias_display, gif,
    gif_open_args, media_app, media_args_without_hydration,
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