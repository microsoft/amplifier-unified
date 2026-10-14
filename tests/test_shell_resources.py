"""Bundled resource demand owns bounded immutable projections, not execution."""
from contextvars import ContextVar
from copy import deepcopy
from types import SimpleNamespace

import pytest

from amplifier_web import shell_resources as resources
from amplifier_web.service import AppError
from amplifier_web.shell_navigation_demand import NAVIGATION_RESOURCES


class Shell:
    def __init__(self):
        self.service = SimpleNamespace(instance_id='host',
            clients=SimpleNamespace(current=ContextVar('resource_client', default='one'),
                                    records={'one': {'kind': 'web'}}),
            state={'revision': 1, 'selectedSessionId': 'chat', 'selectedWorkspaceId': 'w', 'view': {}},
            directory=SimpleNamespace(observation_revision={'navigation':1,'attention':1,'workspace':1}), queue_clients={}, queue_sessions={})
        self.saved = {'composition': {'instances': [{'id':'nav','package':'builtin.chats','slot':'navigation'}],
                                      'presentation': {}}, 'preview': None, 'views': {}, 'baselineView': {}}
        self.recent_limits, self.calls = {}, []
        self.payload = 'bounded'

    def client(self, identity):
        return self.saved

    def scoped_state(self, client, instance):
        return {**self.service.state, 'view': {**client['baselineView'], **client['views'].get(instance['id'], {}).get('view', {})}}

    def recent_key(self, client, instance):
        return (self.service.clients.current.get(), instance['id'])

    def navigation(self, client, instance, *, identity, navigation_resources, context_visible_ids=()):
        self.calls.append(frozenset(navigation_resources))
        result = {'view': self.scoped_state(client, instance)['view'], 'viewRevision': client['views'].get(instance['id'], {}).get('viewRevision', 0),
                  'selectedSessionId': self.service.state['selectedSessionId'],
                  'sidebarNavigation': {'recentView': {}},
                  'attention': {'sessions': {key: 1 for key in context_visible_ids}}, 'payload': self.payload}
        for name in navigation_resources:
            page = {'items': [{'id': name, 'title': self.payload}], 'scope': {}}
            if name == 'pinnedNavigation': page['items'] = [{'kind':'session','row':{'id':name}}]
            if name.startswith('sidebar'): result['sidebarNavigation'][name[7:].lower()] = page
            else: result[name] = page
            if name == 'recentNavigation': result.update(recentShortcuts=page['items'], recentScope={})
            if name == 'workspaceShortcuts': result['unpinnedWorkspaceShortcuts'] = []
        return result

    def manifest(self, package):
        return {}

    def snapshot(self, client, instance, *, identity):
        self.calls.append('custom')
        return {'revision':self.service.state['revision'], 'custom':True}


@pytest.fixture
def owner():
    return resources.ShellResources(Shell())


def register(owner, wanted=('recentNavigation',), demand=1):
    owner.register('one', demand, wanted, resources.scope_for(owner.shell.service.state))


def test_exact_bound_client_monotonic_demand_and_scope(owner):
    register(owner)
    register(owner)
    for bad in (0, -1, True, 1.2, 2**53, '1'):
        with pytest.raises(AppError): register(owner, demand=bad)
    with pytest.raises(AppError): register(owner, ('sidebarSearch',))
    with pytest.raises(AppError): register(owner, ('unknown',), 2)
    with pytest.raises(AppError): owner.register('one',2,[],['wrong',None,'chat',None,None])
    owner.shell.service.clients.current.set(None)
    with pytest.raises(AppError): owner.snapshot('one')
    owner.shell.service.clients.current.set('one')
    register(owner, (), 2)
    with pytest.raises(AppError): register(owner, demand=1)
    assert owner.snapshot('one')['instances']['nav']['resources'].keys() == {'context'}


