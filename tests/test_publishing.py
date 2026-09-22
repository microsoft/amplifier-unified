"""Shared user/agent actions against real local publishing state and sockets."""

import asyncio
import copy
from pathlib import Path
from urllib.request import urlopen

import pytest

from amplifier_web.service import AppError, AppService
from test_service import Runtime


@pytest.fixture
async def app(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    built = workspace / 'dist'
    built.mkdir()
    (built / 'index.html').write_text('<h1>First release</h1>')
    service = AppService(tmp_path / 'app', workspace=workspace, runtime=Runtime())
    await service.dispatch('session.create', {'title': 'Publishing task'})
    service._message(service._session(), 'user', 'Keep this original.', 'text')
    await service.dispatch('view.update', {'patch': {'draft': 'Unsent publishing draft'}})
    yield service
    await service.close()


async def call(app, name, **args):
    return (await app.dispatch('publishing.' + name, {'sessionId': app._session()['id'], **args}))['result']


def get(url):
    with urlopen(url, timeout=3) as response:
        return response.read().decode()


async def test_shared_lifecycle_preserves_sources_messages_and_draft(app):
    sid = app._session()['id']
    original = copy.deepcopy(app._session()['messages'])
    first = await call(app, 'build', siteId='example', sourcePath='dist', requestId='build-one')
    preview = await call(app, 'preview', releaseId=first['id'], requestId='preview-one')
    assert 'First release' in await asyncio.to_thread(get, preview['result']['url'])
    await app.app_bridge('dispatch', {'action': 'publishing.review', 'args': {
        'releaseId': first['id'], 'note': 'Checked the saved page over loopback.', 'requestId': 'review-one'}}, sid)
    deployed = await call(app, 'deploy', siteId='example', releaseId=first['id'], expectedRevision=0, requestId='deploy-one')
    assert deployed['state'] == 'succeeded'
    status = await call(app, 'status', siteId='example')
    assert status['releaseId'] == first['id'] and status['accessPolicy'] == 'loopback-only'
    assert 'First release' in await asyncio.to_thread(get, status['url'])
    source = Path(app._session()['workspace']) / 'dist' / 'index.html'
    source.write_text('<h1>Second release</h1>')
    duplicate = await call(app, 'build', siteId='example', sourcePath='dist', requestId='build-one')
    assert duplicate['id'] == first['id']
    second = await call(app, 'build', siteId='example', sourcePath='dist', requestId='build-two')
    assert second['id'] != first['id']
    await call(app, 'review', releaseId=second['id'], note='Checked second release.', requestId='review-two')
    await call(app, 'deploy', siteId='example', releaseId=second['id'], expectedRevision=status['revision'], requestId='deploy-two')
    updated = await call(app, 'status', siteId='example')
    assert 'Second release' in await asyncio.to_thread(get, updated['url'])
    with pytest.raises(AppError):
        await call(app, 'stop', siteId='example', expectedRevision=status['revision'], requestId='stale-stop')
    await call(app, 'rollback', siteId='example', releaseId=first['id'], expectedRevision=updated['revision'], requestId='rollback-one')
    rolled = await call(app, 'status', siteId='example')
    assert 'First release' in await asyncio.to_thread(get, rolled['url'])
    await call(app, 'stop', siteId='example', expectedRevision=rolled['revision'], requestId='stop-one')
    stopped = await call(app, 'status', siteId='example')
    assert not stopped['url']
    await call(app, 'remove', siteId='example', expectedRevision=stopped['revision'], requestId='remove-one')
    listing = await call(app, 'list')
    assert len(listing['releases']) == 2 and listing['receipts']
    assert app.state['selectedSessionId'] == sid
    assert app.state['view']['draft'] == 'Unsent publishing draft'
    assert app._session()['messages'] == original and not app.runtime.sent
    assert source.read_text() == '<h1>Second release</h1>'


async def test_scope_path_target_and_review_guards(app, tmp_path):
    sid = app._session()['id']
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'index.html').write_text('private')
    (Path(app._session()['workspace']) / 'escape').symlink_to(outside)
    for i, path in enumerate([str(outside), '../outside', 'escape']):
        with pytest.raises(AppError):
            await call(app, 'build', siteId='escape', sourcePath=path, requestId=f'escape-{i}')
    release = await call(app, 'build', siteId='example', sourcePath='dist', requestId='safe-build')
    with pytest.raises(AppError):
        await call(app, 'deploy', siteId='example', releaseId=release['id'], expectedRevision=0, requestId='unreviewed')
    with pytest.raises(AppError):
        await call(app, 'deploy', siteId='example', releaseId=release['id'], expectedRevision=0, requestId='public', accessPolicy='public')
    await app.dispatch('session.create', {'title': 'Another task'})
    other = app._session()['id']
    with pytest.raises(AppError, match='calling'):
        await app.app_bridge('dispatch', {'action': 'publishing.list', 'args': {'sessionId': sid}}, other)
    with pytest.raises(AppError):
        await call(app, 'preview', releaseId=release['id'], requestId='wrong-owner')
    assert not (await call(app, 'list'))['releases']
    with pytest.raises(AppError, match='calling'):
        await app.dispatch('publishing.list', {'sessionId': sid}, origin='agent', caller_session_id=other)


