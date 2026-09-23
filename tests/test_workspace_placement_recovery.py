"""Recover after allocation commits but before workspace registration commits."""
import asyncio
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService


COMMAND = 'crashed-workspace-create'
CRASH = r'''
import asyncio, json, os, sys
from pathlib import Path
from amplifier_web.service import AppService
from amplifier_web import workspace_canvas

async def main():
    home, original, root = map(Path, sys.argv[1:])
    app = AppService(home, workspace=original)
    plan = (await app.dispatch('workspace.prepare', {'name': 'Recovery demo', 'root': str(root)}))['result']
    (home / 'recovery-plan.json').write_text(json.dumps(plan))
    def crash_before_registration(state, action, args):
        assert action == 'workspace.add' and args['path'] == plan['path']
        os._exit(86)
    workspace_canvas.workspace_command = crash_before_registration
    await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='crashed-workspace-create')
    raise AssertionError('Did not reach the crash window')

asyncio.run(main())
'''


def placement_files(home):
    return {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (home / 'workspace-placement').glob('*.json')}


@pytest.fixture
async def crashed(tmp_path):
    home, original, root = tmp_path / 'app', tmp_path / 'original', tmp_path / 'workspaces'
    original.mkdir()
    result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', CRASH, str(home), str(original), str(root)],
                                     capture_output=True, text=True, timeout=30)
    assert result.returncode == 86, result.stderr
    plan = json.loads((home / 'recovery-plan.json').read_text())
    receipt_path = home / 'workspace-placement' / ('receipt-' + hashlib.sha256(COMMAND.encode()).hexdigest() + '.json')
    receipt = json.loads(receipt_path.read_text())
    assert receipt['outcome'] == 'created' and receipt['planId'] == plan['planId']
    created = Path(plan['path'])
    info = created.stat()
    assert receipt['directoryIdentity'] == [info.st_dev, info.st_ino]
    app = AppService(home, workspace=original)
    assert app.db.execute('SELECT 1 FROM commands WHERE id=?', (COMMAND,)).fetchone() is None
    assert all(row['path'] != plan['path'] for row in app.state['workspaces'])
    yield app, plan
    await app.close()


@pytest.mark.parametrize('retry_command', [COMMAND, 'new-transport'])
@pytest.mark.parametrize('replacement', ['directory', 'symlink', 'missing', 'parent-symlink'])
async def test_crash_recovery_refuses_changed_allocation(crashed, tmp_path, retry_command, replacement):
    app, plan = crashed
    created = Path(plan['path'])
    if replacement == 'parent-symlink':
        saved_root = tmp_path / 'saved-root'
        created.parent.rename(saved_root)
        created.parent.symlink_to(saved_root, target_is_directory=True)
    else:
        created.rename(tmp_path / 'saved-allocation')
        if replacement == 'directory':
            created.mkdir()
        elif replacement == 'symlink':
            elsewhere = tmp_path / 'elsewhere'; elsewhere.mkdir()
            created.symlink_to(elsewhere, target_is_directory=True)
    before_files = placement_files(app.data_dir)
    before_workspaces = copy.deepcopy(app.state['workspaces'])
    selected = app.state['selectedWorkspaceId']
    commands = app.db.execute('SELECT * FROM commands ORDER BY id').fetchall()
    with pytest.raises(AppError, match='created folder changed'):
        await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id=retry_command)
    assert app.state['workspaces'] == before_workspaces and app.state['selectedWorkspaceId'] == selected
    assert app.db.execute('SELECT * FROM commands ORDER BY id').fetchall() == commands
    assert placement_files(app.data_dir) == before_files
    assert not app.state['sessions']
    if created.exists():
        assert not list(created.iterdir())


async def test_same_command_crash_recovery_registers_unchanged_allocation_once(crashed):
    app, plan = crashed
    before = placement_files(app.data_dir)
    result = await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id=COMMAND)
    assert result['result']['path'] == plan['path'] and result['result']['outcome'] == 'created'
    assert len([row for row in app.state['workspaces'] if row['path'] == plan['path']]) == 1
    assert placement_files(app.data_dir) == before
    duplicate = await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id=COMMAND)
    assert duplicate['duplicate'] and duplicate['result'] == result['result']
    assert placement_files(app.data_dir) == before


async def test_committed_duplicate_returns_receipt_without_recovering_allocation(crashed, tmp_path, monkeypatch):
    from amplifier_web import workspace_placement
    app, plan = crashed
    result = await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id=COMMAND)
    created = Path(plan['path']); created.rename(tmp_path / 'saved-allocation')
    elsewhere = tmp_path / 'elsewhere'; elsewhere.mkdir()
    created.symlink_to(elsewhere, target_is_directory=True)
    before = placement_files(app.data_dir)
    workspaces = copy.deepcopy(app.state['workspaces'])
    commands = app.db.execute('SELECT * FROM commands ORDER BY id').fetchall()
    def must_not_recover(*args, **kwargs):
        raise AssertionError('A committed duplicate must not repeat placement or registration')
    monkeypatch.setattr(workspace_placement, 'create', must_not_recover)
    duplicate = await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id=COMMAND)
    assert duplicate['duplicate'] and duplicate['result'] == result['result']
    assert app.state['workspaces'] == workspaces
    assert app.db.execute('SELECT * FROM commands ORDER BY id').fetchall() == commands
    assert placement_files(app.data_dir) == before and not list(elsewhere.iterdir())
