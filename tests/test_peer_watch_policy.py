"""Optional peer admission counts and complete bounded one-shot observations."""
import copy
from types import SimpleNamespace

import pytest

from amplifier_web import collaboration, coordination_watches, test_execution_settings as settings
from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, agent_action, send, Runtime
from test_coordination_revisions import watch
from test_peer_inbox_policy import policy


async def test_default_pair_rate_accepts_more_than_32_messages_and_does_not_count(app, monkeypatch):
    source, target = app.state['sessions']
    target['status'] = 'working'
    # Same recorded instant, so the former 32/min ceiling cannot age away.
    monkeypatch.setattr(collaboration, 'time', SimpleNamespace(time=lambda: 2000000000.0))
    statements = []
    app.db.set_trace_callback(lambda sql: statements.append(sql) if 'count(*)' in sql.lower() else None)
    for index in range(40):
        await send(app, f'burst-{index}', mode='notify' if index % 2 else 'queue')
    app.db.set_trace_callback(None)
    assert settings.concurrency(app)['maxPeerMessagesPerMinute'] == 0
    assert len(target['messages']) == 40 and not app.runtime.inputs
    assert not [sql for sql in statements if '$.acceptedAt' in sql]
    assert (await send(app, 'burst-0'))['duplicate']


async def test_positive_pair_rate_rejects_atomically_and_zero_does_not_retry(app, monkeypatch):
    source, target = app.state['sessions']
    target['status'] = 'working'
    clock = [2000000000.0]
    monkeypatch.setattr(collaboration, 'time', SimpleNamespace(time=lambda: clock[0]))
    await policy(app, maxPeerMessagesPerMinute=2)
    await send(app, 'one')
    await send(app, 'two', mode='notify')
    before = app.db.execute('SELECT id,receipt FROM commands ORDER BY rowid').fetchall()
    messages = copy.deepcopy(target['messages'])
    with pytest.raises(AppError, match='configured limit of 2') as exc:
        await send(app, 'rejected')
    assert exc.value.status == 429
    assert app.db.execute('SELECT id,receipt FROM commands ORDER BY rowid').fetchall() == before
    assert target['messages'] == messages and not app.runtime.inputs
    assert (await send(app, 'one'))['duplicate']
    await policy(app, maxPeerMessagesPerMinute=0)
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='rejected'").fetchone()
    await send(app, 'fresh')
    await policy(app, maxPeerMessagesPerMinute=2)
    clock[0] += 61
    await send(app, 'next-minute')


async def test_optional_watch_quota_rejection_dedup_cancel_and_zero(app):
    source, target = app.state['sessions']
    await policy(app, maxPendingWatchesPerTarget=2)
    await watch(app, 'one')
    await watch(app, 'two')
    before = app.db.execute('SELECT id,receipt FROM commands ORDER BY rowid').fetchall()
    with pytest.raises(AppError, match='configured limit of 2') as exc:
        await watch(app, 'rejected')
    assert exc.value.status == 429
    assert app.db.execute('SELECT id,receipt FROM commands ORDER BY rowid').fetchall() == before
    assert (await watch(app, 'one'))['duplicate']
    await agent_action(app, source, 'coordination.watch.cancel', {'sessionId': source['id'], 'watchId': 'one'}, 'cancel')
    await watch(app, 'after-cancel')
    await policy(app, maxPendingWatchesPerTarget=0)
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='rejected'").fetchone()
    await watch(app, 'fresh')
    assert app.collaboration.receipt('one')['watch']['status'] == 'cancelled'
    assert not app.runtime.inputs