async def test_packaging_tracks_execution_folder_and_deletion_retains_ownership(app, tmp_path):
    working = tmp_path / 'checkout'
    working.mkdir()
    (working / 'dist').mkdir()
    (working / 'dist' / 'index.html').write_text('Execution folder output')
    app._session()['workingDirectory'] = str(working)
    release = await call(app, 'build', siteId='example', sourcePath='dist', requestId='execution-build')
    preview = await call(app, 'preview', releaseId=release['id'], requestId='execution-preview')
    assert 'Execution folder output' in await asyncio.to_thread(get, preview['result']['url'])
    from amplifier_web.managed_deletion import _idle
    with pytest.raises(ValueError, match='retained publishing'):
        _idle(app, [app._session()], {app._session()['id']})


async def test_agent_discovers_identical_action_schema(app):
    rows = await app.app_bridge('list_actions', {'prefix': 'publishing.'}, app._session()['id'])
    assert {row['name'] for row in rows} == {'publishing.' + op for op in (
        'list', 'build', 'preview', 'review', 'deploy', 'rollback', 'status', 'logs', 'stop', 'remove',
        'target.list', 'target.save', 'target.inspect', 'target.select', 'target.remove')}


async def test_full_backup_includes_consistent_publishing_snapshot(app):
    import tarfile
    from amplifier_web.recovery import backup
    release = await call(app, 'build', siteId='backup', sourcePath='dist', requestId='backup-build')
    await call(app, 'review', releaseId=release['id'], note='Backup review.', requestId='backup-review')
    await call(app, 'deploy', siteId='backup', releaseId=release['id'], expectedRevision=0, requestId='backup-deploy')
    result = await backup(app)
    with tarfile.open(result['backup']) as archive:
        names = archive.getnames()
        assert 'publishing/state.sqlite3' in names
        assert f'publishing/releases/{release["id"]}/files/index.html' in names
        assert not any(name.endswith('owner.lock') or 'state.sqlite3-wal' in name for name in names)


async def test_conversation_reset_cannot_orphan_owned_publishing(app):
    from amplifier_web.management import Management
    from amplifier_web.recovery import reset
    sid = app._session()['id']
    await call(app, 'build', siteId='reset', sourcePath='dist', requestId='reset-build')
    with pytest.raises(ValueError, match='retained publishing'):
        await reset(Management(app), {'parts': ['conversations'], 'apply': True, 'confirmation': 'RESET'})
    assert app._session()['id'] == sid
    assert (await call(app, 'list'))['releases']


async def test_unknown_receipt_preserves_transport_uncertainty(app, monkeypatch):
    from amplifier_publishing import PublishingError
    def unknown(*_args, **_kwargs):
        raise PublishingError('unknown_outcome', 'Operation outcome is unknown; it will not be replayed')
    monkeypatch.setattr(app.publishing.store, 'build', unknown)
    with pytest.raises(AppError) as caught:
        await call(app, 'build', siteId='unknown', sourcePath='dist', requestId='unknown-build')
    assert caught.value.code == 'unknown_outcome' and caught.value.status == 503


