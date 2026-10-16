import asyncio
from contextvars import ContextVar
import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_web.automatic_history import AutomaticHistory
from amplifier_web.transcript_windows import TranscriptWindows


def service_for(tmp_path, *, managed=True):
    session = {'id': 'app', 'nativeProject': 'project', 'nativeIdentity': 'native',
               'createdAt': 1, 'historyManaged': managed, 'status': 'idle',
               'messages': [{'id': 'web-id', 'role': 'user', 'text': 'saved web', 'attachments': [{'id': 'a'}]}]}
    current = ContextVar('client', default='browser')
    service = SimpleNamespace(lock=asyncio.Lock(), state={'sessions': [session]},
        clients=SimpleNamespace(current=current, records={'browser': {'kind': 'web', 'selectedSessionId': 'app'}}),
        queue_clients={}, queue_sessions={}, projections=SimpleNamespace(detail_bodies={}),
        shell=SimpleNamespace(notify=lambda identity: None))
    service._session = lambda identity, **kwargs: session if identity == 'app' else (_ for _ in ()).throw(ValueError('Unknown session'))
    service._publish_changes = lambda **kwargs: (_ for _ in ()).throw(AssertionError('Display read must not save canonical state'))
    service.history = object.__new__(AutomaticHistory)
    service.history.service = service
    service.history.loads = {}
    service.history.windows = TranscriptWindows(service)
    return service, session


def transcript(tmp_path, values):
    root = tmp_path / 'projects' / 'project' / 'sessions' / 'native'
    root.mkdir(parents=True)
    (root / 'transcript.jsonl').write_text(''.join(json.dumps(value) + '\n' for value in values))
    return root


