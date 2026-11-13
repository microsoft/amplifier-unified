"""Atomic repair admission and explicit no-effect reconciliation boundaries.

All helpers are copied normally, but launch and reconciliation execution are
mocked. These controller checks do not qualify native service or worker proof.
"""
import copy
from pathlib import Path
import stat
import subprocess

import pytest

from amplifier_web import installation_repair as repair
from amplifier_web.service import AppError
from test_installation_repair import host, prepared  # noqa: F401 -- shared fixture


CONFIRM = 'maintenance.installationRepairConfirm'
RECONCILE = 'maintenance.installationRepairReconcile'
STATUS = 'maintenance.installationRepairStatus'
HELPERS = ('installation_repair_worker.py', 'reset.py', 'reset_retention.py',
           'app_release_artifacts.py')


def admission(service):
    path = repair.root(service) / 'admissions.json'
    return (repair._read(path, service.data_dir) if path.exists() else
            {'schema': 1, 'current': None, 'previews': {}})


def assert_helpers(folder):
    for name in HELPERS:
        path = folder / name
        assert path.is_file() and path.read_bytes()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def reconcile_args(host, operation):
    return {'operationId': operation['operationId'],
            'hostInstanceId': host.service.instance_id, 'confirm': True}


def save_operation(host, operation, **changes):
    updated = {**operation, **changes}
    repair._write(repair._folder(host.service, operation['operationId']) / 'operation.json',
                  updated, host.service.data_dir)
    return updated


@pytest.mark.parametrize('filename', ['plan.json', 'operation.json', 'admissions.json'])
@pytest.mark.parametrize('timing', ['before', 'after'])
async def test_write_fault_has_only_durable_index_admission(host, monkeypatch, filename, timing):
    view, args = await prepared(host)
    original = repair._write
    writes = []
    folders = []

    def fault(path, value, home):
        path = Path(path)
        if path.name == 'plan.json':
            folders.append(path.parent)
        if path.name in {'plan.json', 'operation.json', 'admissions.json'}:
            assert not repair.held(host.service)
            folder = folders[-1]
            assert_helpers(folder)
            if path.name != 'plan.json':
                assert (folder / 'plan.json').is_file()
            if path.name == 'admissions.json':
                saved = repair._read(folder / 'operation.json', home)
                assert value == {'schema': 1, 'current': saved['operationId'],
                                 'previews': {view['previewId']: saved['operationId']}}
            writes.append(path.name)
        if path.name == filename and timing == 'before':
            raise OSError('injected before replacement')
        original(path, value, home)
        if path.name == filename and timing == 'after':
            raise OSError('injected after durable replacement')

    with monkeypatch.context() as patch:
        patch.setattr(repair, '_write', fault)
        with pytest.raises(AppError, match='private evidence'):
            await host.service.dispatch(CONFIRM, args)

    assert writes == ['plan.json', 'operation.json', 'admissions.json'][:writes.index(filename) + 1]
    assert len(folders) == 1 and not host.launched
    folder = folders[0]
    assert_helpers(folder)
    committed = filename == 'admissions.json' and timing == 'after'
    expected_index = ({'schema': 1, 'current': folder.name,
                       'previews': {view['previewId']: folder.name}} if committed else
                      {'schema': 1, 'current': None, 'previews': {}})
    assert admission(host.service) == expected_index
    assert repair.held(host.service) is committed
    assert not (repair.root(host.service) / 'latest.json').exists()
    assert not (repair.root(host.service) / ('confirmed-' + view['previewId'] + '.json')).exists()
    exact = (await host.service.dispatch(STATUS, {'previewId': view['previewId']}))['result']['operation']
    assert (exact is not None) is committed
    if committed:
        assert exact == repair._read(folder / 'operation.json', host.service.data_dir)
        assert exact['operationId'] == folder.name and exact['status'] == 'accepted'
    else:
        assert repair._latest(host.service) is None

    # Startup must derive its hold from the same durable commit, never orphans.
    repair.initialize(host.service)
    assert repair.held(host.service) is committed
    assert host.service.state['maintenance']['installationRepair']['operation'] == exact
    assert not host.launched
    retried = (await host.service.dispatch(CONFIRM, args))['result']
    if committed:
        assert retried == exact and not host.launched
        _, different = await prepared(host)
        with pytest.raises(AppError, match='unfinished or unknown'):
            await host.service.dispatch(CONFIRM, different)
        assert admission(host.service) == expected_index and not host.launched
    else:
        # No admission happened: a fresh deliberate confirmation may prepare anew.
        assert retried['operationId'] != folder.name
        assert admission(host.service) == {
            'schema': 1, 'current': retried['operationId'],
            'previews': {view['previewId']: retried['operationId']}}
        assert len(host.launched) == 1
        assert host.launched[0][0].name == retried['operationId']


