import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest

from amplifier_web.host.storage import SessionStore
from amplifier_web.service import AppService
from amplifier_web.session_files import append_event, capture_dir, project_slug, read_event, sessions_dir


def test_project_paths_and_transcripts_share_cli_changes_without_checkpoint_copy(tmp_path):
    workspace = tmp_path / 'Project name.v2_with-underscores'
    assert project_slug(workspace) == str(workspace.resolve()).replace('/', '-')
    home = tmp_path / 'app'
    old = SessionStore(home / 'sessions')
    old.save('root', [{'role': 'user', 'content': 'old'}], {'working_dir': str(workspace), 'hook_metadata': {'keep': True}})
    original = (old.directory('root') / 'checkpoint.json').read_bytes()
    shared = SessionStore.for_app(home, workspace)
    assert shared.load('root')[0][0]['content'] == 'old'
    assert shared.directory('root') == sessions_dir(workspace) / 'root'
    assert not (shared.directory('root') / 'checkpoint.json').exists()
    transcript = shared.directory('root') / 'transcript.jsonl'
    transcript.write_text('{"role":"assistant","content":"new CLI turn"}\n')
    assert shared.load('root')[0][0]['content'] == 'new CLI turn'
    shared.save('root', shared.load('root')[0], {'status': 'completed'})
    assert shared.load('root')[1]['hook_metadata'] == {'keep': True}
    assert (old.directory('root') / 'checkpoint.json').read_bytes() == original


def test_jsonl_works_with_real_context_intelligence_reader(tmp_path):
    from context_intelligence.native_transcript import CaptureLocator, read_native_transcript
    now = datetime.now(timezone.utc).isoformat()
    first = append_event(tmp_path, 'root', 'prompt:submit', {'event_id': 'one', 'timestamp': now, 'prompt': 'Hello ☀'})
    append_event(tmp_path, 'root', 'prompt:complete', {'event_id': 'two', 'timestamp': now, 'response': 'Hello back'})
    page = read_native_transcript(CaptureLocator.from_session_dir(capture_dir(tmp_path, 'root')))
    assert page.status == 'complete'
    assert [(m.role, m.content) for m in page.messages] == [('user', 'Hello ☀'), ('assistant', 'Hello back')]
    assert read_event(first)['data']['prompt'] == 'Hello ☀'
    assert Path(first['$event']).stat().st_mode & 0o777 == 0o600


