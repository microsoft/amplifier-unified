"""Enrollment and installation share the real host's authentication boundary."""
import asyncio
import base64
import json
import os
import re
import subprocess
import time
from unittest.mock import AsyncMock

import pytest

from amplifier_web.auth import new_session
from amplifier_web.server import create_app
from amplifier_web.terminal_devices import TerminalDevices
from amplifier_web.terminal_setup import TerminalSetup
from test_service import Runtime


@pytest.fixture
async def terminal(aiohttp_client, tmp_path, monkeypatch):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False)
    client = await aiohttp_client(app)
    cookie = {'Cookie': 'amplifier_unified_session=' + new_session(app['session_secret'])}
    manager = app['terminal_setup']
    monkeypatch.setattr(manager, 'wheel', AsyncMock(return_value=b'synthetic platform wheel'))
    return client, manager, cookie, app


async def prepare(terminal, **overrides):
    client, manager, cookie, _ = terminal
    args = {'server': 'http://127.0.0.1:8941', 'platform': 'macos-arm64', 'name': 'My Mac', **overrides}
    response = await client.post('/api/actions', headers=cookie,
        json={'action': 'terminal.prepare', 'args': args, 'id': 'one-setup-request'})
    return response


def profile_from_script(text):
    encoded = re.search(r"<<'AMPLIFIER_PROFILE'\n([^\n]+)\nAMPLIFIER_PROFILE", text)[1]
    return json.loads(base64.b64decode(encoded))


async def issued(terminal):
    client, _, cookie, _ = terminal
    receipt = await (await prepare(terminal)).json()
    response = await client.get(receipt['installer']['downloadUrl'], headers=cookie)
    profile = profile_from_script(await response.text())
    response = await client.post('/api/terminal/redeem', json={'grant': profile['grant']})
    assert response.status == 200
    return await response.json(), profile, receipt


async def test_setup_requires_sign_in_and_downloads_never_expose_control_token(terminal):
    client, _, cookie, app = terminal
    page = await client.get('/setup/terminal', allow_redirects=False)
    assert page.status == 307 and '/login?next=' in page.headers['Location']
    assert (await client.get('/setup/terminal', headers=cookie)).status == 200
    assert (await client.get('/setup')).status == 200
    response = await prepare(terminal)
    assert response.status == 200
    receipt = await response.json()
    assert 'grant' not in json.dumps(receipt) and app['control_token'] not in json.dumps(receipt)
    download = receipt['installer']['downloadUrl']
    assert (await client.get(download)).status == 401
    response = await client.get(download, headers=cookie)
    assert response.headers['Cache-Control'] == 'no-store'
    script = await response.text()
    assert app['control_token'] not in script
    assert profile_from_script(script)['server'] == 'http://127.0.0.1:8941'
    assert subprocess.run(['/bin/bash', '-n'], input=script, text=True).returncode == 0


async def test_prepare_is_idempotent_and_changed_retries_are_refused(terminal):
    one = await (await prepare(terminal)).json()
    two = await (await prepare(terminal)).json()
    assert one == two
    assert terminal[1].wheel.await_count == 1
    response = await prepare(terminal, name='Other device')
    assert response.status == 409


async def test_arbitrary_server_and_platform_are_refused_before_download(terminal):
    assert (await prepare(terminal, server='https://attacker.example')).status == 409
    assert (await prepare(terminal, server='http://remote.example')).status == 409
    assert (await prepare(terminal, platform='windows')).status == 400
    assert terminal[1].wheel.await_count == 0


async def test_one_use_grant_is_not_api_auth_and_device_auth_survives_restart(terminal):
    client, manager, cookie, _ = terminal
    device, profile, receipt = await issued(terminal)
    assert (await client.get('/api/state', headers={'Authorization': 'Bearer ' + profile['grant']})).status == 401
    assert (await client.post('/api/terminal/redeem', json={'grant': profile['grant']})).status == 401
    headers = {'Authorization': 'Bearer ' + device['token']}
    assert (await client.get('/api/actions', headers=headers)).status == 200
    restored = TerminalDevices(manager.data_dir)
    assert restored.identify(device['token']) == device['id']
    stored = restored.path.read_text()
    assert device['token'] not in stored and profile['grant'] not in stored
    assert restored.path.stat().st_mode & 0o777 == 0o600
    listing = await (await client.post('/api/actions', headers=cookie,
        json={'action': 'terminal.devices'})).json()
    assert listing['devices'][0]['name'] == 'My Mac'
    assert device['token'] not in json.dumps(listing)