@pytest.mark.asyncio
async def test_bound_selection_reads_only_window_without_canonical_merge(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    from amplifier_web import automatic_history
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    transcript(tmp_path, [{'role': 'user', 'content': str(i)} for i in range(240)])
    service, session = service_for(tmp_path)
    original = copy.deepcopy(session)
    monkeypatch.setattr(automatic_history, 'read_transcript', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Full transcript read')))
    await service.history.load('app')
    assert session == original
    overlay = service.history.windows.project(session)
    assert len(overlay['messages']) == 100
    assert overlay['messages'][0]['text'] == '140'
    assert all(row['displayOnly'] and 'nativeIndex' not in row for row in overlay['messages'])
    assert not overlay['historyLoading']
    assert overlay['sharedHistoryOffset'] is None
    assert overlay['sharedHistoryUserTurnOffset'] is None
    assert overlay['messageWindow']['globalInputUniqueness'] == 'unknown'
    first = copy.deepcopy(overlay)
    older = await service.history.windows.read('app', before=overlay['messageWindow']['before'])
    assert older['items'][0]['text'] == '40'
    assert service.history.windows.project(session) == first


@pytest.mark.asyncio
async def test_web_owned_saved_ids_remain_actionable_without_native_decode(tmp_path, monkeypatch):
    from amplifier_web import automatic_history, transcript_windows
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    transcript(tmp_path, [{'role': 'user', 'content': 'CLI continuation'}])
    service, session = service_for(tmp_path, managed=False)
    original = copy.deepcopy(session)
    monkeypatch.setattr(transcript_windows, '_native_page', lambda *a: (_ for _ in ()).throw(AssertionError('Implicit native decoding')))
    await service.history.load('app')
    view = service.history.windows.project(session)
    assert view['messages'] == original['messages']
    assert view['nativeContinuation']['status'] == 'available'
    assert not view['messages'][0].get('displayOnly')
    assert session == original


@pytest.mark.asyncio
async def test_large_body_is_bounded_on_wire_and_read_by_exact_window_owner(tmp_path, monkeypatch):
    from amplifier_web import automatic_history
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    text = 'x' * 10000
    transcript(tmp_path, [{'role': 'assistant', 'content': text}])
    service, session = service_for(tmp_path)
    await service.history.load('app')
    row = service.history.windows.project(session)['messages'][0]
    assert len(row['text']) == 4096
    detail = await service.history.windows.read_text('app', {**row['textDetail'], 'limit': 20000})
    assert detail['value'] == text
    with pytest.raises(ValueError):
        await service.history.windows.read_text('other', row['textDetail'])
    with pytest.raises(ValueError):
        await service.history.windows.read_text('app', {**row['textDetail'], 'windowKey': 'not-owned'})


@pytest.mark.asyncio
async def test_background_web_observer_routes_bounded_display_without_full_reader(tmp_path, monkeypatch):
    from amplifier_web import automatic_history
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    transcript(tmp_path, [{'role': 'assistant', 'content': 'recent'}])
    service, session = service_for(tmp_path)
    service.clients.current.set(None)
    monkeypatch.setattr(automatic_history, 'read_transcript', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Full read')))
    await service.history.load('app', display=True)
    assert service.history.windows.project(session)['messages'][0]['text'] == 'recent'


@pytest.mark.asyncio
async def test_cancelled_waiter_keeps_physical_reader_owned_and_lock_free(tmp_path, monkeypatch):
    import threading
    from amplifier_web import transcript_windows
    service, _ = service_for(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def held(*args):
        entered.set()
        assert release.wait(2)
        raise ValueError('Controlled discarded read')
    monkeypatch.setattr(transcript_windows, '_native_page', held)
    task = asyncio.create_task(service.history.windows.read('app'))
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
        async with service.lock:
            assert len(service.history.windows.jobs) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(service.history.windows.jobs) == 1
    finally:
        release.set()
        await service.history.windows.close()
    assert not service.history.windows.jobs


@pytest.mark.asyncio
async def test_saved_paging_preserves_ids_and_rejects_changed_source(tmp_path):
    service, session = service_for(tmp_path, managed=False)
    session['messages'] = [{'id': str(i), 'role': 'user', 'text': str(i)} for i in range(250)]
    initial = await service.history.windows.read('app')
    older = await service.history.windows.read('app', before=initial['before'])
    assert [row['id'] for row in initial['items']] == [str(i) for i in range(150, 250)]
    assert [row['id'] for row in older['items']] == [str(i) for i in range(50, 150)]
    session['messages'].append({'id': 'new', 'role': 'user', 'text': 'new'})
    with pytest.raises(ValueError, match='changed'):
        await service.history.windows.read('app', before=older['before'])


@pytest.mark.asyncio
async def test_same_id_middle_edit_invalidates_saved_cursor(tmp_path):
    service, session = service_for(tmp_path, managed=False)
    session['messages'] = [{'id': str(i), 'role': 'user', 'text': str(i)} for i in range(150)]
    page = await service.history.windows.read('app')
    session['messages'][25]['text'] = 'edited without changing identity'
    service.history.windows.invalidate({'app'})
    with pytest.raises(ValueError, match='changed'):
        await service.history.windows.read('app', before=page['before'])


@pytest.mark.asyncio
async def test_native_identity_only_read_never_hydrates_cold_bodies(tmp_path, monkeypatch):
    from amplifier_web import automatic_history
    from amplifier_web.cold_display import ColdRecord
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    transcript(tmp_path, [{'role': 'assistant', 'content': 'native window'}])
    service, original = service_for(tmp_path)
    cold = ColdRecord({**original, '_coldFields': {key: {'$resource': 'unread'} for key in ('messages', 'execution', 'configuration')}}, None)
    dict.pop(cold, 'messages')
    def lookup(identity, *, hydrate=True):
        assert hydrate is False
        return cold
    service._session = lookup
    await service.history.load('app')
    assert service.history.windows.project(cold)['messages'][0]['text'] == 'native window'
    assert set(dict.get(cold, '_coldFields')) == {'messages', 'execution', 'configuration'}


@pytest.mark.asyncio
async def test_cold_web_saved_resource_keeps_canonical_ids_without_any_field_hydration(tmp_path, monkeypatch):
    import sqlite3
    from amplifier_web import automatic_history, cold_display
    from amplifier_web.resource_files import put
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    transcript(tmp_path, [{'role': 'assistant', 'content': 'native copy'}])
    service, original = service_for(tmp_path, managed=False)
    db = sqlite3.connect(tmp_path / 'state.sqlite3')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        values = [{'id': str(i), 'role': 'user', 'text': str(i)} for i in range(240)]
        values[-1]['text'] = 'saved body ' * 1000
        reference = put(db, values)
        cold = cold_display.ColdRecord({**original, '_coldFields': {'messages': reference,
            'execution': {'$resource': 'poison'}, 'configuration': {'$resource': 'poison'}}}, db)
        dict.pop(cold, 'messages')
        service.db = db
        service._session = lambda identity, **kwargs: cold
        monkeypatch.setattr(cold_display, 'load', lambda *args: (_ for _ in ()).throw(AssertionError('Full cold body load')))
        await service.history.load('app')
        view = service.history.windows.project(cold)
        assert [row['id'] for row in view['messages']] == [str(i) for i in range(140, 240)]
        assert not any(row.get('displayOnly') for row in view['messages'])
        assert dict.get(cold, '_coldFields')['messages'] == reference
        detail = view['messages'][-1]['textDetail']
        assert detail['windowKey'] == view['messageWindow']['windowKey']
        text_page = await service.history.windows.read_text('app', {**detail, 'offset': '4096', 'limit': '100'})
        assert text_page['value'] == values[-1]['text'][4096:4196]
        service.history.windows.invalidate({'app'})
        with pytest.raises(ValueError, match='source changed'):
            await service.history.windows.read_text('app', detail)
        # The prior cursor is also invalidated; reload a current window.
        await service.history.windows.load('app')
        view = service.history.windows.project(cold)
        older = await service.history.windows.read('app', before=view['messageWindow']['before'])
        assert [row['id'] for row in older['items']] == [str(i) for i in range(40, 140)]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_field_rechecks_owner_after_source_stat_await(tmp_path, monkeypatch):
    import threading
    from amplifier_web import automatic_history
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    transcript(tmp_path, [{'role': 'assistant', 'content': 'x' * 5000}])
    service, session = service_for(tmp_path)
    await service.history.load('app')
    detail = service.history.windows.project(session)['messages'][0]['textDetail']
    entered, release = threading.Event(), threading.Event()
    original = service.history.windows.reader.source_revision
    def held(*args):
        value = original(*args)
        entered.set()
        assert release.wait(2)
        return value
    monkeypatch.setattr(service.history.windows.reader, 'source_revision', held)
    task = asyncio.create_task(service.history.windows.read_text('app', detail))
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
        session['nativeIdentity'] = 'different-owner'
        release.set()
        with pytest.raises(ValueError, match='source changed'):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cold_web_without_native_and_resource_replacement_are_current(tmp_path, monkeypatch):
    import sqlite3
    from amplifier_web import cold_display
    from amplifier_web.resource_files import put
    service, original = service_for(tmp_path, managed=False)
    original.pop('nativeProject')
    db = sqlite3.connect(tmp_path / 'state.sqlite3')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        reference = put(db, [{'id': 'kept-id', 'role': 'user', 'text': 'before'}])
        cold = cold_display.ColdRecord({**original, '_coldFields': {'messages': reference}}, db)
        dict.pop(cold, 'messages')
        service.db = db
        service._session = lambda identity, **kwargs: cold
        monkeypatch.setattr(cold_display, 'load', lambda *args: (_ for _ in ()).throw(AssertionError('Full body load')))
        await service.history.windows.load('app', only_if_changed=True)
        assert service.history.windows.project(cold)['messages'][0]['text'] == 'before'
        next_reference = put(db, [{'id': 'kept-id', 'role': 'user', 'text': 'after'}])
        dict.get(cold, '_coldFields')['messages'] = next_reference
        service.history.windows.invalidate({'app'})
        assert service.history.windows.project(cold)['historyLoading'] is True
        await service.history.windows.load('app', only_if_changed=True)
        assert service.history.windows.project(cold)['messages'][0]['text'] == 'after'
    finally:
        db.close()


@pytest.mark.asyncio
async def test_managed_rebind_hides_old_display_immediately(tmp_path, monkeypatch):
    from amplifier_web import automatic_history
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    transcript(tmp_path, [{'role': 'assistant', 'content': 'old owner'}])
    service, session = service_for(tmp_path)
    await service.history.load('app')
    assert service.history.windows.project(session)['messages']
    session['nativeIdentity'] = 'rebound'
    assert service.history.windows.project(session)['messages'] == []
    assert service.history.windows.project(session)['historyLoading'] is True


@pytest.mark.asyncio
async def test_real_web_selection_schedules_cold_saved_messages_without_native(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.resource_files import put
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await service.history.close()
        service.history.windows = TranscriptWindows(service)
        await service.dispatch('session.create', {'title': 'Cold'})
        target = service._state['sessions'][-1]
        target.pop('nativeProject', None)
        target['historyManaged'] = False
        reference = put(service.db, [{'id': 'canonical-saved', 'role': 'user', 'text': 'Retained canonical body'}])
        target = ColdRecord({**target, '_coldFields': {'messages': reference}}, service.db)
        dict.pop(target, 'messages', None)
        service._state['sessions'][-1] = target
        service.projections.invalidate(state=service.state)
        service.clients.attach('browser')
        completed = asyncio.Event()
        original = service.history.windows.load
        async def load(*args, **kwargs):
            await original(*args, **kwargs)
            completed.set()
        monkeypatch.setattr(service.history.windows, 'load', load)
        monkeypatch.setattr('amplifier_web.automatic_history.read_transcript', lambda *a, **k: pytest.fail('No canonical full reader'))
        with service.clients.bind('browser'):
            result = await service.dispatch('session.select', {'id': target['id']}, include_state=False)
            assert result['accepted']
            await asyncio.wait_for(completed.wait(), 2)
            page = service.history.windows.project(target)
            assert page['messages'][0]['id'] == 'canonical-saved'
            assert page['messages'][0]['text'] == 'Retained canonical body'
            assert dict.get(target, '_coldFields')['messages'] == reference
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cold_missing_resource_is_unavailable_and_can_retry(tmp_path):
    import sqlite3
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.resource_files import put
    service, original = service_for(tmp_path, managed=False)
    original.pop('nativeProject')
    db = sqlite3.connect(tmp_path / 'state.sqlite3')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        reference = {'$resource': 'a' * 64}
        cold = ColdRecord({**original, '_coldFields': {'messages': reference}}, db)
        dict.pop(cold, 'messages')
        service.db = db
        service._session = lambda identity, **kwargs: cold
        await service.history.windows.load('app')
        view = service.history.windows.project(cold)
        assert not view['historyLoading'] and view['historyError']
        dict.get(cold, '_coldFields')['messages'] = put(db, [{'id': 'restored', 'role': 'user', 'text': 'Recovered'}])
        await service.history.windows.load('app')
        view = service.history.windows.project(cold)
        assert view['messages'][0]['id'] == 'restored'
        assert not view['historyLoading'] and view['historyError'] is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_resident_oversized_message_never_copied_and_neighbors_progress(tmp_path):
    class NoCopy(str):
        def __deepcopy__(self, memo):
            raise AssertionError('Oversized resident body must not be copied')
    service, session = service_for(tmp_path, managed=False)
    session['messages'] = [
        {'id': 'older', 'role': 'user', 'text': 'older'},
        {'id': 'huge', 'role': 'assistant', 'text': NoCopy('x' * (5 * 1024 * 1024))},
        {'id': 'recent', 'role': 'user', 'text': 'recent'},
    ]
    page = await service.history.windows.read('app', limit=2)
    assert [row['id'] for row in page['items']] == ['recent']
    assert page['unavailableRanges'] == [{'index': 1, 'reason': 'record_limit'}]
    older = await service.history.windows.read('app', before=page['before'], limit=2)
    assert [row['id'] for row in older['items']] == ['older']
    assert older['before'] is None
    projected = service.history.windows.project(session)
    assert [row['id'] for row in projected['messages']] == ['older', 'recent']
    assert projected['messageWindow']['unavailableRanges']


@pytest.mark.asyncio
async def test_resident_page_budget_retries_intact_older_record(tmp_path, monkeypatch):
    from amplifier_web import saved_message_window
    monkeypatch.setattr(saved_message_window, 'MAX_BYTES', 1200)
    service, session = service_for(tmp_path, managed=False)
    session['messages'] = [{'id': str(i), 'role': 'user', 'text': 'x' * 100} for i in range(5)]
    page = await service.history.windows.read('app')
    assert 0 < len(page['items']) < 5
    assert not page['unavailableRanges']
    received = list(page['items'])
    while page['before']:
        page = await service.history.windows.read('app', before=page['before'])
        received = page['items'] + received
    assert [row['id'] for row in received] == [str(i) for i in range(5)]


@pytest.mark.asyncio
@pytest.mark.parametrize('cold', [False, True])
async def test_saved_window_reactions_are_current_exact_id_annotations_without_body_hydration(tmp_path, monkeypatch, cold):
    import sqlite3
    from amplifier_web import cold_display
    from amplifier_web.resource_files import put
    service, session = service_for(tmp_path, managed=False)
    session.pop('nativeProject')
    session['messages'] = [{'id': str(i), 'role': 'user', 'text': f'Message {i}'} for i in range(101)]
    class PointAnnotations(dict):
        def items(self):
            raise AssertionError('Do not scan unrelated message annotations')
    session['messageAnnotations'] = PointAnnotations({'1': {'reactions': ['❤️'],
        'attribution': {'caption': 'Untrusted'}, 'text': 'Not the body'}, '0': {'reactions': ['👍']}})
    db = sqlite3.connect(tmp_path / 'state.sqlite3')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        if cold:
            reference = put(db, session['messages'])
            session = cold_display.ColdRecord({**session, '_coldFields': {'messages': reference}}, db)
            dict.pop(session, 'messages')
            service.db = db
            service._session = lambda identity, **kwargs: session
        monkeypatch.setattr(cold_display, 'load', lambda *args: pytest.fail('No full body hydration'))
        await service.history.windows.load('app')
        first = service.history.windows.project(session)
        row = first['messages'][0]
        assert row['id'] == '1' and row['text'] == 'Message 1' and row['reactions'] == ['❤️']
        assert 'attribution' not in row and first['messageWindow']['hasEarlier']
        session['messageAnnotations']['1']['reactions'] = []
        current = service.history.windows.project(session)
        assert current['messages'][0]['reactions'] == []
        assert row['reactions'] == ['❤️'], 'Published rows must stay detached'
        older = await service.history.windows.read('app', before=current['messageWindow']['before'])
        assert older['items'][0]['id'] == '0' and older['items'][0]['reactions'] == ['👍']
    finally:
        db.close()


@pytest.mark.asyncio
async def test_native_display_only_window_does_not_inherit_saved_reactions(tmp_path, monkeypatch):
    from amplifier_web import automatic_history
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    transcript(tmp_path, [{'role': 'assistant', 'content': 'Native observation'}])
    service, session = service_for(tmp_path)
    await service.history.load('app')
    row = service.history.windows.project(session)['messages'][0]
    session['messageAnnotations'] = {row['id']: {'reactions': ['❤️']}}
    current = service.history.windows.project(session)['messages'][0]
    assert current['displayOnly'] and 'reactions' not in current


@pytest.mark.asyncio
@pytest.mark.parametrize('managed', [True, False])
async def test_native_anchor_survives_page_eviction_without_canonical_merge(tmp_path, monkeypatch, managed):
    from amplifier_web import automatic_history, transcript_windows
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    monkeypatch.setattr(transcript_windows, 'MAX_PAGES', 1)
    monkeypatch.setattr(automatic_history, 'read_transcript', lambda *a, **k: pytest.fail('No full transcript read'))
    transcript(tmp_path, [{'role': 'user', 'content': str(i)} for i in range(240)])
    service, session = service_for(tmp_path, managed=managed)
    original = copy.deepcopy(session)
    windows = service.history.windows
    first = await windows.read('app', native=not managed)
    row = first['items'][20]
    # Model only the browser's persisted reference, not retained page bodies.
    reference = json.loads(json.dumps(row['sourceReference']))
    older = await windows.read('app', before=first['before'], native=not managed)
    assert older['items'][0]['text'] == '40'
    assert ('app', first['windowKey']) not in windows.pages
    restored = await windows.read('app', before=reference['restoreCursor'], native=True)
    assert restored == first
    assert next(item for item in restored['items'] if item['id'] == row['id']) == row
    assert len(windows.pages) == 1
    assert restored['coverage']['scannedBytes'] <= 8 * 1024 * 1024
    assert all(item['displayOnly'] and item['sourceReference']['restoreCursor'] == reference['restoreCursor']
               for item in restored['items'])
    assert session == original
