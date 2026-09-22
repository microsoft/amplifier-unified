"""Task-scoped target controls against real isolated private services, no SSH host."""

import base64
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

import pytest

from amplifier_publishing import PublishingError
from amplifier_publishing.remote import SSHClient, digest, unix_request
from amplifier_publishing.service import PublishingService
from amplifier_web.publishing_targets import PublishingTargets, definitions


@pytest.fixture
def environment(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='targets-', dir=Path('/tmp').resolve()) as directory:
        base = Path(directory)
        with PublishingService(base / 'one', base / 'admin-one' / 'control.sock') as first, PublishingService(base / 'two', base / 'admin-two' / 'control.sock') as second:
            db = sqlite3.connect(tmp_path / 'app.sqlite3')

            def session(sid):
                if sid not in {'task', 'other'}:
                    raise ValueError('Task not found')
                return {'id': sid, 'messages': [{'text': 'PRIVATE-TRANSCRIPT-NOT-TRANSFERRED'}]}

            app = SimpleNamespace(db=db, _session=session, credentials='PRIVATE-CREDENTIAL-NOT-TRANSFERRED')
            registry = PublishingTargets(app, lambda: {'id': 'loopback', 'accessPolicy': 'loopback-only'})
            calls = []

            def send(client, request):
                calls.append((client.socket_path, copy.deepcopy(request)))
                return unix_request(client.socket_path, request)

            monkeypatch.setattr(SSHClient, '_send', send)
            yield registry, first, second, calls
            db.close()


def config(service, *, target_id='private', revision=0, sid='task', **changes):
    return {'sessionId': sid, 'targetId': target_id, 'expectedRevision': revision, 'label': 'Private service', 'hostname': 'existing-private-host', 'python': sys.executable, 'socketPath': str(service.socket_path), **changes}


async def choose(registry, service, *, target_id='private', sid='task'):
    await registry.dispatch('publishing.target.save', config(service, target_id=target_id, sid=sid))
    await registry.dispatch('publishing.target.inspect', {'sessionId': sid, 'targetId': target_id, 'expectedRevision': 1})
    return await registry.dispatch('publishing.target.select', {'sessionId': sid, 'targetId': target_id, 'expectedRevision': 1, 'serviceId': service.service_id})


def exported(body=b'<h1>One</h1>', sid='task'):
    manifest = [{'path': 'index.html', 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}]
    return {'sessionId': sid, 'siteId': 'site', 'manifest': manifest, 'manifestDigest': digest(manifest), 'files': {'index.html': base64.b64encode(body).decode()}}


def observed_args(registry, args):
    """Model a caller retaining the target observation with its submitted args."""
    previous = registry.lookup_request(args['sessionId'], args['requestId'])
    target = previous['target'] if previous else registry.resolve(args['sessionId'], args.get('targetId'))
    if target['id'] == 'loopback':
        return args
    return {'targetId': target['id'], 'targetRevision': target['revision'], 'serviceId': target['inspection']['serviceId'], **args}


def build_args(registry, request_id='build', *, observe=True, **more):
    args = {'sessionId': 'task', 'siteId': 'site', 'sourcePath': 'dist', 'requestId': request_id, **more}
    return observed_args(registry, args) if observe else args


async def operation(registry, method, **args):
    request = {'sessionId': 'task', **args}
    if method not in {'list', 'status', 'logs'}:
        request = observed_args(registry, request)
    return await registry.dispatch_remote('publishing.' + method, request)


async def test_save_and_list_never_connect_and_task_scopes_are_distinct(environment):
    registry, service, _, calls = environment
    result = await registry.dispatch('publishing.target.save', config(service))
    assert calls == []
    assert result['selectedTargetId'] == 'loopback' and result['target']['kind'] == 'loopback'
    remote = result['targets'][1]
    assert remote['revision'] == 1 and remote['inspection'] is None and remote['expectedBind'] == '127.0.0.1'
    assert registry.list('other')['targets'] == [registry._local()]
    assert registry.owns_records('task') and not registry.owns_records('other')
    with pytest.raises(PublishingError) as missing:
        await registry.dispatch('publishing.target.inspect', {'sessionId': 'other', 'targetId': 'private', 'expectedRevision': 1})
    assert missing.value.code == 'not_found' and calls == []
    for bad in ({'password': 'do-not-store'}, {'privateKey': '/key'}):
        with pytest.raises(PublishingError) as invalid:
            await registry.dispatch('publishing.target.save', {**config(service), **bad})
        assert invalid.value.code == 'invalid_argument'
    assert 'do-not-store' not in json.dumps(registry.list('task'))


