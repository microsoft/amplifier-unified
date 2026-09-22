"""Discovery is truthful, bounded and passive across the shared action path."""
import asyncio
from importlib import metadata
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import artifact_runtime as inventory
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppService, AppError


def executable(path, content):
    path.write_text(f'#!{sys.executable}\n' + content)
    path.chmod(0o700)
    return path


async def test_discovery_reports_exact_interpreter_missing_packages_and_optional_tools(monkeypatch, tmp_path):
    def package(name):
        if name == 'openpyxl':
            return SimpleNamespace(version='3.1.5', locate_file=lambda value: tmp_path)
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(inventory.metadata, 'distribution', package)
    monkeypatch.setattr(inventory, 'EXECUTABLES', {})
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-not-to-report')
    result = await inventory.discover('fixture')
    assert result['python']['path'] == sys.executable
    assert result['scope'] == 'fixture'
    assert result['python']['packages'][2] == {'name': 'openpyxl', 'status': 'installed',
        'version': '3.1.5', 'location': str(tmp_path), 'importVerified': False}
    assert result['python']['packages'][0]['status'] == 'missing'
    assert set(result['validation'].values()) == {'not_run'}
    assert 'secret-not-to-report' not in json.dumps(result)


@pytest.mark.parametrize('product', ['LibreOffice', 'LibreOfficeDev'])
async def test_explicit_override_has_precedence_and_invalid_override_does_not_fall_back(monkeypatch, tmp_path, product):
    tool = executable(tmp_path / 'office', f'print("{product} 26.2.1.2")\n')
    monkeypatch.setenv('WORK_SOFFICE', str(tool))
    spec = inventory.EXECUTABLES['libreoffice']
    result = await inventory._version(inventory._executable(spec), spec)
    assert result['source'] == 'WORK_SOFFICE'
    assert result['version'] == '26.2.1.2'
    monkeypatch.setenv('WORK_SOFFICE', 'relative/office')
    assert inventory._executable(spec)['status'] == 'invalid'


def test_relative_and_cwd_path_entries_are_never_executed(monkeypatch, tmp_path):
    executable(tmp_path / 'node', 'raise SystemExit("must not run")\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PATH', os.pathsep.join(('', '.', str(tmp_path))))
    assert inventory._executable(inventory.EXECUTABLES['node'])['status'] == 'missing'


@pytest.mark.parametrize('body', [
    'import time; time.sleep(30)\n',
    'import sys; sys.stdout.write("x"*10000); sys.stdout.flush()\n',
    'print("not a recognizable version secret-not-to-report")\n',
])
async def test_unresponsive_or_invalid_version_is_bounded_and_not_echoed(tmp_path, body):
    path = executable(tmp_path / 'node', body)
    result = await asyncio.wait_for(inventory._version({'status': 'available', 'path': str(path)},
        inventory.EXECUTABLES['node']), 5)
    assert result['versionStatus'] == 'unknown'
    assert 'secret-not-to-report' not in str(result)


async def test_worker_discovery_does_not_acquire_execution_ownership(monkeypatch):
    from amplifier_web import runtime_worker
    publications = []
    monkeypatch.setattr(runtime_worker, 'publish', publications.append)
    monkeypatch.setattr(inventory, 'discover', AsyncMock(return_value={'scope': 'worker'}))
    worker = Worker()
    worker.acquire_for_mutation = AsyncMock(side_effect=AssertionError('Must not acquire'))
    await worker.command({'op': 'dependencies', 'id': 'read-1'})
    assert publications == [{'op': 'reply', 'id': 'read-1', 'result': {'scope': 'worker'}}]


async def test_runtime_discovery_never_starts_an_absent_worker():
    manager = RuntimeManager()
    manager._start_locked = AsyncMock(side_effect=AssertionError('Must not start'))
    manager._retired['old'] = ({'id': 'old'}, None)
    result = await manager.dependencies('old')
    assert result['status'] == 'unavailable'
    assert not manager.workers


async def test_existing_worker_inventory_round_trip_does_not_unpark(tmp_path):
    fixture = tmp_path / 'inventory_worker.py'
    fixture.write_text('''import asyncio,json,sys
from amplifier_web.artifact_runtime import discover
for line in sys.stdin:
    data=json.loads(line)
    if data['op']=='start':
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
    elif data['op']=='dependencies':
        print(json.dumps({'op':'reply','id':data['id'],'result':asyncio.run(discover('worker'))}),flush=True)
    elif data['op']=='stop':
        break
''')
    async def emit(*args):
        pass
    manager = RuntimeManager(command=[sys.executable, str(fixture)], startup_timeout=5)
    try:
        await manager.start({'id': 'running', 'workspace': str(tmp_path)}, emit)
        manager.workers['running']['parked'] = True
        result = await manager.dependencies('running')
        assert result['scope'] == 'worker'
        assert result['python']['path'] == sys.executable
        assert manager.workers['running']['parked'] is True
    finally:
        await manager.close()


