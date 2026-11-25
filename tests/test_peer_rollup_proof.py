"""Current-source proof joins for long saved peer runs; no real history/model."""
import asyncio
import copy
import threading
from unittest.mock import AsyncMock

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


@pytest.mark.parametrize('count', [21, 51, 100, 151])
async def test_long_pages_keep_cursor_order_and_immediate_preview(app, count):
    service, source, receiving, mid = app
    long_receiver(service, receiving, count=count)
    with service.clients.bind('preview-client'):
        initial = service.history.windows.project(receiving)
        assert len(initial['messages']) == 20
        before = initial['messageWindow']['before']
        earlier = await service.history.windows.read(receiving['id'], before=before)
        assert len(earlier['items']) == min(count - 20, 100)
        rows = earlier['items'] + initial['messages']
        if earlier['before']:
            oldest = await service.history.windows.read(receiving['id'], before=earlier['before'])
            rows = oldest['items'] + rows
        assert [r['id'] for r in rows] == [r['id'] for r in receiving['messages']]
        meta = initial['messages'][-1]['attribution']['peer']
        result = await service.peer_preview.read({'proof': meta['proof']})
        assert result['page']['items']


@pytest.mark.parametrize('count', [21, 100, 101])
async def test_suggestion_capture_preserves_peer_windows_across_cooling(app, count, monkeypatch):
    from amplifier_web.cold_display import load
    from amplifier_web.prompt_suggestions import BROWSER
    from amplifier_web.state_records import load_owner
    from amplifier_web.suggestion_settings import configure

    service, source, receiving, mid = app
    sid = receiving['id']
    originals = copy.deepcopy(receiving['messages'])
    receiving.update(status='idle', historyManaged=False, messages=[
        {'id': f'prefix-{i}', 'role': 'assistant', 'text': f'Earlier {i}'}
        for i in range(count - len(originals) - 2)
    ] + originals + [
        {'id': 'human-tail', 'role': 'user', 'inputOrigin': 'ui',
         'text': 'Check the next step.<context_file>PRIVATE PAYLOAD</context_file>'},
        {'id': 'assistant-tail', 'role': 'assistant', 'source': 'amplifier',
         'text': 'The complete answer.'},
    ])
    del originals  # Retirement must not strand an issued mutable peer-row alias.
    assert sum(len(row.get('text', '')) for row in receiving['messages']) < 16_000
    service._publish_changes(sessions={sid})
    descriptor, _ = load_owner(service.db, sid)
    saved = load(service.db, descriptor['$viewPayload'])
    # Normal persistence uses the row threshold, independently of resident cooling.
    if count <= 100:
        assert saved['messages'] == receiving['messages']
        assert 'messages' not in saved.get('_coldFields', {})
    else:
        assert 'messages' not in saved
        assert load(service.db, saved['_coldFields']['messages']) == receiving['messages']
    expected_ids = [row['id'] for row in receiving['messages']]
    host, windows = service.prompt_suggestions, service.history.windows
    manager = host.manager
    manager.store.update(service.data_dir, 'global', lambda settings: settings.update(config={
        'providers': [{'id': 'saved', 'module': 'provider-openai',
                       'config': {'api_key': 'synthetic'}}]}))
    configure(manager, {'enabled': True, 'shareRecentConversation': True,
                        'provider': 'saved', 'model': 'utility'}, service.data_dir, 'global')
    service._state['settings']['suggestedMessages'] = True
    service.clients.records['preview-client']['selectedSessionId'] = sid
    probe = AsyncMock(return_value={'status': 'ready', 'suggestion': 'Check the next result.',
                                   'usage': None, 'cost': None})
    monkeypatch.setattr(manager, 'probe', probe)
    token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})

    async def request(identity):
        args = {'sessionId': sid, 'requestId': identity}
        await host.dispatch('session.suggestion.request',
                            {**args, 'expectedLastMessageId': 'assistant-tail'}, 'ui')
        await asyncio.gather(*tuple(host.jobs))
        return await host.dispatch('session.suggestion.status', args, 'ui')

    def forbidden(*args, **kwargs):
        pytest.fail('Utility capture must not hydrate or scan peer originals')

    try:
        with service.clients.bind('preview-client'):
            before = windows.project(receiving)
            page = await windows.read(sid)
            assert len(before['messages']) == 20
            assert len(page['items']) == min(count, 100)
            proof = next(row for row in page['items'] if row['id'] == mid)['attribution']['peer']['proof']
            assert (await service.peer_preview.read({'proof': proof}))['page']['items']
            with monkeypatch.context() as guard:
                guard.setattr(service.cold_display, 'hydrate', forbidden)
                guard.setattr(windows.peer_originals, 'resident', forbidden)
                guard.setattr(windows.peer_originals, 'cold', forbidden)
                first = await request('resident-capture')
            assert first['status'] == 'ready'
            assert probe.await_count == 1
            assert probe.call_args.args[1]['transcript'] == (
                'Human:\nCheck the next step.\n\nAssistant:\nThe complete answer.')
            assert windows.project(receiving) == before
            assert (await windows.read(sid))['items'] == page['items']
            assert (await service.peer_preview.read({'proof': proof}))['page']['items']
            if count > 100:
                service._state['selectedSessionId'] = None
                with monkeypatch.context() as guard:
                    guard.setattr(service.cold_display, 'RECENT_LIMIT', 0)
                    # Force the small fixture's representation transition, not
                    # production eligibility; ownership/protection guards remain.
                    guard.setattr(service.cold_display, 'MIN_BYTES', 1)
                    assert sid in service.cold_display.retire(force=True)
                assert not dict.__contains__(receiving, 'messages')
                with monkeypatch.context() as guard:
                    guard.setattr(service.cold_display, 'hydrate', forbidden)
                    guard.setattr(windows.peer_originals, 'resident', forbidden)
                    guard.setattr(windows.peer_originals, 'cold', forbidden)
                    cold = await request('cold-capture')
                assert cold['status'] == 'ready' and cold['reused']
                assert cold['sourceKey'] == first['sourceKey']
                assert not dict.__contains__(receiving, 'messages')
                cold_page = await windows.read(sid)
                cold_proof = next(row for row in cold_page['items'] if row['id'] == mid)['attribution']['peer']['proof']
                assert (await service.peer_preview.read({'proof': cold_proof}))['page']['items']
                service.cold_display.hydrate(receiving)
                hydrated = await request('hydrated-capture')
                assert hydrated['status'] == 'ready' and hydrated['reused']
                assert hydrated['sourceKey'] == first['sourceKey']
                assert probe.await_count == 1
                fresh_page = await windows.read(sid)
                fresh_proof = next(row for row in fresh_page['items'] if row['id'] == mid)['attribution']['peer']['proof']
                assert (await service.peer_preview.read({'proof': fresh_proof}))['page']['items']
                # Hydration exposes mutable rows, not a committed snapshot.
                # Publish through the normal owner before passive snapshot reuse.
                service._publish_changes(sessions={sid})
            await windows.load(sid)
            latest = windows.project(receiving)
            assert len(latest['messages']) == 20
            earlier = await windows.read(sid, before=latest['messageWindow']['before'])
            assert [row['id'] for row in earlier['items'] + latest['messages']] == expected_ids
            proof = next(row for row in latest['messages'] if row['id'] == mid)['attribution']['peer']['proof']
            assert (await service.peer_preview.read({'proof': proof}))['page']['items']
    finally:
        BROWSER.reset(token)


