"""Host actions retain native history and workspace side effects."""
import uuid

import pytest


def test_selection_boundary_preserves_absent_path():
    from amplifier_web.workspace_selection import workspace_command

    # Full host rows carry a path key; the metadata interpretation also accepts
    # an absent key without widening the browser projection's schema here.
    original = {'id': 'original', 'name': 'Original', 'path': '/missing/original'}
    unresolved = {'id': 'unresolved', 'name': 'Offline'}
    state = {'workspaces': [unresolved, original], 'sessions': [],
             'selectedWorkspaceId': original['id'], 'settings': {'workspace': original['path']}}
    workspace_command(state, 'workspace.select', {'id': unresolved['id']})
    assert state['selectedWorkspaceId'] == unresolved['id']
    assert state['settings']['workspace'] == original['path']
    assert 'path' not in unresolved


@pytest.mark.parametrize('path_fields', [{'path': None}, {'path': ''}], ids=['none', 'empty'])
async def test_selection_preserves_known_and_unresolved_locations(tmp_path, path_fields):
    from amplifier_web.service import AppService

    initial = tmp_path / 'initial'; initial.mkdir()
    service = AppService(tmp_path / 'data', workspace=initial)
    try:
        first = service.state['workspaces'][0]
        unresolved = {'id': 'unresolved', 'name': 'Offline', **path_fields}
        # The host must retain this exact stored spelling, even while unavailable.
        raw_path = str(tmp_path / 'missing') + '/../still-missing'
        custom = {'locations': [{'path': 'opaque-custom-value'}], 'aliases': ['Reports']}
        known = {'id': 'stable-workspace', 'name': 'Reports', 'path': raw_path, **custom}
        service.state['workspaces'][:] = [unresolved, first, known]
        service.state['pinnedWorkspaceIds'] = [known['id'], first['id'], unresolved['id']]
        service.state['pinnedItemIds'] = [f'workspace:{row["id"]}' for row in [unresolved, known, first]]

        service.state['canvas']['open'] = True
        await service.dispatch('workspace.select', {'id': known['id']})
        assert service.state['selectedWorkspaceId'] == known['id']
        assert service.state['settings']['workspace'] == raw_path
        assert known['available'] is False
        assert service.state['canvas']['open'] is False
        assert known['locations'] is custom['locations'] and known['aliases'] is custom['aliases']

        await service.dispatch('workspace.select', {'id': unresolved['id']})
        assert service.state['selectedWorkspaceId'] == unresolved['id']
        assert service.state['settings']['workspace'] == raw_path
        assert all(unresolved.get(key) == value for key, value in path_fields.items())
        assert ('path' in unresolved) == ('path' in path_fields)

        await service.dispatch('workspace.select', {'id': known['id']})
        await service.dispatch('workspace.remove', {'id': known['id']})
        assert service.state['selectedWorkspaceId'] == unresolved['id']
        assert service.state['settings']['workspace'] == raw_path
        assert service.state['pinnedWorkspaceIds'] == [first['id'], unresolved['id']]
        assert service.state['pinnedItemIds'] == [f'workspace:{unresolved["id"]}', f'workspace:{first["id"]}']
        assert service.state['workspaces'][0] is unresolved
    finally:
        await service.close()


