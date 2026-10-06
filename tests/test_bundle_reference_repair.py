"""Name-only repair must preserve canonical content and never start work."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_foundation.session.history import SessionHistoryStore
from amplifier_foundation.session.shared_state import SharedSessionStore
from amplifier_web.bundle_reference_repair import canonicalize, discover
from amplifier_web.host.storage import SessionStore
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService
from amplifier_web.session_files import amplifier_home, project_slug


def native(workspace, identity, *, bundle='Work', parent=None, name='Saved name'):
    directory = amplifier_home() / 'projects' / project_slug(workspace) / 'sessions' / identity
    SessionHistoryStore(directory).save([
        {'role': 'system', 'content': 'Retain these exact instructions.'},
        {'role': 'user', 'content': 'Original question', 'id': 'original-message'}],
        {'session_id': identity, 'working_dir': str(workspace), 'bundle_name': bundle,
         'parent_id': parent, 'name': name, 'model': 'saved-model', 'created': '2026-09-20'})
    return directory


@pytest.fixture
async def app(tmp_path):
    runtime = RuntimeManager(retention={'prewarm_on_select': False})
    runtime.start = AsyncMock(side_effect=AssertionError('repair started a runtime'))
    runtime.send = AsyncMock(side_effect=AssertionError('repair submitted a message'))
    result = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    yield result
    runtime.start.assert_not_awaited()
    runtime.send.assert_not_awaited()
    await result.close()


async def test_preview_and_apply_preserve_transcript_configuration_and_selection(app):
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    sid = session['id']
    session.update(status='starting', selection={'instance': 'original', 'model': 'pinned', 'effort': 'high'})
    app._message(session, 'user', 'Unsent', inputId='input', delivery={'status': 'failed'})
    directory = native(Path(session['workspace']), sid)
    overrides = app.data_dir / 'sessions' / sid / 'configuration.json'
    overrides.parent.mkdir(parents=True)
    overrides.write_text('{"instruction":"Customized instructions"}')
    transcript = directory / 'transcript.jsonl'
    original = (transcript.read_bytes(), transcript.stat().st_mtime_ns)
    metadata = json.loads((directory / 'metadata.json').read_text())
    view = copy.deepcopy(session)
    preview = await canonicalize(app)
    assert preview['counts'] == {'eligible': 1}
    assert json.loads((directory / 'metadata.json').read_text()) == metadata
    assert session['bundle'] == 'Work'
    result = await canonicalize(app, apply=True)
    assert result['complete'] and result['counts'] == {'changed': 1}
    assert result['workReplayed'] is False
    expected = {**metadata, 'bundle_name': 'work'}
    assert json.loads((directory / 'metadata.json').read_text()) == expected
    assert (transcript.read_bytes(), transcript.stat().st_mtime_ns) == original
    assert overrides.read_text() == '{"instruction":"Customized instructions"}'
    for field in ('messages', 'selection', 'title', 'createdAt'):
        assert session[field] == view[field]
    assert session['bundle'] == 'work'
    # The committed projection, not only an in-memory mutation, is canonical.
    saved = json.loads((directory / 'unified/view.json').read_text())
    assert saved['bundle'] == 'work'
    assert (await canonicalize(app, apply=True))['counts'] == {}


async def test_all_root_sources_include_unopened_zero_turn_and_archived(app, tmp_path):
    first = tmp_path / 'one'
    second = tmp_path / 'two'
    first.mkdir()
    second.mkdir()
    paths = [native(first, 'same-id'), native(second, 'same-id')]
    native(first, 'runtime-child', parent='same-id')
    native(first, 'other-root', bundle='anchors')
    empty = native(first, 'empty-root')
    (empty / 'transcript.jsonl').write_text('')
    discovered, issues = discover()
    assert len(discovered) == 3 and not issues
    result = await canonicalize(app, apply=True)
    assert result['counts'] == {'changed': 3} and result['complete']
    for path in [*paths, empty]:
        assert json.loads((path / 'metadata.json').read_text())['bundle_name'] == 'work'
    child = amplifier_home() / 'projects' / project_slug(first) / 'sessions/runtime-child/metadata.json'
    assert json.loads(child.read_text())['bundle_name'] == 'Work'


async def test_live_writer_defers_and_does_not_change_the_view(app):
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    directory = native(Path(session['workspace']), session['id'])
    held = SharedSessionStore(session['workspace'], session['id']).acquire(app='fixture-cli')
    try:
        result = await canonicalize(app, apply=True)
        assert not result['complete'] and result['counts'] == {'busy': 1}
        assert session['bundle'] == 'Work'
        assert json.loads((directory / 'metadata.json').read_text())['bundle_name'] == 'Work'
    finally:
        held.release()
    assert (await canonicalize(app, apply=True))['complete']


async def test_startup_admission_lock_defers_ownerless_stale_status(app):
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    session['status'] = 'starting'
    native(Path(session['workspace']), session['id'])
    import asyncio
    lock = app.runtime._locks.setdefault(session['id'], asyncio.Lock())
    async with lock:
        result = await canonicalize(app, apply=True)
        assert result['counts'] == {'busy': 1} and session['bundle'] == 'Work'
    assert (await canonicalize(app, apply=True))['counts'] == {'changed': 1}


async def test_parked_and_retired_cached_references_become_canonical(app):
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    sid = session['id']
    native(Path(session['workspace']), sid)
    row = {'process': SimpleNamespace(returncode=None), 'parked': True, 'start_session': {'bundle': 'Work'}}
    app.runtime.workers[sid] = row
    app.runtime._retired[sid] = ({'bundle': 'Work'}, None)
    try:
        assert (await canonicalize(app, apply=True))['complete']
        assert row['start_session']['bundle'] == 'work'
        assert app.runtime._retired[sid][0]['bundle'] == 'work'
    finally:
        app.runtime.workers.clear()


async def test_conflicting_alias_and_paths_are_not_silently_replaced(app, monkeypatch):
    from amplifier_web.host import config
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    native(Path(session['workspace']), session['id'])
    original = config.read_config
    def custom(*args, **kwargs):
        result = original(*args, **kwargs)
        result.settings.setdefault('bundle', {}).setdefault('added', {})['Work'] = 'git+https://example.invalid/custom'
        return result
    monkeypatch.setattr(config, 'read_config', custom)
    result = await canonicalize(app, apply=True)
    assert result['counts'] == {'alias-conflict': 1} and not result['complete']
    assert session['bundle'] == 'Work'


async def test_ui_only_chat_has_no_invented_native_history(app):
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    directory = SessionStore.for_app(app.data_dir, session['workspace']).directory(session['id'])
    assert not (directory / 'metadata.json').exists()
    assert (await canonicalize(app, apply=True))['complete']
    assert session['bundle'] == 'work'
    assert not (directory / 'metadata.json').exists()
    assert not (directory / 'transcript.jsonl').exists()


async def test_supported_maintenance_command_records_result_without_runtime(app):
    from amplifier_web.management import Management
    from jsonschema import validate
    from amplifier_web.service import ACTION_DEFINITIONS
    action = 'maintenance.canonicalizeBundleReferences'
    validate({'apply': True}, ACTION_DEFINITIONS[action][1])
    app.management = Management(app)
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    native(Path(session['workspace']), session['id'])
    await app.management.command(action, {'apply': True}, 'metadata-repair')
    assert app.state['maintenance']['bundleReferences']['complete']
    assert app.state['actionStatus'][action]['phase'] == 'ready'
    assert not session['messages'] and session['bundle'] == 'work'


async def test_symlink_metadata_is_retained_and_reported(app):
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    directory = native(Path(session['workspace']), session['id'])
    target = directory / 'original.json'
    path = directory / 'metadata.json'
    path.rename(target)
    path.symlink_to(target)
    result = await canonicalize(app, apply=True)
    assert not result['complete']
    assert path.is_symlink() and json.loads(target.read_text())['bundle_name'] == 'Work'
    assert session['bundle'] == 'Work'


async def test_missing_primary_native_metadata_is_not_reported_as_ui_only_success(app):
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    directory = native(Path(session['workspace']), session['id'])
    path = directory / 'metadata.json'
    path.rename(directory / 'metadata.json.backup')
    result = await canonicalize(app, apply=True)
    assert not result['complete'] and 'missing-primary' in result['counts']
    assert session['bundle'] == 'Work' and not path.exists()
    assert json.loads((directory / 'metadata.json.backup').read_text())['bundle_name'] == 'Work'


@pytest.mark.parametrize('malformed', ['list', 'fifo'])
async def test_invalid_metadata_is_reported_without_aborting_or_blocking(app, malformed):
    import os
    directory = native(Path(app.default_workspace), 'malformed-root')
    path = directory / 'metadata.json'
    if malformed == 'list':
        path.write_text('{"bundle_name":["Work"]}')
    else:
        path.unlink()
        os.mkfifo(path)
    import asyncio
    result = await asyncio.wait_for(canonicalize(app, apply=True), 2)
    assert result['counts'] == {'unreadable': 1} and not result['complete']


async def test_execution_ownership_is_held_until_view_publication(app, monkeypatch):
    from amplifier_foundation.session.shared_state import SessionBusyError
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    native(Path(session['workspace']), session['id'])
    publish = app._publish
    checks = []
    def guarded_publish(**kwargs):
        if kwargs.get('session_ids') == {session['id']}:
            with pytest.raises(SessionBusyError):
                SharedSessionStore(session['workspace'], session['id']).acquire(app='external-fixture')
            checks.append(True)
        return publish(**kwargs)
    monkeypatch.setattr(app, '_publish', guarded_publish)
    assert (await canonicalize(app, apply=True))['complete']
    assert checks == [True]


async def test_cancellation_keeps_ownership_until_detached_metadata_write_finishes(app, monkeypatch):
    import asyncio
    import threading
    from amplifier_web import bundle_reference_repair as repair
    from amplifier_foundation.session.shared_state import SessionBusyError
    await app.dispatch('session.create', {'bundle': 'Work'})
    session = app._session()
    native(Path(session['workspace']), session['id'])
    entered, release = threading.Event(), threading.Event()
    original = repair.repair_metadata
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(repair, 'repair_metadata', blocked)
    task = asyncio.create_task(canonicalize(app, apply=True))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        with pytest.raises(SessionBusyError):
            SharedSessionStore(session['workspace'], session['id']).acquire(app='external-fixture')
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        held = SharedSessionStore(session['workspace'], session['id']).acquire(app='external-fixture')
        held.release()
        # An interrupted partial metadata change reconciles on explicit repeat;
        # no turn or worker has ever run.
        assert (await canonicalize(app, apply=True))['complete']
        assert session['bundle'] == 'work'
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_authenticated_host_action_repairs_and_survives_reopen(authenticated_client, tmp_path):
    import asyncio
    from amplifier_web.server import create_app
    runtime = RuntimeManager(retention={'prewarm_on_select': False})
    runtime.start = AsyncMock(side_effect=AssertionError('no startup allowed'))
    host = await create_app(tmp_path / 'api', workspace=tmp_path, runtime=runtime,
        preload_providers=False, voice=False, background_updates=False)
    client = await authenticated_client(host)
    service = host['service']
    await service.dispatch('session.create', {'bundle': 'Work', 'title': 'Keep title'})
    session = service._session()
    sid = session['id']
    directory = native(tmp_path, sid, name='Keep title')
    transcript = (directory / 'transcript.jsonl').read_bytes()
    response = await client.post('/api/actions', json={
        'id': 'repair-http-once', 'action': 'maintenance.canonicalizeBundleReferences',
        'args': {'apply': True}})
    assert response.status == 200
    async def settled():
        while not service.state.get('maintenance', {}).get('bundleReferences', {}).get('complete'):
            await asyncio.sleep(.01)
    await asyncio.wait_for(settled(), 5)
    await service.close()
    runtime.start.assert_not_awaited()
    assert (directory / 'transcript.jsonl').read_bytes() == transcript
    restored = AppService(tmp_path / 'api', workspace=tmp_path)
    try:
        assert restored._session(sid)['bundle'] == 'work'
        assert restored._session(sid)['title'] == 'Keep title'
        assert restored.state['maintenance']['bundleReferences']['complete']
        assert json.loads((directory / 'metadata.json').read_text())['bundle_name'] == 'work'
    finally:
        await restored.close()