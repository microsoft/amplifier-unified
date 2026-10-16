"""Exact delivery remains durable/visible without rebuilding unrelated views."""
import json

import pytest

from test_automatic_history import app_factory
from test_chat_save_resources import contents
from test_scoped_state_records import chats


@pytest.mark.parametrize('initial_delivery', ['sending', 'failed'])
@pytest.mark.parametrize(('changed_fact', 'same_selection'), [(None, False), (None, True), ('status', False), ('peer_attention', False), ('pending_broad', False)])
async def test_delivery_preserves_other_client_until_shared_facts_change(app_factory, monkeypatch, changed_fact, same_selection, initial_delivery):
    app, rows = chats(app_factory)
    current, peer = rows
    current['status'] = 'working'
    current['messages'][0].update(inputId='input-exact', delivery={'status': initial_delivery})
    receipt = {'accepted': True, 'sessionId': current['id'], 'inputId': 'input-exact', 'delivery': initial_delivery}
    if initial_delivery == 'failed':
        receipt.update(accepted=False, code='worker_startup_failed', status=503, error='Earlier failure')
    if same_selection:
        app.clients.records['1']['selectedSessionId'] = current['id']
    app.db.execute('INSERT INTO commands VALUES (?,?,?)', ('input-exact', 'fixture', json.dumps(receipt)))
    app._publish()
    queues = []
    for client in ('0', '1'):
        with app.clients.bind(client):
            queues.append(app.subscribe())
            app.browser_state()
    projections = []
    original = app.browser_state
    def observe(*args, **kwargs):
        projections.append(app.clients.current.get())
        return original(*args, **kwargs)
    monkeypatch.setattr(app, 'browser_state', observe)
    if changed_fact == 'status':
        current['status'] = 'idle'
    elif changed_fact == 'peer_attention':
        peer.update(status='error', error='Independent peer needs attention')
        app._publish_progress(session_ids={peer['id']}, detail_only=True, record_only=True)
    elif changed_fact == 'pending_broad':
        peer['streaming'] = 'Pending non-detail progress'
        app._publish_progress(session_ids={peer['id']}, detail_only=False, record_only=True)
    await app.on_runtime_event('runtime.delivery', {'sessionId': current['id'], 'inputId': 'input-exact'})
    frame = queues[0].get_nowait()
    visible = next(row for row in frame['sessions'] if row['id'] == current['id'])
    assert visible['messages'][0]['delivery']['status'] == 'accepted'
    assert visible['status'] == current['status']
    assert contents(app, current['id'])['messages'][0]['delivery']['status'] == 'accepted'
    saved_receipt = json.loads(app.db.execute('SELECT receipt FROM commands WHERE id=?', ('input-exact',)).fetchone()[0])
    assert saved_receipt['delivery'] == 'accepted' and saved_receipt['accepted'] is True
    assert 'code' not in saved_receipt and 'error' not in saved_receipt
    assert app.clients.records['0']['drafts'][current['id']] == 'private-0'
    assert app.clients.records['1']['drafts'][peer['id']] == 'private-1'
    if changed_fact is None and not same_selection:
        assert projections == ['0']
        assert queues[1].empty()
    else:
        assert sorted(projections) == ['0', '1']
        other = queues[1].get_nowait()
        if same_selection:
            visible_other = next(row for row in other['sessions'] if row['id'] == current['id'])
            assert visible_other['messages'][0]['delivery']['status'] == 'accepted'
        if changed_fact == 'peer_attention':
            visible_peer = next(row for row in other['sessions'] if row['id'] == peer['id'])
            assert visible_peer['status'] == 'error'
            assert visible_peer['error'] == 'Independent peer needs attention'
            assert contents(app, peer['id'])['error'] == visible_peer['error']
        elif changed_fact == 'pending_broad':
            visible_peer = next(row for row in other['sessions'] if row['id'] == peer['id'])
            assert visible_peer['streaming'] == 'Pending non-detail progress'
            assert contents(app, peer['id'])['streaming'] == visible_peer['streaming']
        assert not app._progress_dirty