async def test_chat_navigation_preserves_saved_identity_and_execution_folder(tmp_path):
    from copy import deepcopy

    from amplifier_web.service import AppService

    initial = tmp_path / 'initial'; initial.mkdir()
    service = AppService(tmp_path / 'data', workspace=initial)
    try:
        await service.dispatch('session.create', {'title': 'Saved conversation'})
        sid = service.state['selectedSessionId']
        session = service._session(sid)
        session['workspaceId'] = 'stable-existing-id'
        session['workers'] = [{'id': 'worker', 'workspace': str(initial), 'cwd': str(initial)}]
        saved = deepcopy({key: session.get(key) for key in
                          ('id', 'workspaceId', 'workspace', 'runtimeSessionId', 'nativeProject', 'workers', 'messages')})
        raw_path = str(tmp_path / 'unavailable') + '/./saved-location'
        row = {'id': session['workspaceId'], 'name': 'Saved workspace', 'path': raw_path,
               'locations': {'custom': 'not interpreted'}}
        service.state['workspaces'].append(row)
        await service.dispatch('session.create', {'title': 'Other conversation'})

        await service.dispatch('session.select', {'id': sid})
        assert service.state['selectedSessionId'] == sid
        assert service.state['selectedWorkspaceId'] == row['id']
        assert service.state['settings']['workspace'] == raw_path
        assert row['available'] is False
        assert {key: service._session(sid).get(key) for key in saved} == saved
        assert row['locations'] == {'custom': 'not interpreted'}
    finally:
        await service.close()

    restored = AppService(tmp_path / 'data')
    try:
        assert {key: restored._session(sid).get(key) for key in saved} == saved
        assert any(item['id'] == row['id'] and item['path'] == raw_path
                   for item in restored.state['workspaces'])
    finally:
        await restored.close()


async def test_host_manual_actions_and_native_observation_preserve_history(tmp_path):
    import asyncio
    import json

    from amplifier_web.service import AppError, AppService
    from amplifier_web.session_files import amplifier_home, project_slug

    initial = tmp_path / 'initial'; initial.mkdir()
    folder = tmp_path / 'reports'; folder.mkdir()
    native_id = str(uuid.uuid4())
    native = amplifier_home() / 'projects' / project_slug(folder) / 'sessions' / native_id
    native.mkdir(parents=True)
    (native / 'metadata.json').write_text(json.dumps({'session_id': native_id, 'working_dir': str(folder),
                                                    'name': 'Native conversation', 'turn_count': 1}))
    transcript = native / 'transcript.jsonl'
    original = json.dumps({'role': 'user', 'content': 'Retained native history'}) + '\n'
    transcript.write_text(original)
    service = AppService(tmp_path / 'data', workspace=initial)
    try:
        first = service.state['workspaces'][0]
        for action, args, message in [
            ('workspace.rename', {'id': 'missing', 'name': '   '}, 'This workspace is no longer registered.'),
            ('workspace.rename', {'id': first['id'], 'name': '   '}, 'Enter a workspace name.'),
            ('workspace.remove', {'id': first['id']}, 'Keep at least one workspace registered.'),
        ]:
            with pytest.raises(AppError) as exc:
                await service.dispatch(action, args, origin='agent')
            assert exc.value.status == 400 and str(exc.value) == message

        await service.dispatch('workspace.add', {'path': str(folder), 'name': 'Reports'}, origin='agent')
        await asyncio.gather(*list(service.tasks))
        identity = service.state['selectedWorkspaceId']
        await service.dispatch('workspace.rename', {'id': identity, 'name': 'Weekly reports'})
        await service.dispatch('workspace.archive', {'id': identity})
        await service.history.refresh(force=True)
        row = next(row for row in service.state['workspaces'] if row['id'] == identity)
        assert row['name'] == 'Weekly reports' and row['path'] == str(folder) and row['archived']
        assert service.directory.resolve(native_id, project_slug(folder))['nativeIdentity'] == native_id
        await service.dispatch('workspace.restore', {'id': identity})
        await service.dispatch('workspace.add', {'path': str(folder / '.')})
        row = next(row for row in service.state['workspaces'] if row['id'] == identity)
        assert row['name'] == 'Weekly reports' and row['archived'] is False
        await service.dispatch('workspace.remove', {'id': identity})
        assert service.state['settings']['workspace'] == str(initial)
        assert transcript.read_text() == original
    finally:
        await service.close()

    restored = AppService(tmp_path / 'data')
    try:
        await restored.history.refresh(force=True)
        assert all(row['id'] != identity for row in restored.state['workspaces'])
        assert restored.state['settings']['workspace'] == str(initial)
        assert transcript.read_text() == original
    finally:
        await restored.close()
