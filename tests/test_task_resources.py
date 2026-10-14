"""Real local allocation, retained ownership and ordinary route authority."""
import asyncio
from concurrent.futures import ProcessPoolExecutor
import copy
import json
from pathlib import Path
import sqlite3
import threading
from unittest.mock import AsyncMock

import pytest

from amplifier_web import task_resources as owner
from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action
from test_coordination_generation import agent


def allocation(home, command='new', kind='scratch-directory', sid='root'):
    home.mkdir(parents=True, exist_ok=True)
    row, fresh = owner.prepare(home, 'workspace', sid, {'sessionId': sid, 'principal': sid}, {'kind':kind}, command)
    if fresh:
        owner.allocate(home, row)
    return owner.read(home, sid, {'id':row['id']})['items'][0]


def args(app, kind='scratch-directory'):
    source = app.state['sessions'][0]
    workspace = next(row for row in app.state['workspaces'] if row['path'] == source['workspace'])
    return {'workspaceId':workspace['id'], 'sessionId':source['id'], 'kind':kind}


@pytest.mark.parametrize('kind', owner.KINDS)
def test_real_allocation_is_private_owned_and_retry_preserves_contents(tmp_path, kind):
    row = allocation(tmp_path / 'home', kind=kind)
    assert row['status'] == 'ready' and row['observation'] == 'present'
    path = Path(row['path'])
    assert path.exists() and not row['fileAccessChanged']
    assert path.stat().st_mode & 0o077 == 0
    if kind == 'sqlite-database':
        with sqlite3.connect(path) as db:
            assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
            db.execute('CREATE TABLE evidence(value TEXT)')
            db.execute('INSERT INTO evidence VALUES(?)', ('retained',))
    else:
        (path / 'unknown.txt').write_text('retained')
    again = allocation(tmp_path / 'home', kind=kind)
    assert again['id'] == row['id'] and again['path'] == row['path']
    retired = owner.retire(tmp_path / 'home', 'root', row['owner'], {'id':row['id'], 'expectedRevision':row['revision']}, 'retire')
    assert retired['status'] == 'retired'
    assert allocation(tmp_path / 'home', kind=kind)['status'] == 'retired'
    if kind == 'sqlite-database':
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT value FROM evidence').fetchone() == ('retained',)
    else:
        assert (path / 'unknown.txt').read_text() == 'retained'


def test_independent_processes_share_one_command_and_distinct_owners(tmp_path):
    home = tmp_path / 'home'
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(allocation, home) for _ in range(8)]
        rows = [future.result() for future in futures]
    assert len({row['id'] for row in rows}) == 1
    assert owner.read(home, 'root', {})['items'][0]['status'] == 'ready'
    second = allocation(home, command='peer-command', sid='peer')
    assert second['directory'] != rows[0]['directory']
    with pytest.raises(ValueError, match='different arguments'):
        allocation(home, sid='peer')
    with pytest.raises(ValueError, match='owned'):
        owner.read(home, 'peer', {'id':rows[0]['id']})


@pytest.mark.parametrize('replacement', ['missing', 'directory', 'symlink'])
def test_missing_replaced_and_symlinked_resources_are_never_recreated(tmp_path, replacement):
    home = tmp_path / 'home'
    row = allocation(home)
    path = Path(row['path'])
    path.rename(path.with_name('saved'))
    if replacement == 'directory':
        path.mkdir()
    elif replacement == 'symlink':
        path.symlink_to(tmp_path)
    read = allocation(home)
    assert read['observation'] in {'missing', 'changed-or-unavailable'}
    assert read['id'] == row['id']
    assert not (tmp_path / 'database.sqlite3').exists()