async def test_revocation_closes_existing_stream_and_refuses_new_commands(terminal):
    client, manager, cookie, app = terminal
    device, _, _ = await issued(terminal)
    headers = {'Authorization': 'Bearer ' + device['token']}
    stream = await client.get('/api/events', headers=headers)
    assert stream.status == 200
    await stream.content.readline()
    response = await client.post('/api/actions', headers=cookie,
        json={'action': 'terminal.revoke', 'args': {'id': device['id']}})
    assert response.status == 200
    await asyncio.wait_for(stream.content.read(), 2)
    assert (await client.get('/api/actions', headers=headers)).status == 401
    assert manager.devices.listing() == []


async def test_revocation_detaches_request_without_cancelling_accepted_work(terminal, monkeypatch):
    client, manager, cookie, app = terminal
    device, _, _ = await issued(terminal)
    started, finish, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    dispatch = app['service'].dispatch
    async def waiting_dispatch(action, *args, **kwargs):
        if action != 'test.accepted-work':
            return await dispatch(action, *args, **kwargs)
        started.set()
        await finish.wait()
        completed.set()
        return {'accepted': True}
    monkeypatch.setattr(app['service'], 'dispatch', waiting_dispatch)
    request = asyncio.create_task(client.post('/api/actions',
        headers={'Authorization': 'Bearer ' + device['token']}, json={'action': 'test.accepted-work'}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        response = await client.post('/api/actions', headers=cookie,
            json={'action': 'terminal.revoke', 'args': {'id': device['id']}})
        assert response.status == 200
        finish.set()
        await asyncio.wait_for(completed.wait(), 2)
    finally:
        finish.set()
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


def test_reused_preparation_id_never_replaces_existing_device_credential(tmp_path):
    devices = TerminalDevices(tmp_path)
    first_grant, _ = devices.grant('First Mac', 'a' * 32)
    first = devices.redeem(first_grant)
    second_grant, _ = devices.grant('Second Mac', 'a' * 32)
    second = devices.redeem(second_grant)
    assert first['id'] != second['id']
    assert devices.identify(first['token']) == first['id']
    assert devices.identify(second['token']) == second['id']


async def test_expired_grants_downloads_and_cross_origin_enrollment_are_refused(terminal, monkeypatch):
    client, manager, cookie, _ = terminal
    receipt = await (await prepare(terminal)).json()
    script = await (await client.get(receipt['installer']['downloadUrl'], headers=cookie)).text()
    profile = profile_from_script(script)
    assert (await client.post('/api/terminal/redeem', json={'grant': profile['grant']}, headers={'Origin': 'https://attacker.example'})).status == 403
    monkeypatch.setattr('amplifier_web.terminal_devices.time.time', lambda: profile['expiresAt'] + 1)
    assert (await client.post('/api/terminal/redeem', json={'grant': profile['grant']})).status == 401
    assert (await client.get(receipt['installer']['downloadUrl'], headers=cookie)).status == 410


async def test_download_failure_does_not_create_grant_or_installer(terminal, monkeypatch):
    client, manager, cookie, _ = terminal
    monkeypatch.setattr(manager, 'wheel', AsyncMock(side_effect=ValueError('Release access is unavailable.')))
    response = await prepare(terminal)
    assert response.status == 409
    assert not list(manager.directory.glob('*.sh'))
    assert manager.devices.read()['grants'] == {}


async def test_external_tls_does_not_embed_old_local_ca_and_missing_active_ca_fails(terminal, monkeypatch):
    client, manager, cookie, _ = terminal
    monkeypatch.setattr('amplifier_web.terminal_setup.ca_bytes', lambda _: b'old local CA')
    manager.tls_method = 'external'
    receipt = await (await prepare(terminal)).json()
    script = await (await client.get(receipt['installer']['downloadUrl'], headers=cookie)).text()
    assert profile_from_script(script)['ca'] == ''
    manager.tls_method = 'ca'
    monkeypatch.setattr('amplifier_web.terminal_setup.ca_bytes', lambda _: None)
    with pytest.raises(ValueError, match='CA is unavailable'):
        await manager.perform('terminal.prepare', {'server': 'http://127.0.0.1:8941',
            'platform': 'macos-arm64', 'name': 'Second Mac'}, 'another-id')


async def test_remote_plain_http_cannot_download_or_redeem_setup_credentials(terminal):
    client, manager, cookie, app = terminal
    app['permitted_hosts'] = app['permitted_hosts'] | {'remote.example'}
    receipt = await (await prepare(terminal)).json()
    headers = {**cookie, 'Host': 'remote.example:8941'}
    response = await client.get(receipt['installer']['downloadUrl'], headers=headers)
    assert response.status == 403
    response = await client.post('/api/terminal/redeem', headers={'Host': 'remote.example:8941'}, json={'grant': 'invalid'})
    assert response.status == 403


async def test_agent_and_web_use_same_installation_path(terminal):
    client, manager, cookie, app = terminal
    result = await app['service'].dispatch('terminal.prepare',
        {'server': 'http://127.0.0.1:8941', 'platform': 'linux-arm64', 'name': 'Linux terminal'},
        origin='agent', command_id='agent-setup')
    response = await client.get(result['installer']['downloadUrl'], headers=cookie)
    assert response.status == 200
    assert "!= 'Linux'" in await response.text()


def test_saved_connection_refuses_credential_path_escape(tmp_path, monkeypatch):
    from amplifier_web.terminal_cli import saved
    monkeypatch.setenv('AMPLIFIER_TERMINAL_HOME', str(tmp_path))
    (tmp_path / 'default.json').write_text(json.dumps({'id': '../other'}))
    with pytest.raises(ValueError, match='incomplete'):
        saved()


def test_managed_launcher_does_not_send_saved_credentials_to_explicit_server(tmp_path, monkeypatch):
    import sys
    from amplifier_web import cli
    record = {'server': 'https://saved.example', 'tokenFile': '/saved/private-token',
              'caFile': '/saved/private-ca', 'environment': '/saved/environment'}
    monkeypatch.setattr('amplifier_web.terminal_cli.saved', lambda: record)
    calls = []
    monkeypatch.setattr(subprocess, 'call', lambda args: calls.append(args) or 0)
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', 'tui', '--server', 'https://other.example', '--list-sessions'])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    assert '/saved/private-token' not in calls[0] and '/saved/private-ca' not in calls[0]
    assert 'https://other.example' in calls[0]


def test_install_defaults_to_saved_service_but_explicit_server_has_no_inherited_secrets(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web.terminal_cli import local_options
    record = {'server': 'https://spark.example', 'tokenFile': '/private/spark-token', 'caFile': '/private/spark-ca'}
    monkeypatch.setattr('amplifier_web.terminal_cli.saved', lambda: record)
    args = SimpleNamespace(server=None, token_file=None, ca_file=None)
    assert local_options(args, tmp_path) == ('https://spark.example', '/private/spark-token', '/private/spark-ca')
    args.server = 'https://different.example'
    assert local_options(args, tmp_path) == ('https://different.example', None, None)


def test_missing_local_credentials_open_browser_instead_of_reading_missing_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web.terminal_cli import install
    monkeypatch.setattr('amplifier_web.terminal_cli.saved', lambda: None)
    monkeypatch.setattr('amplifier_web.terminal_cli.load_server_config', lambda _: {'port': 8941, 'tls': {'method': 'none'}})
    monkeypatch.delenv('AMPLIFIER_UNIFIED_TOKEN', raising=False)
    opened = []
    monkeypatch.setattr('amplifier_web.terminal_cli.webbrowser.open', opened.append)
    install(SimpleNamespace(setup_file=None, server=None, token_file=None, ca_file=None), tmp_path)
    assert opened == ['http://127.0.0.1:8941/setup/terminal']


def test_enrollment_tls_failure_does_not_replace_previous_local_installation(tmp_path, monkeypatch):
    import importlib.metadata
    import sys
    import types
    import urllib.error
    from amplifier_web.terminal_install_client import install
    native = tmp_path / 'native'
    native.write_text('fixture')
    native.chmod(0o700)
    module = types.ModuleType('amplifier_tui.launcher')
    module.executable = lambda _: str(native)
    monkeypatch.setitem(sys.modules, 'amplifier_tui', types.ModuleType('amplifier_tui'))
    monkeypatch.setitem(sys.modules, 'amplifier_tui.launcher', module)
    monkeypatch.setattr(importlib.metadata, 'version', lambda _: '0.4.0rc1')
    class RefuseTLS:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError('certificate verify failed')
    monkeypatch.setattr('urllib.request.build_opener', lambda *args: RefuseTLS())
    (tmp_path / 'default.json').write_text('{"id":"previous"}')
    with pytest.raises(urllib.error.URLError):
        install({'server': 'https://spark.example', 'expiresAt': time.time()+60,
                 'ca': '', 'grant': 'fixture', 'name': 'Mac'}, tmp_path, tmp_path / 'candidate')
    assert (tmp_path / 'default.json').read_text() == '{"id":"previous"}'
    assert not (tmp_path / 'connections').exists()


async def test_registration_correlates_to_exact_setup_without_exposing_grant(terminal):
    client, manager, cookie, _ = terminal
    device, profile, receipt = await issued(terminal)
    response = await client.post('/api/actions', headers=cookie,
                                 json={'action': 'terminal.devices'})
    listing = (await response.json())['devices']
    assert listing[0]['id'] == device['id']
    assert listing[0]['setupId'] == receipt['installer']['id']
    assert listing[0]['setupExpiresAt'] == receipt['installer']['expiresAt']
    assert profile['grant'] not in json.dumps(listing)
    assert device['token'] not in json.dumps(listing)
    # An older enrollment has no setup correlation; it must remain listable.
    with manager.devices.transaction() as state:
        del state['devices'][device['id']]['setupId']
        del state['devices'][device['id']]['setupExpiresAt']
    assert manager.devices.listing() == [{k: listing[0][k] for k in ('id', 'name', 'createdAt')}]


def test_reused_setup_identity_keeps_registration_attempts_distinct(tmp_path, monkeypatch):
    devices = TerminalDevices(tmp_path)
    first_grant, first_expiry = devices.grant('My Mac', 'a' * 32)
    first = devices.redeem(first_grant)
    monkeypatch.setattr('amplifier_web.terminal_devices.time.time', lambda: first_expiry + 1)
    second_grant, second_expiry = devices.grant('My Mac', 'a' * 32)
    second = devices.redeem(second_grant)
    rows = {row['id']: row for row in devices.listing()}
    assert rows[first['id']]['setupId'] == rows[second['id']]['setupId']
    assert rows[first['id']]['setupExpiresAt'] == first_expiry
    assert rows[second['id']]['setupExpiresAt'] == second_expiry
    assert first_expiry != second_expiry


def test_mac_shortcut_runs_without_login_shell_and_preserves_existing_launcher(tmp_path, monkeypatch):
    import plistlib
    import shlex
    from pathlib import Path
    from amplifier_web import terminal_install_client as installer
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    root = tmp_path / '.local/share/amplifier-terminal'
    connection = root / 'connections' / ('a' * 32)
    connection.mkdir(parents=True)
    (connection / 'launch').write_text('#!/bin/sh\nprintf "direct-client-started"\n')
    applications = tmp_path / 'Applications'
    applications.mkdir()
    old = applications / 'Amplifier Terminal - spark.example - aaaaaaaa.command'
    old.write_text('old launcher stays intact')
    # A login-shell startup prompt would block if the replacement ran it.
    (tmp_path / '.zshrc').write_text('echo unexpected-startup-prompt; read answer\n')
    shortcut = installer.create_shortcut(root, connection, 'https://spark.example:8443', 'a' * 32)
    settings = plistlib.loads(shortcut.read_bytes())
    assert shortcut.parent == applications and shortcut.suffix == '.terminal'
    assert settings['RunCommandAsShell'] is True
    assert settings['type'] == 'Window Settings'
    assert settings['shellExitAction'] == 1
    argv = shlex.split(settings['CommandString'])
    assert argv == ['/bin/sh', str(connection / 'launch')]
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and result.stdout == 'direct-client-started'
    assert old.read_text() == 'old launcher stays intact'
    assert shortcut.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='already exists'):
        installer.create_shortcut(root, connection, 'https://spark.example:8443', 'a' * 32)


def test_linux_shortcut_still_executes_saved_connection_and_arguments(tmp_path, monkeypatch):
    from amplifier_web import terminal_install_client as installer
    monkeypatch.setattr(installer.sys, 'platform', 'linux')
    connection = tmp_path / 'connection with spaces'
    connection.mkdir()
    launch = connection / 'launch'
    launch.write_text('#!/bin/sh\nprintf "%s" "$1"\n')
    launch.chmod(0o700)
    shortcut = installer.create_shortcut(tmp_path, connection, 'https://spark.example', 'a' * 32)
    result = subprocess.run([str(shortcut), 'argument with spaces'], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and result.stdout == 'argument with spaces'
    assert shortcut.suffix == '.command'


def test_terminal_command_uses_current_saved_connection_and_forwards_args(tmp_path):
    import sys
    from pathlib import Path
    from amplifier_web.terminal_install_client import create_terminal_command
    root = tmp_path / 'client with spaces'
    root.mkdir()
    for identity, label in [('a' * 32, 'first'), ('b' * 32, 'second')]:
        connection = root / 'connections' / identity
        connection.mkdir(parents=True)
        launch = connection / 'launch'
        launch.write_text('#!/bin/sh\nprintf "%s:%s" ' + label + ' "$1"\n')
        launch.chmod(0o700)
    (root / 'default.json').write_text(json.dumps({'id': 'a' * 32}))
    # Resolve out of the venv, so retiring a failed candidate cannot break it.
    env = tmp_path / 'candidate'
    (env / 'bin').mkdir(parents=True)
    (env / 'bin/python').symlink_to(Path(sys.executable).resolve())
    command, alias = create_terminal_command(root, env)
    assert alias == root / 'bin/amplifier-terminal'
    (env / 'bin/python').unlink()
    first = subprocess.run([str(alias), 'two words'], capture_output=True, text=True, timeout=5)
    assert first.returncode == 0 and first.stdout == 'first:two words'
    (root / 'default.json').write_text(json.dumps({'id': 'b' * 32}))
    second = subprocess.run([str(alias), '--new'], capture_output=True, text=True, timeout=5)
    assert second.returncode == 0 and second.stdout == 'second:--new'
    assert command.stat().st_mode & 0o777 == 0o700


def test_terminal_command_preserves_unrelated_short_command(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from amplifier_web.terminal_install_client import create_terminal_command
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    root = tmp_path / '.local/share/amplifier-terminal'
    root.mkdir(parents=True)
    binary = tmp_path / '.local/bin/amplifier-terminal'
    binary.parent.mkdir(parents=True)
    binary.write_text('existing unrelated executable')
    command, alias = create_terminal_command(root, Path(sys.prefix))
    assert alias is None and command == root / 'launch'
    assert binary.read_text() == 'existing unrelated executable'


def test_terminal_command_handles_incomplete_default_without_path_escape(tmp_path):
    import sys
    from pathlib import Path
    from amplifier_web.terminal_install_client import create_terminal_command
    command, alias = create_terminal_command(tmp_path, Path(sys.prefix))
    for contents in [None, '{invalid', json.dumps({'id': '../other'}), json.dumps({'id': 'a' * 32})]:
        if contents is not None:
            (tmp_path / 'default.json').write_text(contents)
        result = subprocess.run([str(alias)], capture_output=True, text=True, timeout=5)
        assert result.returncode == 1
        assert 'Finish setup' in result.stderr
        assert 'Traceback' not in result.stderr and str(tmp_path) not in result.stderr
