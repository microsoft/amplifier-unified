"""Whole-service scale acceptance: durable directory, bounded working set."""
import time

import pytest

from amplifier_web.history_query import query_history
from amplifier_web.native_catalog import NativeCatalog
from amplifier_web.session_files import amplifier_home
from amplifier_web import state_records
from test_automatic_history import app_factory
from test_native_directory import seed


async def test_warm_service_27000_rows_admits_six_without_loading_dormant_metadata(app_factory, tmp_path, monkeypatch):
    # Periodic GC has separate bounded-proof and dormant-root acceptance.
    # Isolate ordinary directory startup/refresh/query work in this scale test.
    monkeypatch.setattr('amplifier_web.storage_migration.maintenance', lambda service: None)
    app = app_factory()
    identities = []
    for number in range(6):
        row = app._new_session({'title': 'Working chat ' + str(number)})
        row['status'] = 'working'
        identities.append(row['id'])
        app._state['sessions'].append(row)
    app._state['selectedSessionId'] = identities[0]
    app._save()
    home = app.data_dir
    await app.close()
    workspace = tmp_path / 'archive'
    workspace.mkdir()
    native = NativeCatalog(home / 'native-catalog.sqlite3', amplifier_home())
    project, workspace_row = seed(native, workspace, 26994)
    # A warm cache is a directory: no native row body needs decoding at start.
    monkeypatch.setattr(NativeCatalog, 'load', lambda *_: pytest.fail('eager native cache restore'))
    complete_load = state_records.load
    full_reads = []
    monkeypatch.setattr(state_records, 'load', lambda *args, **kwargs: (full_reads.append(1), complete_load(*args, **kwargs))[1])
    warm = app_factory(home=home)
    assert full_reads == []
    assert set(row['id'] for row in warm._state['sessions']) == set(identities)
    assert len(warm._session_projection_refs) == 6
    assert not warm.runtime.started and not warm.runtime.sent
    # Coverage is separately known; no forced discovery/transcript reads in
    # this warm-cache scenario, while normal selected checks remain intact.
    monkeypatch.setattr(warm.history.index, '_invalidations', lambda: (True, set()))
    warm.history.index._reconcile_at = time.monotonic() + 900
    monkeypatch.setattr(warm.history, '_refresh_selected_history', lambda: _nothing())
    await warm.history.refresh(force=False)
    assert len(warm._state['sessions']) == 6
    assert warm.directory.root_count() == 27000
    assert warm.history._catalog_rows == {}
    assert len(warm.history._native_snapshot.get('sessions', [])) <= 6
    assert warm.history.index._files == {}
    assert full_reads == []

    decoded = []
    decode = warm.directory.decode
    monkeypatch.setattr(warm.directory, 'decode', lambda raw: (decoded.append(1), decode(raw))[1])
    state = {**warm._state, 'view': {**warm._state['view'], 'navChatScope': 'all', 'navFilter': 'Straße 2699*'}}
    result = warm.directory.chats(state)
    assert result['total'] == 5 and len(result['items']) == 5
    assert len(decoded) == 5
    assert warm.directory.root_count() == 27000 and len(decoded) == 5
    for _ in range(3):
        await warm.history.refresh(force=False)
        assert len(warm._state['sessions']) == 6
    assert full_reads == []

    before = set(row['id'] for row in warm._state['sessions'])
    page = await query_history(warm, {'action': 'list', 'scope': 'all', 'limit': 5}, identities[0])
    assert len(page['items']) == 5 and page['next_offset'] == 5
    assert set(row['id'] for row in warm._state['sessions']) == before
    assert not warm.runtime.started and not warm.runtime.sent

    # VM work is bounded by the requested identity, not a scan of27k typed rows.
    steps = []
    warm.db.set_progress_handler(lambda: (steps.append(1), 0)[1], 100)
    try:
        target = warm.directory.resolve('saved-26000', project)
    finally:
        warm.db.set_progress_handler(None, 0)
    assert len(steps) * 100 < 20000
    assert target['title'] == 'Straße 26000'
    assert len(warm._state['sessions']) == 6
    owner = warm._admit_session(target)
    assert owner['nativeProject'] == project and owner['historyLoaded'] is False
    assert owner['messages'] == [] and len(warm._state['sessions']) == 7
    assert not warm.runtime.started and not warm.runtime.sent


async def _nothing():
    return None


