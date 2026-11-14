import asyncio
from datetime import datetime, timezone
import sqlite3
from types import SimpleNamespace

import pytest

from amplifier_operations import OperationJournal
from amplifier_web.operation_requests import OperationRequests
from amplifier_web.storage_optimize import SavedStorageOptimize, PREFIX, cutoff
from amplifier_web.errors import AppError


class Adapter:
    def __init__(self, count=3):
        self.count = count
        self.effects = []
        self.block = None

    async def discover(self, operation):
        for number in range(self.count):
            await asyncio.sleep(0)
            yield {'owner': 'fixture', 'sessionId': str(number), 'kind': 'worker' if number else 'root',
                   'bytes': 100, 'status': 'eligible', 'reason': 'Fixture qualified'}

    async def preview(self, row, operation):
        return row

    async def apply(self, row, operation, *, resume=False):
        if self.block:
            await self.block.wait()
        self.effects.append((row['sessionId'], resume))
        return {**row, 'status': 'completed'}


@pytest.fixture
def owner(tmp_path):
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    journal = OperationJournal(tmp_path / 'operations.sqlite3')
    app = SimpleNamespace(db=db, lock=asyncio.Lock(), operations=SimpleNamespace(
        journal=journal, requests=OperationRequests(journal)),
        _session=lambda identity, **kw: {'id': identity, 'workspace': str(tmp_path)})
    adapter = Adapter()
    value = SavedStorageOptimize(app, adapter=adapter)
    app._saved_storage_optimize = value
    yield value
    journal.db.close()
    db.close()


async def command(owner, name, args, identity=None):
    return (await owner.dispatch(PREFIX + name, args, 'ui', identity))['result']


async def preview(owner):
    value = await command(owner, 'preview', {'scope': 'current', 'sessionId': 'root',
        'extractImages': True, 'policyRevision': owner.policy()['revision']}, 'preview')
    await asyncio.gather(*owner.tasks.values())
    return owner.read('user', value['operationId'])


async def test_preview_freezes_choices_and_exact_unknown_command_reconciliation(owner):
    saved = await command(owner, 'policy.save', {'expectedRevision': 0,
        'requestKeepHours': 12, 'responseKeepHours': None}, 'save')
    assert saved == {'revision': 1, 'requestKeepHours': 12, 'responseKeepHours': None}
    value = await preview(owner)
    assert value['policy'] == saved
    assert value['cutoffs']['requestBefore'].endswith('Z')
    assert value['cutoffs']['responseBefore'] is None
    assert value['summary']['coverage']['workers'] == 2
    assert value['summary']['bytes']['source'] == 300
    assert value['summary']['bytes']['unlinked'] is None
    assert not owner.adapter.effects and value['canApply']
    exact = await command(owner, 'read', {'commandId': 'preview'})
    assert exact['status'] == 'recorded' and exact['operationId'] == value['operationId']
    assert (await command(owner, 'read', {'commandId': 'not-observed'}))['status'] == 'unknown'
    with pytest.raises(AppError, match='Confirm'):
        await command(owner, 'apply', {'operationId': value['operationId'],
            'expectedRevision': value['revision'], 'confirmExpiry': False}, 'apply')
    assert not owner.adapter.effects


async def test_apply_exact_retry_does_not_replay_and_read_is_paged(owner):
    owner.adapter.count = 105
    value = await preview(owner)
    assert len(value['rows']) == 50 and value['nextAfter'] == 50
    second = owner.read('user', value['operationId'], after=50)
    assert len(second['rows']) == 50 and second['nextAfter'] == 100
    args = {'operationId': value['operationId'], 'expectedRevision': value['revision'], 'confirmExpiry': False}
    await command(owner, 'apply', args, 'apply')
    await asyncio.gather(*owner.tasks.values())
    assert len(owner.adapter.effects) == 105
    await command(owner, 'apply', args, 'apply')
    assert len(owner.adapter.effects) == 105
    assert owner.read('user', value['operationId'])['status'] == 'completed'
    with pytest.raises(ValueError, match='different'):
        await command(owner, 'apply', {**args, 'confirmExpiry': True}, 'apply')