async def test_index_directory_fsync_failure_retains_exact_admission_without_dispatch(host, monkeypatch):
    view, args = await prepared(host)
    original_write, original_fsync = repair._write, repair.os.fsync
    failed = []

    def writing(path, value, home):
        if Path(path).name != 'admissions.json':
            return original_write(path, value, home)

        def fsync(descriptor):
            if stat.S_ISDIR(repair.os.fstat(descriptor).st_mode):
                # The real atomic replace has landed; fail its directory sync.
                assert admission(host.service) == value
                failed.append(copy.deepcopy(value))
                raise OSError('injected admission directory fsync failure')
            return original_fsync(descriptor)

        with monkeypatch.context() as patch:
            patch.setattr(repair.os, 'fsync', fsync)
            return original_write(path, value, home)

    with monkeypatch.context() as patch:
        patch.setattr(repair, '_write', writing)
        with pytest.raises(AppError, match='private evidence'):
            await host.service.dispatch(CONFIRM, args)
    assert len(failed) == 1 and admission(host.service) == failed[0]
    assert repair.held(host.service) and not host.launched
    repair.initialize(host.service)
    exact = (await host.service.dispatch(STATUS, {'previewId': view['previewId']}))['result']['operation']
    assert exact['operationId'] == failed[0]['current']
    assert (await host.service.dispatch(CONFIRM, args))['result'] == exact
    assert repair.held(host.service) and not host.launched


async def test_dispatch_sees_complete_committed_inputs_and_local_gate(host, monkeypatch):
    view, args = await prepared(host)
    calls = []

    def launched(folder, plan):
        assert_helpers(folder)
        assert repair._read(folder / 'plan.json', host.service.data_dir) == plan
        operation = repair._read(folder / 'operation.json', host.service.data_dir)
        assert admission(host.service) == {
            'schema': 1, 'current': operation['operationId'],
            'previews': {view['previewId']: operation['operationId']}}
        assert repair.held(host.service)
        assert host.service.state['maintenance']['installationRepair']['operation'] == operation
        calls.append(operation)

    monkeypatch.setattr(repair, 'launch', launched)
    operation = (await host.service.dispatch(CONFIRM, args))['result']
    assert calls == [operation]
    assert (await host.service.dispatch(CONFIRM, args))['result'] == operation
    assert calls == [operation]


async def test_projection_loss_reconciles_exact_operation_without_late_confirm_replay(host, monkeypatch):
    view, args = await prepared(host)

    def failed_projection(*args, **kwargs):
        raise OSError('injected projection loss after admission')

    with monkeypatch.context() as patch:
        patch.setattr(repair, '_publish', failed_projection)
        with pytest.raises(AppError, match='private evidence'):
            await host.service.dispatch(CONFIRM, args)
    index = admission(host.service)
    operation = repair._latest(host.service)
    assert index == {'schema': 1, 'current': operation['operationId'],
                     'previews': {view['previewId']: operation['operationId']}}
    assert repair.held(host.service) and not host.launched
    host.service.instance_id = 'replacement-host'
    repair.initialize(host.service)
    target = reconcile_args(host, operation)
    assert host.service.state['maintenance']['installationRepair']['reconcileTarget'] == target
    assert (await host.service.dispatch(CONFIRM, args))['result'] == operation
    calls = []
    folder = repair._folder(host.service, operation['operationId'])
    before = {name: (folder / name).read_bytes() for name in ('plan.json', *HELPERS)}

    def reconciled(command, **kwargs):
        assert command == [host.expected['basePython'], '-I', '-S',
                           str(folder / 'installation_repair_worker.py'), '--reconcile', str(folder)]
        assert kwargs == {'cwd': folder, 'stdin': subprocess.DEVNULL,
                          'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
                          'timeout': 45, 'check': True}
        calls.append(command)
        save_operation(host, operation, status='failed', stage='reconciled-no-effect',
                       serviceState='running', reconciliation={'noReplacement': True})

    monkeypatch.setattr(repair.subprocess, 'run', reconciled)
    assert (await host.service.dispatch(STATUS, {'previewId': view['previewId']}))['result']['operation'] == operation
    assert not calls  # Status/initialization/confirm retry never reconcile implicitly.
    result = (await host.service.dispatch(RECONCILE, target))['result']
    assert result['status'] == 'failed' and result['stage'] == 'reconciled-no-effect'
    assert result['reinstalled'] is False and not repair.held(host.service)
    assert host.service.state['maintenance']['installationRepair']['reconcileTarget'] is None
    assert admission(host.service) == index and not host.launched
    assert {name: (folder / name).read_bytes() for name in before} == before
    assert (await host.service.dispatch(CONFIRM, args))['result'] == result
    assert len(calls) == 1 and not host.launched
    repair.initialize(host.service)
    assert not repair.held(host.service)
    assert (await host.service.dispatch(CONFIRM, args))['result'] == result

    # A successor is a distinct explicit admission; the settled preview survives it.
    host.expected['hostInstanceId'] = host.service.instance_id
    successor_view, successor_args = await prepared(host)
    successor = (await host.service.dispatch(CONFIRM, successor_args))['result']
    assert successor['operationId'] != operation['operationId']
    assert admission(host.service) == {
        'schema': 1, 'current': successor['operationId'],
        'previews': {view['previewId']: operation['operationId'],
                     successor_view['previewId']: successor['operationId']}}
    assert (await host.service.dispatch(CONFIRM, args))['result'] == result
    assert len(host.launched) == 1 and len(calls) == 1
    assert host.service.state['maintenance']['installationRepair']['operation'] == successor