async def test_inspection_selection_and_configuration_revision_guards(environment):
    registry, service, second, calls = environment
    await registry.dispatch('publishing.target.save', config(service))
    with pytest.raises(PublishingError) as uninspected:
        registry.select('task', {'targetId': 'private', 'expectedRevision': 1, 'serviceId': service.service_id})
    assert uninspected.value.code == 'inspection_required'
    observed = await registry.dispatch('publishing.target.inspect', {'sessionId': 'task', 'targetId': 'private', 'expectedRevision': 1})
    target = observed['targets'][1]
    assert target['inspection']['serviceId'] == service.service_id
    assert target['inspection']['capabilities']['authentication'] == 'none'
    assert target['accessPolicy'] == 'loopback-only'
    with pytest.raises(PublishingError) as wrong:
        registry.select('task', {'targetId': 'private', 'expectedRevision': 1, 'serviceId': second.service_id})
    assert wrong.value.code == 'service_mismatch'
    selected = registry.select('task', {'targetId': 'private', 'expectedRevision': 1, 'serviceId': service.service_id})
    repeated = registry.select('task', {'targetId': 'private', 'expectedRevision': 1, 'serviceId': service.service_id})
    assert repeated == selected
    registry.save('task', config(second, revision=1))
    with pytest.raises(PublishingError) as old:
        registry.resolve('task')
    assert old.value.code == 'inspection_required'
    await registry.inspect('task', {'targetId': 'private', 'expectedRevision': 2})
    with pytest.raises(PublishingError) as stale:
        registry.resolve('task')
    assert stale.value.code == 'selection_required'
    registry.select('task', {'targetId': 'private', 'expectedRevision': 2, 'serviceId': second.service_id})
    assert registry.resolve('task')['socketPath'] == str(second.socket_path)
    assert all(request['method'] == 'target' for _, request in calls)


async def test_real_remote_lifecycle_and_exact_transfer_retry(environment):
    registry, service, _, calls = environment
    await choose(registry, service)
    args = build_args(registry)
    binding = registry.bind_request('task', 'publishing.build', args)
    assert binding['serviceId'] == service.service_id and binding['state'] == 'bound'
    assert await registry.resume_request(binding) == (False, None)
    first = await registry.import_release(args, exported(), binding)
    ready, same = await registry.resume_request(binding)
    assert ready is True and same == first
    assert await registry.import_release({**args, 'targetId': 'private'}, exported()) == first
    preview = await operation(registry, 'preview', releaseId=first['id'], requestId='preview')
    assert preview['result']['accessPolicy'] == 'loopback-only'
    await operation(registry, 'review', releaseId=first['id'], requestId='review', note='Checked saved page')
    deployed = await operation(registry, 'deploy', siteId='site', releaseId=first['id'], expectedRevision=0, requestId='deploy')
    second = await registry.import_release(build_args(registry, 'build-2'), exported(b'<h1>Two</h1>'))
    await operation(registry, 'review', releaseId=second['id'], requestId='review-2', note='Checked update')
    await operation(registry, 'deploy', siteId='site', releaseId=second['id'], expectedRevision=1, requestId='deploy-2')
    rolled = await operation(registry, 'rollback', siteId='site', releaseId=first['id'], expectedRevision=2, requestId='rollback')
    assert rolled['result']['releaseId'] == first['id']
    await operation(registry, 'stop', siteId='site', expectedRevision=3, requestId='stop')
    await operation(registry, 'remove', siteId='site', expectedRevision=4, requestId='remove')
    result = await operation(registry, 'list')
    assert result['target']['id'] == result['selectedTargetId'] == 'private'
    assert result['sites'][0]['status'] == 'removed'
    assert len(result['releases']) == 2 and len(result['receipts']) == 10
    logs = await operation(registry, 'logs', siteId='site')
    assert len(logs['items']) == 10
    with pytest.raises(PublishingError) as changed:
        await registry.import_release(args, exported(b'changed bytes'))
    assert changed.value.code == 'request_conflict'
    transmitted = json.dumps(calls)
    assert 'sourcePath' not in transmitted and 'PRIVATE-TRANSCRIPT' not in transmitted and 'PRIVATE-CREDENTIAL' not in transmitted
    assert deployed['state'] == 'succeeded'


