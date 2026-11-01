"""Retained membership must not freeze admitted owners' current activity."""
from copy import deepcopy

import pytest

from test_shell_modules import service
from test_shell_query_owner import prepare, settle, snapshot


async def test_pending_page_keeps_membership_but_updates_live_activity(service, monkeypatch):
    from amplifier_web.shell_query_owner import PendingQuery

    prepare(service)
    initial = await settle(service)
    previous = initial['instances']['chats']['resources']['recentNavigation']
    before = deepcopy(initial)
    assert initial['instances']['chats']['resources']['context']['value']['navigationActivityById']['alpha']['kind'] == 'idle'

    def held(*args, **kwargs):
        raise PendingQuery()

    monkeypatch.setattr(service.projections, 'chats', held)
    service._session('alpha', hydrate=False)['status'] = 'working'
    service._publish_changes(sessions={'alpha'})
    current = snapshot(service)['instances']['chats']
    assert current['pending']['recentNavigation']['status'] == 'pending'
    assert current['resources']['recentNavigation'] == previous
    assert current['resources']['recentNavigation']['value'] is previous['value']
    activity = current['resources']['context']['value']['navigationActivityById']
    assert activity['alpha'] == {'kind': 'working', 'label': 'Working'}
    assert activity['beta'] == {'kind': 'idle', 'label': 'Idle'}
    assert initial == before

    # Progress whose public activity is unchanged neither rebuilds this context
    # nor claims that the held page has completed.
    context = current['resources']['context']
    service._session('alpha', hydrate=False)['streaming'] = 'private token progress'
    service._publish_changes(sessions={'alpha'}, detail_only=True)
    later = snapshot(service)['instances']['chats']
    assert later['resources']['context'] == context
    assert later['resources']['context']['value'] is context['value']
    assert later['pending']['recentNavigation']['status'] == 'pending'


@pytest.mark.parametrize('fields,unread,reviewed,blocked,expected', [
    ({'status': 'working'}, False, False, False, 'working'),
    ({'status': 'idle', 'approvals': [{'id': 'approval-alpha', 'title': 'Allow fixture tool',
                                     'status': 'pending'}]}, False, False, False, 'attention'),
    ({'status': 'idle', 'error': 'private error body'}, False, True, False, 'idle'),
    ({'status': 'idle'}, True, False, False, 'unread'),
    ({'status': 'idle'}, False, False, True, 'attention'),
])
async def test_activity_context_uses_only_visible_admitted_metadata(
        service, monkeypatch, fields, unread, reviewed, blocked, expected):
    from amplifier_web import cold_display
    from amplifier_web.cold_display import ColdRecord

    original = service.state['sessions'][0]
    row = {key: value for key, value in dict.items(original)
           if key not in cold_display.SESSION_FIELDS}
    row.update(fields, _coldFields={name: {'$resource': 'unread'}
                                  for name in cold_display.SESSION_FIELDS})
    service.state['sessions'][0] = ColdRecord(row, service.db)
    service.projections.values.pop(('session-index',), None)
    if blocked:
        service.state.setdefault('runtimeControl', {})['alpha'] = {'task.get': {'task': {'status': 'blocked'}}}
    attention = {'sessions': {'alpha': int(unread)}, 'items': [
        {'id': 'session:alpha', 'sessionId': 'alpha', 'read': reviewed, 'fingerprint': 'owned'}]}
    attention_calls = []

    def scoped_attention(state, *, session_ids):
        assert state is service.state
        assert session_ids == (), 'Context must not request the whole attention inbox'
        attention_calls.append(session_ids)
        return attention

    monkeypatch.setattr(service.projections, 'attention', scoped_attention)

    def forbidden(*args, **kwargs):
        raise AssertionError('Activity context must not read cold bodies or admit dormant owners')

    monkeypatch.setattr(cold_display, 'load', forbidden)
    monkeypatch.setattr(service, '_session', forbidden)
    for name in ('chats', 'workspaces', 'pinned_navigation', 'workspace_shortcuts'):
        monkeypatch.setattr(service.projections, name, forbidden)
    client = service.shell.client('activity-context')
    result = service.shell.navigation(client,
        {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'},
        identity='activity-context', navigation_resources=frozenset(),
        context_visible_ids={'alpha', 'dormant-not-admitted'})
    assert attention_calls == [()]
    assert set(result['navigationActivityById']) == {'alpha'}
    assert result['navigationActivityById']['alpha']['kind'] == expected
    assert set(result['navigationActivityById']['alpha']) == {'kind', 'label'}
    assert not dict.__contains__(service.state['sessions'][0], 'messages')