@pytest.mark.parametrize('condition', ['status_changed', 'idle'])
async def test_one_event_observes_130_watches_with_captured_status_time_and_high_water(app, monkeypatch, condition):
    source, target = app.state['sessions']
    target['status'] = 'working' if condition == 'idle' else 'idle'
    watches = app.collaboration.watches
    baseline = target['status']
    for index in range(130):
        await watch(app, f'watch-{index}', condition, expires=3600)
    assert settings.concurrency(app)['maxPendingWatchesPerTarget'] == 0
    event_time = coordination_watches.time.time()
    clock = [event_time]
    monkeypatch.setattr(coordination_watches, 'time', SimpleNamespace(time=lambda: clock[0]))
    finish = watches.finish
    workspace = target['workspace']
    status = 'idle' if condition == 'idle' else 'working'
    target['status'] = status
    pages = []
    app.db.set_trace_callback(lambda sql: pages.append(sql) if sql.startswith('SELECT rowid, receipt FROM commands') else None)
    def finish_and_register(receipt, state, text, **kwargs):
        finish(receipt, state, text, **kwargs)
        if receipt['requestId'] == 'watch-0':
            # Simulate a registration interleaved after event capture. The new
            # row deliberately has the old baseline: only the high-water fence
            # prevents it from consuming this event, not condition mismatch.
            later = copy.deepcopy(app.collaboration.receipt('watch-129'))
            later['requestId'] = 'later-watch'
            later['subscription']['continuationId'] = 'later-notice'
            app.collaboration.insert('later-watch', 'later-watch', later)
            clock[0] += 7200
            target['status'] = 'stopped'
    monkeypatch.setattr(watches, 'finish', finish_and_register)
    changed = watches.observe(target, 'runtime.status', {'status': status})
    app.db.set_trace_callback(None)
    target.update(status=status, workspace=workspace)
    clock[0] = event_time
    assert changed == {source['id']} and baseline != status
    assert len(pages) == 4 and all('LIMIT 64' in sql and 'rowid<=' in sql and 'rowid>' in sql for sql in pages)
    for index in range(130):
        receipt = app.collaboration.receipt(f'watch-{index}')
        assert receipt['watch']['status'] == ('idle' if condition == 'idle' else 'changed')
        assert receipt['watch']['observedStatus'] == status
        assert receipt['watch']['observedAt'] == receipt['terminalOutcome']['observedAt'] == event_time
    assert app.collaboration.receipt('later-watch')['watch']['status'] == 'waiting'
    notices = app.db.execute("SELECT id,receipt FROM commands WHERE json_extract(receipt,'$.dependencyRequestId') LIKE 'watch-%' ORDER BY rowid").fetchall()
    assert len(notices) == 130 and not app.runtime.inputs
    monkeypatch.setattr(watches, 'finish', finish)
    # Retained completed watches cannot produce a second notification. The
    # later registration may consume this genuinely later event exactly once.
    watches.observe(target, 'runtime.status', {'status': status})
    assert app.db.execute("SELECT id,receipt FROM commands WHERE json_extract(receipt,'$.dependencyRequestId') LIKE 'watch-%' ORDER BY rowid").fetchall() == notices
    assert app.collaboration.receipt('later-watch')['watch']['status'] != 'waiting'


async def test_watch_event_uses_expiry_at_entry_and_no_match_still_advances_pages(app):
    source, target = app.state['sessions']
    watches = app.collaboration.watches
    # Real registrations past both old bounds; an unchanged first page must not
    # prevent the later matching/expired watches from being examined.
    for index in range(66):
        await watch(app, f'watch-{index}', expires=3600)
    for index in range(64):
        receipt = app.collaboration.receipt(f'watch-{index}')
        receipt['watch']['baselineStatus'] = 'working'
        app.collaboration.save(receipt['requestId'], receipt)
    expired = app.collaboration.receipt('watch-64')
    expired['watch']['expiresAt'] = 0
    app.collaboration.save('watch-64', expired)
    target['status'] = 'working'
    watches.observe(target, 'runtime.status', {'status': 'working'})
    assert all(app.collaboration.receipt(f'watch-{i}')['watch']['status'] == 'waiting' for i in range(64))
    assert app.collaboration.receipt('watch-64')['watch']['status'] == 'expired'
    assert app.collaboration.receipt('watch-65')['watch']['status'] == 'changed'


async def test_restart_pages_all_watches_and_preserves_stop_and_unknown_notices(app):
    source, target = app.state['sessions']
    for index in range(130):
        await watch(app, f'watch-{index}', expires=3600)
    # Settle one with a durable unknown delivery; the remaining watches survive
    # restart and an explicit requester Stop without reviving that requester.
    first = app.collaboration.receipt('watch-0')
    app.collaboration.watches.finish(first, 'changed', 'Retained observation')
    identity = first['subscription']['continuationId']
    notice = app.collaboration.receipt(identity)
    notice['delivery'] = 'unknown'
    app.collaboration.save(identity, notice)
    original = app.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()[0]
    source['interruptionRevision'] = 1
    app._publish()
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        pages = []
        reopened.db.set_trace_callback(lambda sql: pages.append(sql) if sql.startswith('SELECT rowid, receipt FROM commands') else None)
        reopened.collaboration.watches.start()
        reopened.db.set_trace_callback(None)
        assert len(reopened.collaboration.watches.handles) == 129
        assert len(pages) >= 4 and all('LIMIT 64' in sql and 'rowid<=' in sql for sql in pages)
        current = reopened._session(target['id'])
        current['status'] = 'working'
        reopened.collaboration.watches.observe(current, 'runtime.status', {'status': 'working'})
        assert reopened.collaboration.receipt('watch-129')['watch']['status'] == 'changed'
        assert reopened.collaboration.receipt('watch-129')['subscription']['status'] == 'suppressed'
        assert reopened.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()[0] == original
        reopened.collaboration.watches.start()
        assert not reopened.runtime.inputs
    finally:
        await reopened.close()
