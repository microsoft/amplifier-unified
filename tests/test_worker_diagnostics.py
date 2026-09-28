import asyncio
import json
import sys

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.worker_diagnostics import save_startup_failure


@pytest.mark.asyncio
async def test_startup_stderr_survives_cleanup_without_becoming_public(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    events = []
    async def emit(kind, data):
        events.append((kind, data))
    fixture = "import sys; sys.stdin.readline(); print('Dependency setup failed: secret-fixture-value', file=sys.stderr, flush=True); raise SystemExit(1)"
    manager = RuntimeManager(command=[sys.executable, '-c', fixture])
    try:
        with pytest.raises(RuntimeError, match='Startup details were saved locally') as error:
            await manager.start({'id': 'probe'}, emit)
        assert not manager.workers
        files = list((tmp_path / 'logs/workers').glob('startup-*.log'))
        assert len(files) == 1
        assert 'Dependency setup failed' in files[0].read_text()
        assert files[0].stat().st_mode & 0o777 == 0o600
        assert files[0].parent.stat().st_mode & 0o777 == 0o700
        assert str(files[0]) in str(error.value)
        assert error.value.diagnostic_path == files[0]
        assert next(data for kind, data in events if kind == 'runtime.error')['diagnosticReceipt'] == files[0].name
        assert 'secret-fixture-value' not in json.dumps(events)
        assert 'secret-fixture-value' not in str(error.value)
        assert not any(kind == 'assistant.message' for kind, _ in events)
    finally:
        await manager.close()


def test_diagnostic_is_bounded_and_optional(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    assert save_startup_failure({'stderr': []}, 1) is None
    path = save_startup_failure({'stderr': ['x' * 100_000]}, 1)
    assert path.stat().st_size < 61_000
    def fail(*_):
        raise OSError('disk unavailable')
    monkeypatch.setattr('amplifier_web.deployment.write_private', fail)
    assert save_startup_failure({'stderr': ['failed']}, 1) is None


def handled_worker(*, stderr='', detail='private startup detail', receipt=None, module=True):
    event = {'type': 'runtime.error', 'error': detail}
    if module:
        event.update(code='module_load_failed', moduleFailures=[{
            'module': 'tool-fixture', 'type': 'tool', 'reason_code': 'validation_failed'}])
    if receipt:
        event['diagnosticReceipt'] = receipt
    return ('import sys,json\njson.loads(sys.stdin.readline())\n'
            f'print({stderr!r}, file=sys.stderr, flush=True)\n'
            f'print({json.dumps(event)!r}, flush=True)\n')


@pytest.mark.asyncio
@pytest.mark.parametrize('has_stderr', [True, False])
async def test_handled_module_error_keeps_private_receipt_even_without_stderr(tmp_path, monkeypatch, has_stderr):
    from amplifier_web.module_failures import ConfiguredModuleError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    monkeypatch.setenv('API_TOKEN', 'synthetic-inherited-secret')
    private = ('protocol_compliance: BackendError: backend handshake failed '
               'api_key="synthetic-config-secret" Authorization: Bearer synthetic-bearer-secret '
               'https://name:synthetic-url-secret@example.invalid API_TOKEN=synthetic-inherited-secret')
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(
        stderr=private if has_stderr else '', detail=private)], startup_timeout=3)
    events = []
    async def emit(kind, data):
        events.append((kind, data))
    try:
        with pytest.raises(ConfiguredModuleError) as error:
            await manager.start({'id': 'handled-fixture'}, emit)
        path = error.value.diagnostic_path
        assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
        content = path.read_text()
        assert 'protocol_compliance' in content and 'BackendError' in content
        assert 'handled-fixture' in content
        for secret in ('synthetic-config-secret', 'synthetic-bearer-secret',
                       'synthetic-url-secret', 'synthetic-inherited-secret'):
            assert secret not in content
            assert secret not in json.dumps(events) + str(error.value)
        payload = next(data for kind, data in events if kind == 'runtime.error')
        assert payload['diagnosticReceipt'] == path.name
        assert 'backend handshake failed' not in json.dumps(events)
        assert len(list(path.parent.glob('startup-*.log'))) == 1
        assert not manager.workers
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_receipt_write_failure_keeps_original_module_failure(tmp_path, monkeypatch):
    from amplifier_web.module_failures import ConfiguredModuleError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    def fail(*_):
        raise OSError('synthetic disk error')
    monkeypatch.setattr('amplifier_web.deployment.write_private', fail)
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(stderr='failed check')])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        with pytest.raises(ConfiguredModuleError, match='tool-fixture') as error:
            await manager.start({'id': 'disk-failure'}, emit)
        assert not hasattr(error.value, 'diagnostic_path')
        assert 'diagnosticReceipt' not in next(data for kind, data in events if kind == 'runtime.error')
        assert 'synthetic disk error' not in json.dumps(events)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_generic_startup_exception_is_private_and_bad_receipt_is_not_followed(tmp_path, monkeypatch):
    from amplifier_web.runtime import RuntimeStartupError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(
        detail='UnexpectedMountError: private implementation detail', receipt='../../other.log', module=False)])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        with pytest.raises(RuntimeStartupError) as error:
            await manager.start({'id': 'generic-failure'}, emit)
        path = error.value.diagnostic_path
        assert path.parent == tmp_path / 'logs/workers'
        assert 'UnexpectedMountError' in path.read_text()
        assert 'private implementation detail' not in json.dumps(events) + str(error.value)
    finally:
        await manager.close()


