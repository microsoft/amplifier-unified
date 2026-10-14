"""Retained reconnect state must not keep disconnected history readers busy."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.event_log_view import EventLogView
from amplifier_web.history_demand import event_sessions, subscribed_sessions
from amplifier_web.state_projections import StateProjections
from test_automatic_history import app_factory


def service():
    state = {'selectedSessionId': 'legacy-chat', 'sessions': []}
    return SimpleNamespace(_state=state, state=state, closed=False,
        clients=SimpleNamespace(records={}), queue_clients={}, queue_sessions={},
        projections=StateProjections())


def test_retained_views_and_drafts_are_not_live_demand():
    host = service()
    host.clients.records = {str(i): {'selectedSessionId': f'chat-{i}',
        'drafts': {f'chat-{i}': 'Keep this draft'}} for i in range(1000)}
    original = copy.deepcopy(host.clients.records)
    assert subscribed_sessions(host) == set()
    assert host.clients.records == original


def test_browser_disconnect_and_reconnect_preserve_independent_views():
    host = service()
    host.clients.records = {'browser': {'selectedSessionId': 'chat', 'drafts': {'chat': 'Unsent'}},
                            'closed': {'selectedSessionId': 'old-chat'}}
    original = copy.deepcopy(host.clients.records)
    host.queue_clients.update(first='browser', second='browser')
    assert subscribed_sessions(host) == {'chat'}
    del host.queue_clients['first']
    assert subscribed_sessions(host) == {'chat'}
    del host.queue_clients['second']
    assert subscribed_sessions(host) == set()
    host.queue_clients['reconnected'] = 'browser'
    assert subscribed_sessions(host) == {'chat'}
    assert host.clients.records == original


def test_terminal_stream_targets_override_saved_selection():
    host = service()
    host.clients.records['tui'] = {'selectedSessionId': 'old-selection'}
    host.queue_clients.update(first='tui', second='tui')
    host.queue_sessions.update(first='watched-chat', second='another-chat')
    assert subscribed_sessions(host) == {'watched-chat', 'another-chat'}
    del host.queue_clients['first']
    assert subscribed_sessions(host) == {'another-chat'}
    assert host.clients.records['tui']['selectedSessionId'] == 'old-selection'


def test_legacy_selection_requires_a_live_anonymous_stream():
    host = service()
    host.queue_clients['legacy'] = None
    assert subscribed_sessions(host) == {'legacy-chat'}
    host.queue_clients.clear()
    assert subscribed_sessions(host) == set()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['working', 'running', 'starting', 'stopping'])
async def test_event_reader_ignores_closed_clients_and_keeps_active_work(status):
    host = service()
    host.clients.records = {'browser': {'selectedSessionId': 'live-chat'},
        'closed-browser': {'selectedSessionId': 'closed-chat'},
        'finished-api': {'selectedSessionId': 'api-chat'},
        'tui': {'selectedSessionId': 'old-tui-selection'}}
    host.queue_clients.update(browser='browser', terminal='tui')
    host.queue_sessions['terminal'] = 'terminal-chat'
    host.state['sessions'] = [{'id': sid, 'status': 'idle'} for sid in
        ['legacy-chat', 'live-chat', 'closed-chat', 'api-chat', 'old-tui-selection', 'terminal-chat']]
    host.state['sessions'].append({'id': 'background-work', 'status': status})
    visited, complete = [], asyncio.Event()
    view = EventLogView(host)

    async def observe(identity):
        visited.append(identity)
        if identity == 'background-work':
            complete.set()

    view.refresh = observe
    view.start()
    try:
        await asyncio.wait_for(complete.wait(), 2)
        assert visited == ['live-chat', 'terminal-chat', 'background-work']
    finally:
        host.closed = True
        await view.close()


def test_event_selection_reuses_catalog_but_tracks_each_live_subscription():
    class Catalog(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    host = service()
    rows = Catalog({'id': f'chat-{i}', 'status': 'idle'} for i in range(23128))
    host.state['sessions'] = rows
    for i, status in enumerate(('working', 'running', 'starting', 'stopping')):
        rows[20000 + i]['status'] = status
    rows[22000]['historyLoading'] = True
    rows[22001]['configurationBusy'] = True
    host.clients.records = {'front': {'selectedSessionId': 'chat-50'},
                            'background': {'selectedSessionId': 'chat-10'},
                            'retained': {'selectedSessionId': 'chat-20'}}
    host.queue_clients.update(front='front', background='background', terminal='front')
    host.queue_sessions['terminal'] = 'chat-30'
    active = [f'chat-{i}' for i in range(20000, 20004)]
    assert event_sessions(host) == ['chat-10', 'chat-30', 'chat-50', *active]
    cold = rows.iterations
    assert cold == 1
    for _ in range(20):
        assert event_sessions(host) == ['chat-10', 'chat-30', 'chat-50', *active]
    host.clients.records['front']['selectedSessionId'] = 'chat-5'
    del host.queue_clients['background']
    host.queue_sessions['terminal'] = 'missing-chat'
    assert event_sessions(host) == ['chat-5', *active]
    host.queue_clients['reconnected'] = 'background'
    assert event_sessions(host) == ['chat-5', 'chat-10', *active]
    assert rows.iterations == cold


async def test_event_selection_observes_same_revision_saves_and_row_replacement(app_factory):
    app = app_factory()
    first = app._new_session({'title': 'First'})
    second = app._new_session({'title': 'Second'})
    first['status'], second['status'] = 'working', 'idle'
    app.state.update(sessions=[first, second], selectedSessionId=None)
    app._save()
    revision = app.state['revision']
    assert event_sessions(app) == [first['id']]
    previous_index = app.projections.sessions(app.state)
    replacement = {**second, 'status': 'starting'}
    first['status'] = 'stopped'
    app.state['sessions'] = [replacement, first]
    app._save()  # A revision number is not the invalidation boundary.
    assert app.state['revision'] == revision
    assert event_sessions(app) == [second['id']]
    assert app.projections.sessions(app.state) is not previous_index
    app.state['sessions'] = [first]
    app._state['selectedSessionId'] = second['id']  # Removed retained selection.
    app._save()
    assert event_sessions(app) == []
    await app.on_runtime_event('runtime.status', {'sessionId': first['id'],
        'status': 'starting', 'preparationProgress': True})
    assert app._progress_dirty
    assert event_sessions(app) == [first['id']]
    await app._flush_pending_progress()
    assert not app._progress_dirty
    assert event_sessions(app) == [first['id']]


def test_event_selection_falls_back_for_pending_live_mutations():
    host = service()
    host.state['sessions'] = [{'id': 'before', 'status': 'working'},
                              {'id': 'after', 'status': 'idle'}]
    assert event_sessions(host) == ['before']
    # Progress may change mutable rows before the coalesced save invalidates
    # their index; both status transitions and replaced rows must be observed.
    host.state['sessions'] = [{'id': 'after', 'status': 'starting'},
                              {'id': 'before', 'status': 'stopped'}]
    host._progress_dirty = True
    assert event_sessions(host) == ['after']
    host.projections.invalidate()
    host._progress_dirty = False
    assert event_sessions(host) == ['after']


@pytest.mark.parametrize('progress_dirty', [False, True])
def test_initial_history_deferral_does_not_hide_refresh_or_failure(progress_dirty):
    host = service()
    row = {'id': 'chat', 'status': 'idle', 'historyLoaded': False, 'historyLoading': True}
    host.state['sessions'] = [row]
    host._state['selectedSessionId'] = 'chat'
    host.queue_clients['browser'] = None
    host._progress_dirty = progress_dirty
    host.history = SimpleNamespace(loads={'chat': SimpleNamespace(locked=lambda: True)})
    assert event_sessions(host) == []
    row['historyLoaded'] = True  # A normal refresh keeps its useful current work.
    assert event_sessions(host) == ['chat']
    row.update(historyLoaded=False, historyLoading=False, historyError='Read failed')
    assert event_sessions(host) == ['chat']  # Failure cannot defer work indefinitely.


@pytest.mark.parametrize('owner,status', [('root', value) for value in ('working', 'running', 'starting', 'stopping')]
                         + [('worker', value) for value in ('running', 'working', 'starting', 'stopping', 'queued', 'pending', 'retrying', 'idle')])
def test_initial_history_deferral_preserves_active_roots_and_workers(owner, status):
    host = service()
    row = {'id': 'chat', 'status': status if owner == 'root' else 'idle',
           'historyLoaded': False, 'historyLoading': True,
           'workers': [{'id': 'child', 'status': status}] if owner == 'worker' else []}
    host.state['sessions'] = [row]
    host._state['selectedSessionId'] = 'chat'
    host.queue_clients['browser'] = None
    host.history = SimpleNamespace(loads={'chat': SimpleNamespace(locked=lambda: True)})
    assert event_sessions(host) == ['chat']


@pytest.mark.parametrize('progress_dirty', [False, True])
@pytest.mark.parametrize('worker_status,expected', [('completed', []), ('stopping', ['chat'])])
def test_initial_history_deferral_never_loads_cold_display(monkeypatch, progress_dirty, worker_status, expected):
    from amplifier_web import cold_display
    def unexpected(*args):
        raise AssertionError('Passive selection must not hydrate cold display bodies')
    monkeypatch.setattr(cold_display, 'load', unexpected)
    host = service()
    references = {key: {'$resource': key} for key in cold_display.SESSION_FIELDS}
    row = cold_display.ColdRecord({'id': 'chat', 'status': 'idle', 'historyLoaded': False,
        'historyLoading': True, 'workers': [{'id': 'child', 'status': worker_status}],
        cold_display.MARKER: references}, None)
    host.state['sessions'] = [row]
    host._state['selectedSessionId'] = 'chat'
    host.queue_clients['browser'] = None
    host._progress_dirty = progress_dirty
    host.history = SimpleNamespace(loads={'chat': SimpleNamespace(locked=lambda: True)})
    assert event_sessions(host) == expected
    assert set(dict.get(row, cold_display.MARKER)) == cold_display.SESSION_FIELDS


async def test_cancelled_initial_history_cannot_keep_projection_deferred(tmp_path, app_factory, monkeypatch):
    import threading
    from amplifier_web import automatic_history
    from test_automatic_history import native_session
    native_session(tmp_path / 'native-project', 'cancelled-root')
    app = app_factory()
    await app.history.refresh()
    row = next(row for row in app.state['sessions'] if row.get('nativeIdentity') == 'cancelled-root')
    row.update(historyLoaded=False, historyLoading=False, workers=[], status='idle')
    app.state['selectedSessionId'] = row['id']
    entered, release, finished = asyncio.Event(), threading.Event(), asyncio.Event()
    loop, original = asyncio.get_running_loop(), automatic_history.read_transcript
    def pending(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(5)
            return original(*args, **kwargs)
        finally:
            loop.call_soon_threadsafe(finished.set)
    monkeypatch.setattr(automatic_history, 'read_transcript', pending)
    loading = asyncio.create_task(app.history.load(row['id']))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert event_sessions(app) == []
        loading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loading
        assert event_sessions(app) == [row['id']]
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 5)
        await asyncio.gather(loading, return_exceptions=True)
    # The abandoned reader cannot publish. A new explicit read may own the
    # lock and complete normally, without execution or message replay.
    assert row['historyLoaded'] is False
    await app.history.load(row['id'])
    assert row['historyLoaded'] and not row['historyLoading']
    assert not app.runtime.started and not app.runtime.sent


async def test_pending_initial_read_projects_once_after_messages_and_keeps_exact_work(tmp_path, app_factory, monkeypatch):
    import threading
    from amplifier_web import automatic_history
    from test_automatic_history import native_session
    from test_event_log_view import append
    from amplifier_web.event_log_view import event_path
    directory = native_session(tmp_path / 'native-project', 'deferred-root')
    app = app_factory()
    await app.history.refresh()
    row = next(row for row in app.state['sessions'] if row.get('nativeIdentity') == 'deferred-root')
    row.update(historyLoaded=False, historyLoading=False, workers=[], status='idle')
    receipt = {'id': 'model', 'kind': 'llm', 'sessionId': row['id'], 'rootSessionId': row['id'],
               'producerId': 'owned-worker', 'revision': 1, 'turnId': 'owned-turn',
               'phase': 'completed', 'startedAt': 1, 'endedAt': 2,
               'usage': {'inputTokens': 7, 'outputTokens': 11}}
    row['execution'] = {'nodes': [], 'turns': [{'id': 'owned-turn', 'phase': 'completed'}],
                        'retiredUsageNodes': [receipt]}
    app.state['selectedSessionId'] = row['id']
    path = event_path(row, 'deferred-root')
    append(path, 'provider:request', {'session_id': 'deferred-root', 'kind': 'llm', 'id': 'model',
           'sessionId': 'deferred-root', 'provider': 'test', 'model': 'fixture',
           'startedAt': 1, 'endedAt': 2, 'phase': 'completed', 'usage': {'inputTokens': 7, 'outputTokens': 11}}, 2)
    entered, release = asyncio.Event(), threading.Event()
    loop, original = asyncio.get_running_loop(), automatic_history.read_transcript
    def pending(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(automatic_history, 'read_transcript', pending)
    loading = asyncio.create_task(app.history.load(row['id']))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert row['historyLoading'] and row['historyLoaded'] is False
        # Repeated poll selection must do no work until the canonical page is
        # admitted. An explicit read is still available to independent callers.
        for _ in range(3):
            assert event_sessions(app) == []
        expected = app.event_log_view.read(EventLogView.projection_input(row))
        assert expected['aggregateUsage']['calls'] == 1
        assert expected['aggregateUsage']['inputTokens'] == 7
    finally:
        release.set()
        await asyncio.wait_for(loading, 5)
    assert row['historyLoaded'] and not row['historyLoading'] and row['messages']
    assert event_sessions(app) == [row['id']]
    await app.event_log_view.refresh(row['id'])
    node = next(node for node in row['execution']['nodes'] if node['id'] == 'model')
    assert node['usage'] == expected['nodes'][0]['usage']
    assert node['producerId'] == 'owned-worker' and node['turnId'] == 'owned-turn'
    assert row['execution']['retiredUsageNodes'] == [receipt]
    assert row['execution']['aggregateUsage']['calls'] == 1
    await app.event_log_view.refresh(row['id'])
    counters = app.performance_diagnostics.snapshot()['counters']
    assert counters['events.projection.rebuilt'] == 1
    assert counters['events.projection.reused'] == 1
    assert not app.runtime.started and not app.runtime.sent
