"""Current-source proof joins for long saved peer runs; no real history/model."""
import asyncio
import copy
import threading

import pytest

from amplifier_web.service import AppError
from test_peer_preview import app, make_cold


def long_receiver(service, receiving, *, count=151):
    originals = copy.deepcopy(receiving['messages'])
    receiving['messages'] = [
        {'id': f'prefix-{i}', 'role': 'assistant', 'text': f'Earlier {i}'}
        for i in range(count - len(originals))
    ] + originals
    service._publish()
    return receiving['messages'][-1]


def shown(service, receiving, mid):
    return next(row for row in service.history.windows.project(receiving)['messages'] if row['id'] == mid)


@pytest.mark.parametrize('change', ['duplicate-id', 'duplicate-input', 'text', 'envelope', 'receipt'])
@pytest.mark.parametrize('cold', [False, True])
async def test_long_original_and_offpage_duplicates_rechecked(app, change, cold):
    service, source, receiving, mid = app
    original = long_receiver(service, receiving)
    rows = copy.deepcopy(receiving['messages'])
    if cold:
        receiving = make_cold(service, receiving, rows)
    with service.clients.bind('preview-client'):
        page = await service.history.windows.read(receiving['id'])
        meta = next(row for row in page['items'] if row['id'] == mid)['attribution']['peer']
        assert service.peer_preview.open({'proof': meta['proof'], 'sourceSessionId': receiving['id']}, 'ui')['accepted']
        # Same count and endpoints. The changed middle row is outside the page.
        if change == 'duplicate-id': rows[10]['id'] = mid
        if change == 'duplicate-input': rows[10]['inputId'] = original['inputId']
        if change == 'text': rows[-1]['text'] = rows[-1]['text'].replace('Navigation', 'Navigatiom')
        if change == 'envelope': rows[-1]['peerEnvelope']['purpose'] = 'Changed nested purpose'
        if change == 'receipt': service.db.execute("DELETE FROM commands WHERE id='peer-preview-input'")
        if cold:
            receiving = make_cold(service, receiving, rows)
        else:
            receiving['messages'][:] = rows
        # No publication required for explicit action-time validation.
        with pytest.raises((AppError, ValueError)):
            await service.peer_preview.read({'proof': meta['proof']})
        page = await service.history.windows.read(receiving['id'])
        assert 'attribution' not in next(row for row in page['items'] if row['id'] == mid)
        service._publish()
        if not cold:
            assert 'attribution' not in shown(service, receiving, mid)


async def test_passive_snapshot_reuses_content_proof_not_scalar_fences(app, monkeypatch):
    service, source, receiving, mid = app
    long_receiver(service, receiving, count=2001)
    windows = service.history.windows
    calls = []
    original = windows.peer_originals.resident
    def counted(rows, wanted):
        calls.append(len(rows))
        return original(rows, wanted)
    monkeypatch.setattr(windows.peer_originals, 'resident', counted)
    with service.clients.bind('preview-client'):
        assert shown(service, receiving, mid)['attribution']['peer']['available']
        assert calls == [2001]
        for i in range(5):
            receiving['streaming'] = f'Current scalar {i}'
            windows.invalidate({receiving['id']})
            assert shown(service, receiving, mid)['attribution']['peer']['available']
        assert calls == [2001]  # No metadata pass per scalar/projection.
        old_snapshot = service._message_preparation.entries[receiving['id']].prepared.identity
        receiving['messages'][10]['id'] = mid
        service._publish()
        assert service._message_preparation.entries[receiving['id']].prepared.identity != old_snapshot
        assert 'attribution' not in shown(service, receiving, mid)
        assert calls == [2001, 2001]
        receiving['messages'][10]['id'] = 'prefix-10'
        service._publish()
        assert shown(service, receiving, mid)['attribution']['peer']['available']
        assert calls == [2001, 2001]  # Exact prior immutable revision can be reused.


async def test_uncommitted_current_page_never_gets_stale_body_or_count_join(app):
    service, source, receiving, mid = app
    row = long_receiver(service, receiving)
    with service.clients.bind('preview-client'):
        original = shown(service, receiving, mid)
        proof = original['attribution']['peer']['proof']
        row['text'] = 'New uncommitted body'
        current = shown(service, receiving, mid)
        assert current['text'] == 'New uncommitted body' and 'attribution' not in current
        with pytest.raises((AppError, ValueError)):
            service.peer_preview.open({'proof': proof, 'sourceSessionId': receiving['id']}, 'ui')
        appended = service._message(receiving, 'assistant', 'Uncommitted current answer')
        frame = service.history.windows.project(receiving)
        assert frame['messages'][-1]['id'] == appended['id']


