"""Separate local listener proof, public reachability, and certificate trust."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import socket
import subprocess
import sys

import aiohttp
from aiohttp import web
import pytest

from amplifier_web import cli, installation_health as health
from amplifier_web.deployment import load_server_config, save_server_config
from amplifier_web.tls import ca_bytes, setup_local_ca, ssl_context, tls_ready


@pytest.mark.asyncio
async def test_status_proves_only_pinned_local_tls_not_configured_public_url(tmp_path, monkeypatch, capsys):
    from amplifier_web import __version__, update_readiness
    from amplifier_web.auth import data_identity

    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    public = 'https://unreachable-onboarding.invalid:9443'
    requests = []
    async def endpoint(request):
        requests.append((request.host, request.headers.get('Authorization')))
        return web.json_response({'ok': True, 'app': 'amplifier-unified', 'version': __version__,
                                  'dataIdentity': data_identity(tmp_path)})
    app = web.Application()
    app.router.add_get('/api/health', endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=ssl_context(tmp_path, config))
    await site.start()
    port = runner.addresses[0][1]
    config = save_server_config(tmp_path, {**config, 'port': port, 'public_origins': [public]})
    token = tmp_path / 'config/auth/control-token'
    token.parent.mkdir(parents=True)
    token.write_text('synthetic-onboarding-token')
    monkeypatch.setattr(health, 'service_state', lambda home: 'running')
    monkeypatch.setattr(update_readiness, 'running_identity', lambda: {'version': __version__, 'revision': None})
    original_get = aiohttp.ClientSession.get
    attempted = []
    def local_only(client, url, **kwargs):
        attempted.append(url)
        assert url == f'https://127.0.0.1:{port}/api/health'
        assert kwargs['allow_redirects'] is False
        assert isinstance(kwargs['ssl'], aiohttp.Fingerprint)
        return original_get(client, url, **kwargs)
    monkeypatch.setattr(aiohttp.ClientSession, 'get', local_only)
    try:
        result = await health.status(tmp_path)
    finally:
        await runner.cleanup()
    assert result['ok'] and result['responding']
    assert result['url'] == f'https://127.0.0.1:{port}'
    assert result['publicUrls'] == [public]
    assert result['publicUrlsVerified'] is False
    assert requests == [(f'localhost:{port}', 'Bearer synthetic-onboarding-token')]
    assert len(attempted) == 1
    health.display(result)
    output = capsys.readouterr().out
    assert f'Host listener: https://127.0.0.1:{port}' in output
    assert f'Configured public address (not checked): {public}' in output
    assert 'DNS, network access and certificate trust' in output
    assert 'synthetic-onboarding-token' not in output


@pytest.mark.parametrize('fault', [None, 'missing', 'malformed', 'mismatched'])
def test_supplied_pair_status_is_independent_of_app_owned_ca(tmp_path, monkeypatch, capsys, fault):
    material = tmp_path / 'supplier'
    setup_local_ca(material, load_server_config(material))
    home = tmp_path / 'host'
    cert, key = material / 'config/tls/leaf.crt', material / 'config/tls/leaf.key'
    if fault == 'missing':
        cert.unlink()
    elif fault == 'malformed':
        cert.write_text('not a certificate')
    elif fault == 'mismatched':
        key = material / 'config/tls/ca.key'
    config = save_server_config(home, {**load_server_config(home),
        'tls': {'method': 'ca', 'cert': str(cert), 'key': str(key)}})
    assert ca_bytes(home) is None
    before = {p.relative_to(tmp_path): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in tmp_path.rglob('*') if p.is_file()}
    assert tls_ready(home, config) is (fault is None)
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(home), 'setup-tls', 'status'])
    cli.main()
    output = capsys.readouterr().out
    assert 'Local CA: not configured' in output
    assert ('TLS listener: ready' if fault is None else 'TLS listener: not configured or not startable') in output
    assert {p.relative_to(tmp_path): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in tmp_path.rglob('*') if p.is_file()} == before


def _minimal_cli(monkeypatch, tmp_path):
    from amplifier_web import server
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(tmp_path),
                                     '--workspace', str(tmp_path), 'serve'])
    monkeypatch.setattr('amplifier_web.settings_migration.migrate_settings', lambda *a, **kw: None)
    monkeypatch.setattr('amplifier_web.host.config.load_config', lambda *a, **kw: None)
    monkeypatch.setattr(server, 'create_app', lambda *a, **kw: web.Application())


@pytest.mark.parametrize('code,hint', [
    (errno.EADDRINUSE, 'already in use'),
    (errno.EADDRNOTAVAIL, 'not available on this computer'),
    (errno.EACCES, 'Permission was denied'),
])
def test_expected_listener_failures_are_actionable_and_do_not_open_browser(tmp_path, monkeypatch, capsys, code, hint):
    _minimal_cli(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.threading, 'Timer', lambda *a, **kw: pytest.fail('Browser scheduled before bind'))
    def fail(*args, **kwargs):
        raise OSError(code, 'private OS detail should not replace guidance')
    monkeypatch.setattr(web, 'run_app', fail)
    with pytest.raises(SystemExit, match=hint) as failure:
        cli.main()
    assert failure.value.__suppress_context__
    assert 'No running process was stopped or replaced' in str(failure.value)
    assert 'private OS detail' not in str(failure.value)
    assert 'listening on' not in capsys.readouterr().out


@pytest.mark.parametrize('error', [OSError(errno.EIO, 'unrelated IO'), PermissionError(errno.EACCES, 'file permissions', '/private/file')])
def test_unrelated_startup_errors_keep_their_identity(tmp_path, monkeypatch, error):
    _minimal_cli(monkeypatch, tmp_path)
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(web, 'run_app', fail)
    with pytest.raises(OSError) as failure:
        cli.main()
    assert failure.value is error


def test_foreground_browser_and_readiness_wait_for_successful_binding(tmp_path, monkeypatch, capsys):
    _minimal_cli(monkeypatch, tmp_path)
    calls = []
    class Timer:
        def __init__(self, delay, callback):
            self.callback = callback
            calls.append('scheduled')
        def start(self):
            self.callback()
        def cancel(self):
            calls.append('cancelled')
    monkeypatch.setattr(cli.threading, 'Timer', Timer)
    monkeypatch.setattr(cli.webbrowser, 'open', lambda url: calls.append(url))
    def bound(app, **kwargs):
        assert not calls
        assert not capsys.readouterr().out
        kwargs['print']('aiohttp has bound every socket')
    monkeypatch.setattr(web, 'run_app', bound)
    cli.main()
    assert calls == ['scheduled', 'http://127.0.0.1:8941', 'cancelled']
    assert 'listening on 127.0.0.1, port 8941 (http)' in capsys.readouterr().out


def test_actual_occupied_port_is_a_concise_cli_refusal_without_takeover(tmp_path):
    # Real aiohttp socket binding through the public CLI entrypoint. Only the
    # application factory is reduced, so this cannot start models or tools.
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as owner:
        owner.bind(('127.0.0.1', 0))
        owner.listen()
        port = owner.getsockname()[1]
        config = load_server_config(tmp_path)
        save_server_config(tmp_path, {**config, 'port': port})
        env = {**os.environ, 'PYTHONPATH': str(root)}
        script = '''
from aiohttp import web
from amplifier_web import cli, server, settings_migration
from amplifier_web.host import config
settings_migration.migrate_settings = lambda *a, **kw: None
config.load_config = lambda *a, **kw: None
server.create_app = lambda *a, **kw: web.Application()
cli.main()
'''
        result = subprocess.run([sys.executable, '-c', script, '--data-dir', str(tmp_path),
            '--workspace', str(tmp_path), '--no-open', 'serve'], cwd=root, env=env,
            capture_output=True, text=True, timeout=20)
        assert result.returncode != 0
        assert 'already in use' in result.stderr
        assert '--port' in result.stderr
        assert 'Traceback' not in result.stdout + result.stderr
        assert 'listening on' not in result.stdout
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            accepted, _ = owner.accept()
            accepted.close()
