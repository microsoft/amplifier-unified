"""Synthetic command failures only: never install, activate or retry a live app."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import updates, update_command_details as details
from amplifier_web.update_diagnostics import CommandFailure, CommandTimeout, command_attempts
from amplifier_web.update_report import event


def excerpt(text, limit=details.STDERR_LIMIT, environment=None):
    capture = details.Capture(limit)
    data = text.encode()
    capture.data.extend(data[:limit])
    capture.total = len(data)
    return details.local_excerpt(capture, environment or {})


@pytest.mark.parametrize('message', [
    'No solution found: fixture-package==9.8.7 is unavailable from the configured index',
    'Conflicting URLs for fixture-package',
    'TLS certificate verification failed',
    'Authentication failed',
    'git fetch failed',
    'Failed to build fixture-package',
    'No space left on device',
    'unexpected argument --fixture-option',
    'unrecognized installer failure',
])
def test_actionable_private_explanation_is_retained_without_guessing_category(message):
    assert excerpt(message)['stderr'] == message


@pytest.mark.parametrize('text', [
    'error https://user:credential-value@private.invalid/path-value?query-value#fragment-value',
    'error git+https://private.invalid/path-value\n',
    'error ssh://user@private.invalid/path-value\n',
    'error git@private.invalid:path-value\n',
    'error Authorization: Bearer credential-value\n folded-value\nunindented-value',
    'error X-Api-Key: credential-value\n folded-value',
    'Cookie: credential-value\n folded-value',
    'password = """credential-value\nmultiline-value\n"""',
    '\x1b]8;;https://private.invalid/path-value\x1b\\link\x1b]8;;\x1b\\',
    '\x1b]52;c;credential-value\x07',
    '\x1bPcredential-value\x1b\\',
    'Author\x1b[31mization: credential-value',
    'Author\rization: credential-value',
    'error /home/account-value/spaced path-value',
    r'error C:\Users\account-value\spaced path-value',
    r'error \\server-value\account-value\path-value',
    'error ~/account-value/path-value',
])
def test_redaction_handles_urls_headers_multiline_paths_and_terminal_sequences(text):
    result = excerpt(text)['stderr']
    for sentinel in ('credential-value', 'folded-value', 'unindented-value', 'multiline-value',
                     'private.invalid', 'path-value', 'query-value', 'fragment-value', 'account-value', 'server-value'):
        assert sentinel not in result
    assert '\x1b' not in result and '\r' not in result


def test_redaction_precedes_truncation_and_capture_cut_discards_partial_line():
    text = 'error: ' + 'a' * (details.EXCERPT_LIMIT - 20) + ' https://private.invalid/path-value\n'
    result = excerpt(text)
    assert result['excerptTruncated']
    assert 'https' not in result['stderr'] and 'private' not in result['stderr']
    assert excerpt('known error\nAuthorization: credential-value', limit=25)['stderr'] == 'known error'
    assert excerpt('https://private.invalid/' + 'x' * 200, limit=30)['stderr'] == ''
    assert excerpt('error ENV-VALUE', environment={'CUSTOM_API_KEY': 'ENV-VALUE'})['stderr'] == 'error [REDACTED]'


@pytest.mark.parametrize('data', [b'\x9d52;c;fixture-c1-value\x07',
                                 b'\x90fixture-dcs-value\x9c',
                                 b'\x9d52;c;fixture-c1-value',
                                 b'\x1bPfixture-dcs-value\x1b\\'])
def test_raw_terminal_strings_do_not_expose_payload(data):
    capture = details.Capture(1000)
    capture.data.extend(data)
    capture.total = len(data)
    assert details.local_excerpt(capture, {})['stderr'] == ''


async def test_real_stderr_flood_is_drained_with_bounded_retention_and_exact_count():
    size = details.STDERR_LIMIT * 20
    with pytest.raises(CommandFailure) as caught:
        await updates.process(sys.executable, '-c',
            f'import os; os.write(1,b"valid stdout"); os.write(2,b"x"*{size}); raise SystemExit(7)', timeout=10)
    failure = caught.value
    assert failure.diagnostic_facts['stderrBytes'] == size
    assert failure.diagnostic_facts['stdoutBytes'] == 12
    assert failure.local_command_details[0]['stderrTruncated'] is True
    assert len(failure.local_command_details[0]['stderr'].encode()) <= details.EXCERPT_LIMIT


async def test_stdout_is_exact_or_explicit_failure_never_partial_success(monkeypatch):
    assert await updates.process(sys.executable, '-c', 'import os;os.write(1,b" a\\x00\\xff\\n")', raw=True) == b' a\x00\xff\n'
    monkeypatch.setattr(details, 'STDOUT_LIMIT', 64)
    with pytest.raises(CommandFailure) as caught:
        await updates.process(sys.executable, '-c', 'import os;os.write(1,b"x"*1000)')
    assert caught.value.diagnostic_facts['stdoutBytes'] == 1000
    assert caught.value.diagnostic_facts['outputLimitExceeded'] is True


async def test_retry_predicate_matches_all_existing_auth_markers_across_capture_chunks():
    from amplifier_web import reset
    # Use the contract's actual marker constants rather than a second classifier.
    markers = next(value for value in reset.repository_auth_failure.__code__.co_consts
                   if isinstance(value, tuple))
    for marker in markers:
        capture = details.Capture(2)
        reader = asyncio.StreamReader()
        reader.feed_data(b'prefix github.com\n' + marker[:2].encode())
        reader.feed_data(marker[2:].encode())
        reader.feed_eof()
        await capture.drain(reader)
        assert capture.auth_failure and capture.github, marker
    capture = details.Capture(2)
    reader = asyncio.StreamReader()
    reader.feed_data(b'AMPLIFIER_UPDATE_PROBE={"ok":false,"repositoryAccessFailure":true}\n'
                     b'AMPLIFIER_UPDATE_PROBE={"ok":true}\n')
    reader.feed_eof()
    await capture.drain(reader)
    assert capture.access_probe


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-group fixture, not Windows containment proof')
async def test_parent_exit_with_inherited_pipes_is_bounded_and_children_terminated():
    import time
    started = time.monotonic()
    with pytest.raises(CommandTimeout):
        await updates.process(sys.executable, '-c',
            'import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])',
            timeout=.15)
    assert time.monotonic() - started < 3


@pytest.mark.parametrize('ending', ['success', 'failure', 'timeout', 'cancel'])
async def test_anonymous_retry_keeps_first_attempt_with_shared_deadline(monkeypatch, ending):
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS', raising=False)
    monkeypatch.setenv('CUSTOM_SECRET', 'first-env-value')
    create = asyncio.create_subprocess_exec
    calls = []
    async def synthetic(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            # Markers after retained prefix still participate in the existing retry.
            code = (f'import os;os.write(2,b"x"*{details.STDERR_LIMIT + 100});'
                    'os.write(2,b"\\nhttps://github.com/example/public SAML enforcement\\nfirst-env-value\\n");'
                    'raise SystemExit(128)')
        elif ending == 'success':
            code = 'print("done")'
        elif ending == 'failure':
            code = 'import sys;sys.stderr.write("second failure first-env-value");raise SystemExit(9)'
        else:
            code = 'import time;time.sleep(30)'
        return await create(sys.executable, '-c', code, **kwargs)
    monkeypatch.setattr(updates.asyncio, 'create_subprocess_exec', synthetic)
    task = asyncio.create_task(updates.process('uv', 'sync', '--locked', timeout=.8))
    if ending == 'cancel':
        while len(calls) < 2:
            await asyncio.sleep(.01)
        task.cancel()
    if ending == 'success':
        result = await task
        assert result == 'done'
    else:
        with pytest.raises({'failure': CommandFailure, 'timeout': CommandTimeout, 'cancel': asyncio.CancelledError}[ending]) as caught:
            await task
        result = caught.value
    assert len(calls) == 2
    facts = result.diagnostic_facts
    assert facts['anonymousRetry'] is True
    assert len(facts['commandAttempts']) == 2
    assert facts['commandAttempts'][0]['exitCode'] == 128
    assert len(result.local_command_details) == 2
    assert 'first-env-value' not in json.dumps(result.local_command_details)
    if ending == 'timeout':
        assert facts['commandAttempts'][1]['timedOut'] is True


async def test_private_sidecar_is_separate_from_state_journal_collector_and_export(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    received = []
    service.diagnostics.record = lambda *args, **kwargs: received.append(args)
    manager.diagnostics.begin('application', 'a' * 40)
    try:
        with pytest.raises(CommandFailure):
            await manager.diagnostics.run('candidate-install', updates.process, sys.executable, '-c',
                'import sys;sys.stderr.write("fixture-package==9.8.7 unavailable from configured index\\n'
                'https://user:credential-value@private.invalid/path-value\\n");raise SystemExit(1)')
        receipt = manager.diagnostics.state['lastFailure']
        path = manager.directory / 'command-details' / f"{receipt['localDetailSlot']:02}.json"
        saved = json.loads(path.read_text())
        assert saved['commandId'] == receipt['commandId']
        assert saved['attemptId'] == receipt['attemptId']
        assert 'fixture-package==9.8.7 unavailable' in json.dumps(saved)
        assert 'credential-value' not in json.dumps(saved) and 'private.invalid' not in json.dumps(saved)
        if os.name != 'nt':
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
        shared = json.dumps([service.state['updates'], received, event(receipt)]) + manager.diagnostics.path.read_text()
        assert 'fixture-package==9.8.7' not in shared
        assert 'credential-value' not in shared and 'private.invalid' not in shared
        assert event(receipt)['commandAttempts'][0]['exitCode'] == 1
        assert 'command-details' in details.guidance(receipt)
        # File slot reuse is bounded and never claims a historical pointer is current.
        old_command = saved['commandId']
        failure = CommandFailure({'exitCode': 1})
        failure.local_command_details = [{'stderr': 'new local explanation'}]
        for i in range(details.DETAIL_SLOTS):
            details.retain(manager.diagnostics, failure, f'{i:032x}', 'candidate-install')
        assert len(list(path.parent.glob('*.json'))) == details.DETAIL_SLOTS
        assert json.loads(path.read_text())['commandId'] != old_command
    finally:
        await service.close()


def test_detail_storage_failure_does_not_replace_command_failure(tmp_path, monkeypatch):
    from amplifier_web.host import config
    failure = CommandFailure({'exitCode': 7})
    failure.local_command_details = [{'stderr': 'actionable explanation'}]
    diagnostics = SimpleNamespace(state={}, manager=SimpleNamespace(directory=tmp_path, home=tmp_path))
    def fail(*args, **kwargs):
        raise OSError('private storage failure')
    monkeypatch.setattr(config, 'write_private', fail)
    assert details.retain(diagnostics, failure, 'a' * 32, 'candidate-install') == {'localDetailStatus': 'unavailable'}
    assert failure.diagnostic_facts['exitCode'] == 7
    assert not list(tmp_path.glob('command-details/*.json'))


def test_doctor_verbose_locates_details_without_reading_them(tmp_path, capsys, monkeypatch):
    from amplifier_web import cli, deployment, tls
    monkeypatch.setattr(deployment, 'load_server_config', lambda _: {'bind': ['127.0.0.1'], 'port': 9999})
    monkeypatch.setattr(tls, 'tls_ready', lambda *args: False)
    monkeypatch.setattr(tls, 'ca_bytes', lambda *args: None)
    monkeypatch.setattr(tls, 'ca_fingerprint', lambda *args: None)
    cli._doctor_details(tmp_path)
    text = capsys.readouterr().out
    assert str(tmp_path / 'updates' / 'command-details') in text
    assert 'review before sharing' in text
    assert not (tmp_path / 'updates').exists()


def test_export_revalidates_attempts_and_omits_even_local_excerpts():
    raw = {'phase': 'candidate-install', 'commandAttempts': [
        {'exitCode': 1, 'stderr': 'private text', 'durationMs': -1, 'stdoutBytes': True, 'stderrBytes': 24},
        {'errorType': 'private text', 'timedOut': True, 'argv': ['private text']},
        {'exitCode': 999}], 'localDetailStatus': 'saved', 'localDetailSlot': '/private/path',
        'stderr': 'private text', 'local_command_details': [{'stderr': 'private text'}]}
    assert command_attempts(raw['commandAttempts']) == [
        {'exitCode': 1, 'stderrBytes': 24}, {'timedOut': True}]
    exported = event(raw)
    assert 'private' not in json.dumps(exported)
    assert 'localDetailSlot' not in exported


def test_retain_revalidates_malformed_or_oversized_private_attributes(tmp_path):
    diagnostics = SimpleNamespace(state={}, manager=SimpleNamespace(directory=tmp_path, home=tmp_path))
    failure = CommandFailure({'exitCode': 1})
    failure.local_command_details = {'stderr': 'not a list'}
    assert details.retain(diagnostics, failure, 'a' * 32, 'candidate-install') == {}
    failure.local_command_details = [{'stderr': 'known error\n' + 'https://user:private-value@host/' +
                                      'x' * (details.STDERR_LIMIT * 2), 'argv': 'private-value'}]
    result = details.retain(diagnostics, failure, 'a' * 32, 'candidate-install')
    assert result['localDetailStatus'] == 'saved'
    text = (tmp_path / 'command-details/00.json').read_text()
    assert 'private-value' not in text and 'argv' not in text
    assert len(text) < 20000


@pytest.mark.parametrize('malformed', [1, {'stderr': 'x'}, [{'stderr': '\ud800'}]])
async def test_optional_detail_processing_never_masks_original_command_failure(tmp_path, malformed):
    from amplifier_web.update_diagnostics import UpdateDiagnostics
    service = SimpleNamespace(state={'updates': {}}, _save_changes=lambda **kwargs: None)
    diagnostics = UpdateDiagnostics(SimpleNamespace(service=service, directory=tmp_path, home=tmp_path))
    original = CommandFailure({'exitCode': 7})
    original.local_command_details = malformed
    async def fail():
        raise original
    with pytest.raises(CommandFailure) as caught:
        await diagnostics.run('candidate-install', fail)
    assert caught.value is original
    assert diagnostics.state['lastFailure']['exitCode'] == 7


def test_retain_reapplies_known_environment_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv('CUSTOM_API_KEY', 'fixture-known-env-value')
    diagnostics = SimpleNamespace(state={}, manager=SimpleNamespace(directory=tmp_path, home=tmp_path))
    original = CommandFailure({'exitCode': 7})
    original.local_command_details = [{'stderr': 'unlabelled fixture-known-env-value'}]
    assert details.retain(diagnostics, original, 'a' * 32, 'candidate-install')['localDetailStatus'] == 'saved'
    assert 'fixture-known-env-value' not in (tmp_path / 'command-details/00.json').read_text()


async def test_failed_stdout_overflow_does_not_change_allowed_auth_retry(monkeypatch):
    monkeypatch.setattr(details, 'STDOUT_LIMIT', 64)
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS', raising=False)
    create, calls = asyncio.create_subprocess_exec, []
    async def synthetic(*args, **kwargs):
        calls.append(args)
        code = ('import os;os.write(1,b"x"*100);'
                'os.write(2,b"https://github.com/example/public SAML enforcement");raise SystemExit(128)'
                if len(calls) == 1 else 'print("done")')
        return await create(sys.executable, '-c', code, **kwargs)
    monkeypatch.setattr(updates.asyncio, 'create_subprocess_exec', synthetic)
    result = await updates.process('uv', 'sync', '--locked')
    assert result == 'done' and len(calls) == 2
    assert result.diagnostic_facts['commandAttempts'][0]['outputLimitExceeded']