def test_preexisting_collision_and_parent_symlink_preserve_foreign_bytes(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    row, _ = owner.prepare(home, 'workspace', 'root', {'sessionId':'root'}, {'kind':'scratch-directory'}, 'collision')
    path = owner.directory(home, row)
    path.mkdir(parents=True)
    (path/'foreign').write_text('keep')
    with pytest.raises(FileExistsError):
        owner.allocate(home, row)
    assert owner.read(home, 'root', {})['items'][0]['status'] == 'unknown'
    assert (path/'foreign').read_text() == 'keep'
    second, _ = owner.prepare(home, 'workspace', 'peer', {'sessionId':'peer'}, {'kind':'scratch-directory'}, 'symlink')
    owner.directory(home, second).parent.symlink_to(tmp_path)
    with pytest.raises(OSError):
        owner.allocate(home, second)
    assert not (tmp_path/second['id']).exists()


def test_receipt_failure_precedes_physical_effect_and_caps_are_bounded(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    with owner.connect(home) as db:
        db.execute("CREATE TRIGGER receipt_failure BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        allocation(home)
    assert not (home/'workspace-resources'/'owned').exists()
    with owner.connect(home) as db:
        db.execute('DROP TRIGGER receipt_failure')
    monkeypatch.setattr(owner, 'MAX_ROOT', 1)
    row = allocation(home)
    with pytest.raises(ValueError, match='capacity'):
        allocation(home, 'second')
    with pytest.raises(ValueError, match='revision'):
        owner.retire(home, 'root', row['owner'], {'id':row['id'], 'expectedRevision':99}, 'bad-retire')
    owner.retire(home, 'root', row['owner'], {'id':row['id'], 'expectedRevision':row['revision']}, 'retire')
    assert allocation(home, 'second')['status'] == 'ready'


def test_effect_failure_and_restarted_owner_keep_uncertain_files_without_replay(tmp_path, monkeypatch):
    home = tmp_path/'home'
    home.mkdir()
    row, _ = owner.prepare(home, 'workspace', 'root', {'sessionId':'root'}, {'kind':'sqlite-database'}, 'interrupted')
    original = owner.observe
    monkeypatch.setattr(owner, 'observe', lambda *a: {'observation':'changed-or-unavailable'})
    with pytest.raises(ValueError, match='changed'):
        owner.allocate(home, row)
    monkeypatch.setattr(owner, 'observe', original)
    retained = owner.read(home, 'root', {})['items'][0]
    assert retained['status'] == 'unknown' and Path(retained['path']).exists()
    again, fresh = owner.prepare(home, 'workspace', 'root', {'sessionId':'root'}, {'kind':'sqlite-database'}, 'interrupted')
    assert not fresh and again['status'] == 'unknown'


async def test_ui_agent_routes_allocate_real_storage_without_policy_changes_or_model_input(app, monkeypatch):
    source = app.state['sessions'][0]
    values = args(app, 'sqlite-database')
    settings = copy.deepcopy(app.state.get('settings'))
    result = await agent_action(app, source, 'workspace.resources.provision', values, 'agent-resource')
    row = result['result']['items'][0]
    assert Path(row['path']).is_file() and row['access']['status'] == 'unknown'
    assert app.state.get('settings') == settings
    assert not app.runtime.inputs
    read_values = {key:value for key,value in values.items() if key != 'kind'}
    monkeypatch.setattr(app.cold_display, 'hydrate', lambda *a: pytest.fail('No history hydration'))
    assert (await app.dispatch('workspace.resources.read', read_values))['result']['items'][0]['id'] == row['id']
    assert (await agent_action(app, source, 'workspace.resources.read', read_values, 'read-resource'))['result']['items'][0]['id'] == row['id']
    retired = await app.dispatch('workspace.resources.retire', {**read_values, 'id':row['id'], 'expectedRevision':row['revision']}, command_id='retire-resource')
    assert retired['result']['items'][0]['status'] == 'retired'


@pytest.mark.parametrize('denial', ['peer', 'child', 'stale', 'missing', 'stopped-assignment'])
async def test_agent_resource_authority_cannot_be_forged(app, denial):
    source, peer = app.state['sessions']
    values, transport = args(app), {}
    if denial == 'peer':
        values['sessionId'] = peer['id']
    elif denial == 'child':
        transport['_runtimeSessionId'] = 'native-child'
    elif denial == 'stale':
        transport['_generationId'] = 'old'
    elif denial == 'missing':
        transport['_generationId'] = None
    else:
        assignment = app.assignments.ensure(source, 'start')
        assignment['status'] = 'stopped'
        app.assignments.save(assignment)
        app.db.commit()
    with pytest.raises(AppError):
        await agent(app, source, 'workspace.resources.provision', values, 'denied-resource', **transport)
    assert not (app.data_dir/'workspace-resources'/'owned').exists()


@pytest.mark.parametrize('change', ['stop', 'move', 'generation'])
async def test_resource_admission_rechecks_after_intent_io(app, monkeypatch, change):
    source = app.state['sessions'][0]
    original = owner.prepare
    def prepare(*a):
        result = original(*a)
        if change == 'stop':
            source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
        elif change == 'move':
            source['workspace'] += '-moved'
        else:
            source['collaborationGeneration'] = {'id':'new','inputIds':['new'],'terminal':False}
        return result
    monkeypatch.setattr(owner, 'prepare', prepare)
    with pytest.raises(AppError):
        await agent_action(app, source, 'workspace.resources.provision', args(app), 'racing-resource')
    assert not (app.data_dir/'workspace-resources'/'owned').exists()
    row = owner.read(app.data_dir, source['id'], {})['items'][0]
    assert row['status'] == 'suppressed'


async def test_cancelled_wait_retains_admitted_effect_until_thread_finishes(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = owner.open_parent
    def pause(*a, **kw):
        if kw.get('create'):
            entered.set()
            assert release.wait(5)
        return original(*a, **kw)
    monkeypatch.setattr(owner, 'open_parent', pause)
    task = asyncio.create_task(app.dispatch('workspace.resources.provision', args(app), command_id='cancelled'))
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = owner.read(app.data_dir, app.state['sessions'][0]['id'], {})['items'][0]
    assert row['status'] == 'ready' and Path(row['path']).is_dir()


@pytest.mark.parametrize('action', ['workspace.resources.read', 'workspace.resources.reservations'])
async def test_resource_reads_recheck_workspace_after_io(app, monkeypatch, action):
    source = app.state['sessions'][0]
    values = {key:value for key,value in args(app).items() if key != 'kind'}
    if action.endswith('reservations'):
        values.pop('sessionId')
    entered, release = asyncio.Event(), asyncio.Event()
    original = asyncio.to_thread
    async def pause(function, *a, **kw):
        entered.set()
        await release.wait()
        return await original(function, *a, **kw)
    monkeypatch.setattr(owner.asyncio, 'to_thread', pause)
    pending = asyncio.create_task(agent_action(app, source, action, values, 'read-race'))
    await entered.wait()
    source['workspace'] += '-moved'
    release.set()
    with pytest.raises(AppError, match='moved'):
        await pending


def test_restart_reconciles_abandoned_effect_but_not_live_owner(tmp_path):
    from filelock import FileLock
    home = tmp_path/'home'
    row = allocation(home)
    path = Path(row['path'])
    (path/'retained').write_text('keep')
    with owner.connect(home) as db:
        interrupted = owner.get(db, 'root', row['id'])
        interrupted['status'] = 'allocating'
        owner.save(db, interrupted)
    lock = FileLock(str(home/'workspace-resources'/('allocation-'+row['id']+'.lock')))
    with lock:
        assert owner.read(home, 'root', {})['items'][0]['status'] == 'allocating'
        with pytest.raises(owner.LockTimeout):
            owner.retire(home, 'root', row['owner'], {'id':row['id'], 'expectedRevision':row['revision']}, 'busy')
    recovered = owner.read(home, 'root', {})['items'][0]
    assert recovered['status'] == 'unknown' and recovered['revision'] == row['revision']+1
    assert allocation(home)['status'] == 'unknown'
    assert (path/'retained').read_text() == 'keep'


async def test_existing_applicable_file_policy_does_not_request_another_grant(app):
    async def observed(sid, paths):
        return {'status':'observed', 'paths':{path:{'status':'allowed', 'settingsChanged':False} for path in paths}}
    app.runtime.resource_access = AsyncMock(side_effect=observed)
    source = app.state['sessions'][0]
    grants = copy.deepcopy(app.configuration_authority.snapshot(source))
    approvals = copy.deepcopy(app.state.get('approvals'))
    result = await agent_action(app, source, 'workspace.resources.provision', args(app), 'already-permitted')
    assert result['result']['items'][0]['access']['status'] == 'allowed'
    assert app.configuration_authority.snapshot(source) == grants
    assert app.state.get('approvals') == approvals
    assert not app.runtime.inputs


async def test_authenticated_session_http_creates_retries_and_retires_only_its_owner(tmp_path, authenticated_client, monkeypatch):
    from amplifier_web.server import create_app
    from test_collaborative_workspaces import Runtime
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    server = await create_app(tmp_path/'app', workspace=tmp_path, runtime=Runtime(),
                              voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(server)
    service = server['service']
    source, peer = [service._new_session({'title':title}) for title in ('Owner','Peer')]
    service.state['sessions'] = [source, peer]
    service.state['selectedSessionId'] = source['id']
    service._publish()
    await client.post('/api/clients/attach', json={'clientId':'resource-client','kind':'tui'})
    headers = {'X-Amplifier-Client':'resource-client'}
    endpoint = f"/api/sessions/{source['id']}/commands"
    values = args(service)
    command = {'id':'http-storage','action':'workspace.resources.provision','args':values}
    response = await client.post(endpoint, headers=headers, json=command)
    assert response.status == 200, await response.text()
    body = await response.json()
    row = body['result']['items'][0]
    response = await client.post(endpoint, headers=headers, json=command)
    assert response.status == 200, await response.text()
    assert (await response.json())['result']['items'][0]['id'] == row['id']
    response = await client.post(endpoint, headers=headers, json={**command,'id':'foreign-storage',
        'args':{**values,'sessionId':peer['id']}})
    assert response.status == 400
    response = await client.post(endpoint, headers=headers, json={'id':'http-retire','action':'workspace.resources.retire',
        'args':{key:value for key,value in {**values,'id':row['id'],'expectedRevision':row['revision']}.items() if key!='kind'}})
    assert response.status == 200, await response.text()
    assert (await response.json())['result']['items'][0]['status'] == 'retired'
    assert Path(row['path']).is_dir()
    assert not service.runtime.inputs


@pytest.mark.parametrize('change', ['stop','generation','workspace'])
async def test_physical_owner_rechecks_after_waiting_for_admission_lock(app, change):
    source = app.state['sessions'][0]
    values = args(app)
    await app.lock.acquire()
    pending = asyncio.create_task(agent_action(app, source, 'workspace.resources.provision', values, 'waiting-owner'))
    try:
        from test_coordination_generation import until
        await until(lambda: (app.data_dir/'workspace-resources').exists())
        # Waiting for the main-loop admission guard keeps all physical writes
        # behind this lock, regardless of when the allocation thread arrives.
        if change=='generation':
            source['collaborationGeneration'] = {'id':'replacement','inputIds':['next'],'terminal':False}
        elif change=='workspace':
            source['workspace'] += '-moved'
        else:
            source['interruptionRevision'] = source.get('interruptionRevision',0)+1
    finally:
        app.lock.release()
    with pytest.raises(AppError):
        await pending
    assert not (app.data_dir/'workspace-resources'/'owned').exists()
    assert owner.read(app.data_dir, source['id'], {})['items'][0]['status']=='suppressed'


def test_sqlite_replacement_is_detected_and_paging_uses_root_index(tmp_path):
    home=tmp_path/'home'
    row=allocation(home, kind='sqlite-database')
    path=Path(row['path'])
    saved=path.with_name('saved.sqlite3')
    path.rename(saved)
    path.symlink_to(saved)
    assert allocation(home, kind='sqlite-database')['observation']=='changed-or-unavailable'
    with owner.connect(home) as db:
        plan=db.execute('EXPLAIN QUERY PLAN SELECT body FROM allocations WHERE root=? ORDER BY rowid DESC LIMIT ? OFFSET ?',('root',26,0)).fetchall()
    assert any('allocations_page' in str(part) for part in plan)
    assert all('TEMP B-TREE' not in str(part) for part in plan)