async def test_cancel_accepts_older_progress_revision_and_preserves_completed_work(owner):
    value = await preview(owner)
    owner.adapter.block = asyncio.Event()
    await command(owner, 'apply', {'operationId': value['operationId'],
        'expectedRevision': value['revision'], 'confirmExpiry': False}, 'apply')
    await asyncio.sleep(0)
    await command(owner, 'cancel', {'operationId': value['operationId'],
        'expectedRevision': value['revision']}, 'cancel')
    owner.adapter.block.set()
    await asyncio.gather(*owner.tasks.values())
    current = owner.read('user', value['operationId'])
    assert current['status'] == 'cancelled'
    assert current['canResume']
    await command(owner, 'resume', {'operationId': value['operationId'],
        'expectedRevision': current['revision']}, 'resume')
    await asyncio.gather(*owner.tasks.values())
    assert len(owner.adapter.effects) == 3
    assert len({row[0] for row in owner.adapter.effects}) == 3


async def test_restart_observes_does_not_replay_and_keeps_command_association(owner):
    value = await preview(owner)
    owner._update(value['operationId'], 'user', status='applying', _phase='apply')
    request, _ = owner.requests.begin('user', PREFIX + 'apply', {'requestId': 'unknown'},
        'ui', resource_id=value['operationId'])
    owner.requests.finish(request, 'outcome_unknown')
    restarted = SavedStorageOptimize(owner.app, adapter=owner.adapter)
    assert not restarted.tasks and not restarted.adapter.effects
    assert restarted.read('user', value['operationId'])['status'] == 'interrupted'
    outcome = await command(restarted, 'read', {'commandId': 'unknown'})
    assert outcome['status'] == 'unknown' and outcome['operationId'] == value['operationId']


@pytest.mark.parametrize('hours', [True, 0, -1, 1.5, '12', 2**53])
def test_invalid_expiry_is_not_coerced(hours):
    with pytest.raises(AppError):
        cutoff(datetime.now(timezone.utc), hours)


def test_large_valid_age_means_no_older_representable_history():
    assert cutoff(datetime.now(timezone.utc), 2**53 - 1).startswith('0001-')


async def test_accounting_replaces_previous_outcome_without_double_counting(owner):
    value = await preview(owner)
    identity = value['operationId']
    row = {'owner': 'fixture', 'sessionId': '0', 'kind': 'root', 'bytes': 100,
           'status': 'completed', '_accounting': {'unlinked': 10, 'assetsAdded': 0,
            'recoveryRetained': 100, 'inlineRemoved': 50, 'refsAndJournalAdded': 12,
            'filesystemDelta': -2}}
    # Other rows unmeasured => total remains explicitly unknown.
    owner._file(identity, 1, row)
    assert owner.read('user', identity)['summary']['bytes']['unlinked'] is None
    for ordinal in (2, 3):
        owner._file(identity, ordinal, {**row, 'kind': 'worker', 'sessionId': str(ordinal)})
    assert owner.read('user', identity)['summary']['bytes']['unlinked'] == 30
    owner._file(identity, 1, row)
    amounts = owner.read('user', identity)['summary']['bytes']
    assert amounts['unlinked'] == 30 and amounts['assetsAdded'] == 0
    assert amounts['filesystemDelta'] == -6 and amounts['recoveryRetained'] == 300


