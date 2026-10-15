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


async def test_live_merge_updates_real_names_once_and_keeps_busy_activity_stable(tmp_path, app_factory):
    directory = native_session(tmp_path / 'native', 'busy-chat')
    app = app_factory()
    await app.history.refresh()
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
        await app.history.refresh()
        assert row['title'] == f'Changed title {revision}'
        assert row['navigationActivityAt'] == 1
        assert app.directory.observation_revision['navigation'][2] == before + 1
        settled = app.directory.observation_revision['navigation'][2]
        await app.history.refresh()
        assert app.directory.observation_revision['navigation'][2] == settled

    # Recovery of an admitted unavailable owner still comes from the live merge.
    row.update(nativeAvailable=False, historyReadOnlyReason='Files were unavailable')
    app._save_changes(sessions={row['id']})
    await app.history.refresh()
    assert row['nativeAvailable'] is True
    assert row.get('historyReadOnlyReason') is None
    assert row['navigationActivityAt'] == 1
