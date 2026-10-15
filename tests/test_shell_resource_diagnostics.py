"""Rebuild evidence explains real invalidations without exporting key values."""
import json

import pytest

from amplifier_web.performance_diagnostics import PerformanceDiagnostics
from amplifier_web.shell_resources import scope_for
from test_shell_resources import owner, register
from test_shell_modules import service, settled_resources


def observe(owner):
    diagnostics = PerformanceDiagnostics()
    owner.shell.service.performance_diagnostics = diagnostics
    owner.shell.service.directory.observation_revision = {
        'root_navigation': ('private-source', 1, 2, 'private-overlay', 'read', 'suppression'),
        'attention': ('private-source', 1, 2, 'private-overlay', 'read', 'suppression')}
    register(owner, ('sidebarRecent',))
    owner.snapshot('one')
    return diagnostics


@pytest.mark.parametrize('index,reason', list(enumerate(('source', 'native', 'local', 'overlay', 'read', 'suppression'))))
def test_navigation_generations_report_the_changed_owner_only(owner, index, reason):
    diagnostics = observe(owner)
    revisions = owner.shell.service.directory.observation_revision
    changed = list(revisions['root_navigation'])
    changed[index] = 'changed-private-value'
    revisions['root_navigation'] = tuple(changed)
    owner.snapshot('one')
    evidence = diagnostics.snapshot()['shellResourceRebuilds']
    assert evidence['total'] == 2
    assert evidence['recent'][-1]['reasons'] == ['navigation.' + reason]
    assert evidence['recent'][-1]['changedGlobals'] == []
    assert 'private' not in json.dumps(evidence)


def test_attention_selection_query_and_bootstrap_have_distinct_evidence(owner):
    diagnostics = observe(owner)
    revisions = owner.shell.service.directory.observation_revision
    revisions['attention'] = ('private-changed-attention',)
    owner.snapshot('one')
    assert diagnostics.snapshot()['shellResourceRebuilds']['total'] == 1
    changed = list(revisions['root_navigation'])
    changed[4] = 'private-root-read-receipt'
    revisions['root_navigation'] = tuple(changed)
    owner.snapshot('one')
    assert diagnostics.snapshot()['shellResourceRebuilds']['recent'][-1]['reasons'] == ['navigation.read']
    owner.shell.service.state['selectedSessionId'] = 'private-other-chat'
    register(owner, ('sidebarRecent',), demand=2)
    owner.snapshot('one')
    assert diagnostics.snapshot()['shellResourceRebuilds']['recent'][-1]['reasons'] == ['selection']
    owner.shell.saved['baselineView']['navRecentView'] = {'navFilter': 'private-query'}
    owner.snapshot('one')
    assert diagnostics.snapshot()['shellResourceRebuilds']['recent'][-1]['reasons'] == ['query']
    owner.shell.saved['revision'] = 1
    owner.snapshot('one')
    evidence = diagnostics.snapshot()['shellResourceRebuilds']
    assert evidence['recent'][-1]['reasons'] == ['bootstrap']
    assert 'private' not in json.dumps(evidence)


def test_unchanged_pages_do_not_record_and_evidence_is_bounded_and_detached(owner):
    diagnostics = observe(owner)
    first = owner.snapshot('one')
    assert owner.snapshot('one') is first
    assert diagnostics.snapshot()['shellResourceRebuilds']['total'] == 1
    for index in range(80):
        owner.shell.saved['baselineView']['navRecentView'] = {'navFilter': f'private-query-{index}'}
        owner.snapshot('one')
    evidence = diagnostics.snapshot()['shellResourceRebuilds']
    assert evidence['total'] == 81
    assert evidence['byReason'] == {'initial': 1, 'query': 80}
    assert evidence['byResource'] == {'sidebarRecent': 81}
    assert len(evidence['recent']) == 60
    assert 'private' not in json.dumps(evidence)
    evidence['recent'][0]['reasons'].append('tampered')
    evidence['recent'][0]['changedGlobals'].append('tampered')
    evidence['byReason']['query'] = 0
    again = diagnostics.snapshot()['shellResourceRebuilds']
    assert again['byReason']['query'] == 80
    assert 'tampered' not in json.dumps(again)


async def test_only_actual_changed_globals_report_resource_invalidations(service):
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces', 'sharedHistory'})
    service.clients.attach('diagnostic-client')
    with service.clients.bind('diagnostic-client'):
        owner = service.shell.resources
        owner.register('diagnostic-client', 1, ['sidebarRecent'], scope_for(service.state))
        first = await settled_resources(service, 'diagnostic-client')
        before = service.performance_diagnostics.snapshot()['shellResourceRebuilds']['total']
        service._publish_changes(globals={'workspaces', 'sharedHistory'})
        assert owner.snapshot('diagnostic-client') is first
        assert service.performance_diagnostics.snapshot()['shellResourceRebuilds']['total'] == before
        service.state['workspaces'][0]['name'] = 'private workspace name'
        service._publish_changes(globals={'workspaces', 'sharedHistory'})
        owner.snapshot('diagnostic-client')
        evidence = service.performance_diagnostics.snapshot()['shellResourceRebuilds']
        assert evidence['total'] > before
        assert evidence['recent'][-1]['reasons'] == ['globals']
        assert evidence['recent'][-1]['changedGlobals'] == ['workspaces']
        assert 'private workspace name' not in json.dumps(evidence)
