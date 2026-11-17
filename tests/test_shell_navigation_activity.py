"""Retained membership must not freeze admitted owners' current activity."""
from copy import deepcopy

import pytest

from test_shell_modules import service
from test_shell_query_owner import prepare, settle, snapshot


def hold_pages(service, monkeypatch, retry_after=0):
    from amplifier_web.shell_query_owner import PendingQuery

    def held(*args, **kwargs):
        raise PendingQuery(retry_after)

    monkeypatch.setattr(service.projections, 'chats', held)


def activity_context(envelope):
    return envelope['instances']['chats']['resources']['context']['value']['navigationActivityById']


def question(**fields):
    return {'id': 'question-alpha', 'prompt': 'Choose the fixture route', 'revision': 1,
            'status': 'pending', 'required': True, **fields}


@pytest.mark.parametrize('retry_after', [0, 1000])
async def test_selection_switch_keeps_reading_owner_activity_not_old_page(service, monkeypatch, retry_after):
    alpha = service.state['sessions'][0]
    alpha.update(status='working', questions=[question()])
    prepare(service)
    initial = await settle(service)
    frozen = deepcopy(initial)
    assert activity_context(initial)['alpha']['label'] == 'Answer requested'
    hold_pages(service, monkeypatch, retry_after)
    alpha['questions'][0].update(status='answered', delivery={'status': 'accepted'})
    service._publish_changes(sessions={'alpha'})
    service.clients.records['browser']['selectedSessionId'] = 'beta'
    current = snapshot(service, demand=2)
    entry = current['instances']['chats']
    assert entry['pending']['recentNavigation']['status'] == ('retry' if retry_after else 'pending')
    assert 'recentNavigation' not in entry['resources']
    assert activity_context(current) == {
        'alpha': {'kind': 'working', 'label': 'Working'},
        'beta': {'kind': 'idle', 'label': 'Idle'}}
    assert entry['resources']['context']['value']['selectedSessionId'] == 'beta'
    # Further pending transitions retain the same IDs, not the original facts.
    service.clients.records['browser']['selectedSessionId'] = None
    alpha['status'] = 'stopped'
    service._publish_changes(sessions={'alpha'})
    later = snapshot(service, demand=3)
    assert activity_context(later)['alpha']['label'] == 'Stopped'
    assert 'recentNavigation' not in later['instances']['chats']['resources']
    assert initial == frozen


async def test_selected_admitted_owner_has_activity_before_any_page(service, monkeypatch):
    prepare(service)
    hold_pages(service, monkeypatch)
    service.state['sessions'][0]['status'] = 'working'
    service._publish_changes(sessions={'alpha'})
    value = snapshot(service)
    assert 'recentNavigation' not in value['instances']['chats']['resources']
    assert activity_context(value) == {'alpha': {'kind': 'working', 'label': 'Working'}}


@pytest.mark.parametrize('fence', [
    'bootstrap', 'host', 'client', 'generation', 'age', 'agent-filter', 'released',
])
async def test_reading_owner_ids_do_not_cross_retention_fences(service, monkeypatch, fence):
    prepare(service)
    await settle(service)
    hold_pages(service, monkeypatch)
    service.clients.records['browser']['selectedSessionId'] = 'beta'
    owner = service.shell.resources
    if fence in {'bootstrap', 'generation', 'agent-filter'}:
        client = service.shell.client('browser')
        if fence == 'bootstrap':
            client['revision'] += 1
        elif fence == 'generation':
            client['views'].setdefault('chats', {})['generation'] = 1
        else:
            client['baselineView']['navShowAgentCreated'] = True
        service.shell.put('client', 'browser', client)
    elif fence == 'host':
        service.instance_id += '-new-host'
    elif fence == 'age':
        service.clients.records['browser']['view']['navAge'] = '7d'
    elif fence == 'released':
        snapshot(service, wanted=(), demand=2)
    elif fence == 'client':
        service.clients.attach('other').update(selectedSessionId='beta', selectedWorkspaceId='two')
    identity = 'other' if fence == 'client' else 'browser'
    value = snapshot(service, identity=identity, demand=3)
    assert 'alpha' not in activity_context(value)
    assert activity_context(value)['beta']['kind'] == 'idle'
    assert 'recentNavigation' not in value['instances']['chats']['resources']
    assert all('reading' not in entry for entry in value['instances'].values())
    assert owner.total_bytes >= owner.clients[identity]['bytes'] > 0


@pytest.mark.parametrize('change', ['same-workspace', 'workspace', 'pins-page'])
async def test_pinned_reading_ids_follow_effective_workspace_and_pin_page(service, monkeypatch, change):
    prepare(service)
    # Both roots start in the pinned workspace, then selection moves away.
    service.state['sessions'][1]['workspaceId'] = 'one'
    service.state['pinnedSessionIds'] = ['alpha', 'beta']
    service._save_changes(sessions={'beta'}, globals={'pinnedSessionIds'})
    client = service.shell.client('browser')
    for instance in client['composition']['instances']:
        if instance['id'] == 'chats':
            instance['scope'] = {'mode': 'pinned', 'workspaceId': 'one'}
    service.shell.put('client', 'browser', client)
    await settle(service, wanted=('pinnedNavigation',))
    from amplifier_web.shell_query_owner import PendingQuery
    def held(*args, **kwargs):
        raise PendingQuery()
    monkeypatch.setattr(service.projections, 'pinned_navigation', held)
    service.clients.records['browser'].update(selectedSessionId='beta', selectedWorkspaceId='two')
    if change == 'workspace':
        # Keep bootstrap stable to exercise the effective-workspace fence itself.
        monkeypatch.setattr(service.shell, 'scoped_state', lambda client, instance:
            {**service.state, 'selectedWorkspaceId': 'two', 'view': service.state['view']})
    elif change == 'pins-page':
        client['baselineView']['navPinnedPage'] = 1
        service.shell.put('client', 'browser', client)
    value = snapshot(service, wanted=('pinnedNavigation',), demand=2)
    assert 'pinnedNavigation' not in value['instances']['chats']['resources']
    assert ('alpha' in activity_context(value)) == (change == 'same-workspace')