async def test_shared_ui_agent_action_is_passive_and_targets_calling_session(monkeypatch, tmp_path):
    async def discover(scope):
        return {'scope': scope, 'python': {'path': sys.executable}}
    monkeypatch.setattr(inventory, 'discover', discover)
    app = AppService(tmp_path / 'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'Caller'})
        caller = app._session()['id']
        await app.dispatch('session.create', {'title': 'Selected'})
        selected = app._session()['id']
        await app.dispatch('view.update', {'patch': {'draft': 'unsent words'}})
        revision = app.state['revision']
        events = list(app.state['events'])
        result = await app.app_bridge('dispatch', {'action': 'runtime.dependencies'}, caller)
        assert result['result']['host']['python']['path'] == sys.executable
        assert result['result']['worker']['sessionId'] == caller
        assert app.state['selectedSessionId'] == selected
        assert app.state['view']['draft'] == 'unsent words'
        assert app.state['revision'] == revision
        assert app.state['events'] == events
        ui = await app.dispatch('runtime.dependencies', {'sessionId': caller})
        assert ui['result'] == result['result']
        assert any(row['name'] == 'runtime.dependencies' for row in app.get_actions())
        with pytest.raises(AppError):
            await app.dispatch('runtime.dependencies', {'sessionId': 'missing'})
    finally:
        await app.close()


async def test_explicit_import_probe_uses_clean_child_and_truthful_results(monkeypatch, tmp_path):
    # Metadata alone must not establish readiness; workspace lookalikes are ignored.
    (tmp_path / 'artifact_shadow.py').write_text('raise RuntimeError("workspace secret")')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    monkeypatch.setattr(inventory, 'ARTIFACT_IMPORTS', {'good': 'json', 'shadow': 'artifact_shadow'})
    rows = [{'name': 'good', 'status': 'installed'}, {'name': 'shadow', 'status': 'installed'}]
    assert await inventory.verify_imports(rows) == 'failed'
    assert rows[0]['importVerified'] is True
    assert rows[1]['importVerified'] is False
    assert rows[1]['importStatus'] == 'failed'
    assert 'workspace secret' not in str(rows)


async def test_explicit_probe_only_changes_import_validation(monkeypatch):
    monkeypatch.setattr(inventory, 'EXECUTABLES', {})
    monkeypatch.setattr(inventory, 'verify_imports', AsyncMock(return_value='passed'))
    result = await inventory.discover('worker', verify=True)
    assert result['validation'] == {'imports': 'passed', 'rendering': 'not_run',
        'visualReview': 'not_run', 'spreadsheetRecalculation': 'not_run'}
    inventory.verify_imports.assert_awaited_once()


async def test_worker_and_shared_action_forward_explicit_import_probe(monkeypatch, tmp_path):
    from amplifier_web import runtime_worker
    publications = []
    monkeypatch.setattr(runtime_worker, 'publish', publications.append)
    probe = AsyncMock(return_value={'scope': 'worker'})
    monkeypatch.setattr(inventory, 'discover', probe)
    await Worker().command({'op': 'dependencies', 'id': 'verified', 'verifyImports': True})
    probe.assert_awaited_once_with('worker', verify=True)
    app = AppService(tmp_path / 'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'Import probe'})
        sid = app._session()['id']
        app.runtime = SimpleNamespace(dependencies=AsyncMock(return_value={'scope': 'worker'}),
            close=AsyncMock(), workers={})
        await app.app_bridge('dispatch', {'action': 'runtime.dependencies', 'args': {'verifyImports': True}}, sid)
        probe.assert_awaited_with('host', verify=True)
        app.runtime.dependencies.assert_awaited_once_with(sid, verify=True)
    finally:
        await app.close()


def test_worker_manifest_provisions_public_authoring_packages():
    from pathlib import Path
    import tomllib
    manifest = tomllib.loads((Path(inventory.__file__).parent / 'runtime_deps/pyproject.toml').read_text())
    requirements = manifest['project']['dependencies']
    assert all(any(spec.startswith(name + '>=') for spec in requirements) for name in inventory.ARTIFACT_IMPORTS)
