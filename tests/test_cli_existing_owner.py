"""Actual POSIX owner conflict is a concise refusal, never an implicit takeover."""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import shlex
import socket
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != 'posix', reason='Publishing ownership requires POSIX flock')
def test_serve_refuses_existing_publishing_owner_without_traceback_or_takeover(tmp_path):
    from amplifier_publishing import Publisher, PublishingError
    from amplifier_web.deployment import load_server_config, save_server_config

    root = Path(__file__).resolve().parents[1]
    home = tmp_path / 'app with spaces'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    # Reserve an unused address throughout the refusal. The second process must
    # fail at publishing ownership before trying to bind any HTTP listener.
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
        config = load_server_config(home)
        save_server_config(home, {**config, 'port': port})
        env = {**os.environ, 'AMPLIFIER_WEB_HOME': str(home),
               'AMPLIFIER_HOME': str(tmp_path / 'native'),
               'AMPLIFIER_UNIFIED_IMPORT_HOME': str(tmp_path / 'legacy'),
               'AMPLIFIER_SESSION_STATE_HOME': str(tmp_path / 'shared'),
               'PYTHONPATH': str(root) + os.pathsep + os.environ.get('PYTHONPATH', '')}
        owner = subprocess.Popen([sys.executable, '-u', '-c',
            "from amplifier_publishing import Publisher; from pathlib import Path; import sys; "
            "p=Publisher(Path(sys.argv[1])); "
            "(p.root/'retained.txt').write_text('original owner evidence'); "
            "print('owned',flush=True); sys.stdin.readline(); p.close()",
            str(home / 'publishing')], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env, cwd=root)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(owner.stdout, selectors.EVENT_READ)
                assert selector.select(10), 'Original publishing owner failed to start'
                assert owner.stdout.readline().strip() == 'owned'
            result = subprocess.run([sys.executable, '-m', 'amplifier_web.cli',
                '--data-dir', str(home), '--workspace', str(workspace), '--no-open', 'serve'],
                text=True, capture_output=True, timeout=20, env=env, cwd=root)
            assert result.returncode != 0
            assert 'already in use' in result.stderr, result.stdout + result.stderr
            assert f'http://127.0.0.1:{port}' in result.stderr
            assert f'--data-dir {shlex.quote(str(home))} service status' in result.stderr
            assert 'Traceback' not in result.stdout + result.stderr
            assert owner.poll() is None, 'A second start must not stop the existing owner'
            assert (home / 'publishing' / 'retained.txt').read_text() == 'original owner evidence'
            with pytest.raises(PublishingError) as still_owned:
                Publisher(home / 'publishing')
            assert still_owned.value.code == 'root_busy'
        finally:
            if owner.poll() is None:
                owner.stdin.write('close\n'); owner.stdin.flush()
            try:
                owner.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                owner.kill(); owner.communicate(timeout=5)
        assert owner.returncode == 0
        # The rejected second process retained no lock; a later explicit start
        # may own the same data only after the original owner voluntarily exits.
        reopened = Publisher(home / 'publishing')
        reopened.close()


@pytest.mark.parametrize('code,cause', [
    ('root_busy', ImportError('POSIX locking is unavailable')),
    ('unsafe_root', BlockingIOError('Unrelated failure')),
])
def test_other_publishing_failures_are_not_misreported_as_running_service(tmp_path, monkeypatch, code, cause):
    from aiohttp import web
    from amplifier_publishing import PublishingError
    from amplifier_web import cli, server

    error = PublishingError(code, 'Specific publishing failure')
    error.__cause__ = cause
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(tmp_path),
                                    '--workspace', str(tmp_path), '--no-open', 'serve'])
    monkeypatch.setattr('amplifier_web.settings_migration.migrate_settings', lambda *a, **kw: None)
    monkeypatch.setattr('amplifier_web.host.config.load_config', lambda *a, **kw: None)
    monkeypatch.setattr(server, 'create_app', lambda *a, **kw: object())
    def failed_start(*args, **kwargs):
        raise error
    monkeypatch.setattr(web, 'run_app', failed_start)
    with pytest.raises(PublishingError) as captured:
        cli.main()
    assert captured.value is error
