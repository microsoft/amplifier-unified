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