async def test_failed_save_revokes_passive_proof_without_discarding_retry_preparation(app, monkeypatch):
    from amplifier_web import session_projection
    service, source, receiving, mid = app
    long_receiver(service, receiving)
    entry = service._message_preparation.entries[receiving['id']]
    with service.clients.bind('preview-client'):
        assert shown(service, receiving, mid)['attribution']['peer']['available']
        receiving['messages'][10]['id'] = mid
        def fail(*args, **kwargs):
            raise ValueError('Synthetic failed save')
        with monkeypatch.context() as patch:
            patch.setattr(session_projection, 'persist', fail)
            with pytest.raises(ValueError, match='failed save'):
                service._save()
        assert service._message_preparation.entries[receiving['id']] is entry
        assert 'attribution' not in shown(service, receiving, mid)
        service._publish()
        assert 'attribution' not in shown(service, receiving, mid)


async def test_cold_to_resident_borrowed_alias_does_not_reuse_frozen_proof(app):
    service, source, receiving, mid = app
    long_receiver(service, receiving)
    receiving = make_cold(service, receiving, copy.deepcopy(receiving['messages']))
    with service.clients.bind('preview-client'):
        page = await service.history.windows.read(receiving['id'])
        proof = page['items'][-1]['attribution']['peer']['proof']
        alias = receiving['messages']
        assert 'messages' not in dict.get(receiving, '_coldFields', {})
        alias[10]['inputId'] = alias[-1]['inputId']
        with pytest.raises((AppError, ValueError)):
            service.peer_preview.open({'proof': proof, 'sourceSessionId': receiving['id']}, 'ui')
        service._publish()
        assert 'attribution' not in shown(service, receiving, mid)


async def test_cold_proof_owner_changed_during_worker_is_rejected(app, monkeypatch):
    service, source, receiving, mid = app
    long_receiver(service, receiving)
    receiving = make_cold(service, receiving, copy.deepcopy(receiving['messages']))
    windows = service.history.windows
    entered, release = threading.Event(), threading.Event()
    actual = windows.peer_originals.cold
    def held(*args):
        result = actual(*args)
        entered.set()
        assert release.wait(3)
        return result
    monkeypatch.setattr(windows.peer_originals, 'cold', held)
    with service.clients.bind('preview-client'):
        task = asyncio.create_task(windows.read(receiving['id']))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
            receiving['createdAt'] += 1
            release.set()
            with pytest.raises(ValueError, match='source changed'):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


async def test_long_pages_keep_cursor_order_and_immediate_preview(app):
    service, source, receiving, mid = app
    long_receiver(service, receiving, count=151)
    with service.clients.bind('preview-client'):
        initial = service.history.windows.project(receiving)
        assert len(initial['messages']) == 100
        before = initial['messageWindow']['before']
        earlier = await service.history.windows.read(receiving['id'], before=before)
        assert len(earlier['items']) == 51
        assert [r['id'] for r in earlier['items'] + initial['messages']] == [r['id'] for r in receiving['messages']]
        meta = initial['messages'][-1]['attribution']['peer']
        result = await service.peer_preview.read({'proof': meta['proof']})
        assert result['page']['items']


@pytest.mark.parametrize('field', ['id', 'inputId'])
async def test_unpublished_offpage_edit_keeps_only_labelled_committed_snapshot(app, field):
    service, source, receiving, mid = app
    long_receiver(service, receiving)
    windows = service.history.windows
    with service.clients.bind('preview-client'):
        prior = windows.project(receiving)
        meta = prior['messages'][-1]['attribution']['peer']
        provenance = prior['messageWindow']['coverage']['provenance']
        assert provenance['basis'] == 'committed-snapshot'
        assert provenance['liveHistoryChecked'] is False
        receiving['messages'][10][field] = receiving['messages'][-1][field]
        current = windows.project(receiving)
        assert current['messages'] == prior['messages']
        assert current['messageWindow']['coverage']['provenance'] == provenance
        assert current['messageWindow']['globalInputUniqueness'] == 'unknown'
        # The cursor reads the SAME snapshot, not the newly duplicated live row.
        older = await windows.read(receiving['id'], before=prior['messageWindow']['before'])
        assert older['items'][10].get(field) != receiving['messages'][10][field]
        assert older['coverage']['provenance'] == provenance
        # Fresh current read and action never inherit snapshot authority.
        fresh = await windows.read(receiving['id'])
        assert 'attribution' not in fresh['items'][-1]
        with pytest.raises((AppError, ValueError)):
            service.peer_preview.open({'proof': meta['proof'], 'sourceSessionId': receiving['id']}, 'ui')
        service._publish()
        latest = windows.project(receiving)
        assert latest['messageWindow']['coverage']['provenance']['revision'] != provenance['revision']
        assert 'attribution' not in latest['messages'][-1]
        with pytest.raises(ValueError):
            await windows.read(receiving['id'], before=prior['messageWindow']['before'])