async def test_lost_success_reconciles_original_target_after_selection_change(environment, monkeypatch):
    registry, service, _, calls = environment
    await choose(registry, service)
    first = await registry.import_release(build_args(registry), exported())
    await operation(registry, 'review', releaseId=first['id'], requestId='review', note='Checked')
    original = SSHClient._send

    def lost_response(client, request):
        result = original(client, request)
        if request['method'] == 'deploy':
            raise PublishingError('unknown_outcome', 'Connection lost after remote success')
        return result

    monkeypatch.setattr(SSHClient, '_send', lost_response)
    args = observed_args(registry, {'sessionId': 'task', 'siteId': 'site', 'releaseId': first['id'], 'expectedRevision': 0, 'requestId': 'uncertain'})
    with pytest.raises(PublishingError) as unknown:
        await registry.dispatch_remote('publishing.deploy', args)
    assert unknown.value.code == 'unknown_outcome'
    assert registry.lookup_request('task', 'uncertain')['state'] == 'unknown'
    registry.select('task', {'targetId': 'loopback', 'expectedRevision': 0})
    result = await registry.dispatch_remote('publishing.deploy', args)
    assert result['state'] == 'succeeded'
    assert registry.lookup_request('task', 'uncertain')['targetId'] == 'private'
    assert sum(request['method'] == 'deploy' for _, request in calls) == 1
    assert sum(request['method'] == 'receipt' for _, request in calls) == 1
    with pytest.raises(PublishingError) as retarget:
        registry.bind_request('task', 'publishing.deploy', {**args, 'targetId': 'loopback'})
    assert retarget.value.code == 'request_conflict'


async def test_unknown_absent_receipt_and_restart_never_replays(environment, monkeypatch):
    registry, service, _, calls = environment
    await choose(registry, service)
    original = SSHClient._send

    def disconnected(client, request):
        if request['method'] == 'import':
            calls.append((client.socket_path, copy.deepcopy(request)))
            raise PublishingError('unknown_outcome', 'Connection failed with uncertain delivery')
        return original(client, request)

    monkeypatch.setattr(SSHClient, '_send', disconnected)
    args = build_args(registry)
    with pytest.raises(PublishingError):
        await registry.import_release(args, exported())
    record = registry.lookup_request('task', 'build')
    record['state'] = 'running'  # Simulate an app crash after durable admission.
    registry._put_request(record)
    restarted = PublishingTargets(registry.app, registry.local_target)
    assert restarted.lookup_request('task', 'build')['state'] == 'unknown'
    for _ in range(2):
        with pytest.raises(PublishingError) as unknown:
            await restarted.import_release(args, exported())
        assert unknown.value.code == 'unknown_outcome'
    assert sum(request['method'] == 'import' for _, request in calls) == 1
    assert sum(request['method'] == 'receipt' for _, request in calls) == 2