@pytest.mark.asyncio
async def test_real_ci_retention_rebinds_without_reenrolling_deliveries(owner, tmp_path, monkeypatch):
    """Candidate-graph check; selected exact CI source is supplied by the runner."""
    from contextlib import contextmanager
    import hashlib
    import json
    import os
    from amplifier_web import storage_optimize_retention as retention
    from amplifier_web.capture_index import index_shared
    from amplifier_web.session_files import capture_dir
    from amplifier_web.runtime import RuntimeManager

    # This check is deliberately not skipped if the selected dependency lacks
    # the new owner API: callers must qualify the exact candidate graph.
    from context_intelligence.prune.policy import Policy
    Policy(diagnostic_sidecars=True)
    directory = capture_dir(str(tmp_path), 'root')
    directory.mkdir(parents=True)
    (directory / 'metadata.json').write_text(json.dumps(
        {'format': 'context-intelligence', 'version': '1.0.0'}))
    body = b'{"synthetic":true}'
    sha = hashlib.sha256(body).hexdigest()
    assets = directory / 'provider-payloads'
    assets.mkdir()
    asset = assets / (sha + '.json')
    asset.write_bytes(body)
    ref = {'sha256': sha, 'bytes': len(body), 'redacted': True, 'truncated': False}
    event = {'event': 'llm:request', 'timestamp': '2020-01-01T00:00:00Z',
             'session_id': 'root', 'data': {'raw': {'huge': 'secret diagnostic' * 100},
                'provider_payloads': {'raw': ref}, 'event_id': 'same', 'status': 'failed',
                'usage': {'cost': 0.1}}}
    path = directory / 'events.jsonl'
    path.write_text(json.dumps(event) + '\n')
    os.utime(path, (1, 1))
    diagnostic_db = tmp_path / 'diagnostics.sqlite3'
    @contextmanager
    def db():
        connection = sqlite3.connect(diagnostic_db)
        try:
            with connection:
                yield connection
        finally:
            connection.close()
    with db() as connection:
        connection.executescript('CREATE TABLE captures(path TEXT PRIMARY KEY,offset INTEGER,inode INTEGER);'
            'CREATE TABLE records(id TEXT PRIMARY KEY,at REAL,stream TEXT,session TEXT,workspace TEXT,event TEXT,data TEXT);'
            'CREATE TABLE deliveries(record_id TEXT,status TEXT,payload TEXT);')
        rows = index_shared(connection, [(str(tmp_path), 'root')], {'streams': ['usage']})
        connection.execute('INSERT INTO deliveries VALUES(?,?,?)', (rows[0][0], 'accepted', '{"exact":"receipt"}'))
        original_records = connection.execute('SELECT id FROM records').fetchall()
    fake_runtime = SimpleNamespace(_closed=False, _locks={}, _preparations={}, workers={},
                                   _check_execution=lambda *args: None)
    fake_runtime.stopped_boundary = lambda session: RuntimeManager.stopped_boundary(fake_runtime, session)
    owner.app.runtime = fake_runtime
    owner.app.diagnostics = SimpleNamespace(flush_lock=asyncio.Lock(), _db=db)
    value = await preview(owner)
    owner._update(value['operationId'], 'user', status='applying', _phase='apply')
    operation = owner._get(value['operationId'], 'user')
    operation['cutoffs'] = {'requestBefore': '2026-01-01T00:00:00Z', 'responseBefore': None}
    row = {'owner': 'context-intelligence', 'sessionId': 'root', 'kind': 'root',
           'bytes': path.stat().st_size, '_path': str(path), '_sourceId': 'root',
           '_sourceSha256': retention.file_hash(path), 'status': 'eligible'}
    result = await retention.apply(owner.app, row, operation)
    assert result['status'] == 'completed', result
    assert not asset.exists()
    changed = json.loads(path.read_text())
    assert changed['data']['provider_payloads_expired']['raw']['sha256'] == sha
    assert changed['data']['usage'] == event['data']['usage']
    with db() as connection:
        assert connection.execute('SELECT id FROM records').fetchall() == original_records
        assert connection.execute('SELECT status,payload FROM deliveries').fetchall() == [('accepted', '{"exact":"receipt"}')]
        assert index_shared(connection, [(str(tmp_path), 'root')], {'streams': ['usage']}) == []
    reconciled = await retention.apply(owner.app, row, operation, resume=True)
    assert reconciled['status'] == 'held'  # Unlink observation is not invented after a lost owner result.


async def test_expiry_directions_are_independent():
    from amplifier_web.storage_optimize_retention import policy
    from context_intelligence.prune.policy import prune_record
    operation = {'cutoffs': {'requestBefore': None, 'responseBefore': '2026-01-01T00:00:00Z'}}
    selected = policy(operation)
    request = {'event': 'llm:request', 'timestamp': '2020-01-01T00:00:00Z', 'data': {'raw': 'keep'}}
    response = {**request, 'event': 'llm:response'}
    assert prune_record(request, selected)[0] == request
    assert 'raw' not in prune_record(response, selected)[0]['data']


