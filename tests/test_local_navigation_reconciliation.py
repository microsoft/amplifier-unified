"""Native reconciliation must not overwrite an admitted owner's live metadata."""
from copy import deepcopy
import json

import pytest

from amplifier_web.shell_resources import scope_for
from test_shell_modules import service, settled_resources
from test_automatic_history import app_factory, native_session


def admitted(service, *, busy=False, availability=True):
    row = service.state['sessions'][0]
    row.update(title='Saved chat', nativeProject='native-project', nativeIdentity='native-chat',
               runtimeSessionId='native-chat', sessionKind='root', historyManaged=True,
               historyLoaded=True, createdAt=1, updatedAt=1, recentActivityAt=20,
               navigationActivityAt=10 if busy else 20, status='working' if busy else 'idle',
               nativeNameSource=None, nativeNameRevision=0, nativeNamePolicyRevision=0,
               autoName=True, nativeParentId=None, parentId=None, sessionPurpose=None)
    if availability:
        row['nativeAvailable'] = True
    else:
        row.pop('nativeAvailable', None)
    native = {'nativeProject':'native-project', 'nativeIdentity':'native-chat',
              'workspaceId':row['workspaceId'], 'workspace':row['workspace'],
              'title':row['title'], 'createdAt':1, 'updatedAt':1,
              'recentActivityAt':20, 'sessionKind':'root'}
    service._save_changes(sessions={row['id']})
    return row, native


@pytest.mark.parametrize('busy,availability', [(False,False), (True,True), (True,False)])
async def test_repeated_native_refresh_and_admitted_save_keep_navigation_generation(service, busy, availability):
    row, native = admitted(service, busy=busy, availability=availability)
    service._save_changes(globals={'workspaces', 'sharedHistory'})
    service.clients.attach('reconciliation-client').update(
        selectedSessionId=row['id'], selectedWorkspaceId=row['workspaceId'])
    with service.clients.bind('reconciliation-client'):
        owner = service.shell.resources
        owner.register('reconciliation-client', 1, ['sidebarRecent', 'workspaceShortcuts'], scope_for(service.state))
        pages = deepcopy(await settled_resources(service, 'reconciliation-client'))
    page_attempts = service.performance_diagnostics.snapshot()['shellResourceRebuilds']['total']
    before = service.directory.observation_revision['navigation']
    frozen = deepcopy(row)
    for _ in range(3):
        service.directory.refresh_native([native], [], service.state)
        service._save_changes(sessions={row['id']})
        assert service.directory.observation_revision['navigation'] == before
        assert row == frozen
        with service.clients.bind('reconciliation-client'):
            assert owner.snapshot('reconciliation-client') == pages
        assert service.performance_diagnostics.snapshot()['shellResourceRebuilds']['total'] == page_attempts
    assert row['navigationActivityAt'] == (10 if busy else 20)


async def test_dormant_overlay_still_receives_native_changes(service):
    row, native = admitted(service, availability=False)
    identity = row['id']
    service.state['sessions'] = [candidate for candidate in service.state['sessions'] if candidate['id'] != identity]
    before = service.directory.observation_revision['navigation']
    service.directory.refresh_native([{**native, 'name':'Updated saved name', 'nameRevision':1,
                                      'recentActivityAt':30}], [], service.state)
    service.db.commit()
    changed = service.directory.resolve(identity)
    assert changed['title'] == 'Updated saved name'
    assert changed['nativeAvailable'] is True
    assert changed['navigationActivityAt'] == 30
    assert service.directory.observation_revision['navigation'] != before


async def test_native_removal_keeps_admitted_owner_unavailable(service):
    row, native = admitted(service)
    before = service.directory.observation_revision['navigation']
    service.directory.refresh_native([], [(native['nativeProject'], native['nativeIdentity'])], service.state)
    stored = service.db.execute('SELECT summary FROM history_local_rows WHERE id=?', (row['id'],)).fetchone()[0]
    removed = json.loads(stored)
    assert removed['nativeAvailable'] is False
    assert 'no longer available' in removed['historyReadOnlyReason']
    assert service.directory.observation_revision['navigation'] != before