@pytest.mark.parametrize('state', ['accepted', 'running', 'unknown'])
@pytest.mark.parametrize('proof', [True, False], ids=['no-effect-proven', 'proof-refused'])
async def test_reconcile_eligible_states_require_worker_proof(host, monkeypatch, state, proof):
    view, confirmation = await prepared(host)
    operation = (await host.service.dispatch(CONFIRM, confirmation))['result']
    operation = save_operation(host, operation, status=state, stage='test-' + state)
    args = reconcile_args(host, operation)
    folder = repair._folder(host.service, operation['operationId'])
    calls = []

    def inspected(command, **kwargs):
        assert command == [host.expected['basePython'], '-I', '-S',
                           str(folder / 'installation_repair_worker.py'), '--reconcile', str(folder)]
        calls.append(command)
        if not proof:
            raise subprocess.CalledProcessError(1, command)
        save_operation(host, operation, status='failed', stage='reconciled-no-effect')

    monkeypatch.setattr(repair.subprocess, 'run', inspected)
    before = (folder / 'operation.json').read_bytes()
    index = admission(host.service)
    repair.initialize(host.service)
    assert host.service.state['maintenance']['installationRepair']['reconcileTarget'] == args
    for query in ({}, {'previewId': view['previewId']}, {'operationId': operation['operationId']}):
        assert (await host.service.dispatch(STATUS, query))['result']['operation'] == operation
    assert not calls and (folder / 'operation.json').read_bytes() == before
    result = (await host.service.dispatch(RECONCILE, args))['result']
    assert len(calls) == 1 and len(host.launched) == 1
    assert repair.held(host.service) is not proof
    assert admission(host.service) == index
    assert (await host.service.dispatch(CONFIRM, confirmation))['result'] == result
    assert len(calls) == 1 and len(host.launched) == 1
    if proof:
        assert result['status'] == 'failed' and result['reinstalled'] is False
        assert host.service.state['maintenance']['installationRepair']['reconcileTarget'] is None
    else:
        assert result == operation and (folder / 'operation.json').read_bytes() == before
        assert host.service.state['maintenance']['installationRepair']['reconcileTarget'] == args


@pytest.mark.parametrize('state,reinstalled,verified', [
    ('accepted', True, False), ('running', True, False), ('unknown', True, False),
    ('reinstalled', True, False), ('reinstalled', False, False),
    ('failed', False, False), ('rolled-back', False, False), ('ready', True, True),
])
async def test_reconcile_ineligible_receipt_never_executes(host, monkeypatch, state, reinstalled, verified):
    _, confirmation = await prepared(host)
    operation = (await host.service.dispatch(CONFIRM, confirmation))['result']
    operation = save_operation(host, operation, status=state, reinstalled=reinstalled,
                               healthVerified=verified, serviceState='running')
    calls = []
    monkeypatch.setattr(repair.subprocess, 'run', lambda *a, **kw: calls.append(a))
    repair.initialize(host.service)
    assert host.service.state['maintenance']['installationRepair']['reconcileTarget'] is None
    with pytest.raises(AppError, match='exact current unfinished non-reinstalled'):
        await host.service.dispatch(RECONCILE, reconcile_args(host, operation))
    assert not calls and len(host.launched) == 1
    assert repair._latest(host.service) == operation


@pytest.mark.parametrize('invalid', ['old-host', 'unconfirmed', 'agent', 'missing-operation', 'noncurrent-operation'])
async def test_reconcile_requires_explicit_current_host_exact_current_operation(host, monkeypatch, invalid):
    _, confirmation = await prepared(host)
    operation = (await host.service.dispatch(CONFIRM, confirmation))['result']
    old_host = host.service.instance_id
    host.service.instance_id = 'replacement-host'
    repair.initialize(host.service)
    args = reconcile_args(host, operation)
    origin = 'ui'
    if invalid == 'old-host':
        args['hostInstanceId'] = old_host
    elif invalid == 'unconfirmed':
        args['confirm'] = False
    elif invalid == 'agent':
        origin = 'agent'
    elif invalid == 'missing-operation':
        args['operationId'] = 'f' * 32
    else:
        save_operation(host, operation, status='failed', stage='preflight-failed')
        host.expected['hostInstanceId'] = host.service.instance_id
        _, successor_args = await prepared(host)
        await host.service.dispatch(CONFIRM, successor_args)
        # Even an otherwise eligible historical receipt is not current authority.
        save_operation(host, operation, status='unknown')
    index = admission(host.service)
    calls = []
    monkeypatch.setattr(repair.subprocess, 'run', lambda *a, **kw: calls.append(a))
    with pytest.raises(AppError):
        await host.service.dispatch(RECONCILE, args, origin=origin)
    assert not calls and admission(host.service) == index
    assert repair.held(host.service)
    assert len(host.launched) == (2 if invalid == 'noncurrent-operation' else 1)
