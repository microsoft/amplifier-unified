"""Canonical owner pages: real listeners, bounded evidence and no work admission."""
import asyncio
import json
from urllib.request import urlopen

import pytest

from amplifier_operations import OperationJournal
from amplifier_publishing import Publisher
from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action
from test_operations import event, final


def test_publishing_resources_show_actual_port_directory_stop_and_restart(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'index.html').write_text('immutable release')
    home = tmp_path / 'publisher'
    with Publisher(home) as owner:
        release = owner.build(source, site_id='site', session_id='task', request_id='build')
        owner.preview(release['id'], session_id='task', request_id='preview')
        monkeypatch.setattr(owner, '_all', lambda *args: pytest.fail('No unbounded owner history reads'))
        page = owner.resources('task')
        preview = next(row for row in page['items'] if row['kind'] == 'preview')
        assert preview['port'] == owner._previews[release['id']].server_port
        assert preview['releaseDirectory'] == str(home / 'releases' / release['id'] / 'files')
        assert urlopen(preview['url']).read() == b'immutable release'
        assert owner.resources('other')['items'] == []
        # An owner-confirmed dead listener is repaired only on this selected page.
        owner._previews[release['id']].stop()
        interrupted = next(row for row in owner.resources('task')['items'] if row['kind'] == 'preview')
        assert interrupted['status'] == 'interrupted' and interrupted['url'] is None and interrupted['port'] is None
        monkeypatch.undo()
        owner.preview(release['id'], session_id='task', request_id='preview-again')
        owner.stop('site', session_id='task', expected_revision=0, request_id='stop')
        assert all(row['status'] == 'stopped' and row['port'] is None for row in owner.resources('task')['items'])
    with Publisher(home) as restored:
        assert all(row['status'] == 'stopped' for row in restored.resources('task')['items'])


def test_publishing_keyset_pages_are_scoped_bounded_and_indexed(tmp_path, monkeypatch):
    with Publisher(tmp_path) as owner:
        for session in ('task', 'other'):
            for i in range(80):
                owner._put('preview', {'id': f'{session}-{i:03}', 'sessionId': session, 'status': 'stopped'})
        monkeypatch.setattr(owner, '_all', lambda *args: pytest.fail('No history scan'))
        first = owner.resources('task', 3)
        second = owner.resources('task', 3, first['nextCursor'])
        assert [row['id'] for row in first['items']] == ['task-000','task-001','task-002']
        assert [row['id'] for row in second['items']] == ['task-003','task-004','task-005']
        plan = owner._db.execute("EXPLAIN QUERY PLAN SELECT kind,id FROM records WHERE json_extract(body,'$.sessionId')=? AND kind IN ('preview','site') AND (kind,id)>(?,?) ORDER BY kind,id LIMIT ?", ('task','preview','task-002',4)).fetchall()
        assert any('publishing_resources' in row[-1] for row in plan)
        assert not any('TEMP B-TREE' in row[-1] for row in plan)
        monkeypatch.undo()


def test_operation_pages_do_not_load_commands_output_or_other_tasks(tmp_path, monkeypatch):
    journal = OperationJournal(tmp_path / 'operations.db')
    try:
        for session in ('task', 'other'):
            for i in range(70):
                journal.ingest(session, session, event(f'{session}-{i:03}', status={'state':'running','pid':i},
                    metadata={'command':'private command','code':'private kernel code','environment':{'secret':'private secret'}}))
        journal.ingest('task', 'task', final('task-069', sequence=2, total_output_bytes=0))
        monkeypatch.setattr(journal, '_record', lambda *args: pytest.fail('Projection must not load full records'))
        first = journal.resources('task', 3)
        second = journal.resources('task', 3, first['nextCursor'])
        assert [row['id'] for row in first['items']] == ['task-069','task-068','task-067']
        assert first['items'][0]['state'] == 'completed'
        assert [row['id'] for row in second['items']] == ['task-066','task-065','task-064']
        assert 'private' not in json.dumps(first)
        assert first['items'][1]['evidence'] == 'recorded-lifecycle'
        with pytest.raises(ValueError, match='cursor'):
            journal.resources('task', 3, 'other-069')
        plan = journal.db.execute("EXPLAIN QUERY PLAN SELECT id FROM operations WHERE session_id=? AND (json_extract(value,'$.createdAt'),id)<(?,?) ORDER BY json_extract(value,'$.createdAt') DESC,id DESC LIMIT ?", ('task',1,'task-067',4)).fetchall()
        assert any('operations_resources' in row[-1] for row in plan)
        assert not any('TEMP B-TREE' in row[-1] for row in plan)
        monkeypatch.undo()
        journal.recover()
        assert journal.resources('task', 3)['items'][1]['state'] == 'outcome_unknown'
        assert journal.resources('task', 3)['items'][0]['state'] == 'completed'
    finally:
        journal.close()


async def test_shared_action_uses_host_bound_task_without_history_or_model_input(app, monkeypatch):
    source, peer = app.state['sessions']
    workspace = next(row for row in app.state['workspaces'] if row['path'] == source['workspace'])
    args = {'workspaceId': workspace['id'], 'sessionId': source['id']}
    monkeypatch.setattr(app.cold_display, 'hydrate', lambda *args: pytest.fail('Resource reads must not hydrate chats'))
    browser = await app.dispatch('workspace.resources.managed', args)
    agent = await agent_action(app, source, 'workspace.resources.managed', args, 'resource-read')
    assert browser['result'] == agent['result']
    assert all(row['status'] == 'unallocated' for row in agent['result']['isolation'])
    assert agent['result']['coverage']['dynamicPreviewDiscovery'] == 'unsupported'
    assert app.runtime.inputs == []
    with pytest.raises(AppError):
        await agent_action(app, source, 'workspace.resources.managed', {**args, 'sessionId':peer['id']}, 'foreign-resource-read')
    with pytest.raises(AppError, match='workspace'):
        await app.dispatch('workspace.resources.managed', {**args, 'workspaceId':'not-current'})


async def test_task_moving_during_owner_read_rejects_stale_workspace_scope(app, monkeypatch):
    source = app.state['sessions'][0]
    workspace = next(row for row in app.state['workspaces'] if row['path'] == source['workspace'])
    entered, release = asyncio.Event(), asyncio.Event()
    original = asyncio.to_thread
    async def delayed(function, *args):
        entered.set()
        await release.wait()
        return await original(function, *args)
    monkeypatch.setattr('amplifier_web.workspace_resources.asyncio.to_thread', delayed)
    task = asyncio.create_task(app.dispatch('workspace.resources.managed', {'workspaceId':workspace['id'],'sessionId':source['id']}))
    await entered.wait()
    source['workspace'] += '-moved'
    release.set()
    with pytest.raises(AppError, match='moved'):
        await task