def test_receipt_redaction_and_bound_survive_large_and_unprintable_errors(tmp_path, monkeypatch):
    from amplifier_web.worker_diagnostics import StartupCapture, MAX_DETAIL_BYTES
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    path = save_startup_failure({'stderr': ['x' * 100_000]}, None, failure='\x01' * 100_000)
    assert path.stat().st_size <= MAX_DETAIL_BYTES
    class BadError(Exception):
        def __str__(self): raise ValueError('Cannot format exception')
    capture = StartupCapture()
    try:
        assert capture.save(BadError(), 'bad-string') is None
        monkeypatch.setattr('amplifier_web.worker_diagnostics.traceback.extract_tb', lambda _: 1 / 0)
        assert capture.save(RuntimeError('original'), 'bad-traceback') is None
    finally:
        capture.close()


@pytest.mark.asyncio
async def test_worker_captures_original_validation_error_before_safe_metadata_replaces_it(tmp_path, monkeypatch):
    import logging
    from types import SimpleNamespace
    from amplifier_web.module_failures import ConfiguredModuleError
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.shared_state import ActivationGate
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    monkeypatch.setenv('SYNTHETIC_TOKEN', 'synthetic-worker-secret')
    monkeypatch.setattr('amplifier_web.runtime_bootstrap.bootstrap_app_package', lambda: None)
    monkeypatch.setattr('amplifier_web.host.config.app_home', lambda: tmp_path / 'app')
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides', lambda *args: None)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending', lambda *args: None)
    runtime = SimpleNamespace(session_id='captured-fixture')
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime', SimpleNamespace(Runtime=lambda **kwargs: runtime))
    logger = logging.getLogger('amplifier_core.loader')
    original_handlers = list(logging.getLogger('amplifier_core').handlers)
    async def fail_prepare(*args, **kwargs):
        logger.error("Module 'tool-fixture' failed validation: protocol_compliance: BackendError: synthetic-worker-secret")
        raise ConfiguredModuleError([{'module': 'tool-fixture', 'type': 'tool', 'reason_code': 'validation_failed'}])
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', fail_prepare)
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker = Worker()
    worker.shared_store = SimpleNamespace()
    worker.shared_handle = SimpleNamespace()
    worker.activation_gate = ActivationGate()
    await worker.start({'id': 'captured-fixture', 'workspace': str(tmp_path)}, recover_bundle=False)
    error = next(event for event in events if event['type'] == 'runtime.error')
    assert error['code'] == 'module_load_failed'
    content = (tmp_path / 'app/logs/workers' / error['diagnosticReceipt']).read_text()
    assert 'protocol_compliance' in content and 'BackendError' in content
    assert 'synthetic-worker-secret' not in content + json.dumps(events)
    assert 'protocol_compliance' not in json.dumps(events)
    assert logging.getLogger('amplifier_core').handlers == original_handlers
    assert worker.execution is None and worker.shutdown.is_set()


@pytest.mark.asyncio
async def test_handled_startup_reference_survives_send_and_copy_diagnostics(tmp_path, monkeypatch):
    from amplifier_web.service import AppService, AppError
    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(
        stderr='protocol_compliance: private backend error')])
    app = AppService(home, manager, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        with pytest.raises(AppError) as error:
            await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Keep this draft'}, command_id='input-fixture')
        assert error.value.code == 'worker_startup_failed'
        session = app._session(sid)
        receipt = session['diagnosticReceipt']
        assert session['messages'][-1]['text'] == 'Keep this draft'
        assert 'private backend error' not in json.dumps(app.browser_state())
        inspected = await app.dispatch('session.inspect', {'id': sid})
        assert inspected['result']['diagnosticReceipt'] == receipt
        assert 'private backend error' not in json.dumps(inspected)
        await app.close()
        app = AppService(home, RuntimeManager(command=[sys.executable, '-c', 'raise SystemExit(3)']), workspace=tmp_path)
        assert app._session(sid)['diagnosticReceipt'] == receipt
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'ready'})
        assert 'diagnosticReceipt' not in app._session(sid)
        inspected = await app.dispatch('session.inspect', {'id': sid})
        assert 'diagnosticReceipt' not in inspected['result']
        assert (home / 'logs/workers' / receipt).is_file()
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_manager_reuses_private_worker_receipt_instead_of_generic_fallback(tmp_path, monkeypatch):
    from amplifier_web.module_failures import ConfiguredModuleError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    receipt = save_startup_failure({'stderr': ['protocol_compliance: original private failure'],
                                    'runtime_id': 'worker-receipt'}, None, failure='ConfiguredModuleError')
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(receipt=receipt.name)])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        with pytest.raises(ConfiguredModuleError) as error:
            await manager.start({'id': 'worker-receipt'}, emit)
        assert error.value.diagnostic_path == receipt
        assert len(list(receipt.parent.glob('startup-*.log'))) == 1
        assert 'original private failure' not in json.dumps(events)
    finally:
        await manager.close()
