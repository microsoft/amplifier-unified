"""Known-empty web hints use the canonical output registry, never transcript scans."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppService
from test_output_media import media_app, media_args


async def test_selected_web_hint_tracks_canonical_attach_unlink_relink(media_app):
    app = media_app
    await app.history.close()
    args = media_args(app)
    sid = args['sessionId']
    app.clients.attach('web')
    with app.clients.bind('web'):
        first = app.browser_state()
        assert next(row for row in first['sessions'] if row['id'] == sid)['linkedMediaAvailable'] is False
        item = (await app.dispatch('outputs.attachMedia', args))['result']
        linked = app.browser_state()
        assert linked is not first
        assert next(row for row in linked['sessions'] if row['id'] == sid)['linkedMediaAvailable'] is True
        assert (await app.outputs.media.describe(sid, args['messageId']))['items'][0]['id'] == item['id']
        await app.dispatch('outputs.unlink', {'sessionId': sid, 'id': item['id'], 'expectedRevision': 1})
        assert next(row for row in app.browser_state()['sessions'] if row['id'] == sid)['linkedMediaAvailable'] is False
        await app.dispatch('outputs.relink', {'sessionId': sid, 'id': item['id'], 'expectedRevision': 2})
        assert next(row for row in app.browser_state()['sessions'] if row['id'] == sid)['linkedMediaAvailable'] is True
    assert 'linkedMediaAvailable' not in app._session(sid)
    assert all('linkedMediaAvailable' not in row for row in app.browser_state()['sessions'])
    for kind in ('tui', 'native', 'api'):
        app.clients.attach(kind, kind=kind)
        with app.clients.bind(kind):
            assert all('linkedMediaAvailable' not in row for row in app.browser_state()['sessions'])


@pytest.mark.parametrize('producer', ['fork', 'import'])
async def test_zero_revision_fork_import_and_restart_use_registry_membership(media_app, producer):
    app = media_app
    await app.history.close()
    args = media_args(app)
    item = (await app.dispatch('outputs.attachMedia', args))['result']
    source = app._session(args['sessionId'])
    target_id = (await app.dispatch('session.create', {'title': 'Target', 'select': False}))['sessionId']
    target = app._session(target_id)
    target['messages'] = deepcopy(source['messages'])
    assert not target.get('mediaRevision')
    app.clients.attach('web')
    with app.clients.bind('web'):
        await app.dispatch('session.select', {'id': target_id})
        assert next(row for row in app.browser_state()['sessions'] if row['id'] == target_id)['linkedMediaAvailable'] is False
    if producer == 'fork':
        app.outputs.fork(source['id'], target)
    else:
        from amplifier_web.portability_data import install_outputs
        record = {**app.outputs.store.read(item['id']), 'id': 'imported-output', 'sessionId': target_id}
        # The exact retained body already exists in this destination database.
        install_outputs(app, {'session': {'id': target_id}, 'resources': {}, 'outputs': [record],
            'comments': [], 'canvas': [], 'outputReceipts': [],
            'observations': {'questions': [], 'operationRequests': []}})
    assert not target.get('mediaRevision'), 'These producers do not use the live attach revision'
    app._publish_changes(sessions={target_id})
    with app.clients.bind('web'):
        frame = app.browser_state()
        assert next(row for row in frame['sessions'] if row['id'] == target_id)['linkedMediaAvailable'] is True
        assert all('linkedMediaAvailable' not in row for row in frame['sessions'] if row['id'] != target_id)
    await app.close()
    restored = AppService(app.data_dir, workspace=app.default_workspace)
    await restored.history.close()
    try:
        with restored.clients.bind('web'):
            frame = restored.browser_state()
            assert next(row for row in frame['sessions'] if row['id'] == target_id)['linkedMediaAvailable'] is True
    finally:
        await restored.close()
