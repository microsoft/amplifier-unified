"""One Windows credential at both addresses; no personal PC or real password."""
import asyncio
import getpass
import io
import os
from pathlib import Path
import re
import ssl
import sys
from types import SimpleNamespace
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
import pytest

from amplifier_web import auth, browser_entry as entry, cli, deployment
from test_browser_code import (LOCAL, REMOTE, PASSWORD, FORM, call, files, home, host,
                               local_ticket, open_ticket, owner_page, signed_in, with_key)


def test_username_is_required_normalized_and_counted_like_a_password(host, capsys, caplog):
    root = host['service'].data_dir
    entry.set_password(root, PASSWORD)
    entry.save_sign_in_settings(root, username='OwnerＫ')
    for address in (LOCAL, REMOTE):
        host['browser_key'] = entry.SavedKey()
        for username, password in (('wrong', PASSWORD), ('OwnerK', 'wrong password'), ('', PASSWORD), ('OwnerK', '')):
            result = with_key(host, password, username=username, at=address)
            assert result.status == 303 and result.headers['Location'] == '/login?error=password'
            assert auth.SESSION_COOKIE not in result.cookies
        assert host['browser_key'].failures == 4
        # The fifth wrong username starts the same wait as a wrong password.
        assert with_key(host, PASSWORD, username='wrong', at=address).headers['Location'] == '/login?error=password'
        waiting = with_key(host, PASSWORD, username='ownerk', at=address)
        assert waiting.headers['Location'] == '/login?error=wait'
        assert host['browser_key'].failures == 5
        host['browser_key']._until = 0
        result = with_key(host, PASSWORD, username='ownerk', at=address)
        assert signed_in(result)
        session = result.cookies[auth.SESSION_COOKIE]
        assert auth.session_is_remote(session.value)
        assert session['secure'] and session['httponly'] and session['samesite'] == 'Strict'
        assert result.headers['Cache-Control'] == 'no-store'
        assert owner_page(host, session=session.value)[0].status == 403
    assert PASSWORD not in capsys.readouterr().out + caplog.text
    assert all(PASSWORD.encode() not in path.read_bytes() for path in root.rglob('*') if path.is_file())


def test_key_needs_the_same_username_and_removed_or_signed_out_password_never_opens(host):
    root = host['service'].data_dir
    entry.save_sign_in_settings(root, username='name')
    key, _ = entry.make_key(root)
    assert not signed_in(with_key(host, key, username='wrong', at=LOCAL))
    assert signed_in(with_key(host, key, username='NAME', at=LOCAL))
    entry.remove_key(root)
    assert not signed_in(with_key(host, key, username='name', at=LOCAL))
    entry.set_password(root, PASSWORD)
    auth.sign_out_all(root)
    assert not signed_in(with_key(host, PASSWORD, username='name', at=LOCAL))


@pytest.mark.parametrize('address', [LOCAL, REMOTE])
def test_same_form_only_with_a_saved_password_and_no_reflected_secrets(host, address):
    def page(query=''):
        return call(host, '/login' + query, method='GET', at=address, origin=None)
    empty = page()
    assert 'autocomplete="username"' not in empty.text and 'type="password"' not in empty.text
    assert entry._WITHOUT_PASSWORD in empty.text
    entry.set_password(host['service'].data_dir, PASSWORD)
    shown = page()
    assert 'autocomplete="username"' in shown.text and 'autocomplete="current-password"' in shown.text
    assert shown.headers['Cache-Control'] == 'no-store'
    assert 'That username or password did not work. Check both and try again.' in page('?error=password').text
    host['browser_key']._until = host['browser_key'].clock() + 1.5
    assert 'Too many wrong sign-in entries. No password is checked for 2 seconds.' in page('?error=wait').text
    if address == LOCAL:
        assert 'src="/browser-entry.js"' in shown.text and 'name="ticket"' in shown.text
    else:
        assert '<script' not in shown.text
    assert PASSWORD not in page('?password=' + PASSWORD).text


def test_local_origin_csrf_and_socket_checks(host):
    entry.set_password(host['service'].data_dir, PASSWORD)
    for options in ({'origin': None}, {'origin': REMOTE}, {'secure': False},
                    {'peer': '192.168.1.20'}, {'sock': '192.168.1.20'},
                    {'headers': {'Cookie': 'wrong'}},
                    {'body': urlencode({'csrf': 'wrong', 'username': getpass.getuser(), 'password': PASSWORD})}):
        result = with_key(host, PASSWORD, at=LOCAL, **options)
        assert result.status == 403 and auth.SESSION_COOKIE not in result.cookies
    assert host['browser_key'].failures == 0


