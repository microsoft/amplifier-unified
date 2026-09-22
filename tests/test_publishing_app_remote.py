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
from test_publishing import app, call as raw_call


async def call(app, name, **args):
    """Normal client submissions retain their initially observed target fence."""
    if name in {'build', 'review', 'preview', 'deploy', 'rollback', 'stop', 'remove'}:
        sid = app._session()['id']
        bound = app.publishing.targets.lookup_request(sid, args['requestId'])
        legacy = any(row['requestId'] == args['requestId'] for row in app.publishing.store.receipts(sid))
        if not legacy:
            target = bound['target'] if bound else app.publishing.targets.resolve(sid, args.get('targetId'))
            if target['id'] != 'loopback':
                args = {'targetId': target['id'], 'targetRevision': target['revision'], 'serviceId': target['inspection']['serviceId'], **args}
    return await raw_call(app, name, **args)


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


async def test_stale_ui_and_agent_target_observations_cannot_import_into_replacement(app, remote):
    original, requests, _ = remote
    sid = app._session()['id']
    stale = {'targetId': 'private', 'targetRevision': 1, 'serviceId': original.service_id,
             'siteId': 'stale', 'sourcePath': 'dist'}
    with tempfile.TemporaryDirectory(prefix='pub-swap-') as folder:
        with PublishingService(Path(folder) / 'store', Path(folder) / 'admin.sock') as replacement:
            # The transport fixture deliberately routes each configured socket.
            from unittest.mock import patch
            def route(client, request):
                requests.append(request.copy())
                return unix_request(client.socket_path, request)
            with patch.object(SSHClient, '_send', route):
                await raw_call(app, 'target.save', targetId='private', expectedRevision=1, label='Replacement',
                               hostname='fixture-host', python='/isolated/python', socketPath=str(replacement.socket_path))
                await raw_call(app, 'target.inspect', targetId='private', expectedRevision=2)
                await raw_call(app, 'target.select', targetId='private', expectedRevision=2, serviceId=replacement.service_id)
                before = len(requests)
                for origin in ('ui', 'agent'):
                    args = {'sessionId': sid, 'requestId': 'stale-' + origin, **stale}
                    with pytest.raises(AppError) as rejected:
                        if origin == 'agent':
                            await app.app_bridge('dispatch', {'action': 'publishing.build', 'args': args}, sid)
                        else:
                            await app.dispatch('publishing.build', args)
                    assert rejected.value.code == 'stale_target'
                    assert app.publishing.targets.lookup_request(sid, args['requestId']) is None
                assert len(requests) == before
                assert not app.publishing.captures
                assert not original.publisher.releases(sid) and not replacement.publisher.releases(sid)
                assert not app.db.execute('SELECT 1 FROM publishing_build_requests WHERE session=?', (sid,)).fetchone()


async def test_missing_remote_observation_is_rejected_before_capture_or_ssh(app, remote):
    _service, requests, _ = remote
    before = len(requests)
    with pytest.raises(AppError) as rejected:
        await raw_call(app, 'build', targetId='private', siteId='missing', sourcePath='dist', requestId='missing-fence')
    assert rejected.value.code == 'target_observation_required'
    assert app.publishing.targets.lookup_request(app._session()['id'], 'missing-fence') is None
    assert not app.publishing.captures and len(requests) == before


async def test_malformed_reconciliation_exposes_authoritative_unknown_receipt(app, remote, monkeypatch):
    _service, requests, send = remote
    from amplifier_publishing import PublishingError
    def uncertain(client, request):
        if request['method'] == 'receipt':
            return {'unexpected': 'Malformed remote receipt'}
        result = send(client, request)
        if request['method'] == 'import':
            raise PublishingError('unknown_outcome', 'The import response was lost')
        return result
    monkeypatch.setattr(SSHClient, '_send', uncertain)
    args = {'siteId': 'uncertain', 'sourcePath': 'dist', 'requestId': 'lost-import'}
    for _ in range(2):
        with pytest.raises(AppError) as rejected:
            await call(app, 'build', **args)
        assert rejected.value.code == 'unknown_outcome' and rejected.value.status == 503
        assert rejected.value.receipt['state'] == 'unknown'
        assert rejected.value.receipt['requestId'] == 'lost-import'
    assert app.publishing.targets.lookup_request(app._session()['id'], 'lost-import')['state'] == 'unknown'
    assert sum(row['method'] == 'import' for row in requests) == 1