async def test_configuration_and_identity_cannot_replace_used_target(environment, monkeypatch):
    registry, service, second, _ = environment
    await choose(registry, service)
    registry.bind_request('task', 'publishing.build', build_args(registry))
    registry.select('task', {'targetId': 'loopback', 'expectedRevision': 0})
    for mutate in (
        lambda: registry.remove('task', {'targetId': 'private', 'expectedRevision': 1}),
        lambda: registry.save('task', config(second, revision=1)),
    ):
        with pytest.raises(PublishingError) as in_use:
            mutate()
        assert in_use.value.code == 'target_in_use'
    original = SSHClient._send

    def swapped(client, request):
        return unix_request(second.socket_path, request) if client.socket_path == str(service.socket_path) else original(client, request)

    monkeypatch.setattr(SSHClient, '_send', swapped)
    with pytest.raises(PublishingError) as replaced:
        await registry.inspect('task', {'targetId': 'private', 'expectedRevision': 1})
    assert replaced.value.code == 'target_mismatch'
    assert registry._row('task', 'private')['inspection']['serviceId'] == service.service_id
    with pytest.raises(PublishingError) as guarded:
        await registry.import_release(build_args(registry), exported())
    assert guarded.value.code == 'target_mismatch'
    assert second.publisher.releases('task') == []


async def test_unused_remove_tombstone_and_legacy_binding(environment):
    registry, service, _, calls = environment
    registry.save('task', config(service))
    result = registry.remove('task', {'targetId': 'private', 'expectedRevision': 1})
    assert [t['id'] for t in result['targets']] == ['loopback']
    with pytest.raises(PublishingError) as retained:
        registry.save('task', config(service, revision=2))
    assert retained.value.code == 'target_removed' and calls == []
    await choose(registry, service, target_id='new-private')
    legacy = registry.bind_request('task', 'publishing.build', build_args(registry, 'old-build', observe=False), fallback_target_id='loopback')
    assert legacy['targetId'] == 'loopback'
    assert registry.bind_request('task', 'publishing.build', build_args(registry, 'old-build'))['targetId'] == 'loopback'
    with pytest.raises(PublishingError) as conflict:
        registry.bind_request('task', 'publishing.build', build_args(registry, 'old-build', targetId='new-private'))
    assert conflict.value.code == 'request_conflict'


async def test_capture_failure_is_durable_and_never_imported(environment):
    registry, service, _, calls = environment
    await choose(registry, service)
    binding = registry.bind_request('task', 'publishing.build', build_args(registry))
    failure = registry.capture_failed(binding, PublishingError('unsafe_source', 'Built output is outside the task workspace'))
    assert failure['state'] == 'failed' and failure['captureFailed'] is True
    with pytest.raises(PublishingError) as saved:
        await registry.resume_request(binding)
    assert saved.value.code == 'unsafe_source'
    with pytest.raises(PublishingError) as repeated:
        await registry.import_release(build_args(registry), exported())
    assert repeated.value.code == 'unsafe_source'
    assert all(request['method'] == 'target' for _, request in calls)
    uncertain = registry.bind_request('task', 'publishing.build', build_args(registry, 'uncertain-capture'))
    registry.capture_failed(uncertain, PublishingError('unknown_outcome', 'Capture interrupted before a result was recorded'))
    with pytest.raises(PublishingError) as unknown:
        await registry.resume_request(uncertain)
    assert unknown.value.code == 'unknown_outcome'
    assert [request['method'] for _, request in calls].count('receipt') == 1
    assert all(request['method'] != 'import' for _, request in calls)


async def test_receipt_reconciliation_rejects_wrong_remote_action(environment, monkeypatch):
    registry, service, _, _ = environment
    await choose(registry, service)
    binding = registry.bind_request('task', 'publishing.build', build_args(registry))
    registry.capture_failed(binding, PublishingError('unknown_outcome', 'Interrupted'))
    original = SSHClient._send

    def wrong_receipt(client, request):
        if request['method'] == 'receipt':
            return {'id': binding['id'], 'sessionId': 'task', 'requestId': 'build', 'action': 'deploy', 'siteId': 'site', 'state': 'succeeded', 'result': {}, 'error': None}
        return original(client, request)

    monkeypatch.setattr(SSHClient, '_send', wrong_receipt)
    with pytest.raises(PublishingError) as invalid:
        await registry.resume_request(binding)
    assert invalid.value.code == 'unknown_outcome'
    assert invalid.value.receipt['reconciliationError']['code'] == 'request_conflict'
    assert registry.lookup_request('task', 'build')['state'] == 'unknown'