def test_first_use_save_and_not_now_are_owner_only_and_remembered(host):
    root = host['service'].data_dir
    entry.save_sign_in_settings(root, dismissed=False)
    result = open_ticket(host, local_ticket(host))
    assert result.headers['Location'] == entry.PASSWORD_PATH + '?first=1'
    session = result.cookies[auth.SESSION_COOKIE].value
    page, _ = owner_page(host, session=session, query='?first=1')
    assert f'value="{getpass.getuser()}"' in page.text
    assert '>Save</button>' in page.text and '>Not now</button>' in page.text
    assert 'Amplifier saves only a salted hash of the password.' in page.text
    skipped, _ = owner_page(host, method='POST', session=session, form={'action': 'skip'})
    assert skipped.status == 303 and skipped.headers['Location'] == '/'
    assert deployment.load_server_config(root)['sign_in_setup_dismissed'] is True
    assert signed_in(open_ticket(host, local_ticket(host)))
    assert 'autocomplete="username"' in owner_page(host)[0].text
    entry.save_sign_in_settings(root, dismissed=False)
    token = entry._owner_csrf(SimpleNamespace(app=host), session)
    result = call(host, entry.PASSWORD_PATH + '?first=1', headers={
        'Content-Type': FORM, 'Cookie': f'{auth.SESSION_COOKIE}={session}'},
        body=urlencode({'csrf': token, 'action': 'set', 'username': 'Chosen name',
                        'password': PASSWORD, 'confirm': PASSWORD}))
    assert result.status == 303 and result.headers['Location'] == '/'
    assert deployment.load_server_config(root)['sign_in_username'] == 'Chosen name'
    assert signed_in(with_key(host, PASSWORD, username='chosen NAME', at=LOCAL))
    assert signed_in(open_ticket(host, local_ticket(host)))


def test_existing_password_uses_windows_account_name_without_changing_record(host):
    root = host['service'].data_dir
    entry.set_password(root, PASSWORD)
    before = auth.sign_in_key_bytes(root)
    assert deployment.load_server_config(root)['sign_in_username'] is None
    assert signed_in(with_key(host, PASSWORD, username=getpass.getuser(), at=LOCAL))
    assert before == auth.sign_in_key_bytes(root)
    assert re.fullmatch(rb'\d+ scrypt 15 8 1 [a-f0-9]{32} [a-f0-9]{64}\n', before)


def test_posix_page_is_byte_for_byte_the_shipped_page_and_pam_stays(host, monkeypatch):
    monkeypatch.setattr(entry, 'is_windows', lambda: False)
    for address in (LOCAL, REMOTE):
        result = call(host, '/login', method='GET', at=address, origin=None)
        csrf = result.cookies[auth.CSRF_COOKIE].value
        raw = (Path(auth.__file__).parent / 'static/login.html').read_text(encoding='utf-8')
        expected = raw.replace('__CSRF__', csrf).replace('__NEXT__', '/').replace('__ERROR__', '')
        assert result.text == host['service'].instance_branding.document(expected, login=True)
    host.router.add_post('/login', auth.post_login)
    monkeypatch.setattr(auth, 'authenticate_pam', lambda user, password: (user, password) == ('host-owner', PASSWORD))
    csrf = auth.new_csrf(host['session_secret'])
    result = call(host, '/login', headers={'Content-Type': FORM, 'Cookie': f'{auth.CSRF_COOKIE}={csrf}'},
                  body=urlencode({'csrf': csrf, 'username': 'host-owner', 'password': PASSWORD}))
    assert signed_in(result)


@pytest.fixture
def password_command(home, monkeypatch):
    from amplifier_web import application_generations, host_dependencies
    monkeypatch.setattr(host_dependencies, 'prepare_environment', lambda: None)
    monkeypatch.setattr(application_generations, 'delegate', lambda: None)
    monkeypatch.setattr(entry, 'is_windows', lambda: True)
    def run(*arguments):
        monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(home), 'browsers', 'password', *arguments])
        return cli.main()
    return run


def test_command_sets_changes_removes_without_host_or_plaintext(password_command, home, monkeypatch, capsys, caplog):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(PASSWORD + '\n'))
    password_command('--stdin', '--username', 'CLI name')
    assert deployment.load_server_config(home)['sign_in_username'] == 'CLI name'
    before = auth.read_sign_in_key(home)
    passwords = iter(['another made-up password', 'another made-up password'])
    monkeypatch.setattr(getpass, 'getpass', lambda prompt: next(passwords))
    password_command()
    assert before != auth.read_sign_in_key(home)
    printed = capsys.readouterr().out + caplog.text
    assert 'Username: CLI name' in printed and "this PC's local Amplifier address" in printed
    for secret in (PASSWORD, 'another made-up password'):
        assert secret not in printed
        assert all(secret.encode() not in path.read_bytes() for path in home.rglob('*') if path.is_file())
    password_command('--remove')
    assert auth.read_sign_in_key(home) is None


