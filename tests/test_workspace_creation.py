from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService


@pytest.fixture
async def service(tmp_path):
    initial = tmp_path / 'initial'
    initial.mkdir()
    app = AppService(tmp_path / 'data', workspace=initial)
    yield app
    await app.close()


async def test_create_workspace_makes_parents_and_an_uncommitted_draft(service, tmp_path):
    folder = tmp_path / 'new' / 'nested' / 'project'
    result = await service.dispatch('workspace.create', {'path': str(folder), 'name': 'My project'}, origin='agent')
    state = result['state']
    workspace = next(w for w in state['workspaces'] if w['id'] == state['selectedWorkspaceId'])

    assert folder.is_dir()
    assert workspace['path'] == str(folder)
    assert workspace['name'] == 'My project'
    assert workspace['available'] is True
    assert state['selectedSessionId'] is None
    assert state['sessions'] == []
    assert state['view']['newSessionDraft']['workspace'] == str(folder)
    assert not service.tasks  # Creating a workspace cannot start a model turn.
    assert state['workspaceExplorer']['totalWorkspaces'] == 2  # Initial and new empty registrations.
    assert state['workspaceExplorer']['selected']['chatCount'] == 0  # Drafts are not saved chats.

    await service.close()
    restored = AppService(service.data_dir)
    try:
        assert restored.state['selectedWorkspaceId'] == workspace['id']
        assert restored.state['selectedSessionId'] is None
        assert restored.state['view']['newSessionDraft']['workspace'] == str(folder)
    finally:
        await restored.close()


async def test_create_existing_workspace_reuses_root_chat_and_preserves_contents(service, tmp_path):
    folder = tmp_path / 'existing'
    folder.mkdir()
    (folder / 'important.txt').write_text('Keep me')
    await service.dispatch('workspace.create', {'path': str(folder)}, command_id='first-create')
    await service.dispatch('session.create', {})
    first_chat = service._session()
    service._message(first_chat, 'user', 'Saved conversation')
    worker = {**first_chat, 'id': 'worker', 'sessionKind': 'worker', 'nativeParentId': first_chat['id'], 'messages': []}
    service.state['sessions'].insert(0, worker)
    service.state['selectedSessionId'] = worker['id']

    await service.dispatch('workspace.create', {'path': str(folder / '.'), 'name': 'Existing'}, command_id='second-create')
    duplicate = await service.dispatch('workspace.create', {'path': str(folder / '.'), 'name': 'Existing'}, command_id='second-create')

    assert service._session()['id'] == first_chat['id']
    assert len(service.state['sessions']) == 2
    assert duplicate['duplicate'] is True
    assert len([w for w in service.state['workspaces'] if w['path'] == str(folder)]) == 1
    assert service._session()['messages'][0]['text'] == 'Saved conversation'
    assert (folder / 'important.txt').read_text() == 'Keep me'


async def test_workspace_with_only_a_worker_opens_an_uncommitted_draft(service, tmp_path):
    folder = tmp_path / 'workers-only'
    folder.mkdir()
    await service.dispatch('workspace.add', {'path': str(folder)})
    worker = service._new_session({})
    worker.update(sessionKind='worker', nativeParentId='missing-parent')
    service.state['sessions'].append(worker)

    await service.dispatch('workspace.create', {'path': str(folder)}, origin='agent')

    assert service.state['selectedSessionId'] is None
    assert service.state['view']['newSessionDraft']['workspace'] == str(folder)
    assert len(service.state['sessions']) == 1
    assert worker in service.state['sessions']


async def test_create_restores_a_missing_registered_folder(service, tmp_path):
    folder = tmp_path / 'restore-me'
    folder.mkdir()
    await service.dispatch('workspace.add', {'path': str(folder), 'name': 'Remembered'})
    identity = service.state['selectedWorkspaceId']
    folder.rmdir()

    await service.dispatch('workspace.create', {'path': str(folder)})

    workspace = next(w for w in service.state['workspaces'] if w['id'] == identity)
    assert workspace['available'] is True
    assert workspace['name'] == 'Remembered'
    assert service.state['view']['newSessionDraft']['workspace'] == str(folder)


@pytest.mark.parametrize('invalid', ['blank', 'file', 'parent-file', 'null-byte'])
async def test_invalid_workspace_path_does_not_change_registration_or_chats(service, tmp_path, invalid):
    file = tmp_path / 'existing-file'
    file.write_text('Keep me')
    path = {'blank': '   ', 'file': str(file), 'parent-file': str(file / 'project'), 'null-byte': str(tmp_path / 'bad\x00path')}[invalid]
    before = service.get_state()

    with pytest.raises(AppError):
        await service.dispatch('workspace.create', {'path': path})

    assert service.state['workspaces'] == before['workspaces']
    assert service.state['sessions'] == before['sessions']
    assert service.state['selectedWorkspaceId'] == before['selectedWorkspaceId']
    assert file.read_text() == 'Keep me'


async def test_failed_creation_does_not_remove_new_parent_folders(service, tmp_path, monkeypatch):
    parent = tmp_path / 'partly-created'
    folder = parent / 'project'
    original_mkdir = Path.mkdir

    def fail_after_parent(path, *args, **kwargs):
        if path == folder:
            original_mkdir(parent)
            (parent / 'another-process.txt').write_text('Keep me')
            raise PermissionError('Permission denied')
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'mkdir', fail_after_parent)
    with pytest.raises(AppError, match='Could not create the workspace folder'):
        await service.dispatch('workspace.create', {'path': str(folder)})

    assert (parent / 'another-process.txt').read_text() == 'Keep me'
    assert len(service.state['workspaces']) == 1
    assert service.state['sessions'] == []