@pytest.mark.parametrize('bounded', [False, True], ids=['default-slice', 'one-step'])
async def test_live_merge_updates_real_names_once_and_keeps_busy_activity_stable(
        tmp_path, app_factory, monkeypatch, record_property, bounded):
    directory = native_session(tmp_path / 'native', 'busy-chat')
    app = app_factory()
    if bounded:
        # Watcher hints are optional; a forced refresh still does bounded work.
        monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, set()))
        app.history.index._recovery_budget = 1

    async def refresh_pass(label):
        observations = []
        try:
            for _ in range(16):
                await app.history.refresh()
                discovery = app.state['sharedHistory']
                catalog = app.history.index._catalog.get(directory.parent.parent.name, 'busy-chat')
                live = next((row for row in app._state['sessions']
                             if row.get('nativeIdentity') == 'busy-chat'), {})
                observations.append({
                    'phase': discovery['reconciliation']['phase'],
                    'steps': app.history.last_scan['reconciliation']['steps'],
                    'error': discovery['error'],
                    'catalogName': (catalog or {}).get('name'),
                    'catalogRevision': (catalog or {}).get('nameRevision'),
                    'liveTitle': live.get('title'),
                    'liveRevision': live.get('nativeNameRevision'),
                    'navigationGeneration': app.directory.observation_revision['navigation'][2],
                })
                assert discovery['error'] is None, observations
                if observations[-1]['phase'] == 'complete':
                    assert app.history.index._recovery is None, observations
                    return observations
            pytest.fail(f'Native fixture did not finish bounded reconciliation: {observations}')
        finally:
            record_property(label, json.dumps(observations))

    # Finish earlier work before changing canonical metadata. Otherwise the
    # next forced refresh can merely finish a pass that already read revision 1.
    await refresh_pass('initial')
    row = app._session('busy-chat')
    row.update(status='working', navigationActivityAt=1, draft='Keep this unsent draft')
    row.pop('nativeAvailable', None)
    app._save_changes(sessions={row['id']})
    metadata_path = directory / 'metadata.json'
    metadata = json.loads(metadata_path.read_text())
    for revision in (1, 2):
        before = app.directory.observation_revision['navigation'][2]
        metadata.update(name=f'Changed title {revision}', name_revision=revision)
        metadata_path.write_text(json.dumps(metadata))
        observations = await refresh_pass(f'rename-{revision}')
        assert observations[-1]['catalogName'] == f'Changed title {revision}', observations
        assert observations[-1]['catalogRevision'] == revision, observations
        assert row['title'] == f'Changed title {revision}', ('live-title-after-complete', observations)
        assert row['nativeNameRevision'] == revision, observations
        assert row['navigationActivityAt'] == 1
        assert row['status'] == 'working'
        assert row['draft'] == 'Keep this unsent draft'
        assert app.directory.observation_revision['navigation'][2] == before + 1, observations
        if bounded:
            assert any(item['phase'] == 'running' for item in observations), observations
        settled = app.directory.observation_revision['navigation'][2]
        await refresh_pass(f'unchanged-{revision}')
        assert app.directory.observation_revision['navigation'][2] == settled

    # Recovery of an admitted unavailable owner still comes from the live merge.
    row.update(nativeAvailable=False, historyReadOnlyReason='Files were unavailable')
    app._save_changes(sessions={row['id']})
    await refresh_pass('availability-recovery')
    assert row['nativeAvailable'] is True
    assert row.get('historyReadOnlyReason') is None
    assert row['navigationActivityAt'] == 1
    assert row['title'] == 'Changed title 2'
    assert row['nativeNameRevision'] == 2
    assert row['status'] == 'working'
    assert row['draft'] == 'Keep this unsent draft'