async def test_passive_snapshot_returns_detached_nested_rows_and_preserves_new_append(app):
    service, source, receiving, mid = app
    long_receiver(service, receiving)
    with service.clients.bind('preview-client'):
        row = shown(service, receiving, mid)
        row['peerEnvelope']['purpose'] = 'Mutating a returned frame'
        assert shown(service, receiving, mid)['peerEnvelope']['purpose'] != row['peerEnvelope']['purpose']
        # Even identical last-row append must not hide a new canonical occurrence.
        receiving['messages'].append(copy.deepcopy(receiving['messages'][-1]))
        frame = service.history.windows.project(receiving)
        assert [r['id'] for r in frame['messages'][-2:]] == [mid, mid]
        assert all('attribution' not in r for r in frame['messages'][-2:])
        assert 'provenance' not in frame['messageWindow']['coverage']


def inline_receiver(service, receiving):
    from amplifier_web.cold_display import ColdRecord, raw
    from amplifier_web.resource_files import prepare
    prepared = prepare(receiving['messages'])
    service.db.execute('INSERT OR REPLACE INTO state_resources VALUES (?, ?)',
                       (prepared.identity, prepared.text))
    service.db.commit()
    cold = ColdRecord({**raw(receiving), '_coldFields': {
        'messages': {'$resource': prepared.identity, 'bytes': prepared.size}}}, service.db)
    dict.pop(cold, 'messages', None)
    service.state['sessions'] = [cold if r['id'] == receiving['id'] else r
                                 for r in service.state['sessions']]
    return cold, prepared


async def test_inline_passive_reuse_never_hashes_or_fetches_whole_index(app, monkeypatch):
    service, source, receiving, mid = app
    long_receiver(service, receiving, count=2001)
    receiving, prepared = inline_receiver(service, receiving)
    windows = service.history.windows
    with service.clients.bind('preview-client'):
        await windows.load(receiving['id'])
        assert shown(service, receiving, mid)['attribution']['peer']['available']
        actual = windows._cold_source
        def forbidden(*args):
            raise AssertionError('Passive frame fetched the full cold index')
        with monkeypatch.context() as patch:
            patch.setattr(windows, '_cold_source', forbidden)
            for i in range(5):
                receiving['streaming'] = str(i)
                assert shown(service, receiving, mid)['attribution']['peer']['available']
            service.db.execute('CREATE TABLE unrelated_scalar(value INTEGER)')
            service.db.execute('INSERT INTO unrelated_scalar VALUES (1)')
            service.db.commit()
            assert 'attribution' not in shown(service, receiving, mid)
        assert actual(receiving).identity == prepared.identity
        await windows.load(receiving['id'])
        assert shown(service, receiving, mid)['attribution']['peer']['available']


@pytest.mark.parametrize('other_connection', [False, True])
async def test_inline_same_length_corruption_never_reuses_proof(app, monkeypatch, other_connection):
    import sqlite3
    service, source, receiving, mid = app
    long_receiver(service, receiving)
    receiving, prepared = inline_receiver(service, receiving)
    windows = service.history.windows
    with service.clients.bind('preview-client'):
        await windows.load(receiving['id'])
        assert shown(service, receiving, mid)['attribution']['peer']['available']
        connection = service.db
        if other_connection:
            path = next(row[2] for row in service.db.execute('PRAGMA database_list') if row[1] == 'main')
            connection = sqlite3.connect(path)
        try:
            changed = prepared.text.replace('Navigation', 'Navigatiom')
            assert len(changed) == len(prepared.text) and changed != prepared.text
            connection.execute('UPDATE state_resources SET value=? WHERE id=?', (changed, prepared.identity))
            connection.commit()
            # The passive call performs no body read/hash even on invalidation.
            with monkeypatch.context() as patch:
                patch.setattr(windows, '_cold_source', lambda *a: (_ for _ in ()).throw(AssertionError('Passive fetch')))
                assert 'attribution' not in shown(service, receiving, mid)
            with pytest.raises(ValueError):
                await windows.read(receiving['id'])
        finally:
            if other_connection:
                connection.close()


async def test_passive_rejected_snapshot_is_not_rescanned_on_each_scalar(app, monkeypatch):
    service, source, receiving, mid = app
    long_receiver(service, receiving, count=2001)
    receiving['messages'][1900]['source'] = 'x' * 201
    service._publish()
    windows = service.history.windows
    actual = windows.peer_originals.resident
    calls = []
    def counted(rows, wanted):
        calls.append(len(rows))
        return actual(rows, wanted)
    monkeypatch.setattr(windows.peer_originals, 'resident', counted)
    with service.clients.bind('preview-client'):
        for i in range(5):
            receiving['streaming'] = str(i)
            assert 'attribution' not in shown(service, receiving, mid)
        assert calls == [2001]
        receiving['messages'][1900].pop('source')
        service._publish()
        assert shown(service, receiving, mid)['attribution']['peer']['available']
        assert calls == [2001, 2001]
