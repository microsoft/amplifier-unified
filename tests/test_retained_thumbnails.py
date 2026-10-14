"""Authored source checks, not executed lane acceptance.

The optional --browser-fixture entry serves the real host for the companion
Chromium journey. All receipts are synthetic; no image/model calls are made.
"""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from amplifier_web.server import create_app
from amplifier_web.artifact_presentation import output_binding
from test_output_images import png


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
    assert calls == [(sid, False)]
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
    from amplifier_web import artifact_presentation
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
    monkeypatch.setattr(artifact_presentation, 'output_bytes',
                        lambda *args: pytest.fail('Rejected tuple opened the body'))
    monkeypatch.setattr(service.outputs, 'image',
                        lambda *args: pytest.fail('Scoped rejection fell back to the raw image URL'))
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
    from amplifier_web import artifact_presentation
    record, row = records[0], artifact(service, records[0])
    row['versions'][0].pop('outputBinding')
    row['versions'].append({'version': 2, 'kind': 'image', 'title': 'Later unrelated body',
                            'body': {'$resource': 'later-body'}})
    row.update(revision=2, body={'$resource': 'later-body'})
    original = artifact_presentation.output_bytes
    opened = []

    def exact_bytes(db, saved):
        opened.append(saved['id'])
        return original(db, saved)

    monkeypatch.setattr(artifact_presentation, 'output_bytes', exact_bytes)
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
        app['control_token'] = 'fixture-retained-thumbnails'
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        print(json.dumps({'url': f'http://127.0.0.1:{port}', 'sessionId': sid, 'messageId': mid,
                          'records': records, 'document': document,
                          'package': str(Path(sys.modules['amplifier_web.server'].__file__).resolve())}), flush=True)
        stopped = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        await stopped.wait()
        await runner.cleanup()


if __name__ == '__main__' and '--browser-fixture' in sys.argv:
    asyncio.run(browser_fixture())