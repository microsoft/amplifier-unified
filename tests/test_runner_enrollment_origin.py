"""Portable connection receipts and fixed input require no local test backend."""
import asyncio
import json
import sys
from types import SimpleNamespace

from amplifier_web import test_runner_enrollment as enrollment
from amplifier_web import registered_test_runners as runners
from amplifier_web import managed_runner_qualification as qualification


def test_saved_request_cancel_and_cold_read_are_portable(tmp_path, monkeypatch):
    class AppError(ValueError):
        def __init__(self, message, status):
            super().__init__(message)
            self.status = status
    monkeypatch.setitem(sys.modules, 'amplifier_web.service', SimpleNamespace(AppError=AppError))
    async def scenario():
        app = SimpleNamespace(data_dir=tmp_path, lock=asyncio.Lock(), state={},
            configuration_authority=SimpleNamespace(check=lambda authority: None))
        app.test_queue = SimpleNamespace(backoff={}, tasks={}, wakeup=asyncio.Event(), start=lambda: None)
        monkeypatch.setattr(runners, 'ssh', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('passive request connected')))
        args = {'expectedRevision': runners.snapshot(app)['revision'], 'requestId': 'portable-request',
                'name': 'Test machine', 'connection': 'approved-machine', 'configPath': ''}
        response = await runners.dispatch(app, 'testing.runners.register', args, None)
        identity = response['result']['enrollments'][0]['id']
        assert runners.snapshot(SimpleNamespace(data_dir=tmp_path))['enrollments'][0]['status'] == 'connecting'
        assert (await runners.dispatch(app, 'testing.runners.register', args, None))['result']['enrollments'][0]['id'] == identity
        await runners.dispatch(app, 'testing.runners.enrollment.cancel', {'id': identity}, None)
        await enrollment.run(SimpleNamespace(app=app, backoff={}, wakeup=asyncio.Event()), identity)
        assert enrollment.read(app, identity)['status'] == 'cancelled'
        assert not runners.snapshot(app)['runners']
    asyncio.run(scenario())


def test_fixed_input_never_uses_callers_git_directory_or_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_DIR', str(tmp_path / 'unrelated-private-repo'))
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'core.sshCommand')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'private-credential-command')
    folder = tmp_path / 'fixed-input'
    assert qualification.source(folder) == str(folder)
    assert (folder / 'input.txt').read_text() == 'runner-input-v1'
    assert (folder / 'uncommitted.txt').read_text() == 'working-tree-v1'
    assert not (tmp_path / 'unrelated-private-repo').exists()
    assert 'private-credential-command' not in (folder / '.git' / 'config').read_text()