async def test_mismatched_import_response_requires_exact_receipt_reconciliation(environment, monkeypatch):
    registry, service, _, calls = environment
    await choose(registry, service)
    original = SSHClient._send

    def changed_response(client, request):
        result = original(client, request)
        return {**result, 'manifestDigest': '0' * 64} if request['method'] == 'import' else result

    monkeypatch.setattr(SSHClient, '_send', changed_response)
    with pytest.raises(PublishingError) as error:
        await registry.import_release(build_args(registry), exported())
    assert error.value.code == 'unknown_outcome'
    binding = registry.lookup_request('task', 'build')
    handled, release = await registry.resume_request(binding)
    assert handled is True and release['manifestDigest'] == exported()['manifestDigest']
    assert sum(request['method'] == 'import' for _, request in calls) == 1


@pytest.mark.parametrize('action', ['review', 'deploy'])
@pytest.mark.parametrize('legacy', [False, True])
async def test_lost_conflict_never_adopts_different_payload_receipt(environment, monkeypatch, action, legacy):
    registry, service, _, calls = environment
    await choose(registry, service)
    release = await registry.import_release(build_args(registry), exported())
    base = {'method': action, 'sessionId': 'task', 'requestId': 'collision', 'releaseId': release['id']}
    if action == 'review':
        old = {**base, 'note': 'Original review note'}
        desired = {**base, 'note': 'Different requested review note'}
    else:
        await operation(registry, 'review', releaseId=release['id'], requestId='review', note='Checked')
        old = {**base, 'siteId': 'site', 'expectedRevision': 0}
        desired = {**base, 'siteId': 'site', 'expectedRevision': 1}
    if legacy:
        if action == 'review':
            service.publisher.review(release['id'], session_id='task', request_id='collision', note=old['note'])
        else:
            service.publisher.deploy(release['id'], session_id='task', request_id='collision', site_id='site', expected_revision=0)
    else:
        registry._client(registry.resolve('task')).request(old)
    previous_calls = sum(request['method'] == action for _, request in calls)
    original = SSHClient._send

    def lost_conflict(client, request):
        try:
            return original(client, request)
        except PublishingError as exc:
            if request.get('requestId') == 'collision' and request['method'] == action and exc.code == 'request_conflict':
                raise PublishingError('unknown_outcome', 'The conflicting response was lost') from exc
            raise

    monkeypatch.setattr(SSHClient, '_send', lost_conflict)
    args = observed_args(registry, {key: value for key, value in desired.items() if key != 'method'})
    with pytest.raises(PublishingError) as uncertain:
        await registry.dispatch_remote('publishing.' + action, args)
    assert uncertain.value.code == 'unknown_outcome'
    record = registry.lookup_request('task', 'collision')
    expected = digest({**desired, 'expectedServiceId': service.service_id})
    assert record['rpcPayloadDigest'] == expected
    for _ in range(2):
        with pytest.raises(PublishingError) as unproven:
            await registry.dispatch_remote('publishing.' + action, args)
        assert unproven.value.code == 'unknown_outcome'
    listing = await operation(registry, 'list')
    row = next(r for r in listing['receipts'] if r['requestId'] == 'collision')
    assert row['state'] == 'unknown' and row['remoteReceiptVerified'] is False
    assert row['reconciliationError']['code'] == 'unknown_outcome'
    assert sum(request['method'] == action for _, request in calls) == previous_calls + 1
    if action == 'review':
        assert service.publisher.releases('task')[0]['review']['note'] == old['note']
    else:
        assert service.publisher.status('site', 'task')['revision'] == 1


async def test_legacy_unknown_local_admission_does_not_fabricate_payload_proof(environment, monkeypatch):
    registry, service, _, calls = environment
    await choose(registry, service)
    original = SSHClient._send

    def lost_success(client, request):
        result = original(client, request)
        if request['method'] == 'import':
            raise PublishingError('unknown_outcome', 'Successful import response was lost')
        return result

    monkeypatch.setattr(SSHClient, '_send', lost_success)
    with pytest.raises(PublishingError):
        await registry.import_release(build_args(registry), exported())
    record = registry.lookup_request('task', 'build')
    record.pop('rpcPayloadDigest')
    registry._put_request(record)
    for _ in range(2):
        with pytest.raises(PublishingError) as unknown:
            await registry.import_release(build_args(registry), exported())
        assert unknown.value.code == 'unknown_outcome'
    assert 'rpcPayloadDigest' not in registry.lookup_request('task', 'build')
    assert sum(request['method'] == 'import' for _, request in calls) == 1


