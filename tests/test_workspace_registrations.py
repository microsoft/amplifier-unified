"""Host actions retain native history and workspace side effects."""
import uuid

import pytest


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