async def test_shared_hook_events_are_indexed_once_redacted_and_never_backfilled(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        now = datetime.now(timezone.utc).isoformat()
        app._session()['runtimeReport'] = {'contextIntelligence': {'enabled': True}}
        # Raw kernel capture is authoritative; neither UI event path should
        # manufacture a second tool event beside the community hook's row.
        app.diagnostics.runtime_event('tool.post', {'tool': 'fixture'}, app._session())
        app.diagnostics.runtime_event('execution.event', {'kind': 'tool', 'phase': 'completed', 'label': 'fixture'}, app._session())
        append_event(tmp_path, sid, 'tool:post', {'timestamp': now, 'tool_name': 'fixture', 'result': 'private content'})
        await app.diagnostics.flush()
        await app.diagnostics.flush()
        rows = app.diagnostics.read(sessionId=sid, stream='tools')['items']
        assert len(rows) == 1 and rows[0]['data']['tool_name'] == 'fixture'
        assert 'private content' not in json.dumps(rows)
        with app.diagnostics._db() as db:
            assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 0
            assert '$event' in db.execute("SELECT data FROM records WHERE stream='tools'").fetchone()[0]
    finally:
        await app.close()


async def test_private_event_database_migrates_once_to_shared_jsonl(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    collector = app.diagnostics
    now = datetime.now(timezone.utc).isoformat()
    data = {'event_id': 'legacy-event', 'session_id': 'root', 'timestamp': now, 'phase': 'done'}
    with collector._db() as db:
        db.execute('INSERT INTO records(id,at,stream,session,workspace,event,data) VALUES(?,?,?,?,?,?,?)',
                   ('legacy-event', time.time(), 'updates', 'root', str(tmp_path), 'update:done', json.dumps(data)))
    collector._initialize_storage()
    collector._initialize_storage()
    assert collector.read()['items'][0]['data']['phase'] == 'done'
    assert len((capture_dir(tmp_path, 'root') / 'events.jsonl').read_text().splitlines()) == 1
    await app.close()


async def test_chat_view_and_canvas_restart_without_database_payloads(tmp_path):
    home = tmp_path / 'app'
    app = AppService(home, workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app._session()['id']
    app._message(app._session(), 'user', 'Keep this conversation')
    await app.dispatch('canvas.show', {'kind': 'html', 'content': '<h1>Keep this artifact</h1>'})
    state_text = app.db.execute('SELECT value FROM state').fetchone()[0]
    assert 'Keep this conversation' not in state_text and '<h1>Keep this artifact' not in state_text
    assert (sessions_dir(tmp_path) / sid / 'unified/view.json').is_file()
    assert all(set(json.loads(row[0])) == {'$blob'} for row in app.db.execute('SELECT value FROM state_resources'))
    await app.close()
    app = AppService(home, workspace=tmp_path)
    try:
        assert app._session()['messages'][0]['text'] == 'Keep this conversation'
        assert app.state['canvas']['content'] == '<h1>Keep this artifact</h1>'
    finally:
        await app.close()


async def test_migration_deduplicates_surfaces_bounds_results_and_preserves_receipts(tmp_path, monkeypatch):
    import amplifier_web.storage_migration as migration
    monkeypatch.setattr(migration, 'RESULT_LIMIT', 2)
    home = tmp_path / 'app'
    app = AppService(home, workspace=tmp_path)
    await app.dispatch('session.create', {})
    state = copy.deepcopy(app.state)
    await app.close()
    body = '<html>' + 'x' * 100_000 + '</html>'
    now = time.time()
    with sqlite3.connect(home / 'app.sqlite3') as db:
        db.execute('DROP TABLE storage_layout')
        db.execute('CREATE TABLE smart_tool_operations(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
        for n in range(20):
            value = json.dumps({'content': body, 'mcp': {'lastOperationId': str(n)}})
            identity = hashlib.sha256(value.encode()).hexdigest()
            db.execute('INSERT INTO state_resources VALUES(?,?)', (identity, value))
            db.execute('INSERT INTO smart_tool_operations VALUES(?,?)', (str(n), json.dumps({'id': str(n), 'status': 'completed', 'updatedAt': now + n, 'result': body})))
        sid = state['sessions'][0]['id']
        state['canvasArtifacts'] = [{'id': 'surface', 'sessionId': sid, 'body': {'$resource': identity}, 'kind': 'mcp-app'}]
        db.execute('UPDATE state SET value=? WHERE id=1', (json.dumps(state),))
    old_size = (home / 'app.sqlite3').stat().st_size
    restored = AppService(home, workspace=tmp_path)
    try:
        assert (home / 'backups/shared-storage-v1/app.sqlite3').is_file()
        assert restored.db.execute('SELECT count(*) FROM smart_tool_operations').fetchone()[0] == 20
        assert restored.db.execute("SELECT count(*) FROM smart_tool_operations WHERE json_extract(value,'$.resultExpired')=1").fetchone()[0] == 18
        assert restored.db.execute('SELECT count(*) FROM state_resources').fetchone()[0] == 3
        assert (home / 'app.sqlite3').stat().st_size < old_size / 3
        from amplifier_web.canvas_library import remember
        restored.state['canvas'] = {'id': 'surface', 'kind': 'mcp-app', 'sessionId': sid, 'content': body, 'mcp': {}}
        for n in range(100):
            restored.state['canvas']['mcp']['contextUpdatedAt'] = n
            remember(restored.state, restored.db)
        from amplifier_web.resource_files import collect
        collect(restored.db, restored.state)
        assert restored.db.execute('SELECT count(*) FROM state_resources').fetchone()[0] == 3
    finally:
        await restored.close()


async def test_backup_compression_does_not_hold_app_lock_or_event_loop(tmp_path, monkeypatch):
    from amplifier_web import recovery
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    await app.dispatch('session.create', {})
    original = recovery._backup_files
    import threading
    started, release = threading.Event(), threading.Event()
    def slow(*args):
        started.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(recovery, '_backup_files', slow)
    task = asyncio.create_task(recovery.backup(app))
    try:
        while not started.is_set():
            await asyncio.sleep(.01)
        assert not app.lock.locked()
        receipt = await asyncio.wait_for(app.dispatch('session.rename', {'id': app._session()['id'], 'title': 'Still responsive'}), 1)
        assert receipt['accepted']
        release.set()
        result = await task
        assert Path(result['backup']).is_file()
    finally:
        release.set()
        await task
        await app.close()


async def test_only_new_selected_native_records_are_forwarded(tmp_path):
    from amplifier_web.diagnostics import DEFAULT
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        append_event(tmp_path, sid, 'tool:post', {'timestamp': '2000-01-01T00:00:00Z', 'tool_name': 'old'})
        cfg = copy.deepcopy(DEFAULT)
        cfg['destinations'] = [{'id': 'chosen', 'url': 'http://localhost:9999', 'enabled': True, 'streams': ['tools']}]
        await app.diagnostics.configure(cfg)
        append_event(tmp_path, sid, 'tool:post', {'timestamp': datetime.now(timezone.utc).isoformat(), 'tool_name': 'new', 'result': 'private content'})
        await app.diagnostics.flush()
        with app.diagnostics._db() as db:
            values = [json.loads(r[0]) for r in db.execute('SELECT payload FROM deliveries')]
            assert len(values) == 1
            assert values[0]['data']['tool_name'] == 'new'
            assert 'private content' not in json.dumps(values)
        assert len(app.diagnostics.read(sessionId=sid, stream='tools')['items']) == 1  # old entry is outside retention
    finally:
        await app.close()


async def test_replaced_capture_invalidates_offsets_and_unknown_version_fails_closed(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        now = datetime.now(timezone.utc).isoformat()
        reference = append_event(tmp_path, sid, 'tool:post', {'timestamp': now, 'tool_name': 'original'})
        await app.diagnostics.flush()
        path = Path(reference['$event'])
        raw = path.read_text().replace('original', 'replaced')
        replacement = path.with_suffix('.replacement');replacement.write_text(raw);replacement.replace(path)
        with pytest.raises(ValueError, match='stale'):
            read_event(reference)
        await app.diagnostics.flush()
        rows = app.diagnostics.read(sessionId=sid, stream='tools')['items']
        assert len(rows) == 1 and rows[0]['data']['tool_name'] == 'replaced'
        meta = path.parent / 'metadata.json';value = json.loads(meta.read_text());value['version'] = '999';meta.write_text(json.dumps(value))
        await app.diagnostics.flush()
        assert app.diagnostics.storage_error
        assert path.read_text() == raw
    finally:
        await app.close()
