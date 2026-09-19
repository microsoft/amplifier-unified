from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.session_navigation import is_top_level


@pytest.fixture
async def service(tmp_path):
    initial = tmp_path / 'initial'
    initial.mkdir()
    app = AppService(tmp_path / 'data', workspace=initial)
    yield app
    await app.close()


async def test_create_workspace_makes_parents_and_an_idle_root_chat(service, tmp_path):
    folder = tmp_path / 'new' / 'nested' / 'project'
    result = await service.dispatch('workspace.create', {'path': str(folder), 'name': 'My project'}, origin='agent')
    state = result['state']
    workspace = next(w for w in state['workspaces'] if w['id'] == state['selectedWorkspaceId'])
    chat = service._session()

    assert folder.is_dir()
    assert workspace['path'] == str(folder)
    assert workspace['name'] == 'My project'
    assert workspace['available'] is True
    assert chat['workspace'] == str(folder)
    assert is_top_level(chat)
    assert chat['messages'] == [] and chat['status'] == 'idle'
    assert not chat.get('runtimeSessionId')
    assert not service.tasks  # Creating a workspace cannot start a model turn.
    explorer_row = next(row for row in state['workspaceExplorer']['rows'] if row['workspaceId'] == workspace['id'])
    assert explorer_row['chatCount'] == 1 and explorer_row['canBrowse'] is False

    restored = AppService(service.data_dir)
    try:
        assert restored.state['selectedWorkspaceId'] == workspace['id']
        assert restored._session()['id'] == chat['id']
    finally:
        await restored.close()


async def test_create_existing_workspace_reuses_root_chat_and_preserves_contents(service, tmp_path):
    folder = tmp_path / 'existing'
    folder.mkdir()
    (folder / 'important.txt').write_text('Keep me')
    await service.dispatch('workspace.create', {'path': str(folder)}, command_id='first-create')
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


async def test_workspace_with_only_a_worker_gets_its_first_root_chat(service, tmp_path):
    folder = tmp_path / 'workers-only'
    folder.mkdir()
    await service.dispatch('workspace.add', {'path': str(folder)})
    worker = service._new_session({})
    worker.update(sessionKind='worker', nativeParentId='missing-parent')
    service.state['sessions'].append(worker)

    await service.dispatch('workspace.create', {'path': str(folder)}, origin='agent')

    assert service._session()['id'] != worker['id']
    assert is_top_level(service._session())
    assert len(service.state['sessions']) == 2
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
    assert service._session()['workspace'] == str(folder)


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