async def test_legacy_remote_receipt_without_proof_stays_unknown_even_for_same_args(environment):
    registry, service, _, calls = environment
    await choose(registry, service)
    release = await registry.import_release(build_args(registry), exported())
    args = {'sessionId': 'task', 'requestId': 'legacy-review', 'releaseId': release['id'], 'note': 'Same review text'}
    service.publisher.review(release['id'], session_id='task', request_id='legacy-review', note=args['note'])
    record = registry.bind_request('task', 'publishing.review', observed_args(registry, args))
    record.update(state='unknown', rpcPayloadDigest=digest({'method': 'review', **args, 'expectedServiceId': service.service_id}))
    registry._put_request(record)
    before = sum(request['method'] == 'review' for _, request in calls)
    with pytest.raises(PublishingError) as unknown:
        await registry.resume_request(record)
    assert unknown.value.code == 'unknown_outcome'
    assert registry.lookup_request('task', 'legacy-review')['state'] == 'unknown'
    assert sum(request['method'] == 'review' for _, request in calls) == before


def test_action_schemas_require_revision_but_keep_service_id_optional_for_loopback():
    def schema(properties, required=None):
        return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required, 'additionalProperties': False}

    rows = definitions(schema, lambda n: {'type': 'string', 'maxLength': n})
    assert set(rows) == {'publishing.target.' + action for action in ('list', 'save', 'inspect', 'select', 'remove')}
    assert rows['publishing.target.save'][1]['required'] == ['sessionId', 'targetId', 'expectedRevision', 'label', 'hostname', 'python', 'socketPath']
    assert 'serviceId' not in rows['publishing.target.select'][1]['required']
    assert rows['publishing.target.select'][1]['properties']['expectedRevision']['minimum'] == 0


@pytest.mark.parametrize('change,code', [
    ({'targetRevision': None}, 'target_observation_required'),
    ({'targetRevision': True}, 'target_observation_required'),
    ({'serviceId': None}, 'target_observation_required'),
    ({'targetRevision': 0}, 'stale_target'),
    ({'serviceId': '00000000-0000-4000-8000-000000000000'}, 'stale_target'),
])
async def test_remote_first_admission_requires_observed_revision_and_identity(environment, change, code):
    registry, service, _, calls = environment
    await choose(registry, service)
    args = {**build_args(registry), **change}
    for field in ('targetRevision', 'serviceId'):
        if args.get(field) is None:
            args.pop(field, None)
    before = len(calls)
    with pytest.raises(PublishingError) as rejected:
        registry.bind_request('task', 'publishing.build', args)
    assert rejected.value.code == code and rejected.value.receipt is None
    assert registry.lookup_request('task', 'build') is None and len(calls) == before


async def test_stale_display_of_same_target_id_cannot_admit_first_request_to_replacement(environment):
    registry, first, second, calls = environment
    await choose(registry, first)
    old_args = build_args(registry)
    registry.save('task', config(second, revision=1))
    await registry.inspect('task', {'targetId': 'private', 'expectedRevision': 2})
    registry.select('task', {'targetId': 'private', 'expectedRevision': 2, 'serviceId': second.service_id})
    before = len(calls)
    with pytest.raises(PublishingError) as stale:
        await registry.import_release(old_args, exported())
    assert stale.value.code == 'stale_target' and stale.value.receipt is None
    assert registry.lookup_request('task', 'build') is None and len(calls) == before
    assert not first.publisher.releases('task') and not second.publisher.releases('task')
    current = build_args(registry)
    result = await registry.import_release(current, exported())
    assert second.publisher.releases('task')[0]['id'] == result['id']
    assert not first.publisher.releases('task')