async def test_first_legacy_migration_keeps_dormant_records_without_resident_catalog(app_factory):
    app = app_factory()
    identities = []
    for number in range(80):
        row = app._new_session({'title': f'Legacy {number}'})
        row['messages'] = [{'id': f'message-{number}', 'role': 'assistant', 'text': f'Saved {number}'}]
        identities.append(row['id'])
        app._state['sessions'].append(row)
    app._state['selectedSessionId'] = identities[0]
    app._state['sessions'][1]['configurationPending'] = True
    app._save()
    await app.close()
    # Reconstruct the pre-directory durable layout, not an eviction/deletion.
    import sqlite3
    with sqlite3.connect(app.data_dir / 'app.sqlite3') as db:
        complete = state_records.load(db)
        state_records.checkpoint(db, complete)
        db.execute('DELETE FROM history_directory_layout')
        db.execute('DELETE FROM history_local_rows')
    restored = app_factory(home=app.data_dir)
    assert {row['id'] for row in restored._state['sessions']} == set(identities[:2])
    assert set(restored._session_projection_refs) == set(identities[:2])
    assert set(restored.cold_display.recent) <= set(identities[:2])
    assert len(state_records.load(restored.db)['sessions']) == 80
    assert restored.directory.resolve(identities[-1])['title'] == 'Legacy 79'
    assert restored._session(identities[-1])['messages'][0]['text'] == 'Saved 79'
    assert not restored.runtime.started and not restored.runtime.sent


def test_metadata_summary_message_count_never_hydrates_cold_body():
    from amplifier_web.history_directory import summary
    class Refusing(dict):
        def get(self, key, default=None):
            if key == 'messages':
                pytest.fail('message hydration')
            return super().get(key, default)
        def __getitem__(self, key):
            if key == 'messages':
                pytest.fail('message hydration')
            return super().__getitem__(key)
    assert summary(Refusing(id='cold', _coldMessageCount=17))['displayMessageCount'] == 17
    assert summary(Refusing(id='hot', messages=[{}, {}]))['displayMessageCount'] == 2
    assert summary(Refusing(id='native', historyLoaded=False, messages=[]))['displayMessageCount'] is None
    assert summary(Refusing(id='page', sharedHistoryTotal=203))['sharedHistoryTotal'] == 203


