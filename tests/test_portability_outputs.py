"""Delayed output reads cannot commit across a task ownership boundary."""
import asyncio
import copy
from pathlib import Path
from threading import Event

import pytest

from amplifier_web import outputs
from amplifier_web.service import AppError
from test_portability import export, host_env, setup_hosts


def saved_content(app, home, sid):
    tables = ('output_records', 'output_comments', 'output_receipts', 'state_resources')
    result = {table: app.db.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall()
              for table in tables}
    result['messages'] = copy.deepcopy(app._session(sid)['messages'])
    result['task'] = copy.deepcopy(app._session(sid)['task'])
    result['draft'] = app._session(sid)['draft']
    for label, root in (('artifacts', app.data_dir / 'artifacts'),
                        ('native_history', home / 'native'),
                        ('archived_history', app.portability.node.directory / 'archives')):
        result[label] = {str(path.relative_to(root)): path.read_bytes()
                         for path in root.rglob('*') if path.is_file()}
    return result


async def delay_output(app, sid, kind, monkeypatch):
    entered = asyncio.Event()
    if kind == 'review':
        resume = asyncio.Event()
        original = outputs.git_snapshot

        async def blocked(*args, **kwargs):
            value = await original(*args, **kwargs)
            entered.set()
            await resume.wait()
            return value

        monkeypatch.setattr(outputs, 'git_snapshot', blocked)
        args = {'sessionId': sid, 'mode': 'unstaged', 'title': 'Delayed review'}
        action = 'outputs.review'
    else:
        resume = Event()
        loop = asyncio.get_running_loop()
        original = outputs.file_bytes

        def blocked(*args, **kwargs):
            value = original(*args, **kwargs)
            loop.call_soon_threadsafe(entered.set)
            if not resume.wait(120):
                raise AssertionError('Test did not release the delayed file read')
            return value

        monkeypatch.setattr(outputs, 'file_bytes', blocked)
        args = {'sessionId': sid, 'kind': 'file', 'path': 'new.txt', 'title': 'Delayed attachment'}
        action = 'outputs.attach'
    pending = asyncio.create_task(app.dispatch(action, args, command_id='delayed-output'))
    try:
        await asyncio.wait_for(entered.wait(), 10)
    except BaseException:
        resume.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        raise
    return pending, resume


async def release_to(left, right, sid, left_home, right_home, repository, monkeypatch):
    host_env(monkeypatch, left_home)
    receipt, _ = await export(left, right, sid, Path(left._session(sid)['workspace']))
    host_env(monkeypatch, right_home)
    staged = (await right.dispatch('portability.stage', {
        'path': receipt['package'], 'repository': str(repository)}))['result']
    host_env(monkeypatch, left_home)
    released = (await left.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'],
        'expectedRevision': receipt['revision'], 'path': staged['receiptPath']}))['result']
    return staged, released


async def activate(app, sid, staged, released):
    return (await app.dispatch('portability.activate', {'sessionId': sid, 'id': staged['id'],
        'expectedRevision': staged['revision'], 'path': released['receiptPath']}))['result']


@pytest.mark.parametrize(('kind', 'boundary'), [
    ('review', 'release'), ('attach', 'release'),
    ('review', 'cancel'), ('attach', 'roundtrip'),
])
async def test_delayed_output_rejects_changed_authority_without_mutating_saved_content(
        tmp_path, monkeypatch, kind, boundary):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    pending = resume = None
    try:
        host_env(monkeypatch, source)
        execution_revision = app._session(sid).get('executionRevision', 0)
        pending, resume = await delay_output(app, sid, kind, monkeypatch)
        if boundary == 'cancel':
            receipt, _ = await export(app, target, sid, root)
            cancelled = (await app.dispatch('portability.cancel', {'sessionId': sid,
                'id': receipt['id'], 'expectedRevision': receipt['revision'],
                'evidence': 'Keep the task on its original host.'}))['result']
            assert cancelled['phase'] == 'cancelled' and not app.portability.fenced(sid)
            assert app._session(sid).get('executionRevision', 0) == execution_revision
        else:
            staged, released = await release_to(app, target, sid, source, destination, destrepo, monkeypatch)
            assert released['phase'] == 'released' and app.portability.fenced(sid)
            if boundary == 'roundtrip':
                host_env(monkeypatch, destination)
                await activate(target, sid, staged, released)
                staged, released = await release_to(target, app, sid, destination, source, root, monkeypatch)
                host_env(monkeypatch, source)
                returned = await activate(app, sid, staged, released)
                assert returned['phase'] == 'active' and not app.portability.fenced(sid)
                assert app._session(sid)['executionRevision'] > execution_revision

        host_env(monkeypatch, source)
        before = saved_content(app, source, sid)
        resume.set()
        with pytest.raises(AppError, match='fenced|changed|transfer|moved'):
            await pending
        assert saved_content(app, source, sid) == before
        assert not app.db.execute('SELECT 1 FROM output_receipts WHERE id=?', ('delayed-output',)).fetchone()
        assert not app.runtime.workers and not target.runtime.workers

        if boundary == 'cancel':
            # A fresh explicit action after cancellation is still admitted.
            new = (await app.dispatch('outputs.write', {'sessionId': sid, 'title': 'New deliberate output',
                'content': 'Written after cancelled transfer review.', 'variant': 'document'},
                command_id='new-after-cancel'))['result']
            assert app.outputs.content(new) == b'Written after cancelled transfer review.'
            assert app._session(sid)['messages'] == before['messages']
    finally:
        if resume is not None:
            resume.set()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


