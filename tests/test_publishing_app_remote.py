"""Shared app actions against a real private service using a controlled transport."""
import asyncio
from pathlib import Path
import tarfile
import tempfile

import pytest

from amplifier_publishing.remote import SSHClient, unix_request, unknown_outcome
from amplifier_publishing.service import PublishingService
from amplifier_web.publishing import Publishing
from amplifier_web.service import AppError
from test_publishing import app, call


@pytest.fixture
async def remote(app, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='pub-app-') as folder:
        with PublishingService(Path(folder) / 'store', Path(folder) / 'admin.sock') as service:
            requests = []
            def send(client, request):
                requests.append(request.copy())
                return unix_request(service.socket_path, request)
            monkeypatch.setattr(SSHClient, '_send', send)
            await call(app, 'target.save', targetId='private', expectedRevision=0, label='Private test',
                       hostname='fixture-host', python='/isolated/python', socketPath=str(service.socket_path))
            assert not requests
            inspected = await call(app, 'target.inspect', targetId='private', expectedRevision=1)
            target = next(row for row in inspected['targets'] if row['id'] == 'private')
            await call(app, 'target.select', targetId='private', expectedRevision=1, serviceId=target['inspection']['serviceId'])
            yield service, requests, send


async def test_remote_capture_is_separate_backup_retained_and_retry_target_frozen(app, remote):
    service, requests, _ = remote
    release = await call(app, 'build', siteId='shared-name', sourcePath='dist', requestId='remote-build')
    assert not (await call(app, 'list', targetId='loopback'))['releases']
    assert (await call(app, 'list'))['releases'][0]['id'] == release['id']
    assert app.publishing.owns_records(app._session()['id'])
    from amplifier_web.recovery import backup
    result = await backup(app)
    with tarfile.open(result['backup']) as archive:
        assert f'publishing-captures/{service.service_id}/releases/{release["id"]}/files/index.html' in archive.getnames()
    await call(app, 'target.select', targetId='loopback', expectedRevision=0)
    (Path(app._session()['workspace']) / 'dist' / 'index.html').unlink()
    count = len(requests)
    assert await call(app, 'build', siteId='shared-name', sourcePath='dist', requestId='remote-build') == release
    assert len(requests) == count
    with pytest.raises(AppError, match='retargeted'):
        await call(app, 'build', siteId='shared-name', sourcePath='dist', requestId='remote-build', targetId='loopback')
    await app.publishing.close()
    app.publishing = Publishing(app)
    assert await call(app, 'build', siteId='shared-name', sourcePath='dist', requestId='remote-build', targetId='private') == release
    assert len(requests) == count


async def test_lost_remote_deploy_response_reconciles_only_against_original_target(app, remote, monkeypatch):
    _service, requests, send = remote
    release = await call(app, 'build', siteId='loss', sourcePath='dist', requestId='build')
    await call(app, 'review', releaseId=release['id'], note='Synthetic service fixture.', requestId='review')
    def lose(client, request):
        result = send(client, request)
        if request['method'] == 'deploy':
            raise unknown_outcome(request)
        return result
    monkeypatch.setattr(SSHClient, '_send', lose)
    args = dict(siteId='loss', releaseId=release['id'], expectedRevision=0, requestId='deploy')
    with pytest.raises(AppError) as caught:
        await call(app, 'deploy', **args)
    assert caught.value.code == 'unknown_outcome'
    await call(app, 'target.select', targetId='loopback', expectedRevision=0)
    receipt = await call(app, 'deploy', **args)
    assert receipt['state'] == 'succeeded'
    assert sum(row['method'] == 'deploy' for row in requests) == 1
    assert requests[-1]['method'] == 'receipt'
    assert not (await call(app, 'list'))['sites']
    state = await call(app, 'status', targetId='private', siteId='loss')
    assert state['status'] == 'running'


async def test_capture_admission_failure_is_visible_without_import_or_recapture(app, remote, monkeypatch):
    service, requests, _ = remote
    capture = app.publishing.capture_store(service.service_id)
    def fail(*args, **kwargs):
        from amplifier_publishing import PublishingError
        raise PublishingError('unknown_outcome', 'Capture interrupted before its durable result')
    monkeypatch.setattr(capture, 'build', fail)
    args = dict(siteId='unknown', sourcePath='dist', requestId='capture-unknown')
    with pytest.raises(AppError):
        await call(app, 'build', **args)
    listing = await call(app, 'list')
    assert listing['receipts'][0]['state'] == 'unknown'
    with pytest.raises(AppError):
        await call(app, 'build', **args)
    assert not any(row['method'] == 'import' for row in requests)
    with pytest.raises(AppError, match='retained'):
        await call(app, 'target.save', targetId='private', expectedRevision=1, label='Moved', hostname='elsewhere', python='/python', socketPath='/socket')


async def test_legacy_local_receipt_does_not_follow_new_remote_selection(app, remote):
    _service, requests, _ = remote
    sid = app._session()['id']
    # A receipt created before the target registry existed has no binding row.
    release = app.publishing.store.build(Path(app._session()['workspace']) / 'dist', site_id='legacy', session_id=sid, request_id='legacy-build')
    note = 'Review predating target registry'
    expected = app.publishing.store.review(release['id'], session_id=sid, request_id='legacy-review', note=note)
    count = len(requests)
    assert await call(app, 'review', releaseId=release['id'], requestId='legacy-review', note=note) == expected
    assert len(requests) == count
    assert app.publishing.targets.lookup_request(sid, 'legacy-review')['targetId'] == 'loopback'
