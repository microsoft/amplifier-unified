from copy import deepcopy
import pytest
from amplifier_web.adaptive_work import work_home
from amplifier_web.chat_navigation import view_patch
from amplifier_web.shell_resource_diagnostics import rebuild_reasons
from test_shell_modules import service
from test_chat_navigation import chat


def test_home_query_rejects_invalid_inputs():
    for patch in ({'workHomeView': 'board'}, {'workHomeScope': None},
                  {'workHomeQuery': 'x'*501}, {'workHomePages': {'idle': True}},
                  {'workHomePages': {'all': 2}}, {'workHomeFilter': 'done'}):
        with pytest.raises(ValueError):
            view_patch(patch)
    view_patch({'workSurface': 'home', 'workHomeView': 'studio', 'workHomeScope': 'all',
                'workHomePages': {'working': 2}})


async def test_scope_search_and_unavailable_project_never_widen(service, monkeypatch):
    before = deepcopy(service.state)
    monkeypatch.setattr(service, '_session', lambda *a, **k: pytest.fail('Browsing hydrated a session'))
    for scope, query, expected in [('all', '', {'alpha', 'beta'}), ('two', '', {'beta'}),
                                  ('two', 'alpha', set()), ('missing', '', set())]:
        result = work_home(service, {**service.state, 'view': {'workHomeScope': scope, 'workHomeQuery': query}})
        assert {row['id'] for row in result['items']} == expected
        assert result['unavailable'] == (scope == 'missing')
    assert service.state == before


async def test_independent_status_pages_are_bounded_and_idle_is_not_completion(service):
    base=service.state['sessions'][0]
    service.state['sessions']=[{**deepcopy(base), 'id': f'item-{i}', 'title': f'Work {i}', 'recentActivityAt': i+1} for i in range(125)]
    first=work_home(service, service.state)
    page=first['groups']['idle']
    assert page['total']==125 and len(page['items'])<=50 and page['pages']>1
    assert first['groups']['unread']['total']==0
    second=work_home(service, {**service.state, 'view': {'workHomePages': {'idle': 1}}})
    assert not {row['id'] for row in page['items']} & {row['id'] for row in second['groups']['idle']['items']}


async def test_home_resource_key_survives_diagnostics_and_query_change(service):
    shell=service.shell; client=shell.client('home-reader'); instance={'id':'chats','package':'builtin.chats','slot':'navigation'}
    state=shell.scoped_state(client,instance)
    one=shell.resources._page_key('workHome',state,instance,((1,2),3),client,7,(1,2,3,4,5,6))
    state['view']['workHomeQuery']='new query'
    two=shell.resources._page_key('workHome',state,instance,((1,2),3),client,7,(1,2,3,4,5,6))
    reasons,_=rebuild_reasons(('bootstrap',one),('bootstrap',two))
    assert reasons==('query',)


async def test_pinned_home_request_identity_and_unavailable_scope_never_widen(service, monkeypatch):
    monkeypatch.setattr(service, '_session', lambda *a, **k: pytest.fail('Browsing hydrated a session'))
    client = service.shell.client('pinned-home')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation',
                'scope': {'mode': 'pinned', 'workspaceId': 'two'}}
    for requested in ('all', 'managed', 'one'):
        service.state['view'].update(workSurface='home', workHomeScope=requested)
        snapshot = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))
        page = snapshot['workHome']
        assert snapshot['workHomePinnedScope'] == page['scope'] == 'two'
        assert page['requestedScope'] == requested
        assert {row['id'] for row in page['items']} == {'beta'}
    keys = []
    for workspace_id, expected in [('one', {'alpha'}), ('two', {'beta'}), ('missing', set())]:
        instance['scope']['workspaceId'] = workspace_id
        page = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
        assert page['scope'] == workspace_id
        assert page['unavailable'] == (workspace_id == 'missing')
        assert {row['id'] for row in page['items']} == expected
        scoped = service.shell.scoped_state(client, instance)
        keys.append(service.shell.resources._page_key('workHome', scoped, instance, ((1, 2), 3), client, 7, (1, 2, 3, 4, 5, 6)))
    assert len(set(keys)) == 3, 'Pinned project changes cannot reuse another project page'
    instance['scope']['workspaceId'] = 'two'
    next(row for row in service.state['workspaces'] if row['id'] == 'two')['available'] = False
    page = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
    assert page['scope'] == 'two' and page['unavailable'] and not page['items']


async def test_current_home_project_context_does_not_depend_on_result_pages(service, monkeypatch):
    monkeypatch.setattr(service.projections, 'chats', lambda *a, **k: pytest.fail('Context queried chat rows'))
    monkeypatch.setattr(service, '_session', lambda *a, **k: pytest.fail('Context hydrated a session'))
    client = service.shell.client('home-context')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'}
    service.state['view'].update(workHomeScope='two', workHomeQuery='pending-search')
    def context():
        snapshot = service.shell.navigation(client, instance, navigation_resources=frozenset())
        assert 'workHome' not in snapshot
        return snapshot['workHomeContext']
    expected = context()
    assert expected['scope'] == expected['requestedScope'] == 'two'
    assert expected['workspace']['id'] == 'two'
    service.state['view'].update(workHomeQuery='another-search', workHomePages={'idle': 2})
    assert context() == expected
    row = next(row for row in service.state['workspaces'] if row['id'] == 'two')
    row['path'] = '/new/current/path'
    assert context()['workspace']['path'] == row['path']
    row['available'] = False
    assert context()['unavailable'] and context()['workspace'] is None
    instance['scope'] = {'mode': 'pinned', 'workspaceId': 'one'}
    assert context()['scope'] == 'one' and context()['requestedScope'] == 'two'
    instance['scope']['workspaceId'] = 'missing'
    assert context()['unavailable'] and context()['workspace'] is None


async def test_new_web_arrival_and_resume_preserve_chat_and_private_draft(service):
    service.state['view']['workHomeView']='studio'
    record=service.clients.attach('fresh-home',kind='web')
    assert record['view']['workSurface']=='home'
    assert 'workHomeView' not in record['view']
    with service.clients.bind('fresh-home'):
        await service.dispatch('view.update', {'patch': {'workSurface':'chat','workHomeView':'focus','draft':'Keep this draft'}})
    selected=record['selectedSessionId']
    resumed=service.clients.attach('resumed-home',resume='fresh-home',kind='web')
    assert resumed['view']['workSurface']=='chat'
    assert resumed['view']['workHomeView']=='focus'
    assert resumed['view']['draft']=='Keep this draft'
    assert resumed['selectedSessionId']==selected


async def test_home_choices_survive_navigation_and_client_resume_without_leaking(service):
    choices = {'workHomeView': 'studio', 'workHomeFilter': 'idle',
               'workHomeScope': 'two', 'workHomeQuery': 'beta',
               'workHomePages': {'idle': 2, 'unread': 1}, 'navPinned': True}
    service.clients.attach('home-continuity', kind='web')
    with service.clients.bind('home-continuity'):
        await service.dispatch('view.update', {'patch': choices})
        for surface in ('chat', 'workspace', 'workspaces', 'chats', 'home'):
            await service.dispatch('view.update', {'patch': {'workSurface': surface}})
            assert all(service.state['view'][key] == value for key, value in choices.items())
    resumed = service.clients.attach('home-resumed', resume='home-continuity', kind='web')
    assert resumed['view']['workSurface'] == 'home'
    assert all(resumed['view'][key] == value for key, value in choices.items())
    independent = service.clients.attach('home-independent', kind='web')
    assert 'workHomeQuery' not in independent['view']
    assert 'workHomePages' not in independent['view']