@pytest.mark.parametrize('entries, message', [(['first password', 'second password'], 'not the same'),
                                             (['short', 'short'], 'at least 12')])
def test_command_refuses_mismatch_and_short_password(password_command, home, monkeypatch, entries, message, capsys):
    values = iter(entries)
    monkeypatch.setattr(getpass, 'getpass', lambda prompt: next(values))
    with pytest.raises(SystemExit, match=message) as failure:
        password_command('--username', 'unused')
    assert auth.read_sign_in_key(home) is None
    assert deployment.load_server_config(home)['sign_in_username'] is None
    for text in entries:
        assert text not in str(failure.value) + capsys.readouterr().out


def test_command_posix_and_arguments_never_accept_password(password_command, monkeypatch, capsys):
    monkeypatch.setattr(entry, 'is_windows', lambda: False)
    with pytest.raises(SystemExit, match='host account password'):
        password_command()
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', 'browsers', 'password', '--password', PASSWORD])
    with pytest.raises(SystemExit):
        cli._parse()
    for shell in ('bash', 'zsh', 'fish'):
        cli._print_completion(shell)
        assert 'password' in capsys.readouterr().out


@pytest.mark.parametrize('text', ['short', 'x' * 257])
def test_stdin_password_length_limits(password_command, home, monkeypatch, text):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(text + '\n'))
    with pytest.raises(SystemExit, match='Use at (least 12|most 256) characters'):
        password_command('--stdin')
    assert auth.read_sign_in_key(home) is None


def test_command_never_falls_back_to_echo_or_prints_input_errors(password_command, home, monkeypatch, capsys):
    def echo_warning(prompt):
        raise getpass.GetPassWarning('No hidden input')
    monkeypatch.setattr(getpass, 'getpass', echo_warning)
    with pytest.raises(SystemExit, match='not read'):
        password_command()
    assert auth.read_sign_in_key(home) is None
    def input_error(prompt):
        raise OSError(PASSWORD)
    monkeypatch.setattr(getpass, 'getpass', input_error)
    with pytest.raises(SystemExit) as stopped:
        password_command()
    assert PASSWORD not in str(stopped.value) + capsys.readouterr().out


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows private files and real HTTPS client')
def test_native_windows_local_https_username_password(home, unused_tcp_port):
    """Runs in windows-local-connection with real private records, TLS and requests."""
    from amplifier_web import tls
    from amplifier_web.instance_branding import InstanceBranding
    native_root = home
    async def journey():
        config = deployment.load_server_config(native_root, overrides={'port': unused_tcp_port})
        config = tls.setup_local_ca(native_root, config)
        origin = f'https://127.0.0.1:{unused_tcp_port}'
        app = web.Application(middlewares=[auth.auth_required])
        app['control_token'] = auth.control_token(native_root)
        app['session_secret'] = auth.session_secret(native_root)
        app['server_config'] = config
        app['allowed_origins'] = frozenset({origin})
        app['sessions'] = auth.Sessions()
        app['service'] = SimpleNamespace(data_dir=native_root, instance_branding=InstanceBranding(native_root, config),
            update_manager=SimpleNamespace(running_identity={'instanceId': 'b' * 32}))
        entry.initialize(app)
        assert isinstance(app['browser_key'], entry.SavedKey)
        app.router.add_get('/login', auth.login_page)
        app.router.add_post(entry.KEY_REDEEM_PATH, entry.key_redeem)
        app.router.add_get(entry.PASSWORD_PATH, entry.password_page)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, '127.0.0.1', unused_tcp_port, ssl_context=tls.ssl_context(native_root, config)).start()
            context = ssl.create_default_context(cadata=tls.exported_ca_bytes(native_root).decode("ascii"))
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context),
                                             cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                async with client.get(origin + '/login') as response:
                    assert 'type="password"' not in await response.text()
                entry.set_password(native_root, PASSWORD)
                entry.save_sign_in_settings(native_root, username='HTTPS owner')
                async with client.get(origin + '/login') as response:
                    text = await response.text()
                    assert 'autocomplete="username"' in text and 'autocomplete="current-password"' in text
                    csrf = response.cookies[auth.CSRF_COOKIE].value
                async with client.post(origin + entry.KEY_REDEEM_PATH, headers={'Origin': origin},
                        data={'csrf': csrf, 'username': 'https OWNER', 'password': PASSWORD}, allow_redirects=False) as response:
                    assert response.status == 303
                    session = response.cookies[auth.SESSION_COOKIE]
                    assert session['secure'] and session['httponly'] and auth.session_is_remote(session.value)
                async with client.get(origin + entry.PASSWORD_PATH, allow_redirects=False) as response:
                    assert response.status == 403
        finally:
            await runner.cleanup()
    asyncio.run(journey())
    print('Native Windows: local form, real HTTPS sign-in, remote-marked cookie, owner-page refusal passed.')