async def attach_saved_media(app, sid, workspace, *, large=False):
    import hashlib
    from test_output_media import gif
    await app.on_runtime_event('assistant.message', {
        'sessionId': sid, 'text': 'Retain media with this assistant.', 'generationId': 'portable-media'})
    message = app._session(sid)['messages'][-1]
    data = gif()
    if large:
        # Below the media-only 32 MiB limit; serialized TOTAL transfer is larger.
        data = data[:-1] + b'\x21\xfe' + (b'\xff' + b'x' * 255) * 105_000 + b'\x00\x3b'
    path = workspace / 'portable.gif'
    path.write_bytes(data)
    result = (await app.dispatch('outputs.attachMedia', {'sessionId': sid, 'messageId': message['id'],
        'title': 'Portable synthetic animation', 'path': path.name,
        'expectedSha256': hashlib.sha256(data).hexdigest()}))['result']
    path.unlink()
    return result, message['id'], data


async def test_media_transfer_rebinds_destination_context_without_changing_history_or_nested_outputs(tmp_path, monkeypatch):
    from amplifier_web.host.storage import SessionStore
    from amplifier_web import resource_files
    from test_output_images import png
    app, target, source, destination, root, destrepo, sid, out1, out2 = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        media, mid, data = await attach_saved_media(app, sid, root)
        await app.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'A later assistant page.'})
        store = SessionStore.for_app(app.data_dir, root)
        raw = (store.directory(sid) / 'transcript.jsonl').read_bytes()
        # Existing small PNG and nested graph must survive alongside media.
        image = png()
        (root / 'page.png').write_bytes(image)
        saved_png = (await app.dispatch('outputs.attach', {'sessionId': sid, 'kind': 'file',
            'path': 'page.png', 'title': 'Unrelated PNG'}))['result']
        (root / 'page.png').unlink()
        leaf = resource_files.put(app.db, {'content': 'Nested retained small resource'})
        parent = resource_files.put(app.db, {'children': [leaf]})
        app._session(sid)['artifactRefs'] = [parent]
        app._save()
        before_messages = copy.deepcopy(app._session(sid)['messages'])
        staged, released = await release_to(app, target, sid, source, destination, destrepo, monkeypatch)
        host_env(monkeypatch, destination)
        active = await activate(target, sid, staged, released)
        assert active['phase'] == 'active'
        session = target._session(sid)
        working = Path(session.get('workingDirectory') or session['workspace'])
        assert working != root and working.is_dir()
        assert session['messages'] == before_messages
        assert SessionStore.for_app(target.data_dir, session['workspace']).history(sid).transcript_path.read_bytes() == raw
        target.clients.attach('destination-media')
        with target.clients.bind('destination-media'):
            await target.dispatch('session.select', {'id': sid})
            items = target.outputs.media.descriptors(sid, mid)['items']
            assert len(items) == 1 and items[0]['id'] == media['id']
            values = {**items[0], 'messageId': mid}
            assert await target.outputs.media.bytes(values) == data
            assert target.outputs.media.observation(target.outputs.record(sid, media['id']))['status'] == 'not_observed'
            with pytest.raises(ValueError):
                await target.outputs.media.bytes({**values, 'messageId': session['messages'][-1]['id']})
        assert target.outputs.content(target.outputs.record(sid, saved_png['id'])) == image
        assert target.outputs.content(target.outputs.record(sid, out1['id'])) == b'First output'
        assert target.outputs.content(target.outputs.record(sid, out2['id'])) == b'Second output'
        assert target.state_resource(parent['$resource']) == {'children': [leaf]}
        assert target.state_resource(leaf['$resource']) == {'content': 'Nested retained small resource'}
        assert not app.runtime.workers and not target.runtime.workers
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


async def test_media_transfer_total_32mib_refuses_without_omission_or_limit_raise(tmp_path, monkeypatch):
    from amplifier_web.output_media import MAX_BYTES as MEDIA_LIMIT
    from amplifier_web.portability_data import MAX_BYTES as TRANSFER_LIMIT
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        media, mid, data = await attach_saved_media(app, sid, root, large=True)
        assert len(data) < MEDIA_LIMIT == TRANSFER_LIMIT == 32 * 1024 * 1024
        before = saved_content(app, source, sid)
        inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
        with pytest.raises(AppError, match='32 MiB transfer limit'):
            await app.dispatch('portability.export', {'sessionId': sid,
                'destination': target.portability.node.identity['id'],
                'sourceRevision': inspected['repository']['sourceRevision'],
                'expectedExecutionRevision': app._session(sid).get('executionRevision', 0),
                'mode': 'carry_dirty', 'reviewedContent': True}, command_id='oversized-media-transfer')
        assert saved_content(app, source, sid) == before
        assert not app.portability.fenced(sid) and not app.portability.jobs
        assert not target.state['sessions']
        app.clients.attach('oversized-retained')
        with app.clients.bind('oversized-retained'):
            assert await app.outputs.media.bytes({**media, 'messageId': mid}) == data
        assert not app.runtime.workers and not target.runtime.workers
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()