def test_quiet_progress_reuses_pages_and_context_without_catalog_or_payload_work(owner):
    register(owner, ('recentNavigation','sidebarSearch'))
    first = owner.snapshot('one'); old = deepcopy(first)
    calls = list(owner.shell.calls)
    owner.shell.service.state.update(revision=2, execution={'text':'unrelated progress'})
    assert owner.snapshot('one') is first
    assert owner.shell.calls == calls
    assert first == old
    owner.shell.saved['views']['nav'] = {'view': {'navRecentView': {'navFilter':'new'}}}
    second = owner.snapshot('one')
    assert owner.shell.calls[len(calls):] == [frozenset({'sidebarSearch'}),frozenset()]
    assert first == old
    assert second['instances']['nav']['resources']['recentNavigation']['value'] is first['instances']['nav']['resources']['recentNavigation']['value']
    assert set(second['instances']['nav']['resources']['context']['value']['attention']['sessions']) == {'recentNavigation','sidebarSearch'}


def test_scope_mismatch_does_no_projection_until_exact_new_demand(owner):
    register(owner);before=owner.snapshot('one');calls=len(owner.shell.calls)
    owner.shell.service.state['selectedSessionId']='other'
    assert owner.snapshot('one') is None
    assert len(owner.shell.calls)==calls
    register(owner,demand=2)
    after=owner.snapshot('one')
    assert after['scope'][0]=='other' and before['scope'][0]=='chat'
    assert after['demandId']==2


def test_semantic_catalog_global_and_owner_changes_invalidate_without_bootstrap_churn(owner):
    register(owner, ('recentNavigation','sidebarPinned'))
    first=owner.snapshot('one');bootstrap=owner.bootstrap_key('one');calls=len(owner.shell.calls)
    owner.shell.saved['views']['nav']={'view':{'navPinnedPage':2},'viewRevision':1}
    owner.snapshot('one')
    assert owner.bootstrap_key('one')==bootstrap
    assert owner.shell.calls[calls:] and 'sidebarPinned' in owner.shell.calls[calls]
    calls=len(owner.shell.calls);owner.invalidate({'locationListing'});owner.snapshot('one')
    assert owner.shell.calls[calls:]==[frozenset()]
    calls=len(owner.shell.calls);owner.shell.service.directory.observation_revision['navigation']=2;owner.snapshot('one')
    assert owner.shell.calls[calls:]==[frozenset({'recentNavigation','sidebarPinned'}),frozenset()]
    owner.shell.saved['views']['nav']['generation']=1
    assert owner.bootstrap_key('one')!=bootstrap
    assert first['instances']['nav']['generation']==0


def test_custom_snapshot_is_preserved_builtin_non_navigation_is_not_projected(owner):
    owner.shell.saved['composition']['instances'].append({'id':'custom','slot':'navigation','package':'digest'})
    register(owner,())
    result=owner.snapshot('one')
    assert set(result['instances'])=={'nav','custom'}
    assert result['instances']['custom']['resources']['customSnapshot']['value']['custom']
    assert owner.shell.calls==[frozenset(),'custom']
    owner.shell.saved['views']['custom'] = {'viewRevision': 7, 'view': {'tab': 'changed'}}
    assert owner.snapshot('one')['instances']['custom']['resources']['customSnapshot']['value']['viewRevision'] == 7


def test_size_failure_is_bounded_memoized_and_does_not_destroy_prior_snapshot(owner,monkeypatch):
    register(owner);prior=owner.snapshot('one');prior_bytes=owner.total_bytes
    monkeypatch.setattr(resources,'MAX_CLIENT_BYTES',1000)
    owner.shell.payload='x'*2000;owner.shell.service.directory.observation_revision['navigation']=2
    error=owner.snapshot('one');calls=len(owner.shell.calls)
    assert error['error']['code']=='shell_resource_limit' and error['instances']=={}
    assert owner.total_bytes==prior_bytes
    owner.shell.service.state['revision']+=1
    owner.shell.recent_limits[('another-client', 'other-instance')] = 100
    assert owner.snapshot('one') is error and len(owner.shell.calls)==calls
    assert prior['instances']['nav']['resources']['context']['value']['payload']=='bounded'
    owner.shell.payload='small';owner.shell.service.directory.observation_revision['navigation']=3
    assert 'error' not in owner.snapshot('one')