async def test_receipt_only_ownership_survives_deletion_and_reset(app):
    from amplifier_web.managed_deletion import _idle
    from amplifier_web.management import Management
    from amplifier_web.recovery import reset
    with pytest.raises(AppError):
        await call(app, 'build', siteId='failed', sourcePath='missing', requestId='failed-build')
    assert not (await call(app, 'list'))['releases']
    assert (await call(app, 'list'))['receipts']
    with pytest.raises(ValueError, match='retained publishing'):
        _idle(app, [app._session()], {app._session()['id']})
    with pytest.raises(ValueError, match='retained publishing'):
        await reset(Management(app), {'parts': ['conversations'], 'apply': True, 'confirmation': 'RESET'})


async def test_exact_build_retry_uses_original_admission_after_source_and_host_change(app, tmp_path):
    first = await call(app, 'build', siteId='retry', sourcePath='dist', requestId='saved-build')
    workspace = Path(app._session()['workspace'])
    (workspace / 'dist').rename(workspace / 'saved')
    (workspace / 'dist').symlink_to(workspace / 'saved')
    assert (await call(app, 'build', siteId='retry', sourcePath='dist', requestId='saved-build')) == first
    app._session().update(workingDirectory=str(tmp_path / 'other'), executionHost={'scope': 'remote', 'id': 'other-host'})
    assert (await call(app, 'build', siteId='retry', sourcePath='dist', requestId='saved-build')) == first
    with pytest.raises(AppError):
        await call(app, 'build', siteId='retry', sourcePath='dist', requestId='new-build')
    with pytest.raises(AppError, match='different'):
        await call(app, 'build', siteId='retry', sourcePath='changed', requestId='saved-build')
    await call(app, 'review', releaseId=first['id'], note='Retained local release.', requestId='review')
    await call(app, 'deploy', siteId='retry', releaseId=first['id'], expectedRevision=0, requestId='deploy')
    await call(app, 'stop', siteId='retry', expectedRevision=1, requestId='stop')
    assert (await call(app, 'status', siteId='retry'))['status'] == 'stopped'


async def test_interrupted_build_admission_retains_unknown_without_replay(app, monkeypatch):
    from amplifier_publishing import PublishingError
    def unavailable(*_args, **_kwargs):
        raise PublishingError('unknown_outcome', 'Interrupted before library admission')
    with monkeypatch.context() as patch:
        patch.setattr(app.publishing.store, 'build', unavailable)
        with pytest.raises(AppError):
            await call(app, 'build', siteId='pending', sourcePath='dist', requestId='pending-build')
    with pytest.raises(AppError, match='not be replayed'):
        await call(app, 'build', siteId='pending', sourcePath='dist', requestId='pending-build')
    listing = await call(app, 'list')
    assert not listing['releases']
    assert listing['receipts'][0]['state'] == 'unknown'


async def test_history_cleanup_preserves_old_publishing_owner(app):
    from amplifier_web.management import Management
    sid = app._session()['id']
    await call(app, 'build', siteId='cleanup', sourcePath='dist', requestId='cleanup-build')
    await app.dispatch('session.create', {'title': 'Current task'})
    owner = app._session(sid)
    owner.update(createdAt=0, updatedAt=0, status='idle')
    for message in owner['messages']:
        message['createdAt'] = 0
    await Management(app).perform('history.cleanup', {'days': 1, 'apply': True, 'purge': True})
    assert app._session(sid)['id'] == sid
    assert not any(row['id'] == sid for row in app.state['cleanupPreview']['sessions'])


@pytest.mark.parametrize('request_id', ['build release', 'build\n', '.build', ''])
async def test_invalid_request_id_never_records_admission(app, request_id):
    with pytest.raises(AppError):
        await call(app, 'build', siteId='invalid', sourcePath='dist', requestId=request_id)
    assert not (await call(app, 'list'))['receipts']
    assert not app.publishing.owns_records(app._session()['id'])
