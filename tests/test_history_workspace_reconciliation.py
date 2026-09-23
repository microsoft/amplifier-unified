"""Workspace refresh indexes placeholders without rescanning the full registry."""
import asyncio
import copy
import sqlite3
import uuid
from types import SimpleNamespace

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
        self.queue_clients = {}
        self.clients = SimpleNamespace(records={})

    def _publish(self):
        self.publications += 1


@pytest.fixture(autouse=True)
def deletion_ledger(monkeypatch):
    from amplifier_web.managed_deletion import initialize
    db = sqlite3.connect(':memory:')
    initialize(db)
    monkeypatch.setattr(ReconciliationService, 'db', db, raising=False)
    yield
    db.close()


def workspace(identity, project, path=None, **extra):
    return {'id': identity, 'nativeProject': project, 'name': project, 'path': path,
            'available': bool(path), 'sessionCount': 0, 'workerSessionCount': 0, **extra}


def history_for(service, rows):
    history = AutomaticHistory(service)
    service.state['sharedHistory']['loading'] = False
    history.index.scan_if_changed = lambda **kwargs: (object(), {'workspaces': copy.deepcopy(rows), 'sessions': []})
    return history


async def test_reused_catalog_revision_stays_detached_and_reconciles_in_place_changes(monkeypatch):
    from amplifier_web.automatic_history import identity

    service = ReconciliationService([])
    history = AutomaticHistory(service)
    catalog = {'workspaces': [], 'sessions': [
        {'id': 'catalog-id', 'nativeProject': 'project', 'nativeIdentity': 'saved',
         'workspace': None, 'workspaceId': None, 'sessionKind': 'root',
         'title': 'Original', 'transcriptRevision': [1, 2]},
    ]}
    token = object()
    monkeypatch.setattr(history.index, 'scan_if_changed',
                        lambda **kwargs: (token, catalog if kwargs['since'] is not token else None))
    await history.refresh(force=False)
    saved = service.state['sessions'][0]
    revision = saved['nativeRevision']
    assert revision == [1, 2] and revision is not catalog['sessions'][0]['transcriptRevision']

    # Reusing an unchanged native generation cannot skip edits to either side.
    saved['nativeRevision'][0] = -1
    saved['description'] = 'Locally stale metadata'
    await history.refresh(force=False)
    assert saved['nativeRevision'] == [1, 2] and saved['description'] == ''
    catalog['sessions'][0]['transcriptRevision'][1] = 3
    catalog['sessions'][0]['title'] = 'Externally changed'
    await history.refresh(force=False)
    assert saved['nativeRevision'] == [1, 3]
    assert saved['title'] == 'Externally changed'
    saved['nativeRevision'][0] = -2
    assert catalog['sessions'][0]['transcriptRevision'] == [1, 3]

    # Loaded history keeps its old stamp so the selected-view loader can detect
    # a changed transcript; an absent stamp still receives a detached value.
    saved.update(historyLoaded=True, nativeRevision=[0, 0])
    await history.refresh(force=False)
    assert saved['nativeRevision'] == [0, 0]
    del saved['nativeRevision']
    await history.refresh(force=False)
    assert saved['nativeRevision'] == [1, 3]
    assert saved['nativeRevision'] is not catalog['sessions'][0]['transcriptRevision']

    # Filters added and removed after warming are live even without a native
    # revision change. Native identity is project-scoped, not the catalog ID.
    service.state['sessions'] = []
    service.state['hiddenNativeSessions'] = [identity('project', 'saved')]
    await history.refresh(force=False)
    assert service.state['sessions'] == []
    service.state['hiddenNativeSessions'] = []
    await history.refresh(force=False)
    assert service.state['sessions'][0]['nativeIdentity'] == 'saved'
    assert history._native_revision is token
    assert service.state['sharedHistory']['error'] is None


