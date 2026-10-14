"""Authored source checks, not executed lane acceptance.

The optional --browser-fixture entry serves the real host for the companion
Chromium journey. All receipts are synthetic; no image/model calls are made.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

MODE = os.environ.get('AMPLIFIER_BROWSER_TEST_MODE', 'source')
if MODE not in {'source', 'installed'}:
    raise ValueError('AMPLIFIER_BROWSER_TEST_MODE must be source or installed')
if __name__ == '__main__' and MODE == 'source':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from amplifier_web.server import create_app
from amplifier_web.artifact_presentation import output_binding
from test_output_images import png

if __name__ == '__main__' and MODE == 'installed':
    import amplifier_web.server as _server
    _installed = Path(_server.__file__).resolve()
    if (_installed.is_relative_to(Path(__file__).resolve().parents[1])
            or not _installed.is_relative_to(Path(sys.prefix).resolve())):
        raise RuntimeError('Retained-thumbnail fixture must use the installed package')


class NoModel:
    async def start(self, *args, **kwargs):
        raise AssertionError('Thumbnail browsing must not initialize a model')

    async def send(self, *args, **kwargs):
        raise AssertionError('Thumbnail browsing must not submit model input')

    async def stop(self, *args, **kwargs):
        pass

    async def close(self):
        pass


async def saved_host(root, count=2):
    app = await create_app(root / 'app', workspace=root, runtime=NoModel(),
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Retained thumbnail fixture'})
    session = service._session()
    service._message(session, 'user', 'Create synthetic image concepts')
    mid, sid = session['messages'][0]['id'], session['id']
    records = []
    for index in range(count):
        name = 'concept-' + str(index + 1)
        image = png(100 + index, 60)
        (root / (name + '.png')).write_bytes(image)
        receipt = {'schema': 'amplifier.image.receipt.v1', 'status': 'completed',
                   'requestId': name, 'requestHash': 'a' * 64, 'backend': 'fixture',
                   'model': 'fixture', 'operation': 'generate', 'inputs': [],
                   'artifact': {'path': name + '.png', 'sha256': hashlib.sha256(image).hexdigest(),
                                'bytes': len(image), 'width': 100 + index, 'height': 60,
                                'mimeType': 'image/png', 'mode': 'RGB'}}
        (root / (name + '.json')).write_text(json.dumps(receipt))
        records.append((await service.dispatch('outputs.attachImage', {
            'sessionId': sid, 'messageId': mid, 'title': 'Concept ' + str(index + 1),
            'receiptPath': name + '.json'}))['result'])
    document = (await service.dispatch('canvas.show', {
        'sessionId': sid, 'messageId': mid, 'kind': 'markdown',
        'title': 'Ordinary inline document', 'content': '# Independent ordinary renderer',
        'presentation': {'chat': 'inline', 'canvas': 'unchanged'}}))['result']
    return app, sid, mid, records, document


def artifact(service, record):
    return next(row for row in service._state['canvasArtifacts'] if row['id'] == record['canvasId'])


def image_path(record):
    return '/api/outputs/' + record['id'] + '/image'


def tuple_query(sid, mid, record, **changes):
    return {'sessionId': sid, 'artifactId': record['canvasId'], 'version': '1',
            'messageId': mid, 'sha256': record['sha256'], **changes}


def make_true_legacy(service, record):
    row = artifact(service, record)
    row['publications'][0].pop('outputId')
    row['publications'][0].pop('imageGeneration')
    for saved in [row, *row['versions']]:
        saved.pop('outputBinding', None)
    return row


@pytest.fixture
async def retained(authenticated_client, tmp_path):
    app, sid, mid, records, document = await saved_host(tmp_path)
    service = app['service']
    service.clients.attach('thumbnail-client')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'thumbnail-client'
    return app, service, client, sid, mid, records, document


async def test_exact_retained_http_and_legacy_output_image_do_not_hydrate_history(retained, monkeypatch):
    app, service, client, sid, mid, records, _ = retained
    record = records[0]
    expected = png(100, 60)
    # Read authority comes from the admitted saved publication, not live text.
    service._session(sid)['messages'] = []
    real_session = service._session
    calls = []

    def metadata_only(identity=None, *, hydrate=True):
        calls.append((identity, hydrate))
        assert hydrate is False, 'The image byte path hydrated full history'
        return real_session(identity, hydrate=False)

    monkeypatch.setattr(service, '_session', metadata_only)
    monkeypatch.setattr(service.cold_display, 'hydrate',
                        lambda *args: pytest.fail('Thumbnail read loaded cold conversation history'))
    response = await client.get(image_path(record), params=tuple_query(sid, mid, record))
    assert response.status == 200
    assert await response.read() == expected
    assert response.headers['X-Amplifier-Image-SHA256'] == record['sha256']
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Content-Type'] == 'image/png'
    assert calls == [(sid, False), (sid, False)], 'Authority is fresh after the detached worker'
    # Missing optional projected hash is allowed only with canonical binding.
    query = tuple_query(sid, mid, record)
    query.pop('sha256')
    assert (await client.get(image_path(record), params=query)).status == 200
    # The existing general output image URL stays available and nonhydrating.
    response = await client.get(image_path(record))
    assert response.status == 200 and await response.read() == expected
    # The existing authenticated host owner may read without an attached
    # browser. A client ID is context checking, not a new account ACL.
    client.session.headers.pop('X-Amplifier-Client')
    response = await client.get(image_path(record), params=query)
    assert response.status == 200 and await response.read() == expected
    client.session.headers['X-Amplifier-Client'] = 'not-attached'
    assert (await client.get(image_path(record), params=query)).status == 409
    client.session.headers['X-Amplifier-Client'] = 'thumbnail-client'
    assert all(not hydrate for _, hydrate in calls)
    assert service.clients.records['thumbnail-client'].get('canvasViews', {}).get('inline', {}) == {}


@pytest.mark.parametrize('case', [
    'session', 'artifact', 'version', 'message', 'output', 'hash',
    'missing-session', 'missing-artifact', 'missing-version', 'missing-message',
    'empty-session', 'empty-hash', 'hash-only', 'duplicate-version',
    'forged-marker', 'forged-receipt', 'request-id-only', 'current-body',
    'unlinked', 'removed', 'compact', 'none', 'changed-binding', 'wrong-client-scope',
])
async def test_rejected_http_tuple_never_opens_bytes_or_falls_back(retained, monkeypatch, case):
    _, service, client, sid, mid, records, _ = retained
    from amplifier_web import outputs
    record = records[0]
    row = artifact(service, record)
    path, query = image_path(record), tuple_query(sid, mid, record)
    wrong = {'session': ('sessionId', 'foreign'), 'artifact': ('artifactId', 'foreign'),
             'version': ('version', '99'), 'message': ('messageId', 'foreign'),
             'hash': ('sha256', '0' * 64)}
    if case in wrong:
        key, value = wrong[case]
        query[key] = value
    elif case == 'output':
        path = image_path(records[1])
    elif case.startswith('missing-'):
        query.pop({'session': 'sessionId', 'artifact': 'artifactId',
                   'version': 'version', 'message': 'messageId'}[case[8:]])
    elif case == 'empty-session':
        query['sessionId'] = ''
    elif case == 'empty-hash':
        query['sha256'] = ''
    elif case == 'hash-only':
        query = {'sha256': record['sha256'], 'verified': 'true'}
    elif case == 'duplicate-version':
        query = [*query.items(), ('version', '1')]
    elif case == 'forged-marker':
        row['publications'][0]['imageGeneration']['requestId'] = 'forged'
    elif case in {'forged-receipt', 'request-id-only'}:
        value = service.outputs.store.read(record['id'])
        if case == 'forged-receipt':
            value['imageGeneration']['provenance'] = 'caller verified'
        else:
            value.pop('imageGeneration')
        service.db.execute('UPDATE output_records SET value=? WHERE id=?',
                           (json.dumps(value), value['id']))
        query['verified'] = 'true'  # A caller assertion cannot restore authority.
    elif case == 'current-body':
        row['versions'][0]['body'] = {'$resource': 'not-the-admitted-body'}
    elif case == 'unlinked':
        value = service.outputs.store.read(record['id'])
        value['linked'] = False
        service.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(value), value['id']))
    elif case == 'removed':
        service._state['canvasArtifacts'].remove(row)
    elif case in {'compact', 'none'}:
        row['publications'][0]['chat'] = case
    elif case == 'changed-binding':
        row['versions'][0]['outputBinding']['sha256'] = '0' * 64
    elif case == 'wrong-client-scope':
        service.clients.records['thumbnail-client']['selectedSessionId'] = None
    monkeypatch.setattr(outputs, 'thumbnail_bytes',
                        lambda *args: pytest.fail('Rejected tuple opened the body'))
    original_read = service.outputs.retained_thumbnail

    async def scoped_only(identity, values=None):
        assert values is not None, 'Scoped rejection fell back to the raw image URL'
        return await original_read(identity, values)

    monkeypatch.setattr(service.outputs, 'retained_thumbnail', scoped_only)
    response = await client.get(path, params=query)
    assert response.status in {404, 409}
    assert response.headers['Content-Type'].startswith('application/json')


async def test_http_auth_and_cross_origin_precede_saved_tuple_guard(retained, monkeypatch):
    app, service, client, sid, mid, records, _ = retained
    monkeypatch.setattr(service.outputs, 'retained_thumbnail',
                        lambda *args: pytest.fail('Unauthenticated/cross-origin request reached tuple guard'))
    path, query = image_path(records[0]), tuple_query(sid, mid, records[0])
    assert (await client.get(path, params=query, headers={'Origin': 'https://untrusted.example'})).status == 403
    client.session.headers['Authorization'] = ''
    client.session.cookie_jar.clear()
    assert (await client.get(path, params=query, allow_redirects=False)).status in {401, 403}


async def test_legacy_canonical_join_qualifies_exact_old_version_not_current_body(retained, monkeypatch):
    _, service, client, sid, mid, records, _ = retained
    from amplifier_web import outputs
    record, row = records[0], artifact(service, records[0])
    row['versions'][0].pop('outputBinding')
    row['versions'].append({'version': 2, 'kind': 'image', 'title': 'Later unrelated body',
                            'body': {'$resource': 'later-body'}})
    row.update(revision=2, body={'$resource': 'later-body'})
    original = outputs.thumbnail_bytes
    opened = []

    def exact_bytes(source, saved):
        opened.append(saved['id'])
        return original(source, saved)

    monkeypatch.setattr(outputs, 'thumbnail_bytes', exact_bytes)
    response = await client.get(image_path(record), params=tuple_query(sid, mid, record))
    assert response.status == 200 and await response.read() == png(100, 60)
    assert opened == [record['id']]
    # A second canonical-looking legacy join is ambiguous, not read authority.
    duplicate = {key: copy.deepcopy(value) for key, value in record.items()
                 if key not in {'id', 'sessionId', 'version', 'revision', 'createdAt'}}
    service.outputs.store.create(sid, duplicate)
    opened.clear()
    assert (await client.get(image_path(record), params=tuple_query(sid, mid, record))).status == 409
    assert opened == []


@pytest.mark.parametrize('damage', ['hash', 'length', 'crc', 'oversized', 'not-png'])
async def test_http_retained_bytes_still_enforce_canonical_integrity_and_png_bounds(retained, damage):
    _, service, client, sid, mid, records, _ = retained
    from amplifier_web.resource_files import put
    import base64
    record = service.outputs.store.read(records[0]['id'])
    row = artifact(service, record)
    data = png(4097, 1) if damage == 'oversized' else b'not a PNG' if damage == 'not-png' else png(100, 60)
    if damage == 'crc':
        data = data[:-1] + bytes([data[-1] ^ 1])
    record.update(body=put(service.db, {'content': 'data:image/png;base64,' + base64.b64encode(data).decode()}),
                  sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    if damage == 'hash':
        record['sha256'] = '0' * 64
    elif damage == 'length':
        record['bytes'] += 1
    service.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(record), record['id']))
    for saved in [row, row['versions'][0]]:
        saved.update(body=record['body'], contentResource=record['body'], outputBinding=output_binding(record))
    assert (await client.get(image_path(record), params=tuple_query(sid, mid, record))).status == 409


@pytest.mark.parametrize('case', ['valid', 'unlinked', 'ambiguous'])
async def test_http_legacy_qualification_is_read_only_with_unrelated_latest_open(
        retained, monkeypatch, case):
    _, service, client, sid, mid, records, document = retained
    from amplifier_web import resource_files, canvas_library, state_storage
    response = await client.post('/api/actions', json={
        'action': 'canvas.select', 'args': {'id': document['id']}})
    assert response.status == 200
    record = records[0]
    make_true_legacy(service, record)
    if case == 'unlinked':
        service.outputs.store.link(record['id'], record['revision'], False)
    elif case == 'ambiguous':
        duplicate = {key: value for key, value in service.outputs.store.read(record['id']).items()
                     if key not in {'id', 'sessionId', 'version', 'revision', 'createdAt'}}
        service.outputs.store.create(sid, duplicate)
    task = getattr(service, '_storage_maintenance_task', None)
    if task is not None:
        await task
    with service.clients.bind('thumbnail-client'):
        primary = copy.deepcopy(service.state['canvas'])
    assert primary['id'] == document['id'] and primary.get('content')
    assert not primary.get('selectedVersion') and not primary.get('outputBinding')
    assert not primary.get('contentResource')
    before = copy.deepcopy(service._state['canvasArtifacts'])
    events = copy.deepcopy(service._state['events'])
    revision = service._state['revision']
    commands = service.db.execute('SELECT * FROM commands ORDER BY id').fetchall()
    resources = service.db.execute('SELECT * FROM state_resources ORDER BY id').fetchall()

    def forbidden(*args, **kwargs):
        pytest.fail('Automatic qualification entered body or Canvas persistence')

    real_session = service._session

    def metadata_only(identity=None, *, hydrate=True):
        assert hydrate is False
        return real_session(identity, hydrate=False)

    with monkeypatch.context() as patch:
        patch.setattr(service, '_session', metadata_only)
        patch.setattr(service.cold_display, 'hydrate', forbidden)
        patch.setattr(service, '_save', forbidden)
        patch.setattr(resource_files, 'put', forbidden)
        patch.setattr(canvas_library, 'put', forbidden)
        patch.setattr(state_storage, 'resource', forbidden)
        for attempt in range(2):
            response = await client.post('/api/actions', json={
                'id': 'qualification-read-' + str(attempt),
                'action': 'canvas.versions.inspect', 'args': {
                    'sessionId': sid, 'id': record['canvasId'], 'version': 1,
                    'messageId': mid, 'qualifyLegacyGeneration': True}})
            assert response.status == (200 if case == 'valid' else 409)
            result = await response.json()
            if case == 'valid':
                assert result['result']['legacyGeneration']['outputId'] == record['id']
                assert result['revision'] == revision and result['effects'] == []
        assert service._state['revision'] == revision
        assert service._state['canvasArtifacts'] == before
        assert service._state['events'] == events
        assert service.db.execute('SELECT * FROM commands ORDER BY id').fetchall() == commands
        assert service.db.execute('SELECT * FROM state_resources ORDER BY id').fetchall() == resources
    with service.clients.bind('thumbnail-client'):
        assert service.state['canvas'] == primary


@pytest.mark.parametrize('version', [None, '1', True, 99])
async def test_unlink_legacy_image_ignores_invalid_publication_version(retained, version):
    _, service, client, sid, mid, records, _ = retained
    record = records[0]
    row = make_true_legacy(service, record)
    invalid = {'messageId': mid, 'version': version}
    row['publications'].append(invalid)
    response = await client.post('/api/actions', json={'action': 'outputs.unlink', 'args': {
        'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}})
    assert response.status == 200
    assert service.outputs.store.read(record['id'])['linked'] is False
    assert row['publications'][0]['chat'] == 'none'
    assert invalid == {'messageId': mid, 'version': version}


async def test_ten_retained_reads_allocate_zero_views_and_release_is_not_revocation(
        authenticated_client, tmp_path):
    app, sid, mid, records, document = await saved_host(tmp_path, 10)
    service = app['service']
    service.clients.attach('ten-images')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'ten-images'
    for record in records:
        response = await client.get(image_path(record), params=tuple_query(sid, mid, record))
        assert response.status == 200 and (await response.read()).startswith(b'\x89PNG')
    assert service.clients.records['ten-images'].get('canvasViews', {}).get('inline', {}) == {}

    async def action(name, args):
        response = await client.post('/api/actions', json={'action': name, 'args': args})
        assert response.status == 200
        return (await response.json())['result']

    selected = await action('canvas.views.inline', {
        'resourceId': records[0]['canvasId'], 'version': 1, 'messageId': mid, 'previewId': 'selected'})
    ordinary = await action('canvas.views.inline', {
        'resourceId': document['id'], 'version': 1, 'messageId': mid, 'previewId': 'ordinary'})
    assert len(service.clients.records['ten-images']['canvasViews']['inline']) == 2
    target = {key: selected[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    await action('canvas.views.release', target)
    response = await client.get(image_path(records[0]), params=tuple_query(sid, mid, records[0]))
    assert response.status == 200 and await response.read() == png(100, 60)
    with service.clients.bind('ten-images'):
        assert any(view['viewId'] == ordinary['viewId'] for view in service.canvas_views.project()['views'])


async def test_two_true_legacy_variants_restart_job_pruning_and_unchanged_evidence(
        authenticated_client, tmp_path, monkeypatch):
    from amplifier_web import artifact_presentation
    app, sid, mid, records, _ = await saved_host(tmp_path)
    service = app['service']
    for record in records:
        make_true_legacy(service, record)
    service._session(sid)['execution']['imageGeneration'] = []
    original = copy.deepcopy(service._state['canvasArtifacts'])
    evidence = [service.outputs.store.read(record['id']) for record in records]
    service._save()
    await service.close()
    reopened = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=NoModel(),
                                voice=False, preload_providers=False, background_updates=False)
    service = reopened['service']
    service.clients.attach('legacy-tab')
    client = await authenticated_client(reopened)
    client.session.headers['X-Amplifier-Client'] = 'legacy-tab'
    monkeypatch.setattr(artifact_presentation, 'output_bytes',
                        lambda *args: pytest.fail('Metadata qualification opened a body'))
    for index, record in enumerate(records):
        response = await client.post('/api/actions', json={'action': 'canvas.versions.inspect', 'args': {
            'sessionId': sid, 'id': record['canvasId'], 'version': 1,
            'messageId': mid, 'qualifyLegacyGeneration': True}})
        assert response.status == 200
        qualified = (await response.json())['result']['legacyGeneration']
        assert qualified['status'] == 'generated'
        assert (qualified['outputId'], qualified['outputVersion'], qualified['sha256']) == (
            record['id'], record['version'], record['sha256'])
        response = await client.get(image_path(record), params=tuple_query(sid, mid, record))
        assert response.status == 200 and await response.read() == png(100 + index, 60)
    assert service._state['canvasArtifacts'] == original
    assert [service.outputs.store.read(record['id']) for record in records] == evidence
    assert service.clients.records['legacy-tab'].get('canvasViews', {}).get('inline', {}) == {}
    # Unlink uses the known canonical Canvas identity, with no saved-body rewrite.
    record = records[0]
    response = await client.post('/api/actions', json={'action': 'outputs.unlink', 'args': {
        'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}})
    assert response.status == 200
    row = artifact(service, record)
    assert row['publications'][0]['chat'] == 'none'
    assert 'outputId' not in row['publications'][0] and 'imageGeneration' not in row['publications'][0]
    assert row['versions'] == original[0]['versions']
    assert (await client.get(image_path(record), params=tuple_query(sid, mid, record))).status == 409
    assert (await client.get(image_path(record))).status == 200, 'Library access survives unlink'
    updated = service.outputs.store.read(record['id'])
    await client.post('/api/actions', json={'action': 'outputs.relink', 'args': {
        'sessionId': sid, 'id': record['id'], 'expectedRevision': updated['revision']}})
    assert row['publications'][0]['chat'] == 'none', 'Relink does not replay publication'


@pytest.mark.parametrize('field,value', [
    ('outputId', None), ('outputId', 'forged'), ('imageGeneration', None),
    ('imageGeneration', {}), ('imageGeneration', {'verified': True}),
])
async def test_explicit_partial_legacy_fields_never_downgrade(retained, monkeypatch, field, value):
    from amplifier_web import outputs
    _, service, client, sid, mid, records, _ = retained
    row = make_true_legacy(service, records[0])
    row['publications'][0][field] = value
    monkeypatch.setattr(outputs, 'thumbnail_bytes',
                        lambda *args: pytest.fail('Malformed publication opened bytes'))
    assert (await client.get(image_path(records[0]), params=tuple_query(sid, mid, records[0]))).status == 409
    response = await client.post('/api/actions', json={'action': 'canvas.versions.inspect', 'args': {
        'sessionId': sid, 'id': row['id'], 'version': 1, 'messageId': mid, 'qualifyLegacyGeneration': True}})
    assert response.status == 409


@pytest.mark.parametrize('change', ['session', 'workspace', 'publication', 'body', 'generation', 'output-version',
                                  'origin', 'unlinked', 'ambiguous', 'index', 'unrelated', 'preview'])
async def test_detached_worker_thread_and_fresh_post_await_authority(retained, monkeypatch, change):
    import threading
    from amplifier_web import outputs
    _, service, client, sid, mid, records, _ = retained
    record = records[0]
    row = make_true_legacy(service, record)
    entered, finish = threading.Event(), threading.Event()
    owner = threading.get_ident()
    original = outputs.thumbnail_bytes

    def held(source, copied):
        assert threading.get_ident() != owner
        assert not service.lock.locked(), 'PNG work retained the owner lock'
        assert not hasattr(source, 'execute') and not hasattr(copied, 'execute')
        entered.set()
        assert finish.wait(10), 'Test did not release the detached worker'
        return original(source, copied)

    monkeypatch.setattr(outputs, 'thumbnail_bytes', held)
    pending = asyncio.create_task(client.get(image_path(record), params=tuple_query(sid, mid, record)))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with service.lock:
            if change in {'session', 'workspace'}:
                service.clients.records['thumbnail-client']['selected' + change.title() + 'Id'] = 'foreign'
            elif change == 'publication':
                row['publications'][0]['chat'] = 'none'
            elif change == 'body':
                row['versions'][0]['body'] = {'$resource': '0' * 64}
            elif change in {'generation', 'origin', 'unlinked', 'output-version'}:
                value = service.outputs.store.read(record['id'])
                if change == 'generation':
                    value['imageGeneration']['requestId'] = 'changed'
                elif change == 'origin':
                    value['origin']['messageId'] = 'changed'
                elif change == 'output-version':
                    value['version'] += 1
                else:
                    value['linked'] = False
                service.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(value), value['id']))
            elif change == 'ambiguous':
                duplicate = {k: v for k, v in service.outputs.store.read(record['id']).items()
                             if k not in {'id', 'sessionId', 'version', 'revision', 'createdAt'}}
                service.outputs.store.create(sid, duplicate)
            elif change == 'index':
                service.db.execute('DELETE FROM state_resources WHERE id=?', (record['body']['$resource'],))
            elif change == 'unrelated':
                service._state['revision'] += 1
                service._session(sid, hydrate=False)['title'] = 'Unrelated title change'
            else:
                service.clients.records['thumbnail-client']['canvasViews'] = {}
        finish.set()
        response = await pending
        assert response.status == (200 if change in {'unrelated', 'preview'} else 409)
        if response.status == 200:
            assert await response.read() == png(100, 60)
    finally:
        finish.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_worker_cancellation_settles_before_releasing_admission(retained, monkeypatch):
    import threading
    from amplifier_web import outputs
    _, service, _, sid, mid, records, _ = retained
    entered, finish = threading.Event(), threading.Event()
    original = outputs.thumbnail_bytes

    def held(source, record):
        entered.set()
        assert finish.wait(10)
        return original(source, record)

    monkeypatch.setattr(outputs, 'thumbnail_bytes', held)
    with service.clients.bind('thumbnail-client'):
        task = asyncio.create_task(service.outputs.retained_thumbnail(records[0]['id'], tuple_query(sid, mid, records[0])))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert service.outputs.thumbnail_workers._value == 1
        assert not service.lock.locked()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.outputs.thumbnail_workers._value == 2
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('damage', ['json-envelope', 'decoded-limit', 'symlink', 'resource-hash'])
async def test_worker_preserves_separate_envelope_and_regular_file_integrity_limits(retained, damage):
    import base64
    from amplifier_web.outputs import MAX_FILE, MAX_IMAGE_ENVELOPE
    from amplifier_web.resource_files import put, root
    _, service, client, sid, mid, records, _ = retained
    record = service.outputs.store.read(records[0]['id'])
    row = make_true_legacy(service, record)
    data = b'x' * (MAX_FILE + 1) if damage == 'decoded-limit' else png(100, 60)
    body = {'content': 'data:image/png;base64,' + base64.b64encode(data).decode()}
    if damage == 'json-envelope':
        body['padding'] = 'x' * (MAX_IMAGE_ENVELOPE + 1)
    record.update(body=put(service.db, body), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    for saved in [row, row['versions'][0]]:
        saved.update(body=record['body'], contentResource=record['body'])
    service.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(record), record['id']))
    path = root(service.db) / (record['body']['$resource'] + '.json')
    if damage == 'symlink':
        target = path.with_suffix('.original')
        path.rename(target)
        path.symlink_to(target)
    elif damage == 'resource-hash':
        path.write_text(json.dumps({'content': body['content'], 'forged': True}))
    assert (await client.get(image_path(record), params=tuple_query(sid, mid, record))).status == 409


async def test_existing_legacy_admission_helper_keeps_its_byte_validation(retained, monkeypatch):
    from amplifier_web import artifact_presentation
    _, service, _, _, _, records, _ = retained
    row = make_true_legacy(service, records[0])
    opened = []
    original = artifact_presentation.output_bytes

    def validate(db, record):
        opened.append(record['id'])
        return original(db, record)

    monkeypatch.setattr(artifact_presentation, 'output_bytes', validate)
    assert artifact_presentation.legacy_generation_metadata(service, row, 1)['id'] == records[0]['id']
    assert opened == []
    assert artifact_presentation.legacy_generation_output(service, row, 1)['id'] == records[0]['id']
    assert opened == [records[0]['id']]


async def test_three_http_reads_share_two_detached_worker_admissions(retained, monkeypatch):
    import threading
    from amplifier_web import outputs
    _, service, client, sid, mid, records, _ = retained
    finish, entered = threading.Event(), threading.Event()
    guard = threading.Lock()
    active = peak = total = 0
    original = outputs.thumbnail_bytes

    def held(source, record):
        nonlocal active, peak, total
        with guard:
            active += 1
            total += 1
            peak = max(peak, active)
            if total == 2:
                entered.set()
        try:
            assert finish.wait(10)
            return original(source, record)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(outputs, 'thumbnail_bytes', held)
    tasks = [asyncio.create_task(client.get(image_path(records[index % 2]),
             params=tuple_query(sid, mid, records[index % 2]))) for index in range(3)]
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert total == 2 and peak == 2
        assert not service.lock.locked()
        finish.set()
        responses = await asyncio.gather(*tasks)
        assert all(response.status == 200 for response in responses)
        bodies = [await response.read() for response in responses]
        assert all(body.startswith(b'\x89PNG') for body in bodies)
        assert total == 3 and peak == 2
    finally:
        finish.set()
        await asyncio.gather(*tasks, return_exceptions=True)


async def browser_fixture():
    """Real host + saved outputs for a separately executed browser check."""
    import os
    import signal
    import tempfile
    from aiohttp import web
    from amplifier_web import auth

    with tempfile.TemporaryDirectory(prefix='unified-retained-thumbnails-') as temporary:
        root = Path(temporary)
        os.environ.update(AMPLIFIER_HOME=str(root / 'native'), AMPLIFIER_WEB_HOME=str(root / 'app'),
                          AMPLIFIER_SESSION_STATE_HOME=str(root / 'sessions'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'))
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'fixtures'))
        from configured_browser import seed_connection
        seed_connection(root / 'native')
        auth.authenticate_pam = lambda user, password: (user, password) == ('thumbnail-fixture', 'fixture-password')
        app, sid, mid, records, document = await saved_host(root, 10)
        legacy = '--legacy-thumbnails' in sys.argv
        if legacy:
            service = app['service']
            for record in records:
                make_true_legacy(service, record)
            service._session(sid)['execution']['imageGeneration'] = []
            service._save()
            await service.close()
            # Cold host restart before the browser sees either legacy variant.
            app = await create_app(root / 'app', workspace=root, runtime=NoModel(),
                                   voice=False, preload_providers=False, background_updates=False)
        app['control_token'] = 'fixture-retained-thumbnails'
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        print(json.dumps({'url': f'http://127.0.0.1:{port}', 'sessionId': sid, 'messageId': mid,
                          'records': records, 'document': document, 'legacy': legacy,
                          'mode': MODE,
                          'package': str(Path(sys.modules['amplifier_web.server'].__file__).resolve())}), flush=True)
        stopped = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        await stopped.wait()
        await runner.cleanup()


if __name__ == '__main__' and '--browser-fixture' in sys.argv:
    asyncio.run(browser_fixture())