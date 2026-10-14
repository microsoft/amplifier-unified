"""Resource demand bounds bundled reads; explicit complete APIs remain available."""
from copy import deepcopy
import json
import pytest

from amplifier_web.service import AppError
from amplifier_web.shell_navigation_demand import NAVIGATION_RESOURCES, parse_navigation_resources
from test_shell_modules import service, stage, record_validation


def test_resource_parser_requires_demand_and_bounds_untrusted_input():
    assert parse_navigation_resources('[]') == frozenset()
    assert parse_navigation_resources('["recentNavigation","recentNavigation"]') == {'recentNavigation'}
    for value in (None, 'null', '{}', '"recentNavigation"', '[0]', '[{}]', '["unknown"]', '[', ' '*4097,
                  json.dumps(['recentNavigation']*17), '['*1000, 12):
        with pytest.raises(AppError): parse_navigation_resources(value)


async def test_explicit_full_snapshot_and_custom_packages_keep_existing_contract(service):
    client = service.shell.client('demand-client')
    instance = {'id':'chats','package':'builtin.chats','slot':'navigation'}
    full = service.shell.snapshot(client, instance, identity='demand-client')
    assert 'navigationResources' not in full
    assert full == service.shell.snapshot(client, instance, identity='demand-client', navigation_resources=None)
    digest = await stage(service)
    record_validation(service, digest)
    custom = {**instance, 'id':'custom', 'package':digest}
    before = service.shell.snapshot(client, custom, identity='demand-client')
    assert service.shell.snapshot(client, custom, identity='demand-client', navigation_resources=frozenset()) == before
    assert all(key in before for key in ('chatNavigation','homeNavigation','recentNavigation','workspaceExplorer'))
    receipt = await service.dispatch('shell.query', {'clientId':'demand-client','instanceId':'chats'}, origin='agent')
    assert 'chatNavigation' in receipt['result'] and 'navigationResources' not in receipt['result']


@pytest.mark.parametrize('resource', sorted(NAVIGATION_RESOURCES))
async def test_each_requested_resource_matches_complete_snapshot(service, resource):
    client = service.shell.client('demand-client')
    # A pinned scope exercises the home alias and preserves its workspace fence.
    instance = {'id':'chats','package':'builtin.chats','slot':'navigation',
                'scope':{'mode':'pinned','workspaceId':'two'}}
    full = service.shell.snapshot(client, instance, identity='demand-client')
    scoped = service.shell.snapshot(client, instance, identity='demand-client', navigation_resources=frozenset({resource}))
    assert scoped['navigationResources'] == [resource]
    if resource.startswith('sidebar'):
        field = resource.removeprefix('sidebar').lower()
        assert scoped['sidebarNavigation'][field] == full['sidebarNavigation'][field]
        assert set(scoped['sidebarNavigation']) == {'recentView', field}
    else:
        assert scoped[resource] == full[resource]
    if resource == 'recentNavigation':
        assert scoped['recentScope'] == full['recentScope']
        assert scoped['recentShortcuts'] == full['recentShortcuts']
    if resource == 'workspaceShortcuts':
        assert scoped['unpinnedWorkspaceShortcuts'] == full['unpinnedWorkspaceShortcuts']
    for key in ('chatNavigation','homeNavigation','recentNavigation','workspaceExplorer','workspaceOverview','pinnedNavigation'):
        assert (key in scoped) == (key == resource)
    assert scoped['view'] == full['view'] and scoped['selectedWorkspaceId'] == 'two'


async def test_quiet_sidebar_never_constructs_hidden_pages(service, monkeypatch):
    client = service.shell.client('demand-client')
    before = deepcopy(service.state)
    calls = []
    original = service.projections.chats
    def chats(state, *, section=None):
        calls.append(section)
        assert section == 'shortcuts', 'Unrequested page was constructed'
        return original(state, section=section)
    monkeypatch.setattr(service.projections, 'chats', chats)
    def forbidden(*args, **kwargs): raise AssertionError('Unrequested workspace page')
    monkeypatch.setattr(service.projections, 'workspaces', forbidden)
    monkeypatch.setattr(service.projections, 'workspace_shortcuts', forbidden)
    wanted = frozenset({'recentNavigation'})
    result = service.shell.inspect('demand-client', snapshots=True, navigation_resources=wanted)
    snapshots = [row for row in result['snapshots'].values() if row.get('navigationResources') is not None]
    assert snapshots and calls == ['shortcuts']*len(snapshots)
    for row in snapshots:
        assert row['navigationResources'] == ['recentNavigation']
        assert row['sidebarNavigation'].keys() == {'recentView'}
        visible = {item['id'] for item in row['recentShortcuts']}
        assert set(row['attention']['sessions']) <= visible
        assert set(row['conversationOrganization']['archived']) <= visible
    assert service.state == before


async def test_empty_demand_retains_controller_metadata_without_pages(service, monkeypatch):
    client = service.shell.client('demand-client')
    def forbidden(*args, **kwargs): raise AssertionError('Unrequested projection')
    for method in ('chats','pinned_navigation','workspaces','workspace_shortcuts'):
        monkeypatch.setattr(service.projections, method, forbidden)
    row = service.shell.snapshot(client, {'id':'workspaces','package':'builtin.workspaces','slot':'navigation'},
        identity='demand-client', navigation_resources=frozenset())
    assert row['navigationResources'] == []
    assert row['attention'] == {'sessions':{},'items':[]}
    assert all(key in row for key in ('view','settings','workspaceDefaults','workspaces','library','generation','sharedHistory'))