async def test_older_inline_page_reuses_observed_peer_proof_on_open(app, monkeypatch):
    service, source, receiving, mid = app
    long_receiver(service, receiving, count=51)
    service._message_preparation.entries.clear()  # First open after loading an older inline record.
    windows = service.history.windows
    with service.clients.bind('preview-client'):
        await windows.load(receiving['id'])
        assert shown(service, receiving, mid)['attribution']['peer']['available']
        with monkeypatch.context() as context:
            def unexpected_scan(*args, **kwargs):
                pytest.fail('Repeated a full resident proof on passive projection')
            context.setattr(windows.peer_originals, 'resident', unexpected_scan)
            assert shown(service, receiving, mid)['attribution']['peer']['available']
        proof = shown(service, receiving, mid)['attribution']['peer']['proof']
        receiving['messages'][0]['id'] = mid
        with pytest.raises((AppError, ValueError)):
            await service.peer_preview.read({'proof': proof})


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


async def test_inline_explicit_reread_can_requalify_after_unrelated_worker_time_commit(app, monkeypatch):
    service, source, receiving, mid = app
    long_receiver(service, receiving)
    receiving, _ = inline_receiver(service, receiving)
    windows = service.history.windows
    entered, release = threading.Event(), threading.Event()
    actual = windows.peer_originals.cold
    def held(*args):
        result = actual(*args)
        entered.set()
        assert release.wait(3)
        return result
    service.db.execute('CREATE TABLE scalar_during_read(value INTEGER)')
    service.db.commit()
    monkeypatch.setattr(windows.peer_originals, 'cold', held)
    with service.clients.bind('preview-client'):
        task = asyncio.create_task(windows.read(receiving['id']))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
            service.db.execute('INSERT INTO scalar_during_read VALUES (1)')
            service.db.commit()
            release.set()
            page = await task
            assert page['items'][-1]['attribution']['peer']['available']
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