async def test_background_gc_collects_large_dormant_catalog_off_lock(tmp_path, monkeypatch):
    """A complete large catalog proof must progress without a permanent deadline."""
    import asyncio
    import json
    import sqlite3
    from types import SimpleNamespace
    from amplifier_web import resource_files, storage_migration
    from amplifier_web.performance_diagnostics import PerformanceDiagnostics
    db = sqlite3.connect(tmp_path / 'roots.sqlite3')
    db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('CREATE TABLE state_resources(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
    state_records.initialize(db)
    retained = resource_files.put(db, {'retained': True})
    garbage = resource_files.put(db, {'candidate': True})
    db.execute('INSERT INTO state VALUES(1,?)', (json.dumps({'revision': 0, 'sessions': []}),))
    db.executemany('INSERT INTO state_records VALUES(?,?,?)', (
        ('session', f'saved-{index:05}', json.dumps({'id': f'saved-{index:05}', 'keep': retained, 'title': 'x' * 1000}))
        for index in range(27000)))
    db.commit()
    service = SimpleNamespace(db=db, _state={}, closed=False, lock=asyncio.Lock(),
                              performance_diagnostics=PerformanceDiagnostics())
    import threading
    from amplifier_web import resource_roots
    parsed = []
    owner = threading.get_ident()
    original = resource_roots.references
    def decode(snapshot):
        assert threading.get_ident() != owner and not service.lock.locked()
        result = original(snapshot)
        parsed.append(len(snapshot.records))
        return result
    monkeypatch.setattr(resource_roots, 'references', decode)
    began = time.monotonic()
    try:
        await storage_migration._maintenance_async(service)
        elapsed = time.monotonic() - began
        assert parsed == [27000]
        assert not service._resource_scan_status['blocked']
        assert not db.in_transaction and not service.lock.locked()
        assert db.execute('SELECT count(*) FROM state_records').fetchone()[0] == 27000
        assert db.execute('SELECT count(*) FROM state_resources').fetchone()[0] == 1
        assert (resource_files.root(db) / (retained['$resource'] + '.json')).is_file()
        assert not (resource_files.root(db) / (garbage['$resource'] + '.json')).exists()
        print(json.dumps({'durableRows': 27000, 'decodedRows': sum(parsed), 'elapsedMs': elapsed * 1000,
                          'operations': service.performance_diagnostics.snapshot()['operations']}))
    finally:
        if getattr(service, '_resource_scan', None):
            service._resource_scan.close()
        db.close()


@pytest.mark.parametrize('cached', [False, True])
async def test_pristine_native_owner_retention_requires_queryable_canonical_backing(app_factory, tmp_path, cached):
    from amplifier_web.native_history import NativeHistory
    from test_automatic_history import native_session
    workspace = tmp_path / 'native-owner'
    native_session(workspace, 'pristine-native')
    app = app_factory()
    index = NativeHistory(amplifier_home(), cache_path=app.data_dir / 'native-catalog.sqlite3')
    index.scan()
    owner = app._session('pristine-native', hydrate=False)
    selected = app._new_session({'title': 'Keep active'})
    app._state['sessions'].append(selected)
    app._state['selectedSessionId'] = selected['id']
    if not cached:
        with index._catalog.connect() as db:
            db.execute('DELETE FROM native_rows')
    app._save()
    saved, _ = state_records.load_owner(app.db, owner['id'])
    stored = app.db.execute('SELECT id FROM history_local_rows WHERE id=?', (owner['id'],)).fetchone()
    assert (saved is None) == cached
    assert (stored is None) == cached
    app._state['sessions'].remove(owner)  # Deliberate working-set eviction only.
    app.projections.values.pop(('session-index',), None)
    app._session_projection_refs.pop(owner['id'], None)
    metadata = app.directory.resolve(owner['id'])
    assert metadata is not None
    admitted = app._admit_session(metadata)
    assert admitted['id'] == owner['id'] and admitted['historyLoaded'] is False
    assert not app.runtime.started and not app.runtime.sent
    index.close()


async def test_verified_legacy_name_observation_cannot_override_newer_intervening_rename(app_factory, tmp_path, monkeypatch):
    import asyncio
    import json
    import threading
    from amplifier_web.history_directory import HistoryDirectory
    from test_automatic_history import native_session
    path = native_session(tmp_path / 'naming-race', 'naming-race', metadata={'name': 'First canonical'})
    app = app_factory()
    await app.history.refresh()
    app._session('naming-race', hydrate=False)
    await app.dispatch('session.rename', {'id': 'naming-race', 'title': 'First web rename'}, include_state=False)
    raw = json.loads((path / 'metadata.json').read_text())
    raw.update(name='Legacy external title', name_revision=0, name_policy_revision=0)
    (path / 'metadata.json').write_text(json.dumps(raw))
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    verify = HistoryDirectory.verify_naming_conflicts
    def blocked(pending):
        result = verify(pending)
        assert result
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return result
    monkeypatch.setattr(HistoryDirectory, 'verify_naming_conflicts', staticmethod(blocked))
    task = asyncio.create_task(app.history.refresh())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await app.dispatch('session.rename', {'id': 'naming-race', 'title': 'Newest user rename'}, include_state=False)
        release.set()
        await asyncio.wait_for(task, 5)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert app._session('naming-race', hydrate=False)['title'] == 'Newest user rename'
    assert app.directory.resolve('naming-race')['title'] == 'Newest user rename'
    assert json.loads((path / 'metadata.json').read_text())['name'] == 'Newest user rename'
    assert not app.runtime.started and not app.runtime.sent


async def test_agent_dispatch_does_not_collapse_ambiguous_dormant_native_alias(app_factory, tmp_path, monkeypatch):
    from amplifier_web.service import AppError
    app = app_factory()
    catalog = NativeCatalog(app.data_dir / 'native-catalog.sqlite3', amplifier_home())
    seed(catalog, tmp_path / 'one-project', 1)
    seed(catalog, tmp_path / 'other-project', 1)
    app.directory.bind(catalog)
    owner = app._session(catalog.lookup('saved-00000')[0]['id'], hydrate=False)
    assert len(app._state['sessions']) == 1
    async def rejected_dispatch(*args, **kwargs):
        pytest.fail('An ambiguous native alias reached action dispatch')
    monkeypatch.setattr(app, 'dispatch', rejected_dispatch)
    with pytest.raises(AppError, match='more than one project'):
        await app.app_bridge('dispatch', {'action': 'session.rename',
            'args': {'id': 'saved-00000', 'title': 'Wrongly authorized rename'}}, owner['id'])
    assert len(app._state['sessions']) == 1
    assert not app.runtime.started and not app.runtime.sent