async def test_recorded_legacy_args_retry_without_fence_keeps_original_target(environment):
    registry, service, _, _ = environment
    await choose(registry, service)
    old_args = build_args(registry, observe=False)
    record = registry.bind_request('task', 'publishing.build', build_args(registry))
    # This is a retained pre-fence admission, not a fresh request without a fence.
    record['signature'] = digest({'action': 'publishing.build', 'args': {**old_args, 'targetId': 'private'}})
    registry._put_request(record)
    registry.select('task', {'targetId': 'loopback', 'expectedRevision': 0})
    retried = await registry.import_release(old_args, exported())
    assert service.publisher.releases('task')[0]['id'] == retried['id']
    assert registry.lookup_request('task', 'build')['targetId'] == 'private'


@pytest.mark.parametrize('code', ['invalid_response', 'request_conflict', 'integrity_error', 'target_mismatch'])
async def test_reconciliation_read_failures_retain_unknown_outcome_and_diagnostic(environment, monkeypatch, code):
    registry, service, _, _ = environment
    await choose(registry, service)
    record = registry.bind_request('task', 'publishing.build', build_args(registry))
    registry.capture_failed(record, PublishingError('unknown_outcome', 'Capture outcome was lost'))
    original = SSHClient._send

    def failed_read(client, request):
        if request['method'] == 'receipt':
            raise PublishingError(code, 'Receipt could not be verified')
        return original(client, request)

    monkeypatch.setattr(SSHClient, '_send', failed_read)
    with pytest.raises(PublishingError) as error:
        await registry.resume_request(record)
    assert error.value.code == 'unknown_outcome'
    assert error.value.receipt['state'] == 'unknown'
    assert error.value.receipt['reconciliationError']['code'] == code
    assert registry.lookup_request('task', 'build')['error']['code'] == 'unknown_outcome'


async def test_invalid_cached_success_is_checked_offline_before_retry_or_projection(environment):
    registry, service, _, calls = environment
    await choose(registry, service)
    first = await registry.import_release(build_args(registry), exported())
    await operation(registry, 'review', releaseId=first['id'], requestId='review', note='Reviewed')
    await operation(registry, 'deploy', releaseId=first['id'], siteId='site', expectedRevision=0, requestId='deploy')
    cached = registry.lookup_request('task', 'deploy')
    cached['result']['result']['url'] = 'http://10.9.8.7:12345/'
    registry._put_request(cached)
    before = len(calls)
    with pytest.raises(PublishingError) as rejected:
        await registry.resume_request(cached)
    assert rejected.value.code == 'unknown_outcome'
    assert rejected.value.receipt['state'] == 'unknown'
    assert rejected.value.receipt['result'] is None
    assert rejected.value.receipt['reconciliationError']['code'] == 'target_mismatch'
    assert '10.9.8.7' not in json.dumps(rejected.value.receipt)
    assert len(calls) == before
    registry._put_request(cached)  # Independently exercise list/log projection.
    rows = registry._merge_receipts('task', registry.resolve('task'), [])
    projected = next(r for r in rows if r['requestId'] == 'deploy')
    assert projected['state'] == 'unknown' and projected['result'] is None
    assert '10.9.8.7' not in json.dumps(projected) and len(calls) == before


