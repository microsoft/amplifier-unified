"""A publication shares read preparation, never mutable or client-owned state."""
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from amplifier_web import history_directory
from test_active_client_projections import fixture
from test_automatic_history import app_factory
from test_cold_display import add_history


@pytest.mark.parametrize('client_count', [1, 3])
async def test_publication_prepares_web_batch_once_and_keeps_snapshots_private(
        app_factory, monkeypatch, client_count):
    app, rows = fixture(app_factory, count=12)
    rows[0].update(manualUnread='publication-unread', approvals=[
        {'id': 'permission', 'title': 'Original request', 'status': 'pending'}])
    app.state['fixtureCatalog'] = {'nested': {'values': [1, 2]}}
    queues = []
    for index in range(client_count):
        # Initial shell-client creation writes its saved baseline. It must
        # invalidate a frame; measure the existing read-only client views here.
        with app.clients.bind(f'client-{index}'):
            app.shell.client(f'client-{index}')
            queues.append(app.subscribe())
    with app.clients.bind('client-0'):
        duplicate = app.subscribe()

    calls, projected, owners = [], [], []
    projecting = False
    original_scalars, original_browser = history_directory._scalars, app.browser_state

    def scalars(row, *args, **kwargs):
        if projecting:
            calls.append(row['id'])
        return original_scalars(row, *args, **kwargs)

    def browser(*args, **kwargs):
        nonlocal projecting
        if not projected:
            owners.extend(row['id'] for row in app.state['sessions'])
        projected.append(app.clients.current.get())
        projecting = True
        try:
            return original_browser(*args, **kwargs)
        finally:
            projecting = False

    monkeypatch.setattr(history_directory, '_scalars', scalars)
    monkeypatch.setattr(app, 'browser_state', browser)

    def publish():
        calls.clear()
        projected.clear()
        owners.clear()
        app._publish()
        # Count only reads inside real browser projection, excluding _save's
        # separate canonical write/validation work. Duplicate queues share one
        # projection, but distinct clients must still receive private values.
        assert Counter(projected) == Counter(f'client-{i}' for i in range(client_count))
        assert owners and Counter(calls) == Counter(owners)
        snapshots = [queue.get_nowait() for queue in queues]
        assert duplicate.get_nowait() is snapshots[0]
        assert app.directory._read_frame.get() is None
        # Compare the complete output with independent ordinary reads, each
        # preparing its own frame after the publication has ended.
        app._client_snapshots.clear()
        for index, snapshot in enumerate(snapshots):
            with app.clients.bind(f'client-{index}'):
                assert original_browser() == snapshot
            assert snapshot['selectedSessionId'] == rows[index]['id']
            selected = next(row for row in snapshot['sessions'] if row['id'] == rows[index]['id'])
            assert selected['draft'] == app.clients.records[f'client-{index}']['drafts'][rows[index]['id']]
        return snapshots

    first = publish()
    frozen = deepcopy(first)
    if client_count > 1:
        assert first[0]['fixtureCatalog'] is not first[1]['fixtureCatalog']
    notice = app.db.execute(
        'SELECT key,fingerprint FROM history_directory_notices WHERE owner=? AND key=?',
        (rows[0]['id'], f"unread:{rows[0]['id']}")).fetchone()
    assert notice is not None
    rows[0]['approvals'][0]['title'] = 'Changed nested request'
    app.state['fixtureCatalog']['nested']['values'].append(3)
    app.state.setdefault('attentionRead', {})[notice[0]] = notice[1]
    app.clients.records['client-0']['drafts'][rows[0]['id']] = 'Changed private draft'
    second = publish()
    selected = next(row for row in second[0]['sessions'] if row['id'] == rows[0]['id'])
    assert selected['approvals'][0]['title'] == 'Changed nested request'
    assert second[0]['fixtureCatalog']['nested']['values'] == [1, 2, 3]
    assert second[0]['attention'] != first[0]['attention']
    assert first == frozen


@pytest.mark.parametrize('reader', ['explicit', 'unbound', 'nonweb'])
async def test_mixed_publication_keeps_full_readers_outside_frame(app_factory, monkeypatch, reader):
    app, rows = fixture(app_factory, count=12)
    for workspace in app.state['workspaces']:
        Path(workspace['path']).mkdir(parents=True, exist_ok=True)
    cold = add_history(app, 'publication-full-history')
    messages = deepcopy(cold['messages'])
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    assert app.cold_display.is_cold(cold)
    with app.clients.bind('client-0'):
        web_queue = app.subscribe()
    if reader == 'explicit':
        with app.clients.bind('client-0'):
            full_queue = app.subscribe(cold['id'])
    elif reader == 'unbound':
        full_queue = app.subscribe()
    else:
        record = app.clients.attach('full-reader', kind='api')
        record['selectedSessionId'] = cold['id']
        with app.clients.bind('full-reader'):
            full_queue = app.subscribe()

    observed = []
    original_browser, original_session = app._browser_state, app.session_state

    def browser(*args, **kwargs):
        client = app.clients.record()
        if client is None or client['kind'] != 'web':
            assert app.directory._read_frame.get() is None
            observed.append(reader)
        return original_browser(*args, **kwargs)

    def session(identity):
        assert app.directory._read_frame.get() is None
        observed.append(reader)
        return original_session(identity)

    monkeypatch.setattr(app, '_browser_state', browser)
    monkeypatch.setattr(app, 'session_state', session)
    app._publish()
    assert observed == [reader]
    full = full_queue.get_nowait()
    if reader == 'explicit':
        assert next(row for row in full['sessions'] if row['id'] == cold['id'])['messages'] == messages
    assert web_queue.get_nowait()['selectedSessionId'] == rows[0]['id']
    assert app.directory._read_frame.get() is None


async def test_failed_publication_does_not_project_or_deliver(app_factory, monkeypatch):
    app, rows = fixture(app_factory, count=12)
    with app.clients.bind('client-0'):
        queue = app.subscribe()
    revision = app.state['revision']

    def failed_save():
        raise OSError('publication save failed')

    with monkeypatch.context() as failure:
        failure.setattr(app, '_save', failed_save)
        failure.setattr(app, 'browser_state', lambda: pytest.fail('Projected an uncommitted publication'))
        with pytest.raises(OSError, match='publication save failed'):
            app._publish(session_ids={rows[0]['id']})
        assert app.state['revision'] == revision
        assert app._progress_dirty and app._progress_session_ids == {rows[0]['id']}
        assert queue.empty()
        assert app.directory._read_frame.get() is None
