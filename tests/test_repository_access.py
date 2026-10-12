"""Public downloads must not depend on a usable signed-in GitHub account."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
from unittest.mock import AsyncMock

import httpx
import pytest

from amplifier_web import app_updates, reset, updates, bundles, smart_tools


def test_reset_reads_public_release_before_restricted_cli(monkeypatch):
    monkeypatch.setattr(reset.shutil, 'which', lambda _: '/fixture/gh')
    monkeypatch.setattr(reset, 'command', lambda *a, **kw: pytest.fail('Must not invoke signed-in gh'))
    requests = []
    def public(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b'{"tag_name":"v1.2.3"}')
    monkeypatch.setattr(reset.urllib.request, 'urlopen', public)
    assert reset.release_metadata({})['tag_name'] == 'v1.2.3'
    assert not requests[0].has_header('Authorization')


@pytest.mark.parametrize('anonymous', [False, True])
def test_reset_auth_fallback_is_optional(monkeypatch, anonymous):
    monkeypatch.setattr(reset.shutil, 'which', lambda _: '/fixture/gh')
    def unavailable(*a, **kw):
        raise urllib.error.HTTPError('https://api.github.com', 403, 'rate limit', {}, None)
    monkeypatch.setattr(reset.urllib.request, 'urlopen', unavailable)
    calls = []
    def command(*a, **kw):
        calls.append(a)
        return subprocess.CompletedProcess(a, 0, '{"tag_name":"v1.2.3"}')
    monkeypatch.setattr(reset, 'command', command)
    if anonymous:
        with pytest.raises(RuntimeError, match='public release'):
            reset.release_metadata({'AMPLIFIER_GITHUB_ACCESS': 'anonymous'})
        assert not calls
    else:
        assert reset.release_metadata({})['tag_name'] == 'v1.2.3'
        assert calls[0][:2] == ('gh', 'api')


@pytest.mark.parametrize('status,anonymous', [(200, False), (403, False), (404, True)])
async def test_update_metadata_public_first_and_explicit_no_auth(monkeypatch, status, anonymous):
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'anonymous' if anonymous else 'auto')
    monkeypatch.setattr(app_updates.shutil, 'which', lambda _: '/fixture/gh')
    authenticated = AsyncMock(return_value='{"tag_name":"v1.2.3"}')
    monkeypatch.setattr(app_updates, 'process', authenticated)
    requests = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers):
            requests.append(headers)
            return httpx.Response(status, json={'tag_name': 'v1.2.3'}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    if anonymous:
        with pytest.raises(RuntimeError, match='release channel'):
            await app_updates.github_read('repos/example/public/releases/latest')
    else:
        assert json.loads(await app_updates.github_read('repos/example/public/releases/latest'))['tag_name'] == 'v1.2.3'
    assert 'Authorization' not in requests[0]
    assert authenticated.await_count == (1 if status == 403 else 0)


def test_anonymous_git_ignores_personal_headers_helpers_and_rewrites(tmp_path):
    config = tmp_path / 'gitconfig'
    config.write_text('[url "ssh://git@invalid.example/"]\n insteadOf = https://github.com/\n'
                      '[http]\n extraHeader = Authorization: synthetic-credential\n'
                      '[credential]\n helper = !exit 77\n')
    original = {**os.environ, 'GIT_CONFIG_GLOBAL': str(config), 'GH_TOKEN': 'synthetic-token',
                'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.extraHeader',
                'GIT_CONFIG_VALUE_0': 'Authorization: inherited-fixture',
                'HTTPS_PROXY': 'http://proxy.example:8080'}
    env = reset.repository_environment(original, anonymous=True)
    assert env['HTTPS_PROXY'] == original['HTTPS_PROXY']
    assert 'GH_TOKEN' not in env
    output = subprocess.run(['git', 'config', '--list'], env=env, cwd=tmp_path,
                            check=True, capture_output=True, text=True).stdout
    assert 'synthetic-credential' not in output and 'inherited-fixture' not in output
    assert 'insteadof' not in output and 'helper=!exit' not in output
    assert 'credential.helper=\n' in output and 'http.extraheader=\n' in output
    assert config.read_text().count('synthetic-credential') == 1
    assert original['GH_TOKEN'] == 'synthetic-token'


async def test_update_subprocess_carries_anonymous_policy_to_nested_installers(monkeypatch):
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'anonymous')
    monkeypatch.setenv('GH_TOKEN', 'synthetic-token')
    output = await updates.process(sys.executable, '-c',
        'import os,json; print(json.dumps({k:v for k,v in os.environ.items() if k.startswith(("GIT_CONFIG", "GH_TOKEN", "AMPLIFIER_GITHUB_ACCESS"))}))')
    env = json.loads(output)
    assert env['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'
    assert env['GIT_CONFIG_GLOBAL'] == os.devnull
    assert 'GH_TOKEN' not in env
    assert smart_tools._install_environment()['GIT_CONFIG_GLOBAL'] == os.devnull
    assert smart_tools._install_environment()['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'


async def test_bundle_fetch_inherits_anonymous_policy(monkeypatch):
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'anonymous')
    child = AsyncMock()
    child.communicate.return_value = (b'index', b'')
    child.returncode = 0
    create = AsyncMock(return_value=child)
    monkeypatch.setattr(bundles.asyncio, 'create_subprocess_exec', create)
    assert await bundles.git('ls-remote', 'https://github.com/example/public') == b'index'
    env = create.call_args.kwargs['env']
    assert env['GIT_CONFIG_GLOBAL'] == os.devnull
    assert env['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'