async def test_revision_alias_is_detached_and_missing_or_null_stamp_is_preserved(monkeypatch):
    service = ReconciliationService([])
    history = AutomaticHistory(service)
    row = {'id': 'catalog-id', 'nativeProject': 'project', 'nativeIdentity': 'saved',
           'workspace': None, 'workspaceId': None, 'sessionKind': 'root',
           'title': 'Original', 'transcriptRevision': [1, 2]}
    catalog = {'workspaces': [], 'sessions': [row]}
    token = object()
    monkeypatch.setattr(history.index, 'scan_if_changed', lambda **kwargs: (token, catalog))
    await history.refresh(force=False)
    saved = service.state['sessions'][0]
    saved['nativeRevision'] = row['transcriptRevision']
    await history.refresh(force=True)
    assert saved['nativeRevision'] == row['transcriptRevision']
    assert saved['nativeRevision'] is not row['transcriptRevision']
    row['transcriptRevision'] = None
    await history.refresh(force=False)
    assert 'nativeRevision' in saved and saved['nativeRevision'] is None
    del row['transcriptRevision']
    del saved['nativeRevision']
    await history.refresh(force=False)
    assert 'nativeRevision' in saved and saved['nativeRevision'] is None
    assert service.state['sharedHistory']['error'] is None


async def test_reused_native_catalog_reconciles_local_filters_errors_and_selections(tmp_path, monkeypatch):
    import json
    from amplifier_web.native_history import NativeHistory
    from amplifier_web.session_files import project_slug
    home, folder = tmp_path / 'native', tmp_path / 'workspace'
    folder.mkdir()
    project = project_slug(folder)
    directory = home / 'projects' / project / 'sessions' / 'saved'
    directory.mkdir(parents=True)
    (directory / 'metadata.json').write_text(json.dumps({'working_dir': str(folder), 'bundle': 'anchors'}))
    (directory / 'transcript.jsonl').write_text('saved transcript\n')
    service = ReconciliationService([])
    history = AutomaticHistory(service)
    history.index = NativeHistory(home)
    await history.refresh(force=False)
    await history.refresh(force=False)  # Settle learned workspace paths.
    token = history._native_revision
    original_revision = history._native_snapshot['sessions'][0]['transcriptRevision'][:]
    saved = service.state['sessions'][0]
    saved['nativeRevision'][0] = -1
    service.state['sharedHistory']['error'] = 'Previous refresh failed'
    queue = object()
    service.queue_clients[queue] = 'background-tab'
    service.queue_sessions = {queue: saved['id']}
    selected_loads = []

    async def load(identity):
        selected_loads.append(identity)

    monkeypatch.setattr(history, 'load', load)
    monkeypatch.setattr('amplifier_web.automatic_history.revision', lambda row: original_revision)
    await history.refresh(force=False)
    assert history._native_revision is token
    assert saved['nativeRevision'] == original_revision
    assert history._native_snapshot['sessions'][0]['transcriptRevision'] == original_revision
    assert service.state['sharedHistory']['error'] is None
    assert selected_loads == [saved['id']]

    # Local tombstones filter this refresh, not the retained unfiltered catalog.
    monkeypatch.setattr('amplifier_web.managed_deletion.tombstones', lambda db: [{'project': project}])
    await history.refresh(force=False)
    assert history._native_revision is token
    assert history.last_scan['sessions'] == []
    assert len(history._native_snapshot['sessions']) == 1
    monkeypatch.setattr('amplifier_web.managed_deletion.tombstones', lambda db: [])
    service.state['sessions'] = []
    await history.refresh(force=False)
    assert service.state['sessions'][0]['nativeIdentity'] == 'saved'
    assert history._native_revision is token

    # Managed ownership/availability is independent of native metadata stamps.
    monkeypatch.setattr('amplifier_web.automatic_history.catalog_locations', lambda snapshot: {str(folder): False})
    await history.refresh(force=False)
    saved = service.state['sessions'][0]
    assert saved['workspaceId'] is None and saved['workspaceAvailable'] is False
    monkeypatch.setattr('amplifier_web.automatic_history.catalog_locations', lambda snapshot: {str(folder): True})
    await history.refresh(force=False)
    assert saved['workspaceAvailable'] is True
    assert history._native_revision is token

    history.hide_session(service.state['sessions'][0])
    service.state['sessions'] = []
    await history.refresh(force=False)
    assert service.state['sessions'] == []
    service.state['hiddenNativeSessions'] = []
    await history.refresh(force=False)
    assert service.state['sessions'][0]['nativeIdentity'] == 'saved'
    assert history._native_revision is token


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
