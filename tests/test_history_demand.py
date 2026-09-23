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
