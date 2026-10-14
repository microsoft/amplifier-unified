"""Browsing history retains a working set without losing owners or aliases."""
import copy
import gc

import pytest

from amplifier_web.history_residency import RECENT
from amplifier_web.state_records import load, load_owner
from test_automatic_history import app_factory, native_session


@pytest.fixture
def no_cyclic_collection():
    """Retirement must not wait for an unrelated allocation to collect UI cycles."""
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


async def saved_directory(app_factory, count=32):
    app = app_factory()
    identities = []
    for number in range(count):
        row = app._new_session({'title': f'Saved owner {number}'})
        row['messages'] = [{'id': f'message-{number}', 'role': 'assistant', 'text': f'Exact saved {number}'}]
        app._state['sessions'].append(row)
        identities.append(row['id'])
    del row
    app._state['selectedSessionId'] = identities[0]
    app._save()
    await app.close()
    return app_factory(home=app.data_dir), identities


def browse(app, identity):
    app._session(identity, hydrate=False)
    app._state['selectedSessionId'] = identity
    app.cold_display.touch(identity)
    app._publish()


@pytest.mark.parametrize('native', [False, True, 'unresolved'])
async def test_sequential_browsing_bounds_resident_rows_references_and_view_cache(app_factory, tmp_path, native, no_cyclic_collection):
    if native:
        from amplifier_web.native_history import NativeHistory
        from amplifier_web.session_files import amplifier_home
        workspace = tmp_path / 'native-folder'
        identities = [f'native-{number:03d}' for number in range(32)]
        for identity in identities:
            folder = native_session(workspace, identity,
                                    metadata={'working_dir': None} if native == 'unresolved' else None)
            if native == 'unresolved':
                (folder / 'context-intelligence/metadata.json').write_text('{}')
        app = app_factory()
        index = NativeHistory(amplifier_home(), cache_path=app.data_dir / 'native-catalog.sqlite3')
        index.scan(); index.close()
    else:
        app, identities = await saved_directory(app_factory)
    for identity in identities:
        browse(app, identity)
        assert len(app._state['sessions']) <= RECENT
        assert len(app._session_projection_refs) <= RECENT
        assert len(app._view_cache) <= RECENT
    assert {row['id'] for row in app._state['sessions']} == set(identities[-RECENT:])
    assert all(app.directory.resolve(identity) is not None for identity in identities)
    browse(app, identities[0])
    assert len(app._state['sessions']) <= RECENT
    if not native:
        assert app._session(identities[0])['messages'][0]['text'] == 'Exact saved 0'
        assert len(load(app.db)['sessions']) == len(identities)
    assert not app.runtime.started and not app.runtime.sent


async def test_borrowed_rows_nested_lists_runtime_aliases_and_active_pending_owners_stay_resident(app_factory):
    app, identities = await saved_directory(app_factory)
    borrowed_row = app._session(identities[1])
    borrowed_messages = app._session(identities[2])['messages']
    app._session(identities[3], hydrate=False)
    borrowed_runtime = {'operation': {'receipt': 'original'}}
    app._state.setdefault('runtimeControl', {})[identities[3]] = borrowed_runtime
    app._session(identities[4], hydrate=False)['status'] = 'working'
    app._session(identities[5], hydrate=False)['configurationPending'] = {'revision': 2}
    for identity in identities[6:]:
        browse(app, identity)
    resident = {row['id'] for row in app._state['sessions']}
    assert set(identities[1:6]) <= resident
    assert len(resident) <= RECENT + 5
    borrowed_row['title'] = 'Changed through retained owner alias'
    borrowed_messages.append({'id': 'borrowed-write', 'role': 'user', 'text': 'Keep this later mutation'})
    borrowed_runtime['operation']['receipt'] = 'changed'
    app._publish()
    assert app._session(identities[1]) is borrowed_row
    assert app._session(identities[2])['messages'] is borrowed_messages
    assert load_owner(app.db, identities[3])[1]['operation']['receipt'] == 'changed'
    del borrowed_row, borrowed_messages, borrowed_runtime
    for identity in identities[6:]:
        browse(app, identity)
    resident = {row['id'] for row in app._state['sessions']}
    assert identities[4] in resident and identities[5] in resident
    assert not resident.intersection(identities[1:4])
    assert app._session(identities[1])['title'] == 'Changed through retained owner alias'
    assert app._session(identities[2])['messages'][-1]['text'] == 'Keep this later mutation'
    assert app._state['runtimeControl'].get(identities[3]) is None
    assert load_owner(app.db, identities[3])[1]['operation']['receipt'] == 'changed'


async def test_connected_selection_stays_hot_then_reconnect_restores_same_dormant_owner_and_draft(app_factory):
    app, identities = await saved_directory(app_factory)
    target = identities[1]
    app._session(target, hydrate=False)
    app.clients.attach('other-window')
    with app.clients.bind('other-window'):
        app.clients.records['other-window']['selectedSessionId'] = target
        app.clients.draft(target, 'Unsent text survives residency retirement')
        queue = app.subscribe()
    for identity in identities[2:]:
        browse(app, identity)
    assert target in {row['id'] for row in app._state['sessions']}
    before = copy.deepcopy(app.clients.records['other-window'])
    app.unsubscribe(queue)
    for identity in identities[2:]:
        browse(app, identity)
    assert target not in {row['id'] for row in app._state['sessions']}
    assert app.clients.records['other-window']['selectedSessionId'] == target
    assert app.clients.records['other-window']['drafts'] == before['drafts']
    app.clients.attach('reconnected', resume='other-window')
    with app.clients.bind('reconnected'):
        assert app._session()['id'] == target
        assert app.clients.record()['drafts'][target] == 'Unsent text survives residency retirement'
    assert not app.runtime.started and not app.runtime.sent


def test_one_wide_owner_cannot_exhaust_retirement_budget_before_rechecking(monkeypatch):
    from amplifier_web import history_residency

    marker = 'Unusually wide saved display payload'
    owner = {'messages': [marker] * 20000}
    examined = 0
    real_type = type

    def measured_type(value):
        nonlocal examined
        if value is marker:
            examined += 1
        return real_type(value)

    # Charge work as it is examined, avoiding wall-clock or machine-speed
    # assertions. A deadline checked only between containers misses this case.
    monkeypatch.setattr(history_residency, 'type', measured_type, raising=False)
    monkeypatch.setattr(history_residency.time, 'monotonic', lambda: examined / 1000)
    assert not history_residency.exclusive(owner)
    # Deadlines may be amortized over a small batch; they must not wait for
    # the entire 20,000-element container to finish.
    assert examined <= 128
    assert len(owner['messages']) == 20000
