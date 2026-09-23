"""Shared actions with real Git, Foundation native locks and persisted outputs."""
import base64
import copy
import json
from pathlib import Path

import pytest

from amplifier_foundation.session import SharedSessionStore, SessionTransferFencedError
from amplifier_web.host.storage import SessionStore
from amplifier_web.portability import Portability
from amplifier_web.portability_policy import readiness_policy
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService, AppError
from amplifier_worktrees.git import atomic, digest, git
from test_worktrees import repository


def host_env(monkeypatch, root):
    monkeypatch.setenv('AMPLIFIER_HOME', str(root / 'native'))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(root / 'locks'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(root / 'app'))


async def setup_hosts(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.mkdir()
    destination = tmp_path / 'destination'; destination.mkdir()
    root = repository(source)
    destrepo = destination / 'repo'
    git(destination, 'clone', '--no-local', str(root), str(destrepo))
    host_env(monkeypatch, source)
    runtime = RuntimeManager(); runtime.retention.wake = lambda: None
    app = AppService(source / 'app', runtime, workspace=root)
    await app.dispatch('session.create', {})
    session = app._session(); sid = session['id']
    session.update(selection={'provider': 'fixture', 'model': 'test-model'},
        task={'id': 'task-original', 'revision': 4, 'objective': 'Finish the original work', 'status': 'active'},
        messages=[{'id': 'typed', 'role': 'user', 'text': 'Keep this task'},
                  {'id': 'spoken', 'role': 'user', 'text': 'Spoken history', 'via': 'call', 'voiceId': 'voice-original'}],
        draft='Unsent draft stays unsent', artifactRefs=[])
    store = SessionStore.for_app(app.data_dir, root)
    store.save(sid, [{'role': 'user', 'content': 'Keep this task'}, {'role': 'assistant', 'content': 'Completed original work'}], {'name': 'Portable task'})
    atomic(app.data_dir / 'sessions' / sid / 'control-state.json', {'task': session['task'], 'selection': session['selection'], 'budget': {'maxIterations': 12}})
    app._save()
    out1 = (await app.dispatch('outputs.write', {'sessionId': sid, 'title': 'Original', 'variant': 'document', 'content': 'First output', 'messageId': 'typed'}))['result']
    out2 = (await app.dispatch('outputs.write', {'sessionId': sid, 'title': 'Revision', 'variant': 'document', 'content': 'Second output', 'parentId': out1['id'], 'evidenceIds': [out1['id']]}))['result']
    await app.dispatch('outputs.comment', {'sessionId': sid, 'id': out2['id'], 'body': 'Retain this review'})
    (root / 'a.txt').write_text('staged\n'); git(root, 'add', 'a.txt')
    (root / 'a.txt').write_text('staged\nunstaged\n')
    (root / 'new.txt').write_text('Untracked work')
    host_env(monkeypatch, destination)
    target_runtime = RuntimeManager(); target_runtime.retention.wake = lambda: None
    target = AppService(destination / 'app', target_runtime, workspace=destrepo)
    for left, right in ((app, target), (target, app)):
        atomic(left.portability.node.directory / 'peers.json', {right.portability.node.identity['id']: right.portability.node.identity})
    # These service tests isolate account-network behavior. The separate probe
    # tests and two-process acceptance exercise the subprocess boundary.
    def policy(self, payload, workspace):
        selection = payload['intent']['selection']
        return readiness_policy('provider-fixture', selection.get('instance') or selection['provider'],
                                selection['model'], selection.get('effort') or 'high')
    monkeypatch.setattr(Portability, 'destination_policy', policy)
    async def checks(self, payload, workspace, policy):
        assert (Path(workspace) / '.git').is_file()
        return {'runtimeVerified': True, 'accountVerified': True, 'nativeFenceVerified': True,
                'credentialsOrigin': 'destination', 'readinessPolicy': policy, 'readinessPolicyHash': digest(policy)}
    monkeypatch.setattr(Portability, 'destination_checks', checks)
    return app, target, source, destination, root, destrepo, sid, out1, out2


async def export(app, target, sid, root):
    inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
    args = {'sessionId': sid, 'destination': target.portability.node.identity['id'],
            'sourceRevision': inspected['repository']['sourceRevision'], 'expectedExecutionRevision': app._session(sid).get('executionRevision', 0),
            'mode': 'carry_dirty', 'reviewedContent': True}
    started = (await app.app_bridge('dispatch', {'action': 'portability.export', 'id': 'export-original', 'args': args}, sid))['result']
    await __import__('asyncio').gather(*list(app.portability.jobs))
    receipt = app.portability.node.get(started['id'])
    assert receipt['phase'] == 'prepared', receipt
    return receipt, args


async def test_ui_agent_transfer_preserves_canonical_history_lineage_and_dirty_work(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, out1, out2 = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        original = SessionStore.for_app(app.data_dir, root).directory(sid) / 'transcript.jsonl'
        raw = original.read_bytes()
        receipt, args = await export(app, target, sid, root)
        duplicate = (await app.app_bridge('dispatch', {'action': 'portability.export', 'id': 'export-original', 'args': args}, sid))['result']
        assert duplicate['duplicate']
        with pytest.raises(SessionTransferFencedError): SharedSessionStore(root, sid).acquire(app='independent-cli')
        with pytest.raises(ValueError, match='handoff'): await app.runtime.start(copy.deepcopy(app._session(sid)), app.on_runtime_event)
        host_env(monkeypatch, destination)
        staged = (await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)}))['result']
        working = Path(staged['destinationState']['workspace'])
        assert not target.state['sessions']
        with pytest.raises(SessionTransferFencedError): SharedSessionStore(working, sid).acquire(app='independent-tui')
        assert (working / 'a.txt').read_text() == 'staged\nunstaged\n'
        assert git(working, 'show', ':a.txt') == b'staged\n'
        assert (working / 'new.txt').read_text() == 'Untracked work'
        host_env(monkeypatch, source)
        released = (await app.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': receipt['revision'], 'path': staged['receiptPath']}))['result']
        assert not original.exists()
        assert (Path(released['archive']) / 'transcript.jsonl').read_bytes() == raw
        with pytest.raises(SessionTransferFencedError): SharedSessionStore(root, sid).acquire(app='independent-cli')
        host_env(monkeypatch, destination)
        active = (await target.dispatch('portability.activate', {'sessionId': sid, 'id': staged['id'], 'expectedRevision': staged['revision'], 'path': released['receiptPath']}))['result']
        assert active['phase'] == 'active' and not target.runtime.workers
        session = target._session(sid)
        assert session['nativeIdentity'] == sid and session['task']['id'] == 'task-original'
        assert session['draft'] == 'Unsent draft stays unsent'
        assert any(row.get('voiceId') == 'voice-original' for row in session['messages'])
        assert SessionStore.for_app(target.data_dir, working).history(sid).transcript_path.read_bytes() == raw
        assert target.outputs.store.read(out2['id'])['parentId'] == out1['id']
        assert target.outputs.store.read(out2['id'])['evidenceIds'] == [out1['id']]
        assert target.outputs.content(target.outputs.store.read(out2['id'])) == b'Second output'
        assert target.outputs.store.comments(out2['id'])[0]['body'] == 'Retain this review'
        held = SharedSessionStore(working, sid).acquire(app='independent-tui'); held.release()
        assert app.portability.fenced(sid) and not target.portability.fenced(sid)
        assert not list((destination / 'native').rglob('keys.env'))
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


async def test_rejected_account_stage_remains_fenced_and_never_replayed(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        receipt, _ = await export(app, target, sid, root)
        async def reject(*_): raise ValueError('Destination account is unavailable')
        monkeypatch.setattr(Portability, 'destination_checks', reject)
        host_env(monkeypatch, destination)
        with pytest.raises(AppError, match='account is unavailable'):
            await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)})
        assert target.portability.node.get(receipt['id'])['phase'] == 'unknown'
        duplicate = (await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)}))['result']
        assert duplicate['duplicate'] and duplicate['phase'] == 'unknown'
        assert not target.runtime.workers
        assert app.portability.fenced(sid)
        host_env(monkeypatch, source)
        cancelled = (await app.dispatch('portability.cancel', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': receipt['revision'], 'evidence': 'Destination credentials unavailable; keep this task here.'}))['result']
        assert not app.portability.fenced(sid)
        held = SharedSessionStore(root, sid).acquire(app='source-restored'); held.release()
        host_env(monkeypatch, destination)
        unknown = target.portability.node.get(receipt['id'])
        discarded = (await target.dispatch('portability.discard', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': unknown['revision'], 'path': cancelled['receiptPath']}))['result']
        assert discarded['phase'] == 'discarded' and not target.state['sessions']
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


async def test_interrupted_release_can_finish_exact_archival_without_rollback(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        receipt, _ = await export(app, target, sid, root)
        host_env(monkeypatch, destination)
        staged = (await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)}))['result']
        host_env(monkeypatch, source)
        import os
        original_replace = os.replace
        def fail_archive(src, dst):
            if '/archives/' in str(dst) and str(dst).endswith('transcript.jsonl'):
                raise OSError('Injected archive interruption')
            return original_replace(src, dst)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'replace', fail_archive)
            with pytest.raises(AppError, match='archive interruption'):
                await app.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': receipt['revision'], 'path': staged['receiptPath']})
        interrupted = app.portability.node.get(receipt['id'])
        assert interrupted['phase'] == 'unknown' and interrupted['previousPhase'] == 'releasing'
        assert SharedSessionStore(root, sid).transfer_fence()['phase'] == 'committed'
        released = (await app.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': interrupted['revision'], 'path': staged['receiptPath']}))['result']
        assert released['phase'] == 'released' and app.portability.fenced(sid)
        assert (Path(released['archive']) / 'transcript.jsonl').exists()
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


async def test_activation_commits_before_unfencing_and_retry_does_not_reinstall(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        receipt, _ = await export(app, target, sid, root)
        with pytest.raises(AppError, match='fenced'):
            await app.dispatch('outputs.write', {'sessionId': sid, 'title': 'Late', 'content': 'Cannot disappear', 'variant': 'document'})
        host_env(monkeypatch, destination)
        staged = (await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)}))['result']
        assert not (SessionStore.for_app(target.data_dir, staged['destinationState']['workspace']).directory(sid) / 'transcript.jsonl').exists()
        host_env(monkeypatch, source)
        released = (await app.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': receipt['revision'], 'path': staged['receiptPath']}))['result']
        host_env(monkeypatch, destination)
        arguments = {'sessionId': sid, 'id': staged['id'], 'expectedRevision': staged['revision'], 'path': released['receiptPath']}
        with monkeypatch.context() as patch:
            def fail_clear(*_): raise OSError('Injected native clear interruption')
            patch.setattr(Portability, 'finish_native_activation', fail_clear)
            with pytest.raises(AppError, match='native clear interruption'):
                await target.dispatch('portability.activate', arguments)
        active = target.portability.node.get(staged['id'])
        assert active['phase'] == 'active' and target.portability.fenced(sid)
        native = SessionStore.for_app(target.data_dir, staged['destinationState']['workspace']).directory(sid) / 'transcript.jsonl'
        before = native.stat().st_mtime_ns
        duplicate = (await target.dispatch('portability.activate', arguments))['result']
        assert duplicate['duplicate'] and not target.portability.fenced(sid)
        assert native.stat().st_mtime_ns == before and not target.runtime.workers
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


async def test_transfer_reviews_schedules_and_advances_execution_revision_on_roundtrip(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)

    def save_active_schedule(host, identity, execution_revision):
        host.schedules.store.put({'id': identity, 'sessionId': sid, 'revision': 1,
            'status': 'active', 'nextDue': 1, 'executionRevision': execution_revision,
            'taskId': 'task-original', 'taskRevision': 4, 'interruptionRevision': 0,
            'prompt': 'Review retained work', 'kind': 'monitor', 'destination': 'same_task',
            'missedRunPolicy': 'latest', 'notificationPolicy': 'changes',
            'spec': {'kind': 'interval', 'seconds': 3600}, 'createdAt': 0, 'updatedAt': 0})

    reviewed_before_quiesce = []
    for host in (app, target):
        monkeypatch.setattr(host.runtime, 'control', AsyncMock(side_effect=AssertionError('Unexpected task control')))
        monkeypatch.setattr(host.runtime, 'scheduled_input', AsyncMock(side_effect=AssertionError('Unexpected scheduled input')))
        monkeypatch.setattr(host.schedules, 'task', AsyncMock(side_effect=AssertionError('Unexpected schedule preparation')))
        quiesce = host.runtime.quiesce_for_handoff

        async def checked_quiesce(*args, _host=host, _quiesce=quiesce, **kwargs):
            assert all(row['status'] == 'needs_review' for row in _host.schedules.store.list(sid))
            reviewed_before_quiesce.append(_host.data_dir)
            return await _quiesce(*args, **kwargs)

        monkeypatch.setattr(host.runtime, 'quiesce_for_handoff', checked_quiesce)

    activation_revisions = []
    finish_native = Portability.finish_native_activation

    def checked_finish(self, row, session):
        assert all(schedule['status'] == 'needs_review' for schedule in self.app.schedules.store.list(sid))
        assert self.app.portability.fenced(sid)
        activation_revisions.append(session['executionRevision'])
        return finish_native(self, row, session)

    monkeypatch.setattr(Portability, 'finish_native_activation', checked_finish)

    async def transfer(left, right, left_home, right_home, repository):
        host_env(monkeypatch, left_home)
        receipt, _ = await export(left, right, sid, Path(left._session(sid)['workspace']))
        host_env(monkeypatch, right_home)
        staged = (await right.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(repository)}))['result']
        host_env(monkeypatch, left_home)
        released = (await left.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'],
            'expectedRevision': receipt['revision'], 'path': staged['receiptPath']}))['result']
        host_env(monkeypatch, right_home)
        return (await right.dispatch('portability.activate', {'sessionId': sid, 'id': staged['id'],
            'expectedRevision': staged['revision'], 'path': released['receiptPath']}))['result']

    try:
        host_env(monkeypatch, source)
        app._session(sid)['executionRevision'] = 2
        save_active_schedule(app, 'original-schedule', 2)
        app._save()

        active = await transfer(app, target, source, destination, destrepo)
        assert active['generation'] == 1
        assert target._session(sid)['executionRevision'] == 3
        assert app.schedules.store.get(sid, 'original-schedule')['status'] == 'needs_review'
        assert not target.schedules.store.list(sid)  # Imported schedules remain historical evidence.

        # The returning host can retain a newer local execution association;
        # neither transfer generation nor the incoming revision may replace it.
        host_env(monkeypatch, source)
        app._session(sid)['executionRevision'] = 5
        save_active_schedule(app, 'retained-local-schedule', 5)
        app._save()
        host_env(monkeypatch, destination)
        save_active_schedule(target, 'destination-schedule', 3)
        target._save()

        returned = await transfer(target, app, destination, source, root)
        assert returned['generation'] == 2
        assert app._session(sid)['executionRevision'] == 6
        assert activation_revisions == [3, 6]
        assert reviewed_before_quiesce == [app.data_dir, target.data_dir]
        assert app.schedules.store.get(sid, 'retained-local-schedule')['revision'] == 2

        for host, home in ((app, source), (target, destination)):
            host_env(monkeypatch, home)
            assert all(row['status'] == 'needs_review' for row in host.schedules.store.list(sid))
            await host.schedules.tick()
            assert not host.schedules.store.runs(sid)
            host.schedules.task.assert_not_awaited()
            host.runtime.control.assert_not_awaited()
            host.runtime.scheduled_input.assert_not_awaited()
            assert not host.runtime.workers
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()