async def test_completed_page_replaces_reading_ids_and_never_accumulates(service, monkeypatch):
    prepare(service)
    await settle(service)
    service.clients.records['browser']['selectedSessionId'] = 'beta'
    original = service.shell.navigation
    def replacement(client, instance, **kwargs):
        value = original(client, instance, **kwargs)
        if kwargs.get('_page_only'):
            value['recentNavigation']['items'] = [row for row in value['recentNavigation']['items']
                                                  if row['id'] == 'beta']
            value['recentShortcuts'] = value['recentNavigation']['items']
        return value
    monkeypatch.setattr(service.shell, 'navigation', replacement)
    await settle(service, demand=2)
    hold_pages(service, monkeypatch)
    service.clients.records['browser']['selectedSessionId'] = None
    value = snapshot(service, demand=3)
    assert set(activity_context(value)) == {'beta'}
    reading = service.shell.resources.clients['browser']['instances']['chats']['reading']
    assert reading['recentNavigation']['ids'] == ('beta',)


async def test_held_page_activity_work_is_bounded_by_reading_ids(service, monkeypatch):
    from amplifier_web import cold_display, navigation_summary
    from amplifier_web.cold_display import ColdRecord
    prepare(service)
    await settle(service)
    hold_pages(service, monkeypatch)
    service.clients.records['browser']['selectedSessionId'] = 'beta'
    for number in range(200):
        # Unrelated admitted cold histories must not add activity work or reads.
        service.state['sessions'].append(ColdRecord({
            'id': f'unrelated-{number}', 'status': 'idle', 'workspace': '/fixture',
            'workspaceId': 'one', 'title': 'Unrelated history',
            'createdAt': 1, 'recentActivityAt': 1, 'navigationActivityAt': 1,
            '_coldFields': {name: {'$resource': 'unread'} for name in cold_display.SESSION_FIELDS}},
            service.db))
    service.projections.values.pop(('session-index',), None)
    # Establish the ordinary directory metadata before measuring the changed
    # context path, not initial admitted-directory reconciliation work.
    service.directory.observation_revision
    calls = []
    in_context = False
    navigation = service.shell.navigation
    def context_navigation(*args, **kwargs):
        nonlocal in_context
        in_context = kwargs.get('context_visible_ids') is not None
        try:
            return navigation(*args, **kwargs)
        finally:
            in_context = False
    activity = navigation_summary.activity
    def counted(row, *args, **kwargs):
        if in_context:
            calls.append(row['id'])
        return activity(row, *args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError('Held-page context hydrated/admitted a history')
    monkeypatch.setattr(navigation_summary, 'activity', counted)
    monkeypatch.setattr(service.shell, 'navigation', context_navigation)
    monkeypatch.setattr(cold_display, 'load', forbidden)
    monkeypatch.setattr(service, '_session', forbidden)
    try:
        value = snapshot(service, demand=2)
        # Default composition has two interested instances, each with exactly two
        # reading owners; 200 unrelated histories do not become activity inputs.
        assert sorted(calls) == ['alpha', 'alpha', 'beta', 'beta']
        calls.clear()
        assert snapshot(service, demand=2) == value
        # Pending queries revisit context keys, but reuse unchanged activity.
        assert calls == []
    finally:
        service.state['sessions'] = service.state['sessions'][:2]


@pytest.mark.parametrize('questions,label', [
    ([{'id': 'one', 'status': 'pending', 'required': True}], 'Answer requested'),
    ([{'id': 'one', 'status': 'answered', 'delivery': {'status': 'unknown'}}], 'Check answer delivery'),
    ([{'id': 'one', 'status': 'answered', 'delivery': {'status': 'rejected'}}], 'Check answer delivery'),
    ([{'id': 'one', 'status': 'answered', 'delivery': {'status': 'accepted'}},
      {'id': 'two', 'status': 'pending', 'required': True}], 'Answer requested'),
])
async def test_retained_owner_preserves_question_priority(service, monkeypatch, questions, label):
    prepare(service)
    await settle(service)
    hold_pages(service, monkeypatch)
    row = service.state['sessions'][0]
    questions = [question(**fields) for fields in questions]
    row.update(status='working', questions=deepcopy(questions))
    service._publish_changes(sessions={'alpha'})
    service.clients.records['browser']['selectedSessionId'] = 'beta'
    value = snapshot(service, demand=2)
    assert activity_context(value)['alpha'] == {'kind': 'attention', 'label': label}
    assert row['questions'] == questions  # Presentation acknowledges nothing.


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
    ({'status': 'idle'}, False, False, True, 'idle'),
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
