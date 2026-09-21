"""Retained reconnect state must not keep disconnected history readers busy."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.event_log_view import EventLogView
from amplifier_web.history_demand import subscribed_sessions


def service():
    state = {'selectedSessionId': 'legacy-chat', 'sessions': []}
    return SimpleNamespace(_state=state, state=state, closed=False,
        clients=SimpleNamespace(records={}), queue_clients={}, queue_sessions={})


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
