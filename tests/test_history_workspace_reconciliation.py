"""Workspace refresh indexes placeholders without rescanning the full registry."""
import asyncio
import copy
import uuid

import pytest

from amplifier_web.automatic_history import AutomaticHistory


class ReconciliationService:
    """Exercise the real refresh merge without unrelated projections or saves."""
    def __init__(self, workspaces):
        self.state = {'workspaces': workspaces, 'sessions': [], 'settings': {'bundle': 'anchors'},
                      'canvasArtifacts': [], 'canvas': {}, 'selectedSessionId': None}
        self.lock = asyncio.Lock()
        self.closed = False
        self.publications = 0

    def _publish(self):
        self.publications += 1


def workspace(identity, project, path=None, **extra):
    return {'id': identity, 'nativeProject': project, 'name': project, 'path': path,
            'available': bool(path), 'sessionCount': 0, 'workerSessionCount': 0, **extra}


def history_for(service, rows):
    history = AutomaticHistory(service)
    service.state['sharedHistory']['loading'] = False
    history.index.scan = lambda **kwargs: {'workspaces': copy.deepcopy(rows), 'sessions': []}
    return history


class CountedWorkspace(dict):
    project_lookups = 0

    def get(self, key, default=None):
        if key == 'nativeProject':
            type(self).project_lookups += 1
        return super().get(key, default)


@pytest.mark.parametrize('unresolved', [False, True])
async def test_unchanged_refresh_uses_linear_project_lookups(tmp_path, unresolved):
    counts = []
    for size in (64, 256):
        rows = [workspace(f'workspace-{number}', f'project-{number}',
                          None if unresolved else str(tmp_path)) for number in range(size)]
        service = ReconciliationService([CountedWorkspace(row) for row in rows])
        history = history_for(service, rows)
        before = copy.deepcopy(service.state['workspaces'])
        CountedWorkspace.project_lookups = 0
        await history.refresh()
        counts.append(CountedWorkspace.project_lookups)
        assert service.state['sharedHistory']['error'] is None
        assert service.state['workspaces'] == before
        assert service.publications == 0
        # Count real registry accesses, not elapsed time: the prior full scan
        # performs size**2 project lookups even when nothing changed.
        assert counts[-1] <= 8 * size
    assert counts[1] <= 5 * counts[0]


async def test_duplicate_project_rows_migrate_placeholders_once_and_preserve_scopes(tmp_path):
    first = workspace('old-first', 'project', name='First alias', favorite={'color': 'blue'})
    second = workspace('old-second', 'project', name='Second alias')
    target = workspace('resolved-first', 'project', str(tmp_path), name='My existing project', aliases=['Existing alias'])
    other = workspace('resolved-second', 'project', str(tmp_path), name='Other registration')
    unrelated = workspace('unrelated-placeholder', 'other-project', name='Leave me alone')
    service = ReconciliationService([first, second, target, other, unrelated])
    service.state.update(selectedWorkspaceId=second['id'],
                         sessions=[{'id': 'chat', 'workspaceId': first['id']}],
                         canvasArtifacts=[{'id': 'artifact', 'workspaceId': second['id']}],
                         canvas={'id': 'active', 'workspaceId': first['id']})
    discovered_target = {**target, 'name': 'project'}
    history = history_for(service, [discovered_target, other, discovered_target])
    await history.refresh()
    assert service.state['sharedHistory']['error'] is None
    assert [row['id'] for row in service.state['workspaces']] == [target['id'], other['id'], unrelated['id']]
    assert target['name'] == 'My existing project'
    assert target['aliases'] == ['Existing alias', 'First alias', 'Second alias']
    assert target['favorite'] == {'color': 'blue'}
    assert target['favorite'] is not first['favorite']
    assert other['name'] == 'Other registration' and 'aliases' not in other
    assert service.state['selectedWorkspaceId'] == target['id']
    assert service.state['settings']['workspace'] == str(tmp_path)
    assert service.state['sessions'][0]['workspaceId'] == target['id']
    assert service.state['canvasArtifacts'][0]['workspaceId'] == target['id']
    assert service.state['canvas']['workspaceId'] == target['id']
    assert service.publications == 1
    await history.refresh()
    assert service.state['sharedHistory']['error'] is None
    assert service.publications == 1
    assert target['aliases'] == ['Existing alias', 'First alias', 'Second alias']


async def test_placeholders_added_during_scan_are_visible_to_later_rows(tmp_path):
    service = ReconciliationService([])
    service.state.update(selectedWorkspaceId='first', sessions=[{'id': 'chat', 'workspaceId': 'first'}])
    rows = [workspace('first', 'project', name='Custom name'), workspace('second', 'project'),
            workspace('resolved', 'project', str(tmp_path)), workspace('another-resolved', 'project', str(tmp_path))]
    await history_for(service, rows).refresh()
    assert service.state['sharedHistory']['error'] is None
    assert [row['id'] for row in service.state['workspaces']] == ['resolved', 'another-resolved']
    assert service.state['workspaces'][0]['name'] == 'Custom name'
    assert service.state['workspaces'][1]['name'] == 'project'
    assert service.state['selectedWorkspaceId'] == 'resolved'
    assert service.state['sessions'][0]['workspaceId'] == 'resolved'


async def test_same_id_project_change_updates_placeholder_index(tmp_path):
    moved = workspace('moving', 'old-project', name='Preserved name')
    existing = workspace('other-placeholder', 'new-project', name='Preserved alias')
    service = ReconciliationService([moved, existing])
    rows = [workspace('moving', 'new-project'), workspace('resolved-old', 'old-project', str(tmp_path)),
            workspace('resolved-new', 'new-project', str(tmp_path))]
    await history_for(service, rows).refresh()
    assert service.state['sharedHistory']['error'] is None
    by_id = {row['id']: row for row in service.state['workspaces']}
    assert set(by_id) == {'resolved-old', 'resolved-new'}
    assert by_id['resolved-old']['name'] == 'old-project'
    assert 'aliases' not in by_id['resolved-old']
    assert by_id['resolved-new']['name'] == 'Preserved name'
    assert by_id['resolved-new']['aliases'] == ['Preserved alias']


async def test_hidden_resolution_does_not_consume_or_resurrect_placeholders(tmp_path):
    hidden_project = 'hidden-project'
    old_hidden_id = uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-project:{hidden_project}').hex
    hidden = workspace(old_hidden_id, hidden_project, name='Hidden custom name')
    retained = workspace('retained-placeholder', 'other-project', name='Retained custom name')
    service = ReconciliationService([hidden, retained])
    service.state['hiddenNativeWorkspaces'] = [old_hidden_id, 'hidden-target']
    rows = [workspace('hidden-resolved', hidden_project, str(tmp_path)),
            workspace('hidden-target', 'other-project', str(tmp_path))]
    history = history_for(service, rows)
    await history.refresh()
    assert service.state['sharedHistory']['error'] is None
    assert service.state['workspaces'] == [hidden, retained]
    assert service.state['hiddenNativeWorkspaces'] == [old_hidden_id, 'hidden-target', 'hidden-resolved']
    assert service.publications == 1
    await history.refresh()
    assert service.publications == 1