def test_inactive_client_grace_reclaims_capacity_but_active_stream_never_evicted(owner,monkeypatch):
    clock=[0.0];monkeypatch.setattr(resources.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(resources,'MAX_CLIENTS',1)
    register(owner);baseline=owner.snapshot('one')
    owner.shell.service._client_snapshots = {'one': {'shellResources': baseline}}
    owner.shell.service._client_snapshot_preferences = {'one': 'preferences'}
    owner.shell.service.queue_clients={'queue':'one'};owner.shell.service.queue_sessions={'queue':None}
    clock[0]=100;assert owner.registered('one')
    assert owner.shell.service._client_snapshots['one']['shellResources'] is baseline
    owner.shell.service.clients.records['two']={'kind':'web'};owner.shell.service.clients.current.set('two')
    with pytest.raises(AppError): owner.register('two',1,[],resources.scope_for(owner.shell.service.state))
    owner.shell.service.queue_clients={};clock[0]=200;assert owner.registered('one')
    clock[0]=259;assert owner.registered('one')
    clock[0]=260;assert not owner.registered('one') and owner.total_bytes==0
    assert owner.shell.service._client_snapshots == {}
    assert owner.shell.service._client_snapshot_preferences == {}
    owner.register('two',1,[],resources.scope_for(owner.shell.service.state))


@pytest.mark.parametrize('resource',sorted(NAVIGATION_RESOURCES))
def test_resource_field_split_preserves_partial_snapshot_contract(owner,resource):
    register(owner,(resource,));result=owner.snapshot('one')['instances']['nav']['resources']
    assert set(result)=={resource,'context'}
    if resource.startswith('sidebar'):
        assert set(result[resource]['value']['sidebarNavigation'])=={resource[7:].lower()}
    else:
        assert resource in result[resource]['value']
    assert result['context']['value']['sidebarNavigation']=={'recentView':{}}


from test_shell_modules import service


async def test_real_navigation_resources_reassemble_exact_existing_snapshot(service):
    service.clients.attach('resource-real')
    with service.clients.bind('resource-real'):
        owner = resources.ShellResources(service.shell)
        owner.register('resource-real',1,sorted(NAVIGATION_RESOURCES),resources.scope_for(service.state))
        envelope = owner.snapshot('resource-real')
        client = service.shell.client('resource-real')
        for instance in client['composition']['instances']:
            expected = service.shell.navigation(client,instance,identity='resource-real',
                navigation_resources=NAVIGATION_RESOURCES)
            actual = {}
            for item in envelope['instances'][instance['id']]['resources'].values():
                for key,value in item['value'].items():
                    if key=='sidebarNavigation': actual.setdefault(key,{}).update(value)
                    else: actual[key]=value
            expected.pop('navigationResources')
            assert actual==expected
            assert 'draft' not in actual['view']


async def test_attention_only_saved_changes_refresh_visible_unselected_notices(service):
    service._save_changes(sessions={'alpha', 'beta'})
    service.clients.attach('resource-attention')
    with service.clients.bind('resource-attention'):
        owner = resources.ShellResources(service.shell)
        service.shell.client('resource-attention')['baselineView']['navChatScope'] = 'all'
        owner.register('resource-attention', 1, ['recentNavigation'], resources.scope_for(service.state))
        first = owner.snapshot('resource-attention')
        old = deepcopy(first)
        before = service.directory.observation_revision
        row = service._session('beta', hydrate=False)
        assert service.state.get('selectedSessionId') != 'beta'
        row['manualUnread'] = 'manual-one'
        service._save_changes(sessions={'beta'})
        after = service.directory.observation_revision
        assert after['navigation'] == before['navigation']
        assert after['attention'] != before['attention']
        second = owner.snapshot('resource-attention')
        assert second != first and first == old
        context = second['instances']['chats']['resources']['context']['value']
        assert context['attention']['sessions']['beta'] > 0
        row.update(error='A provider error', errorAt=1)
        service._save_changes(sessions={'beta'})
        errored = owner.snapshot('resource-attention')
        nav = service.directory.observation_revision['navigation']
        row['errorAt'] = 2
        service._save_changes(sessions={'beta'})
        assert service.directory.observation_revision['navigation'] == nav
        refreshed = owner.snapshot('resource-attention')
        assert refreshed != errored
        assert refreshed['instances']['chats']['resources']['context']['revision'] > errored['instances']['chats']['resources']['context']['revision']


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
async def test_unavailable_custom_package_does_not_break_builtin_resources(service, damage):
    from test_shell_modules import stage, record_validation, prepare, command
    digest = await stage(service)
    record_validation(service, digest)
    service.clients.attach('resource-custom')
    composition = deepcopy(service.shell.client('resource-custom')['composition'])
    composition['instances'].append({'id':'optional','package':digest,'slot':'navigation'})
    change = await prepare(service, composition, client='resource-custom')
    await command(service, 'shell.changes.apply', clientId='resource-custom', changeId=change, expectedRevision=0)
    with service.clients.bind('resource-custom'):
        owner = service.shell.resources
        owner.register('resource-custom',1,[],resources.scope_for(service.state))
        first = owner.snapshot('resource-custom')
        assert 'customSnapshot' in first['instances']['optional']['resources']
        path = service.shell.directory / (digest + '.mjs')
        if damage == 'missing': path.unlink()
        else: path.write_text('corrupted source')
        service.state['revision'] += 1
        after = owner.snapshot('resource-custom')
        assert after['instances']['optional']['resources'] == {}
        assert 'context' in after['instances']['chats']['resources']
        bootstrap = service.shell.inspect('resource-custom', bundled=True)
        assert 'error' in bootstrap['packages'][digest]
        assert bootstrap['shellResources']['instances']['chats']['resources']['context']


def test_global_budget_never_discards_active_owner_and_recovers_after_idle_retirement(owner, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(resources.time, 'monotonic', lambda: clock[0])
    register(owner)
    first = owner.snapshot('one')
    used = owner.total_bytes
    monkeypatch.setattr(resources, 'MAX_TOTAL_BYTES', used + 1)
    owner.shell.service.clients.records['two'] = {'kind': 'web'}
    owner.shell.service.clients.current.set('two')
    owner.register('two', 1, ['recentNavigation'], resources.scope_for(owner.shell.service.state))
    limited = owner.snapshot('two')
    assert limited['error']['code'] == 'shell_resource_limit'
    assert owner.total_bytes == used and owner.clients['one']['envelope'] is first
    owner.shell.service.queue_clients = {'queue': 'two'}
    owner.shell.service.queue_sessions = {'queue': None}
    clock[0] = 61
    recovered = owner.snapshot('two')
    assert 'error' not in recovered and not owner.registered('one')
    assert 0 < owner.total_bytes <= resources.MAX_TOTAL_BYTES


async def test_retired_browser_baseline_cannot_reconnect_as_registered(service, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(resources, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    service.clients.attach('resource-retired')
    with service.clients.bind('resource-retired'):
        owner = service.shell.resources
        scope = resources.scope_for(service.state)
        owner.register('resource-retired', 1, [], scope)
        old = service.browser_state()
        assert 'shellResources' in old
        clock[0] = 61
        assert not owner.registered('resource-retired')
        assert 'resource-retired' not in service._client_snapshots
        fresh = service.browser_state()
        assert fresh is not old and 'shellResources' not in fresh
        assert 'resource-retired' in service.clients.records
        owner.register('resource-retired', 2, [], scope)
        assert owner.snapshot('resource-retired')['demandId'] == 2


def test_bootstrap_tracks_descriptor_revision_and_preview_identity_only(owner):
    client = owner.shell.saved
    client['revision'] = 0
    baseline = owner.bootstrap_key('one')
    client['preview'] = {'id': 'preview-one', 'composition': deepcopy(client['composition'])}
    preview = owner.bootstrap_key('one')
    assert preview != baseline
    client['preview']['id'] = 'preview-two'
    assert owner.bootstrap_key('one') != preview
    # Applying an identical effective composition still replaces the preview
    # descriptor and advances the CAS revision used by subsequent commands.
    client['preview'] = None
    client['revision'] = 1
    applied = owner.bootstrap_key('one')
    assert applied not in {baseline, preview}
    client['reported'] = {'instances': {'nav': 'ready'}}
    client['views']['nav'] = {'generation': 0, 'viewRevision': 99, 'view': {'navFilter': 'private filter'}}
    owner.shell.service.state.update(revision=999, execution={'text': 'progress'})
    assert owner.bootstrap_key('one') == applied