@pytest.mark.parametrize('action', ['preview', 'deploy', 'rollback'])
@pytest.mark.parametrize('changed', [{'url': 'http://10.9.8.7:12345/'}, {'accessPolicy': 'private-network'}])
async def test_invalid_target_result_stays_unknown_on_retry_list_and_logs(environment, monkeypatch, action, changed):
    registry, service, _, calls = environment
    await choose(registry, service)
    first = await registry.import_release(build_args(registry), exported())
    args = {'requestId': 'invalid-result', 'releaseId': first['id']}
    if action != 'preview':
        await operation(registry, 'review', releaseId=first['id'], requestId='review', note='Reviewed')
        args.update(siteId='site', expectedRevision=0)
    if action == 'rollback':
        await operation(registry, 'deploy', releaseId=first['id'], siteId='site', expectedRevision=0, requestId='first-deploy')
        second = await registry.import_release(build_args(registry, 'second-build'), exported(b'<h1>Two</h1>'))
        await operation(registry, 'review', releaseId=second['id'], requestId='second-review', note='Reviewed two')
        await operation(registry, 'deploy', releaseId=second['id'], siteId='site', expectedRevision=1, requestId='second-deploy')
        args['expectedRevision'] = 2
    original = SSHClient._send

    def corrupt(receipt):
        if isinstance(receipt, dict) and receipt.get('requestId') == args['requestId']:
            return {**receipt, 'result': {**receipt['result'], **changed}}
        return receipt

    def changed_response(client, request):
        result = original(client, request)
        return [corrupt(row) for row in result] if request['method'] == 'receipts' else corrupt(result)

    monkeypatch.setattr(SSHClient, '_send', changed_response)
    for _ in range(2):
        with pytest.raises(PublishingError) as rejected:
            await operation(registry, action, **args)
        assert rejected.value.code == 'unknown_outcome'
        assert rejected.value.receipt['state'] == 'unknown'
        assert registry.lookup_request('task', args['requestId'])['state'] == 'unknown'
    for method in ('list', 'logs'):
        listing = await operation(registry, method, **({'siteId': 'site'} if method == 'logs' else {}))
        record = registry.lookup_request('task', args['requestId'])
        assert record['state'] == 'unknown' and record['remoteReceiptVerified'] is False
        assert record['reconciliationError']['code'] == 'target_mismatch'
        # Preview initially has only releaseId, so it need not appear in site logs.
        if action != 'preview' or method == 'list':
            row = next(r for r in listing['receipts' if method == 'list' else 'items'] if r['requestId'] == args['requestId'])
            assert row['state'] == 'unknown' and row['result'] is None
    assert sum(request.get('requestId') == args['requestId'] and request['method'] == action for _, request in calls) == 1
    # A later valid receipt may resolve the same effect, still without resending.
    monkeypatch.setattr(SSHClient, '_send', original)
    receipt = await operation(registry, action, **args)
    assert receipt['state'] == 'succeeded'
    assert sum(request.get('requestId') == args['requestId'] and request['method'] == action for _, request in calls) == 1


@pytest.mark.parametrize('state', ['running', 'unknown', 'failed'])
@pytest.mark.parametrize('changed', [{'url': 'http://10.9.8.7:12345/'}, {'accessPolicy': 'private-network'}])
async def test_non_success_receipt_result_policy_is_checked_before_adoption(environment, monkeypatch, state, changed):
    registry, service, _, calls = environment
    await choose(registry, service)
    release = await registry.import_release(build_args(registry), exported())
    await operation(registry, 'review', releaseId=release['id'], requestId='review', note='Reviewed')
    args = {'requestId': 'lost-deploy', 'siteId': 'site', 'releaseId': release['id'], 'expectedRevision': 0}
    original = SSHClient._send

    def corrupt(receipt):
        if isinstance(receipt, dict) and receipt.get('requestId') == args['requestId']:
            return {**receipt, 'state': state, 'result': {**receipt['result'], **changed}}
        return receipt

    def uncertain_response(client, request):
        result = original(client, request)
        if request['method'] == 'deploy':
            raise PublishingError('unknown_outcome', 'Deployment response was lost')
        return [corrupt(row) for row in result] if request['method'] == 'receipts' else corrupt(result)

    monkeypatch.setattr(SSHClient, '_send', uncertain_response)
    for _ in range(2):
        with pytest.raises(PublishingError) as unknown:
            await operation(registry, 'deploy', **args)
        assert unknown.value.code == 'unknown_outcome'
        assert unknown.value.receipt['state'] == 'unknown'
    for method in ('list', 'logs'):
        result = await operation(registry, method, **({'siteId': 'site'} if method == 'logs' else {}))
        row = next(r for r in result['receipts' if method == 'list' else 'items'] if r['requestId'] == args['requestId'])
        assert row['state'] == 'unknown' and row['result'] is None
        assert row['remoteReceiptVerified'] is False
        assert row['reconciliationError']['code'] == 'target_mismatch'
        assert 'remoteReceipt' not in row and '10.9.8.7' not in json.dumps(row)
    assert sum(request.get('requestId') == args['requestId'] and request['method'] == 'deploy' for _, request in calls) == 1