async def test_real_service_dispatch_exposes_preview_and_exact_command_read(tmp_path):
    from amplifier_web.service import AppService
    from test_service import Runtime
    service = AppService(tmp_path / 'app', workspace=tmp_path, runtime=Runtime())
    try:
        result = await service.dispatch(PREFIX + 'preview', {'scope': 'all',
            'extractImages': True, 'policyRevision': 0}, command_id='http-exact', include_state=False)
        assert result['accepted'] and result['result']['status'] == 'previewing'
        result = await service.dispatch(PREFIX + 'read', {'commandId': 'http-exact'}, include_state=False)
        assert result['result']['status'] == 'recorded'
        assert result['result']['operationId']
    finally:
        await service.close()


@pytest.mark.parametrize('change', ['native', 'workspace', 'execution'])
def test_source_writer_fence_binds_frozen_path_and_revision(owner, tmp_path, change):
    from amplifier_web.storage_optimize_retention import source_owner
    from amplifier_web.session_files import capture_dir
    source = {'id': 'root', 'workspace': str(tmp_path), 'executionRevision': 0, 'interruptionRevision': 0}
    owner.app._session = lambda *args, **kwargs: source
    row = {'_sourceId': 'root', 'sessionId': 'root', '_sourceBinding': [0, 0],
           '_path': str(capture_dir(str(tmp_path), 'root') / 'events.jsonl')}
    assert source_owner(owner.app, row) is source
    if change == 'native':
        source['runtimeSessionId'] = 'changed'
    elif change == 'workspace':
        source['workspace'] = str(tmp_path / 'moved')
    else:
        source['executionRevision'] += 1
    with pytest.raises(ValueError, match='changed'):
        source_owner(owner.app, row)


async def test_repeated_cancellation_does_not_release_owned_thread_early():
    import threading
    from amplifier_web.storage_optimize_retention import settle_owned
    started, release, finished, cancel_flag = (threading.Event() for _ in range(4))
    def effect():
        started.set()
        release.wait()
        finished.set()
    task = asyncio.create_task(settle_owned(effect, cancel_flag))
    while not started.is_set():
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert cancel_flag.is_set() and not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not finished.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_discovery_uses_native_roots_and_keeps_worker_coverage(owner, tmp_path, monkeypatch):
    import json
    from amplifier_web.storage_optimize_owners import OwnerAdapters
    from amplifier_web.session_files import sessions_dir
    home = sessions_dir(str(tmp_path))
    for identity, parent in (('root', None), ('child', 'root'), ('other', None)):
        directory = home / identity
        directory.mkdir(parents=True)
        (directory / 'metadata.json').write_text(json.dumps({'parent_id': parent}))
        (directory / 'transcript.jsonl').write_text('{"role":"user","content":"original"}\n')
    unrelated_cwd = tmp_path / 'unrelated-cwd'
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    adapter = OwnerAdapters(owner.app)
    operation = {'scope': 'current', '_workspace': str(tmp_path), '_nativeId': 'root'}
    rows = [row async for row in adapter.discover(operation)]
    assert {(row['sessionId'], row['kind']) for row in rows} == {('root', 'root'), ('child', 'worker')}
    operation['scope'] = 'workspace'
    assert {row['sessionId'] async for row in adapter.discover(operation)} == {'root', 'child', 'other'}
    operation.update(scope='all', _workspace=None)
    assert {row['sessionId'] async for row in adapter.discover(operation)} == {'root', 'child', 'other'}


async def test_source_accounting_keeps_unknown_coverage_unknown(owner):
    value = await preview(owner)
    identity = value['operationId']
    owner._file(identity, 1, {'sessionId': '0', 'kind': 'root', 'status': 'held', 'bytes': None})
    assert owner.read('user', identity)['summary']['bytes']['source'] is None
    owner._file(identity, 1, {'sessionId': '0', 'kind': 'root', 'status': 'held', 'bytes': 100})
    assert owner.read('user', identity)['summary']['bytes']['source'] == 300